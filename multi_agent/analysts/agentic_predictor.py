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

from analysts import fundamentals_analyst, news_analyst, fundamental_factor_analyst, technical_analyst, futures_fundamental_analyst
from core.debate_engine import DebateEngine
from core.data_layer import get_realtime_price, is_futures, get_stock_data, calc_technical_indicators, multi_period_backtest, tf_quotes
from core.us_data import get_us_stock_data, is_us_ticker
from core.scenario_backtests import scenario_backtests, recommend_scenario, SCENARIO_NAME_CN, SCENARIO_DESC
from core.db import get_predictions_conn, save_predictions as _db_save_predictions
import pandas as pd

DB_PATH = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'llm_predictions.db')
PARAMS_PATH = os.path.join(PROJECT_ROOT, 'multi_agent', 'config', 'predictor_params.json')

# ============================================================
# 统一超参数配置（选股、预测、回测一致）
# 从 config/predictor_params.json 读取，支持自动调参无需改代码
# ============================================================
def _load_params():
    try:
        with open(PARAMS_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f'⚠️ 读取参数失败 {e}，使用默认')
        return {}

_PARAMS = _load_params()
WEIGHTS = _PARAMS.get('weights', {
    'technical': 0.22,
    'fundamental': 0.25,
    'sentiment': 0.15,
    'macro': 0.18,
    'debate': 0.25,
})
THRESHOLD = _PARAMS.get('threshold', {
    'strong_bull': 62,
    'bull': 55,
    'neutral_high': 52,
    'neutral_low': 48,
    'bear': 43,
    'strong_bear': 38,
})
HARD_RULES = _PARAMS.get('hard_rules', {
    'macro_bearish_block_bullish': True,
    'macro_bearish_force_bearish_if_tech_below': 55,
    'macro_bearish_score_threshold': 50,
})

# 保持向后兼容的引用
PARAMS = _PARAMS

POSITION_MAP = {
    '看多': 0.25,
    '看空': 0.15,
    '中性': 0.0,
}

HORIZON_THRESHOLD = {'strong': 1.5, 'weak': 0.5}

# 信号中文化映射
SIGNAL_CN = {
    'bullish': '看多',
    'bearish': '看空',
    'neutral': '中性',
    'weak_neutral': 'weak_neutral',
}
MACRO_SIGNAL_CN = {
    'bullish': '偏多',
    'bearish': '偏空',
    'neutral': '中性',
}


def _get_market_momentum() -> Dict:
    """获取主要市场指数近期动量，用于反制宏观滞后信号。"""
    from core.data_layer import get_stock_data
    try:
        indices = {'000001': '上证', '000016': '上证50', '000905': '中证500', '399006': '创业板'}
        returns = {}
        for code, name in indices.items():
            try:
                r = get_stock_data(code)
                if isinstance(r, tuple):
                    r = r[0]
                if r is None or len(r) < 6:
                    continue
                ret5 = r['close'].iloc[-1] / r['close'].iloc[-6] - 1
                ret20 = r['close'].iloc[-1] / r['close'].iloc[-21] - 1 if len(r) >= 21 else 0
                returns[name] = {'ret5': float(ret5), 'ret20': float(ret20)}
            except Exception:
                continue
        return returns
    except Exception:
        return {}


# 同花顺行业名称 -> 行业代码 (THS)
_THS_INDUSTRY_MAP = {
    '半导体': '881121', '通信设备': '881129', '通信服务': '881162', '光伏设备': '881279',
    '电池': '881281', '煤炭开采加工': '881105', '钢铁': '881112', '电力': '881145',
    '银行': '881155', '证券': '881157', '保险': '881156', '房地产开发': '881153',
    '医药商业': '881154', '生物制品': '881142', '化学制药': '881140', '医疗器械': '881144',
    '中药': '881141', '医疗服务': '881175', '电力设备': '881120', '通用设备': '881117',
    '专用设备': '881118', '汽车整车': '881125', '汽车零部件': '881126', '电子化学品': '881172',
    '元件': '881270', '消费电子': '881124', '光学光电子': '881122', '计算机设备': '881130',
    '软件开发': '881272', 'IT服务': '881271', '传媒': '881164', '游戏': '881275',
    '广告营销': '881163', '影视院线': '881274', '出版': '881166', '电视广播': '881160',
    '油气开采及服务': '881107', '石油加工贸易': '881180', '燃气': '881146', '环保': '881181',
    '建筑装饰': '881116', '建筑材料': '881115', '工程机械': '881268', '自动化设备': '881171',
    '军工电子': '881276', '航天装备': '881265', '航空装备': '881266', '地面兵装': '881264',
    '航海装备': '881267', '小金属': '881170', '金属新材料': '881114', '工业金属': '881168',
    '贵金属': '881169', '冶钢原料': '881113', '非金属材料': '881167', '化学原料': '881108',
    '化学制品': '881109', '农化制品': '881263', '塑料': '881265', '橡胶': '881266',
    '农副食品加工': '881103', '食品加工制造': '881134', '饮料制造': '881133', '白酒': '881273',
    '非白酒': '881273', '休闲食品': '881274', '调味发酵品': '881275', '纺织制造': '881135',
    '服装家纺': '881136', '美容护理': '881182', '造纸': '881137', '包装印刷': '881138',
    '家居用品': '881139', '家用轻工': '881140', '珠宝首饰': '881141', '饰品': '881142',
    '白色家电': '881131', '黑色家电': '881132', '小家电': '881173', '厨卫电器': '881174',
    '照明设备': '881175', '其他家电': '881176', '贸易': '881159', '一般零售': '881158',
    '专业连锁': '881161', '互联网电商': '881177', '旅游零售': '881159', '酒店餐饮': '881284',
    '旅游景区': '881285', '教育': '881178', '体育': '881179', '物流': '881152', '港口航运': '881148',
    '公路铁路运输': '881149', '航空运输': '881150', '机场航运': '881151', '快递': '881153',
    '供应链物流': '881152', '综合': '881165', '个护用品': '881184', '动物保健': '881185',
    '农产品加工': '881103', '种植业与林业': '881101', '渔业': '881104', '养殖业': '881102',
    '饲料': '881105', '汽车服务': '881127', '其他交运设备': '881128', '交运设备': '881128',
    '非银金融': '881156', '多元金融': '881283', '金融租赁': '881283', '信托': '881283',
    '期货': '881283', '农商行': '881155', '城商行': '881155', '股份制银行': '881155', '国有大型银行': '881155',
}

# 把我们关注的板块关键词映射到同花顺行业名称
_SECTOR_KEYWORD_MAP = {
    '稀土': '小金属', '永磁': '金属新材料', '通信': '通信设备', '半导体': '半导体', '芯片': '半导体',
    '机器人': '自动化设备', '人工智能': '软件开发', '算力': '计算机设备', '电池': '电池', '光伏': '光伏设备',
    '有色': '工业金属', '有色金属': '工业金属', '煤炭': '煤炭开采加工', '钢铁': '钢铁', '电力': '电力',
    '银行': '银行', '证券': '证券', '保险': '保险', '房地产': '房地产开发', '医药': '化学制药',
    '军工': '军工电子', '航空航天': '航天装备', '传媒': '传媒', '5G': '通信设备', '计算机': '计算机设备',
    '新能源车': '汽车整车', '锂电': '电池', '储能': '电池', '食品饮料': '食品加工制造', '白酒': '白酒',
}


def _get_sector_momentum(sector: str) -> Dict:
    """获取行业/主题指数近5日/20日动量（基于同花顺行业指数）。"""
    import akshare as ak
    if not sector:
        return {}
    try:
        ths_name = _SECTOR_KEYWORD_MAP.get(sector)
        if not ths_name:
            return {}
        code = _THS_INDUSTRY_MAP.get(ths_name)
        if not code:
            return {}
        df = ak.stock_board_industry_index_ths(symbol=ths_name, start_date='20250101', end_date='20260714')
        if df is None or len(df) < 6:
            return {}
        df['日期'] = pd.to_datetime(df['日期'])
        df = df.sort_values('日期')
        ret5 = df['收盘价'].iloc[-1] / df['收盘价'].iloc[-6] - 1
        ret20 = df['收盘价'].iloc[-1] / df['收盘价'].iloc[-21] - 1 if len(df) >= 21 else 0
        return {'ret5': float(ret5), 'ret20': float(ret20), 'sector_name': ths_name, 'code': code}
    except Exception:
        return {}


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
                     macro_report: Optional[Dict] = None, ticker: str = '', sector: str = '') -> Dict:
    tech_score = technical_report.get('score', 50)
    tech_rating = technical_report.get('rating', '中性')
    tech_snapshot = technical_report.get('tech_snapshot', {})

    fund_score = fundamental_report.get('score', 50) if 'error' not in fundamental_report else 50
    news_score = (news_report.get('sentiment_score', 0) + 1) * 50
    macro_score = macro_report.get('macro_score', 50) if macro_report else 50

    bull_score = bull_arg.get('score', 0)
    bear_score = bear_arg.get('score', 0)
    net_debate = bull_score - bear_score

    # 动态权重：期货基本面信号极端时提高其权重，降低技术面滞后干扰
    ticker = ticker or technical_report.get('ticker', '')
    if is_futures(ticker) and abs(fund_score - 50) >= 5:
        w_tech = max(0.10, WEIGHTS['technical'] - 0.08)
        w_fund = min(0.40, WEIGHTS['fundamental'] + 0.10)
        shift = WEIGHTS['technical'] - w_tech
        w_news = max(0.05, WEIGHTS['sentiment'] - shift / 2)
    else:
        w_tech = WEIGHTS['technical']
        w_fund = WEIGHTS['fundamental']
        w_news = WEIGHTS['sentiment']

    weighted = (
        tech_score * w_tech +
        fund_score * w_fund +
        news_score * w_news +
        macro_score * WEIGHTS['macro'] +
        (50 + net_debate * 8) * WEIGHTS['debate']
    )
    # 期货基本面信号足够强时，直接给予方向性偏置
    if is_futures(ticker) and abs(fund_score - 50) >= 5:
        if fund_score < 45:
            weighted -= 6  # 基本面偏空，压低综合分
        elif fund_score > 55:
            weighted += 6
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
        # 对称规则：宏观 bullish 环境下直接拦截 bearish 信号
        elif macro_report.get('macro_signal') == 'bullish' and raw_signal == 'bearish' and macro_report.get('macro_score', 50) >= 60:
            weighted = THRESHOLD['neutral_low']  # 强制压入 neutral 区间
            macro_override = 15
            macro_note = f"宏观 bullish 拦截 bearish ({macro_report.get('macro_score', 50)}/100)"
        else:
            macro_override = get_macro_score_override(macro_report, raw_signal)
            # 高波动环境下（如宏观 bearish 或市场广度<30），修正力度加倍
            breadth_score = macro_report.get('market_breadth', {}).get('score', 50)
            if macro_report.get('macro_signal') == 'bearish' and breadth_score < 30:
                macro_override *= 2.0
            elif macro_report.get('macro_signal') == 'bullish' and breadth_score > 70:
                macro_override *= 1.5
            # 期货对宏观更敏感：宏观 bearish 时额外 -5，宏观 bullish 时额外 +3
            if is_futures(technical_report.get('ticker', '')) and macro_report.get('macro_signal') == 'bearish':
                macro_override -= 5
            elif is_futures(technical_report.get('ticker', '')) and macro_report.get('macro_signal') == 'bullish':
                macro_override += 3
            weighted = max(0, min(100, weighted + macro_override))

    # 统一信号判定：收窄 neutral 范围，46-52 才为中性
    if weighted >= THRESHOLD['strong_bull']:
        signal = '看多'
    elif weighted >= THRESHOLD['bull']:
        signal = '看多'
    elif weighted <= THRESHOLD['strong_bear']:
        signal = '看空'
    elif weighted <= THRESHOLD['bear']:
        signal = '看空'
    else:
        signal = '中性'

    # 期货基本面强信号时，收窄 neutral 区间到 48-50，并给方向性偏置
    if is_futures(ticker) and signal == '中性':
        if weighted < 50 and fund_score < 45:
            signal = '看空'
            weighted = THRESHOLD['bear'] - 1
        elif weighted > 50 and fund_score > 55:
            signal = '看多'
            weighted = THRESHOLD['bull']
        elif weighted == 50 and abs(fund_score - 50) >= 5:
            signal = '看空' if fund_score < 50 else '看多'

    # 复盘硬规则：宏观 bearish 时压制 bullish；若市场 5 日强反弹则放宽
    market_momentum = _get_market_momentum()
    market_bullish = any(m.get('ret5', 0) > 0.015 for m in market_momentum.values()) if market_momentum else False
    sector_momentum = _get_sector_momentum(sector) if sector else {}
    sector_bullish = sector_momentum.get('ret5', 0) > 0.015
    sector_bearish = sector_momentum.get('ret5', 0) < -0.015
    if (HARD_RULES.get('macro_bearish_block_bullish') and macro_report and
            macro_report.get('macro_score', 50) < HARD_RULES.get('macro_bearish_score_threshold', 50)):
        tech_threshold = HARD_RULES.get('macro_bearish_force_bearish_if_tech_below', 55)
        if signal == '看多':
            if market_bullish:
                # 市场反弹时保留 bullish 但压降评分
                weighted = min(weighted, THRESHOLD['bull'] - 1)
            else:
                signal = '看空' if tech_score < tech_threshold else '中性'
                weighted = THRESHOLD['bear'] - 1 if tech_score < tech_threshold else 50
        elif signal == '中性' and tech_score < tech_threshold and not market_bullish:
            signal = '看空'
            weighted = THRESHOLD['bear'] - 1
    # 行业动量修正：行业与大盘同步反弹时，不强制 bearish；行业同步下跌时，不强制 bullish
    if sector_bullish and market_bullish and signal == '看空':
        signal = '中性'
        weighted = 50
    if sector_bearish and not market_bullish and signal == '看多':
        signal = '中性'
        weighted = 50

    # 宏观偏多时禁止看空硬规则（对称于 macro_bearish_block_bullish）
    if (HARD_RULES.get('macro_bullish_block_bearish') and macro_report and
            macro_report.get('macro_score', 50) >= HARD_RULES.get('macro_bullish_score_threshold', 60) and
            signal == '看空'):
        macro_sig = macro_report.get('macro_signal', 'neutral')
        if macro_sig == 'bullish':
            # 除非技术面极弱(score<40)，否则强制转 neutral
            if tech_score >= HARD_RULES.get('macro_bullish_force_bullish_if_tech_above', 50):
                signal = '看多'
                weighted = THRESHOLD['bull']
            else:
                signal = '中性'
                weighted = THRESHOLD['neutral_low']

    # 宏观-市场一致性规则：避免在市场普涨/普跌日方向大量错误
    strong_breadth = sum(1 for m in market_momentum.values() if m.get('ret5', 0) > 0.015)
    weak_breadth = sum(1 for m in market_momentum.values() if m.get('ret5', 0) < -0.015)
    market_breadth_score = macro_report.get('market_breadth', {}).get('score', 50) if macro_report else 50
    high_breadth = market_breadth_score > 60 and macro_report.get('market_breadth', {}).get('advances', 0) > macro_report.get('market_breadth', {}).get('declines', 0)
    low_breadth = market_breadth_score < 20 and macro_report.get('market_breadth', {}).get('declines', 0) > macro_report.get('market_breadth', {}).get('advances', 0)
    if macro_report and macro_report.get('macro_score', 50) > 60 and (strong_breadth >= 2 or high_breadth) and signal == '看空':
        signal = '中性'
        weighted = 50
    elif macro_report and macro_report.get('macro_score', 50) < 40 and (weak_breadth >= 2 or low_breadth) and signal == '看多':
        signal = '中性'
        weighted = 50

    confidence = round(max(0.5, min(0.95, 0.5 + abs(weighted - 50) / 50 * 0.5 + min(abs(net_debate) * 0.08, 0.4))), 2)

    # 低置信度信号降级为 weak_neutral，不输出方向性建议
    if confidence < 0.62:
        signal = 'weak_neutral'
        position_pct = 0.0
    else:
        base_position = POSITION_MAP.get(signal, 0.0)
        position_pct = round(min(base_position * confidence, 0.25), 3)

    support = tech_snapshot.get('boll_down') or tech_snapshot.get('ma60') or 0
    resistance = tech_snapshot.get('boll_up') or tech_snapshot.get('ma5') or 0

    macro_note = ""
    if macro_report:
        ms = macro_report.get('macro_signal', 'neutral')
        macro_note = f"宏观{MACRO_SIGNAL_CN.get(ms, ms)}({macro_report.get('macro_score', 50)}/100)"
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


def _fast_technical_analysis(ticker: str, name: str = "", macro_report: Optional[Dict] = None, category: str = '') -> Dict:
    """
    轻量技术面分析：仅 get_stock_data + calc_technical_indicators，
    跳过 AdaptivePredictor 与复杂回测，单标约 0.5-2 秒。
    新增：TickFlow 实时行情校验与评分增强。
    """
    if category == 'US' or is_us_ticker(ticker):
        df = get_us_stock_data(ticker)
        tickflow_available = False
    else:
        df, _ = get_stock_data(ticker, calibrate=False)
        tickflow_available = True
    df = calc_technical_indicators(df)
    latest = df.iloc[-1]
    cp = float(latest['close'])

    # TickFlow 实时行情校验（仅 A 股/期货）
    tf_data = {}
    tf_price = None
    tf_change_pct = 0.0
    tf_turnover = 0.0
    if tickflow_available:
        try:
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
    # 从全局 macro_report 获取宏观信号，对策略推荐做宏观偏置
    macro_signal = 'neutral'
    macro_score = 50
    if macro_report:
        macro_signal = macro_report.get('macro_signal', 'neutral')
        macro_score = macro_report.get('macro_score', 50)
    recommended = recommend_scenario(scenarios, macro_signal=macro_signal, macro_score=macro_score) if scenarios else {}

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
            technical = _fast_technical_analysis(ticker, name, macro_report, category=category)
        else:
            technical = technical_analyst.analyze(ticker, name)
            # 若传统技术面分析未产出 prediction，fallback 到轻量技术面
            if not technical.get('prediction'):
                technical = _fast_technical_analysis(ticker, name, macro_report, category=category)

        # 期货 fast 模式跳过新闻；期货使用专门基本面分析师
        if is_fut:
            ff = futures_fundamental_analyst.analyze(ticker, name)
            fundamental = {
                'score': ff.get('score', 50),
                'rating': ff.get('bias', 'N/A'),
                'fundamentals': {
                    'inventory': ff.get('data', {}).get('inventory'),
                    'basis': ff.get('data', {}).get('basis'),
                    'foreign': ff.get('data', {}).get('foreign'),
                    'warehouse': ff.get('data', {}).get('warehouse'),
                    'reasons': ff.get('reasons', []),
                },
                'error': 'ok',
            }
            news = {'sentiment_score': 0, 'sentiment': '中性', 'keywords': []}
        elif fast or ultra:
            fundamental = {'score': 50, 'rating': 'N/A', 'fundamentals': {}, 'error': 'skipped'}
            news = {'sentiment_score': 0, 'sentiment': '中性', 'keywords': []}
        else:
            if category == 'US':
                # 美股使用轻量基本面因子模型
                ff = fundamental_factor_analyst.analyze_fundamental_factors(ticker, name, category='US')
                quality_score = 50
                if ff['piotroski_f_score']['score'] is not None:
                    quality_score += (ff['piotroski_f_score']['score'] - 4.5) * 5
                if ff['altman_z_score']['score'] is not None:
                    z = ff['altman_z_score']['score']
                    if z > 2.99:
                        quality_score += 5
                    elif z < 1.81:
                        quality_score -= 8
                if ff['beneish_m_score']['score'] is not None:
                    m = ff['beneish_m_score']['score']
                    if m > -1.78:
                        quality_score -= 7
                quality_score = max(0, min(100, quality_score))
                fundamental = {
                    'score': round(quality_score, 1),
                    'rating': ff['piotroski_f_score']['signals'],
                    'fundamentals': {
                        'piotroski_f_score': ff['piotroski_f_score']['score'],
                        'altman_z_score': ff['altman_z_score']['score'],
                        'beneish_m_score': ff['beneish_m_score']['score'],
                        'beneish_flag': ff['beneish_m_score']['flag'],
                        'altman_zone': ff['altman_z_score']['zone'],
                    },
                    'error': 'ok',
                }
                news = {'sentiment_score': 0, 'sentiment': '中性', 'keywords': []}
            elif category == 'ETF':
                # ETF 使用费率/规模/集中度/跟踪误差质量因子
                from analysts import etf_quality_analyst
                eq = etf_quality_analyst.analyze_etf_quality(ticker, name)
                fundamental = {
                    'score': eq.get('quality_score', 50),
                    'rating': f"质量评分{eq.get('quality_score', 50)}",
                    'fundamentals': {
                        'management_fee': eq.get('fee', {}).get('management'),
                        'custody_fee': eq.get('fee', {}).get('custody'),
                        'total_fee': eq.get('fee', {}).get('total'),
                        'scale': eq.get('scale'),
                        'tracking_error': eq.get('tracking', {}).get('tracking_error'),
                        'tracking_is_proxy': eq.get('tracking', {}).get('is_proxy'),
                        'concentration_top10': eq.get('concentration', {}).get('top10'),
                        'concentration_top20': eq.get('concentration', {}).get('top20'),
                        'years_since_establish': eq.get('years_since_establish'),
                        'quality_reasons': eq.get('reasons', []),
                    },
                    'error': 'ok',
                }
                news = news_analyst.analyze(ticker, name)
            else:
                fundamental = fundamentals_analyst.analyze(ticker, name)
                news = news_analyst.analyze(ticker, name)

        bull = DebateEngine.bull_argument(technical, fundamental, news)
        bear = DebateEngine.bear_argument(technical, fundamental, news)

        verdict = _manager_verdict(technical, fundamental, news, bull, bear, macro_report=macro_report, ticker=ticker, sector=sector)

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
            'component_scores': {**verdict['component_scores'],
                                 'fundamental_score': fundamental.get('score', 50),
                                 'fundamental': fundamental.get('fundamentals', {})},
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
            SELECT id, ticker, signal, horizon_1d_return, current_price, category
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
            category = row['category'] or '个股'
            try:
                if category == 'US':
                    # 美股：取 pred_date 的收盘价（应为 current_price）与下一交易日收盘价
                    next_date = _next_trading_date_str(pred_date, calendar='us')
                    actual_price = get_us_price(ticker, as_of_date=next_date)
                else:
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


def _next_trading_date_str(current_date: str, calendar: str = 'us') -> str:
    """简单交易日推算：美股跳过周末；A股/期货跳过周末（暂不考虑节假日）。"""
    d = datetime.strptime(current_date, '%Y-%m-%d')
    if calendar == 'us':
        delta = 1 if d.weekday() < 4 else (7 - d.weekday())
    else:
        # A股/期货：周末后推到周一
        delta = 1 if d.weekday() < 5 else (7 - d.weekday())
    return (d + timedelta(days=delta)).strftime('%Y-%m-%d')

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
