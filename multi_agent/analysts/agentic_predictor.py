#!/usr/bin/env python3
"""
多 Agent LLM 预测系统 - 统一版（支持ETF/个股/期货，线程池并行）

输入: 任意标的(ticker, name, sector, category)
输出: 结构化预测 JSON，写入 llm_predictions.db 的 agentic_predictions 表

Agent 流程:
1. 技术面分析师 (technical_analyst)  →  技术评分、趋势、回测
2. 基本面分析师 (fundamentals_analyst) →  估值、财务评分（个股/ETF）
3. 新闻情绪分析师 (news_analyst)  →  情绪分数、关键词（个股/ETF）
4. Bull Agent  →  收集看涨证据
5. Bear Agent  →  收集看跌证据
6. 研究经理 (research_manager)  →  综合裁决最终方向/置信度/目标价/止损

统一性:
- 所有资产走同一 predict_one 接口
- 统一信号、置信度、仓位阈值
- 统一 horizon 1/3/5/10日
- 统一验证和回测
"""
import sys
import os
import json
import sqlite3
import warnings
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, MULTI_AGENT)

warnings.filterwarnings('ignore')

from analysts import fundamentals_analyst, news_analyst
from core.debate_engine import DebateEngine
from core.data_layer import get_realtime_price, is_futures, get_stock_data, calc_technical_indicators, multi_period_backtest, tf_quotes
from core.scenario_backtests import scenario_backtests, recommend_scenario, SCENARIO_NAME_CN, SCENARIO_DESC
from core.db import get_predictions_conn, save_predictions as _db_save_predictions
import pandas as pd

DB_PATH = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'llm_predictions.db')

# ============================================================
# 统一超参数配置（选股、预测、回测一致）
WEIGHTS = {
    'technical': 0.22,  # 复盘后降低：下跌趋势中技术面常逆势误导
    'fundamental': 0.25,
    'sentiment': 0.15,
    'macro': 0.18,      # 复盘后提高：宏观 bearish 时技术面容易失效
    'debate': 0.25,      # 复盘后提高：多空辩论比滞后技术更可靠
}

THRESHOLD = {
    'strong_bull': 62,
    'bull': 55,
    'neutral_high': 52,
    'neutral_low': 48,
    'bear': 43,
    'strong_bear': 38,
}

POSITION_MAP = {
    'bullish': 0.25,
    'bearish': 0.15,
    'neutral': 0.0,
}

HORIZON_THRESHOLD = {'strong': 1.5, 'weak': 0.5}
MAX_WORKERS = 4  # 线程池大小，避免数据源被封


def _get_conn() -> sqlite3.Connection:
    """向后兼容：提供预测数据库连接。"""
    return get_predictions_conn()


def _horizon_label(return_pct: float) -> str:
    if return_pct > HORIZON_THRESHOLD['strong']:
        return '看涨'
    elif return_pct > HORIZON_THRESHOLD['weak']:
        return '偏多'
    elif return_pct < -HORIZON_THRESHOLD['strong']:
        return '看跌'
    elif return_pct < -HORIZON_THRESHOLD['weak']:
        return '偏空'
    else:
        return '震荡'


def _calc_target_stop(current_price: float, signal: str, tech_snapshot: Dict, avg_return: float) -> Tuple[Optional[float], Optional[float]]:
    if current_price <= 0:
        return None, None

    if signal == 'bullish':
        target = current_price * (1 + max(abs(avg_return), 2.0) / 100)
        stop_candidates = [
            tech_snapshot.get('boll_down') or 0,
            tech_snapshot.get('ma20') or 0,
            current_price * 0.95,
        ]
        stop = max([s for s in stop_candidates if s > 0] or [current_price * 0.95])
        stop = min(stop, current_price * 0.97)
    elif signal == 'bearish':
        target = current_price * (1 - max(abs(avg_return), 2.0) / 100)
        stop_candidates = [
            tech_snapshot.get('boll_up') or 99999,
            tech_snapshot.get('ma20') or 99999,
            current_price * 1.05,
        ]
        stop = min([s for s in stop_candidates if s > current_price] or [current_price * 1.05])
        stop = max(stop, current_price * 1.03)
    else:
        target = current_price * (1 + avg_return / 100)
        stop = current_price * 0.98

    return round(target, 3), round(stop, 3)


def _manager_verdict(technical_report: Dict, fundamental_report: Dict, news_report: Dict,
                     bull_arg: Dict, bear_arg: Dict,
                     macro_report: Optional[Dict] = None) -> Dict:
    tech_score = technical_report.get('score', 50)
    tech_rating = technical_report.get('rating', '中性')
    tech_snapshot = technical_report.get('tech_snapshot', {})

    fund_score = fundamental_report.get('score', 50) if 'error' not in fundamental_report else 50
    news_score = (news_report.get('sentiment_score', 0) + 1) * 50
    macro_score = macro_report.get('macro_score', 50) if macro_report else 50

    bull_score = bull_arg.get('score', 0)
    bear_score = bear_arg.get('score', 0)
    net_debate = bull_score - bear_score

    weighted = (
        tech_score * WEIGHTS['technical'] +
        fund_score * WEIGHTS['fundamental'] +
        news_score * WEIGHTS['sentiment'] +
        macro_score * WEIGHTS['macro'] +
        (50 + net_debate * 8) * WEIGHTS['debate']
    )
    weighted = max(0, min(100, weighted))

    # 叠加宏观修正（单次评分，不循环放大）
    macro_override = 0
    macro_note = ""
    if macro_report:
        from analysts.macro_analyst import get_macro_score_override
        raw_signal = 'bullish' if weighted >= THRESHOLD['bull'] else 'bearish' if weighted <= THRESHOLD['bear'] else 'neutral'
        # 强干预：宏观 bearish 环境下直接拦截 bullish 信号
        if macro_report.get('macro_signal') == 'bearish' and raw_signal == 'bullish':
            weighted = THRESHOLD['bear'] - 1  # 强制压入 bearish 区间
            macro_override = -20
            macro_note = f"宏观 bearish 拦截 bullish ({macro_report.get('macro_score', 50)}/100)"
        else:
            macro_override = get_macro_score_override(macro_report, raw_signal)
            # 高波动环境下（如宏观 bearish 或市场广度<30），修正力度加倍
            breadth_score = macro_report.get('market_breadth', {}).get('score', 50)
            if macro_report.get('macro_signal') == 'bearish' and breadth_score < 30:
                macro_override *= 2.0
            elif macro_report.get('macro_signal') == 'bullish' and breadth_score > 70:
                macro_override *= 1.5
            weighted = max(0, min(100, weighted + macro_override))

    # 统一信号判定：收窄 neutral 范围，46-52 才为中性
    if weighted >= THRESHOLD['strong_bull']:
        signal = 'bullish'
    elif weighted >= THRESHOLD['bull']:
        signal = 'bullish'
    elif weighted <= THRESHOLD['strong_bear']:
        signal = 'bearish'
    elif weighted <= THRESHOLD['bear']:
        signal = 'bearish'
    else:
        signal = 'neutral'

    # 复盘硬规则：宏观 < 50 时禁止 bullish；技术面 < 55 且宏观 < 50 强制 bearish
    if macro_report and macro_report.get('macro_score', 50) < 50:
        if signal == 'bullish':
            signal = 'bearish' if tech_score < 55 else 'neutral'
            weighted = THRESHOLD['bear'] - 1 if tech_score < 55 else 50
        elif signal == 'neutral' and tech_score < 55:
            signal = 'bearish'
            weighted = THRESHOLD['bear'] - 1

    confidence = round(max(0.5, min(0.95, 0.5 + abs(weighted - 50) / 50 * 0.5 + min(abs(net_debate) * 0.08, 0.4))), 2)

    base_position = POSITION_MAP[signal]
    position_pct = round(min(base_position * confidence, 0.25), 3)

    support = tech_snapshot.get('boll_down') or tech_snapshot.get('ma60') or 0
    resistance = tech_snapshot.get('boll_up') or tech_snapshot.get('ma5') or 0

    macro_note = ""
    if macro_report:
        macro_note = f"宏观{macro_report.get('macro_signal', 'neutral')}({macro_report.get('macro_score', 50)}/100)"
    reasons = [
        f"技术面{tech_rating}({tech_score}/100)",
        f"基本面{fundamental_report.get('rating', 'N/A')}({fund_score}/100)",
        f"新闻情绪{news_report.get('sentiment_score', 0):+.2f}",
        f"多空辩论 看涨{bull_score} vs 看跌{bear_score}",
    ]
    if macro_note:
        reasons.append(macro_note)

    return {
        'signal': signal,
        'confidence': confidence,
        'weighted_score': round(weighted, 1),
        'position_pct': position_pct,
        'key_support': round(support, 3) if support else None,
        'key_resistance': round(resistance, 3) if resistance else None,
        'reasoning': " | ".join(reasons),
        'bull_points': bull_arg.get('points', []),
        'bear_points': bear_arg.get('points', []),
        'component_scores': {
            'technical': tech_score,
            'fundamental': fund_score,
            'sentiment': round(news_score, 1),
            'debate_net': net_debate,
            'macro_override': macro_override,
        },
    }


def _fast_technical_analysis(ticker: str, name: str = "") -> Dict:
    """
    轻量技术面分析：仅 get_stock_data + calc_technical_indicators，
    跳过 AdaptivePredictor 与复杂回测，单标约 0.5-2 秒。
    新增：TickFlow 实时行情校验与评分增强。
    """
    df, _ = get_stock_data(ticker, calibrate=False)
    df = calc_technical_indicators(df)
    latest = df.iloc[-1]
    cp = float(latest['close'])

    # TickFlow 实时行情校验
    tf_data = {}
    tf_price = None
    tf_change_pct = 0.0
    tf_turnover = 0.0
    try:
        from core.data_layer import tf_quotes
        tf_data = tf_quotes([ticker]).get(ticker, {})
        tf_price = tf_data.get('price')
        tf_change_pct = tf_data.get('change_pct', 0.0) or 0.0
        tf_turnover = tf_data.get('turnover_rate', 0.0) or 0.0
    except Exception:
        pass

    if tf_price and cp > 0:
        price_dev = abs(tf_price / cp - 1) * 100
        if price_dev > 3.0:
            # 偏差过大，保留数据源收盘价并记录警告
            tf_data['price_warning'] = f"TickFlow价格{tf_price}与数据源收盘价{cp}偏差{price_dev:.2f}%"
        elif price_dev < 2.0:
            # 偏差在 2% 内，用 TickFlow 最新价作为 current_price
            cp = tf_price
    elif tf_price:
        cp = tf_price

    def _val(col, ndigits=2, default=0):
        v = latest.get(col)
        return round(float(v), ndigits) if pd.notna(v) and v is not None else default

    tech_snapshot = {
        'current_price': round(cp, 2),
        'price_date': str(df.index[-1].date()) if hasattr(df.index[-1], 'date') else str(df.index[-1]),
        'ma5': _val('ma5'), 'ma10': _val('ma10'), 'ma20': _val('ma20'), 'ma60': _val('ma60'),
        'macd_dif': _val('macd_dif', 4), 'macd_dea': _val('macd_dea', 4), 'macd_hist': _val('macd_hist', 4),
        'rsi_14': _val('rsi_14', 1), 'kdj_k': _val('kdj_k', 1), 'kdj_d': _val('kdj_d', 1),
        'boll_up': _val('boll_up'), 'boll_mid': _val('boll_mid'), 'boll_down': _val('boll_down'),
        'vol_ratio': _val('vol_ratio', 2), 'annual_vol_20d': _val('annual_vol_20d', 1),
        'momentum_5d': _val('momentum_5d', 2), 'momentum_20d': _val('momentum_20d', 2),
        'tickflow_price': tf_price,
        'tickflow_change_pct': round(tf_change_pct * 100, 3) if tf_change_pct else None,
        'tickflow_turnover': round(tf_turnover, 4) if tf_turnover else None,
    }

    signals = []
    if pd.notna(latest['ma5']) and pd.notna(latest['ma10']) and pd.notna(latest['ma20']):
        if float(latest['ma5']) > float(latest['ma10']) > float(latest['ma20']):
            signals.append(("🟢", "均线多头排列"))
        elif float(latest['ma5']) < float(latest['ma10']) < float(latest['ma20']):
            signals.append(("🔴", "均线空头排列"))
    if latest['macd_hist'] > 0:
        signals.append(("🟢", "MACD红柱"))
    else:
        signals.append(("🔴", "MACD绿柱"))
    if latest['rsi_14'] < 30: signals.append(("🟢", "RSI超卖"))
    elif latest['rsi_14'] > 70: signals.append(("🔴", "RSI超买"))

    # 轻量评分（加入 TickFlow 涨跌幅与换手）
    score = 50
    reasons = []
    if pd.notna(latest['ma60']):
        ma60_dist = (cp / float(latest['ma60']) - 1) * 100
        if ma60_dist > 0:
            score += 8; reasons.append(f"MA60上方{ma60_dist:+.1f}%")
        else:
            score -= 5; reasons.append(f"MA60下方{ma60_dist:+.1f}%")
    if latest['macd_hist'] > 0: score += 6; reasons.append("MACD红柱")
    else: score -= 4; reasons.append("MACD绿柱")
    if 30 < latest['rsi_14'] < 70: score += 3; reasons.append("RSI合理")
    elif latest['rsi_14'] < 30: score += 4; reasons.append("RSI超卖反弹")
    else: score -= 3; reasons.append("RSI超买")
    vol = tech_snapshot['annual_vol_20d']
    if vol < 30: score += 3; reasons.append("低波")
    elif vol > 60: score -= 2; reasons.append("高波")
    # TickFlow 实时涨跌幅修正
    if tf_change_pct > 0.03:
        score += 2; reasons.append("TickFlow实时涨>3%")
    elif tf_change_pct < -0.03:
        score -= 2; reasons.append("TickFlow实时跌>3%")
    # 换手活跃度（仅个股）
    if tf_turnover and 0.02 < tf_turnover < 0.15:
        score += 1; reasons.append("TickFlow换手适中")
    elif tf_turnover and tf_turnover > 0.20:
        score -= 1; reasons.append("TickFlow换手过高")
    score = max(0, min(100, score))

    rating = "偏多" if score >= 75 else "中性偏多" if score >= 60 else "中性" if score >= 40 else "中性偏空" if score >= 25 else "偏空"

    # 轻量预测：用 5/20 日动量外推 1/3/5/10 日
    m5 = tech_snapshot['momentum_5d'] / 5 if tech_snapshot['momentum_5d'] else 0
    m20 = tech_snapshot['momentum_20d'] / 20 if tech_snapshot['momentum_20d'] else 0
    avg_daily = (m5 + m20) / 2
    # 加入 TickFlow 实时涨跌修正
    if tf_change_pct:
        avg_daily = avg_daily * 0.7 + (tf_change_pct * 100) * 0.3
    predictions = []
    for days, label in [(1, '1d'), (3, '3d'), (5, '5d'), (10, '10d')]:
        pred_return = avg_daily * days
        # 用更宽松的阈值才判定方向（1日1.5%，3日2.5%，5日3.5%，10日5%）
        thr = 1.5 + max(0, (days - 1)) * 0.5
        pred_direction = '上涨' if pred_return > thr else '下跌' if pred_return < -thr else '震荡'
        predictions.append({
            'day': days, 'pred_price': round(cp * (1 + pred_return / 100), 3), 'pred_return': round(pred_return, 3),
            'pred_direction': pred_direction,
        })
    prediction = {
        'trend': '看涨' if avg_daily > 0.3 else '看跌' if avg_daily < -0.3 else '震荡',
        'avg_return': round(avg_daily / 100, 5),
        'predictions': predictions,
    }

    backtest = multi_period_backtest(df, periods=[30, 60]) if len(df) >= 30 else []
    scenarios = scenario_backtests(df, periods=[30, 60], ticker=ticker) if len(df) >= 30 else []
    recommended = recommend_scenario(scenarios) if scenarios else {}

    return {
        'analyst': '技术面分析师(轻量+TickFlow)',
        'ticker': ticker, 'name': name, 'current_price': round(cp, 2),
        'score': score, 'rating': rating,
        'backtest_results': backtest,
        'scenarios': scenarios,
        'recommended_scenario': recommended,
        'tech_snapshot': tech_snapshot,
        'signals': signals,
        'reasons': reasons,
        'prediction': prediction,
        'tickflow': tf_data,
    }


def predict_one(ticker: str, name: str = '', sector: str = '', category: str = '个股',
                fast: bool = False, ultra: bool = False,
                macro_report: Optional[Dict] = None) -> Optional[Dict]:
    """对单个标的进行统一多 Agent 预测。fast=True 跳过基本面和新闻情绪，仅技术面+多空辩论。ultra=True 使用轻量技术面分析，速度最快。macro_report 为全局宏观分析，影响经理裁决。"""
    try:
        is_fut = is_futures(ticker)

        if ultra:
            technical = _fast_technical_analysis(ticker, name)
        else:
            technical = technical_analyst.analyze(ticker, name)

        # 期货和 fast 模式跳过基本面和新闻；ultra 模式启用基本面和新闻
        if is_fut or fast:
            fundamental = {'score': 50, 'rating': 'N/A', 'fundamentals': {}, 'error': 'skipped'}
            news = {'sentiment_score': 0, 'sentiment': '中性', 'keywords': []}
        else:
            fundamental = fundamentals_analyst.analyze(ticker, name)
            news = news_analyst.analyze(ticker, name)

        bull = DebateEngine.bull_argument(technical, fundamental, news)
        bear = DebateEngine.bear_argument(technical, fundamental, news)

        verdict = _manager_verdict(technical, fundamental, news, bull, bear, macro_report=macro_report)

        current_price = technical.get('current_price', 0)
        price_date = technical.get('price_date') or technical.get('tech_snapshot', {}).get('price_date', '')
        predictions = technical.get('prediction', {}).get('predictions', [])
        if predictions:
            avg_return = sum(p.get('pred_return', 0) for p in predictions) / len(predictions)
            horizons = {}
            horizon_returns = {}
            for i, p in enumerate(predictions[:4]):
                key = {0: '1d', 1: '3d', 2: '5d', 3: '10d'}.get(i)
                if key:
                    horizons[key] = _horizon_label(p.get('pred_return', 0))
                    horizon_returns[f'{key}_return'] = p.get('pred_return', 0)
        else:
            avg_return = 0
            horizons = {'1d': '震荡', '3d': '震荡', '5d': '震荡', '10d': '震荡'}
            horizon_returns = {}

        for k in ['1d', '3d', '5d', '10d']:
            if k not in horizons:
                horizons[k] = '震荡'

        target, stop = _calc_target_stop(current_price, verdict['signal'], technical.get('tech_snapshot', {}), avg_return)

        backtest = technical.get('backtest_results', [])
        scenarios = technical.get('scenarios', [])
        recommended = technical.get('recommended_scenario', {})
        backtest_summary = {
            'periods': [{'period': b['period_name'], 'return': b['total_return'],
                         'max_drawdown': b['max_drawdown'], 'sharpe': b['sharpe']}
                        for b in backtest[:4]],
            'scenarios': scenarios,
            'recommended_scenario': recommended,
        } if backtest else {'scenarios': scenarios, 'recommended_scenario': recommended}

        return {
            'ticker': ticker,
            'name': name or technical.get('name', ticker),
            'sector': sector,
            'category': category,
            'current_price': current_price,
            'price_date': price_date,
            'signal': verdict['signal'],
            'confidence': verdict['confidence'],
            'weighted_score': verdict['weighted_score'],
            'target_price': target,
            'stop_loss': stop,
            'position_pct': verdict['position_pct'],
            'horizon_1d': horizons['1d'],
            'horizon_3d': horizons['3d'],
            'horizon_5d': horizons['5d'],
            'horizon_10d': horizons['10d'],
            'horizon_1d_return': horizon_returns.get('1d_return', 0),
            'horizon_3d_return': horizon_returns.get('3d_return', 0),
            'horizon_5d_return': horizon_returns.get('5d_return', 0),
            'horizon_10d_return': horizon_returns.get('10d_return', 0),
            'key_support': verdict['key_support'],
            'key_resistance': verdict['key_resistance'],
            'reasoning': verdict['reasoning'],
            'bull_points': verdict['bull_points'],
            'bear_points': verdict['bear_points'],
            'component_scores': verdict['component_scores'],
            'backtest_summary': backtest_summary,
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {'error': str(e), 'ticker': ticker, 'name': name}


def save_predictions(predictions: List[Dict]) -> Dict:
    """向后兼容：使用 DAO 保存预测。"""
    return _db_save_predictions(predictions)


def generate_for_watchlist(watchlist_path: str = None, categories: List[str] = None,
                           max_workers: int = MAX_WORKERS, fast: bool = False, ultra: bool = False,
                           macro_report: Optional[Dict] = None) -> Dict:
    """多线程批量生成预测。fast=True 跳过基本面/新闻，ultra=True 额外使用轻量技术面分析。macro_report 传入全局宏观分析。"""
    if watchlist_path is None:
        watchlist_path = os.path.join(MULTI_AGENT, 'watchlist.json')

    with open(watchlist_path, 'r', encoding='utf-8') as f:
        watchlist = json.load(f)

    if categories is None:
        categories = ['ETF', '个股', '期货']

    items = [w for w in watchlist if w.get('category') in categories]
    print(f"🎯 多 Agent 预测: {len(items)} 个标的 ({', '.join(categories)}), 并发={max_workers}, fast={fast}, ultra={ultra}")

    predictions = []
    errors = 0

    def _predict(item):
        return predict_one(item['ticker'], item['name'], item.get('sector', item.get('theme', '')), item.get('category', '个股'), fast=fast, ultra=ultra, macro_report=macro_report)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_item = {executor.submit(_predict, item): item for item in items}
        for future in as_completed(future_to_item):
            item = future_to_item[future]
            try:
                result = future.result(timeout=120)
                if 'error' in result:
                    print(f"  ❌ {item['ticker']}: {result['error']}")
                    errors += 1
                else:
                    predictions.append(result)
                    print(f"  ✅ {item['ticker']} {result['signal']} 置信度{result['confidence']} 评分{result['weighted_score']}")
            except Exception as e:
                print(f"  ❌ {item['ticker']}: {e}")
                errors += 1

    stats = save_predictions(predictions)
    stats['errors'] += errors
    print(f"\n✅ 保存 {stats['saved']} 条, 失败 {stats['errors']} 条")
    return {'predictions': predictions, 'stats': stats}


def validate_predictions(pred_date: str = None) -> Dict:
    """统一验证 agentic 预测。

    每天验证 horizon=1 的预测：用 pred_date 当日的预测，对比下一交易日（或当前）的实际价格。
    对于 horizon=3/5/10，只在已到达对应日期时验证（暂不自动验证）。
    """
    if pred_date is None:
        # 默认验证最近一个有预测数据的日期
        conn = _get_conn()
        latest = conn.execute("SELECT MAX(pred_date) FROM agentic_predictions").fetchone()[0]
        conn.close()
        pred_date = latest

    if not pred_date:
        return {'validated': 0, 'message': '无预测数据'}

    conn = _get_conn()
    try:
        cur = conn.cursor()
        rows = cur.execute("""
            SELECT id, ticker, signal, horizon_1d_return, current_price
            FROM agentic_predictions
            WHERE pred_date = ?
            AND id NOT IN (
                SELECT DISTINCT prediction_id FROM unified_validation_results
                WHERE source_table = 'agentic' AND prediction_id IS NOT NULL
            )
        """, (pred_date,)).fetchall()

        if not rows:
            return {'validated': 0, 'message': f'{pred_date} 无待验证预测'}

        validated = 0
        correct = 0
        for row in rows:
            ticker = row['ticker']
            pred_price = row['current_price'] or 0
            try:
                rt = get_realtime_price(ticker)
                actual_price = rt['price'] if rt else 0
            except Exception:
                continue
            if actual_price <= 0 or pred_price <= 0:
                continue

            actual_return = (actual_price - pred_price) / pred_price
            pred_return = float(row['horizon_1d_return'] or 0)
            pred_direction = 'up' if pred_return > 0 else 'down' if pred_return < 0 else 'flat'
            # 1日方向阈值从 0.5% 放宽到 1.5%，过滤日内噪音
            actual_direction = 'up' if actual_return > 0.015 else 'down' if actual_return < -0.015 else 'flat'
            direction_correct = (pred_direction == actual_direction) if pred_direction != 'flat' else 0

            cur.execute("""
                INSERT INTO unified_validation_results
                (prediction_id, source_table, ticker, horizon, pred_signal, actual_price, actual_return, direction_correct)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (row['id'], 'agentic', ticker, 1, row['signal'], actual_price, actual_return, direction_correct))
            validated += 1
            if direction_correct:
                correct += 1

        conn.commit()
        accuracy = round(correct / validated * 100, 1) if validated > 0 else 0
        return {'validated': validated, 'correct': correct, 'accuracy': accuracy, 'pred_date': pred_date}
    finally:
        conn.close()


def get_validation_stats() -> Dict:
    """获取 agentic 验证统计"""
    conn = _get_conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM unified_validation_results WHERE source_table='agentic'").fetchone()[0]
        correct = conn.execute("SELECT SUM(direction_correct) FROM unified_validation_results WHERE source_table='agentic'").fetchone()[0]
        accuracy = round(correct / total * 100, 1) if total > 0 else 0

        by_horizon = {}
        for h in [1, 3, 5, 10]:
            r = conn.execute("""
                SELECT COUNT(*), SUM(direction_correct) FROM unified_validation_results
                WHERE source_table='agentic' AND horizon=?
            """, (h,)).fetchone()
            if r and r[0]:
                by_horizon[f'{h}d'] = {
                    'total': r[0], 'correct': r[1] or 0,
                    'accuracy': round(r[1] / r[0] * 100, 1)
                }
        return {'total': total, 'correct': correct, 'accuracy': accuracy, 'by_horizon': by_horizon}
    finally:
        conn.close()


# ============================================================
# CLI
# ============================================================
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='多 Agent LLM 预测系统（统一版）')
    parser.add_argument('--ticker', type=str, help='单个标的')
    parser.add_argument('--name', type=str, default='', help='标的名称')
    parser.add_argument('--category', type=str, default='个股', help='标的类别')
    parser.add_argument('--watchlist', type=str, help='watchlist 文件路径')
    parser.add_argument('--categories', type=str, default='ETF,个股,期货', help='逗号分隔的 category 过滤')
    parser.add_argument('--output', type=str, help='输出 JSON 文件（可选）')
    parser.add_argument('--validate', action='store_true', help='[已弃用] 旧方向验证不再使用，统一使用回测指标')
    parser.add_argument('--workers', type=int, default=MAX_WORKERS, help='并发线程数')
    parser.add_argument('--fast', action='store_true', help='跳过基本面和新闻，仅技术面+多空辩论')
    parser.add_argument('--ultra', action='store_true', help='使用轻量技术面分析，速度最快')
    args = parser.parse_args()

    if args.validate:
        print(json.dumps({
            "message": "[已弃用] 方向验证（validation_results / unified_validation_results）样本少、准确率接近随机，已不再使用。统一回测口径：multi_period_backtest（30/60/90/120天收益、最大回撤、夏普）。"
        }, ensure_ascii=False, indent=2))
    elif args.ticker:
        result = predict_one(args.ticker, args.name, category=args.category, fast=args.fast, ultra=args.ultra)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        cats = [c.strip() for c in args.categories.split(',')]
        # CLI 默认启动宏观分析并传入
        macro_report = None
        try:
            from analysts.macro_analyst import analyze as macro_analyze
            macro_report = macro_analyze()
            print(f"[宏观] 评分 {macro_report['macro_score']} 信号 {macro_report['macro_signal']}")
        except Exception as e:
            print(f"[宏观] 分析失败，跳过: {e}")
        result = generate_for_watchlist(args.watchlist, cats, max_workers=args.workers, fast=args.fast, ultra=args.ultra, macro_report=macro_report)
        if args.output:
            with open(args.output, 'w', encoding='utf-8') as f:
                json.dump(result['predictions'], f, ensure_ascii=False, indent=2, default=str)
