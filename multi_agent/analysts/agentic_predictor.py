#!/usr/bin/env python3
"""
多 Agent LLM 预测系统

输入: 标的(ticker, name, category)
输出: 结构化预测 JSON，写入 llm_predictions.db 的 agentic_predictions 表

Agent 流程:
1. 技术面分析师 (technical_analyst)  →  技术评分、趋势、回测
2. 基本面分析师 (fundamentals_analyst) →  估值、财务评分
3. 新闻情绪分析师 (news_analyst)  →  情绪分数、关键词
4. Bull Agent  →  收集看涨证据
5. Bear Agent  →  收集看跌证据
6. 研究经理 (research_manager)  →  综合裁决最终方向/置信度/目标价/止损

关键: 不用调用外部 Kimi API，而是把各 Agent 输出结构化后，用规则/加权法融合。
这样不依赖 MOONSHOT_API_KEY，速度快、可回测、可解释。
"""
import sys
import os
import json
import sqlite3
import warnings
from datetime import datetime, timedelta
from typing import Dict, List, Optional

# 项目根目录
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, MULTI_AGENT)

warnings.filterwarnings('ignore')

from analysts import technical_analyst, fundamentals_analyst, news_analyst
from core.debate_engine import DebateEngine
from core.data_layer import get_realtime_price

DB_PATH = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'llm_predictions.db')

# 权重配置
WEIGHTS = {
    'technical': 0.35,
    'fundamental': 0.25,
    'sentiment': 0.15,
    'debate': 0.25,
}

SIGNAL_MAP = {
    'bullish': 'bullish',
    'bearish': 'bearish',
    'neutral': 'neutral',
    '偏多': 'bullish',
    '偏空': 'bearish',
    '看涨': 'bullish',
    '看跌': 'bearish',
    '中性': 'neutral',
    '震荡': 'neutral',
}


def _get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS agentic_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            name TEXT,
            sector TEXT,
            signal TEXT NOT NULL,  -- bullish/bearish/neutral
            confidence REAL,  -- 0.0-1.0
            target_price REAL,
            stop_loss REAL,
            position_pct REAL,  -- 建议仓位比例 0-1
            horizon_1d TEXT,
            horizon_3d TEXT,
            horizon_5d TEXT,
            horizon_10d TEXT,
            key_support REAL,
            key_resistance REAL,
            reasoning TEXT,
            bull_points TEXT,  -- JSON
            bear_points TEXT,  -- JSON
            component_scores TEXT,  -- JSON
            backtest_summary TEXT,  -- JSON
            current_price REAL,
            pred_date TEXT NOT NULL,
            pred_time TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_agentic_pred_date ON agentic_predictions(pred_date);
        CREATE INDEX IF NOT EXISTS idx_agentic_ticker ON agentic_predictions(ticker);
    """)
    return conn


def _horizon_label(return_pct: float) -> str:
    if return_pct > 1.5:
        return '看涨'
    elif return_pct > 0.5:
        return '偏多'
    elif return_pct < -1.5:
        return '看跌'
    elif return_pct < -0.5:
        return '偏空'
    else:
        return '震荡'


def _calc_target_stop(current_price: float, signal: str, tech_snapshot: Dict, predictions: List[Dict]) -> tuple:
    """根据信号和技术指标计算目标价和止损价"""
    if current_price <= 0:
        return None, None

    # 用5日预测收益估算目标价
    if predictions:
        avg_return = sum(p['pred_return'] for p in predictions if 'pred_return' in p) / len(predictions)
    else:
        avg_return = 0

    if signal == 'bullish':
        target = current_price * (1 + max(abs(avg_return), 2.0) / 100)
        # 止损：布林下轨或MA20，取较高者（最多-5%）
        stop = max(
            tech_snapshot.get('boll_down') or 0,
            tech_snapshot.get('ma20') or 0,
            current_price * 0.95
        )
        stop = min(stop, current_price * 0.97)  # 限制最大止损-3%
    elif signal == 'bearish':
        target = current_price * (1 - max(abs(avg_return), 2.0) / 100)
        stop = min(
            tech_snapshot.get('boll_up') or 99999,
            tech_snapshot.get('ma20') or 99999,
            current_price * 1.05
        )
        stop = max(stop, current_price * 1.03)
    else:
        target = current_price * (1 + avg_return / 100)
        stop = current_price * 0.98

    return round(target, 3), round(stop, 3)


def _manager_verdict(technical_report: Dict, fundamental_report: Dict, news_report: Dict,
                     bull_arg: Dict, bear_arg: Dict) -> Dict:
    """
    研究经理裁决：综合所有 Agent 输出，给出最终方向、置信度和仓位
    """
    tech_score = technical_report.get('score', 50)
    tech_rating = technical_report.get('rating', '中性')
    tech_snapshot = technical_report.get('tech_snapshot', {})

    fund_score = fundamental_report.get('score', 50) if 'error' not in fundamental_report else 50
    news_score = (news_report.get('sentiment_score', 0) + 1) * 50  # -1~1 -> 0~100

    bull_score = bull_arg.get('score', 0)
    bear_score = bear_arg.get('score', 0)
    net_debate = bull_score - bear_score

    # 综合评分
    weighted = (
        tech_score * WEIGHTS['technical'] +
        fund_score * WEIGHTS['fundamental'] +
        news_score * WEIGHTS['sentiment'] +
        (50 + net_debate * 8) * WEIGHTS['debate']  # 辩论净信号映射到0-100
    )
    weighted = max(0, min(100, weighted))

    # 最终方向
    if weighted >= 62 and net_debate >= 1:
        signal = 'bullish'
    elif weighted <= 42 and net_debate <= -1:
        signal = 'bearish'
    elif weighted >= 55:
        signal = 'bullish' if net_debate >= 0 else 'neutral'
    elif weighted <= 45:
        signal = 'bearish' if net_debate <= 0 else 'neutral'
    else:
        signal = 'neutral'

    # 置信度：基于加权分散程度
    confidence = abs(weighted - 50) / 50 * 0.6 + min(abs(net_debate) * 0.05, 0.3) + 0.1
    confidence = round(max(0.15, min(0.95, confidence)), 2)

    # 仓位建议：高置信+强信号=重仓，低置信=轻仓/观望
    if signal == 'bullish':
        position_pct = min(0.25, confidence * 0.3)
    elif signal == 'bearish':
        position_pct = min(0.15, confidence * 0.2)
    else:
        position_pct = 0.0

    # 关键位
    support = tech_snapshot.get('boll_down') or tech_snapshot.get('ma60') or 0
    resistance = tech_snapshot.get('boll_up') or tech_snapshot.get('ma5') or 0

    # 核心推理
    reasons = []
    reasons.append(f"技术面{tech_rating}({tech_score}/100)")
    reasons.append(f"基本面{fundamental_report.get('rating', 'N/A')}({fund_score}/100)")
    reasons.append(f"新闻情绪{news_report.get('sentiment_score', 0):+.2f}")
    reasons.append(f"多空辩论 看涨{bull_score} vs 看跌{bear_score}")
    reasoning = " | ".join(reasons)

    # 1/3/5/10日预测标签
    predictions = technical_report.get('prediction', {}).get('predictions', [])
    horizons = {}
    for i, p in enumerate(predictions[:4]):
        key = {0: '1d', 1: '3d', 2: '5d', 3: '10d'}.get(i)
        if key:
            horizons[key] = _horizon_label(p.get('pred_return', 0))
    # 补齐
    for k in ['1d', '3d', '5d', '10d']:
        if k not in horizons:
            horizons[k] = '震荡'

    return {
        'signal': signal,
        'confidence': confidence,
        'weighted_score': round(weighted, 1),
        'position_pct': round(position_pct, 2),
        'key_support': round(support, 3) if support else None,
        'key_resistance': round(resistance, 3) if resistance else None,
        'reasoning': reasoning,
        'horizons': horizons,
        'bull_points': bull_arg.get('points', []),
        'bear_points': bear_arg.get('points', []),
        'component_scores': {
            'technical': tech_score,
            'fundamental': fund_score,
            'sentiment': round(news_score, 1),
            'debate_net': net_debate,
        },
    }


def predict_one(ticker: str, name: str = '', sector: str = '') -> Optional[Dict]:
    """对单个标的进行多 Agent 预测"""
    try:
        # 1. 技术面分析
        technical = technical_analyst.analyze(ticker, name)

        # 2. 基本面分析（ETF可能数据有限，但会返回）
        fundamental = fundamentals_analyst.analyze(ticker, name)

        # 3. 新闻情绪分析
        news = news_analyst.analyze(ticker, name)

        # 4. Bull / Bear 辩论
        bull = DebateEngine.bull_argument(technical, fundamental, news)
        bear = DebateEngine.bear_argument(technical, fundamental, news)

        # 5. 研究经理裁决
        verdict = _manager_verdict(technical, fundamental, news, bull, bear)

        # 6. 计算目标价和止损
        current_price = technical.get('current_price', 0)
        target, stop = _calc_target_stop(
            current_price,
            verdict['signal'],
            technical.get('tech_snapshot', {}),
            technical.get('prediction', {}).get('predictions', [])
        )

        # 回测摘要
        backtest = technical.get('backtest_results', [])
        backtest_summary = {
            'periods': [{'period': b['period_name'], 'return': b['total_return'],
                         'max_drawdown': b['max_drawdown'], 'sharpe': b['sharpe']}
                        for b in backtest[:4]]
        } if backtest else {}

        return {
            'ticker': ticker,
            'name': name or technical.get('name', ticker),
            'sector': sector,
            'current_price': current_price,
            'signal': verdict['signal'],
            'confidence': verdict['confidence'],
            'weighted_score': verdict['weighted_score'],
            'target_price': target,
            'stop_loss': stop,
            'position_pct': verdict['position_pct'],
            'horizon_1d': verdict['horizons']['1d'],
            'horizon_3d': verdict['horizons']['3d'],
            'horizon_5d': verdict['horizons']['5d'],
            'horizon_10d': verdict['horizons']['10d'],
            'key_support': verdict['key_support'],
            'key_resistance': verdict['key_resistance'],
            'reasoning': verdict['reasoning'],
            'bull_points': verdict['bull_points'],
            'bear_points': verdict['bear_points'],
            'component_scores': verdict['component_scores'],
            'backtest_summary': backtest_summary,
        }
    except Exception as e:
        return {'error': str(e), 'ticker': ticker, 'name': name}


def save_predictions(predictions: List[Dict]) -> Dict:
    """批量保存 agentic 预测到数据库"""
    conn = _get_conn()
    try:
        stats = {'saved': 0, 'errors': 0}
        today = datetime.now().strftime('%Y-%m-%d')
        now = datetime.now().strftime('%H:%M')

        for p in predictions:
            if 'error' in p:
                stats['errors'] += 1
                continue
            try:
                conn.execute("""
                    INSERT INTO agentic_predictions
                    (ticker, name, sector, signal, confidence, target_price, stop_loss, position_pct,
                     horizon_1d, horizon_3d, horizon_5d, horizon_10d,
                     key_support, key_resistance, reasoning,
                     bull_points, bear_points, component_scores, backtest_summary,
                     current_price, pred_date, pred_time)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    p['ticker'], p['name'], p.get('sector', ''),
                    p['signal'], p['confidence'], p['target_price'], p['stop_loss'], p['position_pct'],
                    p['horizon_1d'], p['horizon_3d'], p['horizon_5d'], p['horizon_10d'],
                    p['key_support'], p['key_resistance'], p['reasoning'],
                    json.dumps(p['bull_points'], ensure_ascii=False),
                    json.dumps(p['bear_points'], ensure_ascii=False),
                    json.dumps(p['component_scores'], ensure_ascii=False),
                    json.dumps(p['backtest_summary'], ensure_ascii=False),
                    p['current_price'], today, now
                ))
                stats['saved'] += 1
            except Exception as e:
                stats['errors'] += 1
                print(f"  ❌ 保存失败 {p.get('ticker')}: {e}")
        conn.commit()
        return stats
    finally:
        conn.close()


def generate_for_watchlist(watchlist_path: str = None, category_filter: str = 'ETF') -> Dict:
    """对 watchlist 中指定 category 的标的批量生成预测"""
    if watchlist_path is None:
        watchlist_path = os.path.join(MULTI_AGENT, 'watchlist.json')

    with open(watchlist_path, 'r', encoding='utf-8') as f:
        watchlist = json.load(f)

    items = [w for w in watchlist if w.get('category') == category_filter]
    print(f"🎯 多 Agent 预测: {len(items)} 个 {category_filter} 标的")

    predictions = []
    for item in items:
        ticker = item['ticker']
        name = item['name']
        sector = item.get('sector', item.get('theme', ''))
        print(f"  → {ticker} {name}")
        result = predict_one(ticker, name, sector)
        if 'error' not in result:
            predictions.append(result)
            print(f"    {result['signal']} 置信度{result['confidence']} 评分{result['weighted_score']}")
        else:
            print(f"    ❌ {result['error']}")

    stats = save_predictions(predictions)
    print(f"\n✅ 保存 {stats['saved']} 条, 失败 {stats['errors']} 条")
    return {'predictions': predictions, 'stats': stats}


# ============================================================
# CLI
# ============================================================
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='多 Agent LLM 预测系统')
    parser.add_argument('--ticker', type=str, help='单个标的')
    parser.add_argument('--name', type=str, default='', help='标的名称')
    parser.add_argument('--watchlist', type=str, help='watchlist 文件路径')
    parser.add_argument('--category', type=str, default='ETF', help='watchlist category 过滤')
    parser.add_argument('--output', type=str, help='输出 JSON 文件（可选）')
    args = parser.parse_args()

    if args.ticker:
        result = predict_one(args.ticker, args.name)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        if args.output:
            with open(args.output, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    else:
        result = generate_for_watchlist(args.watchlist, args.category)
        if args.output:
            with open(args.output, 'w', encoding='utf-8') as f:
                json.dump(result['predictions'], f, ensure_ascii=False, indent=2, default=str)
