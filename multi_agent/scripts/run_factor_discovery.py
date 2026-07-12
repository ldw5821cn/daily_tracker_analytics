"""多因子发现引擎：规模化规则因子 + LLM 组合/变异 + 批量回测。

设计目标：
- 规则因子库 30+ 个，覆盖趋势/反转/动量/波动/量价/筹码等维度
- 参数扫描：对同一逻辑做参数变体，快速扩展到 100+ 候选
- 组合因子：将表现最好的单因子等权/投票组合
- 统一数据加载：一次性加载标的池数据，批量跑所有因子
- 评估指标：收益、夏普、最大回撤、胜率、Calmar、交易频率
"""
import json
import os
import re
import sys
import itertools
from typing import List, Dict
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'multi_agent'))

from core.llm_client import chat
from core.backtest_utils import parse_backtest_summary

FACTOR_DB_PATH = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'llm_factors.json')

# ─────────────────────────────────────────
# 1. 规则基线因子模板（30+）
# ─────────────────────────────────────────
BASE_FACTOR_TEMPLATES = [
    # 趋势类
    {'name': 'MA20趋势', 'desc': '价格站上MA20做多，跌破做空', 'code': "signal = np.where(close > ma20, 1, -1)"},
    {'name': 'MA60趋势', 'desc': '价格站上MA60做多，跌破做空', 'code': "signal = np.where(close > ma60, 1, -1)"},
    {'name': '均线多头排列', 'desc': 'MA5>MA10>MA20 做多，空头排列做空', 'code': "signal = np.where((ma5 > ma10) & (ma10 > ma20), 1, np.where((ma5 < ma10) & (ma10 < ma20), -1, 0))"},
    {'name': '双均线金叉', 'desc': 'MA5上穿MA20做多，下穿做空', 'code': "ma5_prev, ma20_prev = ma5.shift(1), ma20.shift(1)\nsignal = np.where((ma5 > ma20) & (ma5_prev <= ma20_prev), 1, np.where((ma5 < ma20) & (ma5_prev >= ma20_prev), -1, 0))"},
    {'name': '价格偏离MA20', 'desc': '价格偏离MA20过远（+5%）做空，过低（-5%）做多', 'code': "dev = close / ma20 - 1\nsignal = np.where(dev < -0.05, 1, np.where(dev > 0.05, -1, 0))"},

    # 动量类
    {'name': '5日动量', 'desc': '5日收益为正做多，为负做空', 'code': "mom5 = close.pct_change(5)\nsignal = np.where(mom5 > 0, 1, -1)"},
    {'name': '10日动量', 'desc': '10日收益为正做多，为负做空', 'code': "mom10 = close.pct_change(10)\nsignal = np.where(mom10 > 0, 1, -1)"},
    {'name': '20日动量', 'desc': '20日收益为正做多，为负做空', 'code': "mom20 = close.pct_change(20)\nsignal = np.where(mom20 > 0, 1, -1)"},
    {'name': '动量加速', 'desc': '5日动量大于10日动量做多，反之做空', 'code': "mom5, mom10 = close.pct_change(5), close.pct_change(10)\nsignal = np.where(mom5 > mom10, 1, -1)"},

    # MACD 类
    {'name': 'MACD柱状', 'desc': 'MACD柱状为正做多，为负做空', 'code': "signal = np.where(macd_hist > 0, 1, -1)"},
    {'name': 'MACD金叉', 'desc': 'DIF上穿DEA做多，下穿做空', 'code': "macd_hist_prev = macd_hist.shift(1)\nsignal = np.where((macd_hist > 0) & (macd_hist_prev <= 0), 1, np.where((macd_hist < 0) & (macd_hist_prev >= 0), -1, 0))"},

    # RSI 类
    {'name': 'RSI超卖反弹', 'desc': 'RSI14<30做多，>70做空', 'code': "signal = np.where(rsi_14 < 30, 1, np.where(rsi_14 > 70, -1, 0))"},
    {'name': 'RSI中轴', 'desc': 'RSI14>50做多，<50做空', 'code': "signal = np.where(rsi_14 > 50, 1, -1)"},
    {'name': 'RSI动量', 'desc': 'RSI14大于前一日做多，反之做空', 'code': "rsi_prev = rsi_14.shift(1)\nsignal = np.where(rsi_14 > rsi_prev, 1, -1)"},
    {'name': 'RSI过滤', 'desc': '多头排列且RSI 40-70做多', 'code': "signal = np.where((ma5 > ma10) & (ma10 > ma20) & (rsi_14 > 40) & (rsi_14 < 70), 1, 0)"},

    # 布林带类
    {'name': 'BOLL下轨反弹', 'desc': '触及布林下轨做多，上轨做空', 'code': "signal = np.where(close <= boll_down, 1, np.where(close >= boll_up, -1, 0))"},
    {'name': 'BOLL中轨趋势', 'desc': '价格上穿布林中轨做多，下穿做空', 'code': "boll_mid = (boll_up + boll_down) / 2\nsignal = np.where(close > boll_mid, 1, -1)"},

    # 波动率类
    {'name': '突破20日高点', 'desc': '价格创20日新高做多，创20日新低做空', 'code': "hh = close.rolling(20).max()\nll = close.rolling(20).min()\nsignal = np.where(close >= hh.shift(1), 1, np.where(close <= ll.shift(1), -1, 0))"},
    {'name': '波动率收缩', 'desc': '20日波动率低于5日波动率时跟随趋势', 'code': "vol20 = close.pct_change().rolling(20).std()\nvol5 = close.pct_change().rolling(5).std()\nsignal = np.where((vol20 < vol5) & (close > ma5), 1, np.where((vol20 < vol5) & (close < ma5), -1, 0))"},

    # 量价类
    {'name': '放量突破', 'desc': '放量突破MA20做多，缩量跌破MA20做空', 'code': "signal = np.where((close > ma20) & (vol_ratio > 1.5), 1, np.where((close < ma20) & (vol_ratio < 0.8), -1, 0))"},
    {'name': '量价齐升', 'desc': '价格上涨且放量做多，下跌且放量做空', 'code': "ret = close.pct_change()\nsignal = np.where((ret > 0) & (vol_ratio > 1.2), 1, np.where((ret < 0) & (vol_ratio > 1.2), -1, 0))"},
    {'name': '缩量回调', 'desc': '价格下跌但缩量（洗盘）做多', 'code': "ret = close.pct_change()\nsignal = np.where((ret < 0) & (vol_ratio < 0.7), 1, 0)"},

    # 综合类
    {'name': '趋势动量共振', 'desc': '价格站上MA60且5日动量正做多', 'code': "signal = np.where((close > ma60) & (momentum_5d > 0), 1, np.where(close < ma60, -1, 0))"},
    {'name': 'MACD_RSI共振', 'desc': 'MACD正且RSI 40-70做多', 'code': "signal = np.where((macd_hist > 0) & (rsi_14 > 40) & (rsi_14 < 70), 1, 0)"},
    {'name': '均线MACD共振', 'desc': 'MA多头排列且MACD正做多', 'code': "signal = np.where((ma5 > ma10) & (ma10 > ma20) & (macd_hist > 0), 1, np.where((ma5 < ma10) & (macd_hist < 0), -1, 0))"},

    # 更多量价/波动类
    {'name': 'MACD量能背离', 'desc': 'MACD红柱放大且价格高于MA5做多', 'code': "macd_hist_prev = macd_hist.shift(1)\nsignal = np.where((macd_hist > macd_hist_prev) & (close > ma5), 1, 0)"},
    {'name': '双底放量', 'desc': '价格接近布林下轨且放量做多', 'code': "signal = np.where((close <= boll_down * 1.03) & (vol_ratio > 1.2), 1, 0)"},
    {'name': '缩量反弹', 'desc': '价格上涨但缩量时谨慎做多', 'code': "ret = close.pct_change()\nsignal = np.where((ret > 0) & (vol_ratio < 0.8), 1, 0)"},
    {'name': '高波动过滤', 'desc': '波动率较高时做空，较低时做多', 'code': "vol20 = close.pct_change().rolling(20).std()\nmed = vol20.rolling(60).median()\nsignal = np.where((vol20 > med) & (close < ma5), -1, np.where((vol20 < med) & (close > ma5), 1, 0))"},
    {'name': '通道突破', 'desc': '突破20日高点且放量做多', 'code': "hh = close.rolling(20).max()\nll = close.rolling(20).min()\nsignal = np.where((close >= hh.shift(1)) & (vol_ratio > 1.3), 1, np.where(close <= ll.shift(1), -1, 0))"},
    {'name': 'RSI背离', 'desc': '价格创新低但RSI未创新低做多', 'code': "price_ll = close <= close.rolling(20).min().shift(1)\nrsi_ll = rsi_14 <= rsi_14.rolling(20).min().shift(1)\nsignal = np.where(price_ll & (~rsi_ll), 1, 0)"},
    {'name': '动量波动', 'desc': '5日动量大于20日动量且波动率较低做多', 'code': "mom5, mom20 = close.pct_change(5), close.pct_change(20)\nvol20 = close.pct_change().rolling(20).std()\nmed = vol20.rolling(60).median()\nsignal = np.where((mom5 > mom20) & (vol20 < med), 1, np.where((mom5 < mom20) & (vol20 > med), -1, 0))"},
    {'name': 'BOLL压缩', 'desc': '布林带收窄后放量突破上轨做多', 'code': "boll_width = (boll_up - boll_down) / ((boll_up + boll_down) / 2)\nwidth_prev = boll_width.shift(1)\nsignal = np.where((boll_width < width_prev) & (close > boll_up) & (vol_ratio > 1.2), 1, 0)"},
    {'name': 'MA20回踩', 'desc': '价格回踩MA20且未跌破做多', 'code': "prev_close = close.shift(1)\nsignal = np.where((prev_close > ma20) & (close >= ma20 * 0.98) & (close <= ma20 * 1.02), 1, 0)"},
    {'name': 'MACD零轴', 'desc': 'MACD柱由负转正穿越零轴做多', 'code': "macd_hist_prev = macd_hist.shift(1)\nsignal = np.where((macd_hist > 0) & (macd_hist_prev <= 0), 1, np.where((macd_hist < 0) & (macd_hist_prev >= 0), -1, 0))"},
    {'name': '量价背离', 'desc': '价格上涨但成交量下降做空', 'code': "ret = close.pct_change()\nsignal = np.where((ret > 0) & (vol_ratio < 0.7), -1, 0)"},
    {'name': '突破回踩', 'desc': '创20日新高后回踩不破前高做多', 'code': "hh = close.rolling(20).max()\nsignal = np.where((close > hh.shift(1)) & (close.shift(1) >= hh.shift(2)), 1, 0)"},

    # 更多动量/反转类
    {'name': '动量12日', 'desc': '12日收益为正做多，为负做空', 'code': "mom = close.pct_change(12)\nsignal = np.where(mom > 0, 1, -1)"},
    {'name': '动量15日', 'desc': '15日收益为正做多，为负做空', 'code': "mom = close.pct_change(15)\nsignal = np.where(mom > 0, 1, -1)"},
    {'name': '动量30日', 'desc': '30日收益为正做多，为负做空', 'code': "mom = close.pct_change(30)\nsignal = np.where(mom > 0, 1, -1)"},
    {'name': '动能衰减', 'desc': '5日动量大于10日但小于20日，动能在衰减时退出', 'code': "mom5, mom10, mom20 = close.pct_change(5), close.pct_change(10), close.pct_change(20)\nsignal = np.where((mom5 > mom10) & (mom10 < mom20), 1, np.where((mom5 < mom10) & (mom10 > mom20), -1, 0))"},

    # 更多 RSI 类
    {'name': 'RSI金叉', 'desc': 'RSI上穿50做多，下穿50做空', 'code': "rsi_prev = rsi_14.shift(1)\nsignal = np.where((rsi_14 > 50) & (rsi_prev <= 50), 1, np.where((rsi_14 < 50) & (rsi_prev >= 50), -1, 0))"},
    {'name': 'RSI快速反转', 'desc': 'RSI从低于20快速反弹到30以上做多', 'code': "rsi_prev = rsi_14.shift(1)\nsignal = np.where((rsi_prev < 20) & (rsi_14 > 30), 1, 0)"},
    {'name': 'RSI高位回落', 'desc': 'RSI从高于80回落到70以下做空', 'code': "rsi_prev = rsi_14.shift(1)\nsignal = np.where((rsi_prev > 80) & (rsi_14 < 70), -1, 0)"},

    # 更多 MACD 类
    {'name': 'MACD背离', 'desc': '价格新低但MACD柱未新低做多', 'code': "price_ll = close <= close.rolling(20).min().shift(1)\nmacd_ll = macd_hist <= macd_hist.rolling(20).min().shift(1)\nsignal = np.where(price_ll & (~macd_ll), 1, 0)"},
    {'name': 'MACD缩量', 'desc': 'MACD柱为正但缩小，动量减弱做空', 'code': "macd_hist_prev = macd_hist.shift(1)\nsignal = np.where((macd_hist > 0) & (macd_hist < macd_hist_prev), -1, 0)"},
    {'name': 'MACD量能共振', 'desc': 'MACD为正且价格站上MA60且放量做多', 'code': "signal = np.where((macd_hist > 0) & (close > ma60) & (vol_ratio > 1.2), 1, 0)"},

    # 更多布林带类
    {'name': 'BOLL百分位', 'desc': '价格位于布林带下轨附近做多，上轨附近做空', 'code': "boll_pct = (close - boll_down) / (boll_up - boll_down + 1e-6)\nsignal = np.where(boll_pct < 0.1, 1, np.where(boll_pct > 0.9, -1, 0))"},
    {'name': 'BOLL突破中轨', 'desc': '价格突破布林中轨且放量做多', 'code': "boll_mid = (boll_up + boll_down) / 2\nprev_close = close.shift(1)\nprev_mid = boll_mid.shift(1)\nsignal = np.where((close > boll_mid) & (prev_close <= prev_mid) & (vol_ratio > 1.1), 1, 0)"},
    {'name': 'BOLL收口', 'desc': '布林带收窄后，价格站上中轨做多', 'code': "boll_width = (boll_up - boll_down) / ((boll_up + boll_down) / 2)\nwidth_ma = boll_width.rolling(20).mean()\nboll_mid = (boll_up + boll_down) / 2\nsignal = np.where((boll_width < width_ma) & (close > boll_mid), 1, 0)"},

    # 更多波动率类
    {'name': 'ATR突破', 'desc': '价格突破昨日高点加上0.5倍ATR做多', 'code': "atr = (close.rolling(14).max() - close.rolling(14).min()).rolling(14).mean()\nsignal = np.where(close > close.shift(1) + 0.5 * atr, 1, 0)"},
    {'name': '波动率均值回归', 'desc': '波动率处于60日低位且价格上涨做多', 'code': "vol20 = close.pct_change().rolling(20).std()\nrank = vol20.rolling(60).apply(lambda x: x.rank().iloc[-1] / len(x), raw=False)\nsignal = np.where((rank < 0.2) & (close > ma5), 1, 0)"},
    {'name': '波动率突破', 'desc': '波动率突破60日均值且价格上涨做多', 'code': "vol20 = close.pct_change().rolling(20).std()\nvol_ma = vol20.rolling(60).mean()\nsignal = np.where((vol20 > vol_ma) & (close > ma20), 1, 0)"},

    # 更多量价类
    {'name': '放量阳线', 'desc': '收阳线且成交量是20日均量1.5倍以上做多', 'code': "ret = close.pct_change()\nsignal = np.where((ret > 0) & (vol_ratio > 1.5), 1, 0)"},
    {'name': '缩量阴线', 'desc': '收阴线但成交量萎缩，卖压不足做多', 'code': "ret = close.pct_change()\nsignal = np.where((ret < 0) & (vol_ratio < 0.6), 1, 0)"},
    {'name': '成交量突破', 'desc': '成交量是5日均量2倍以上且价格上涨做多', 'code': "signal = np.where((vol_ratio > 2.0) & (close > close.shift(1)), 1, 0)"},
    {'name': '量价齐跌', 'desc': '价格下跌且成交量放大，继续看空', 'code': "ret = close.pct_change()\nsignal = np.where((ret < 0) & (vol_ratio > 1.3), -1, 0)"},
    {'name': '均量线金叉', 'desc': '5日成交量均线上穿20日均量做多', 'code': "vol5 = vol_ratio.rolling(5).mean()\nvol20m = vol_ratio.rolling(20).mean()\nsignal = np.where((vol5 > vol20m) & (vol5.shift(1) <= vol20m.shift(1)), 1, 0)"},

    # 筹码/支撑类
    {'name': '跳空回补', 'desc': '向上跳空后价格未回补缺口做多', 'code': "gap = close - close.shift(1)\nsignal = np.where((gap > 0.01 * close.shift(1)) & (close > close.shift(1).rolling(5).max().shift(1)), 1, 0)"},
    {'name': 'V型反弹', 'desc': '前5日大幅下跌，近3日反弹超过5%做多', 'code': "past5 = close.pct_change(5)\npast3 = close.pct_change(3)\nsignal = np.where((past5 < -0.10) & (past3 > 0.05), 1, 0)"},
    {'name': '平台突破', 'desc': '价格突破20日盘整区间上轨且放量做多', 'code': "hh = close.rolling(20).max()\nll = close.rolling(20).min()\nrange_pct = (hh - ll) / ((hh + ll) / 2)\nsignal = np.where((range_pct < 0.08) & (close > hh.shift(1)) & (vol_ratio > 1.2), 1, 0)"},

    # 综合/过滤类
    {'name': '趋势过滤RSI', 'desc': 'MA20趋势向上且RSI未超买做多', 'code': "ma20_trend = ma20 > ma20.shift(5)\nsignal = np.where(ma20_trend & (rsi_14 < 70), 1, 0)"},
    {'name': '动量过滤波动率', 'desc': '5日动量为正且20日波动率处于低位做多', 'code': "mom5 = close.pct_change(5)\nvol20 = close.pct_change().rolling(20).std()\nvol_low = vol20 < vol20.rolling(60).median()\nsignal = np.where((mom5 > 0) & vol_low, 1, 0)"},
    {'name': 'RSI_MACD趋势', 'desc': 'RSI>50且MACD为正且价格站上MA20做多', 'code': "signal = np.where((rsi_14 > 50) & (macd_hist > 0) & (close > ma20), 1, 0)"},
    {'name': '多周期共振', 'desc': '日线和周级别均线均向上时做多', 'code': "trend = (close > ma20) & (ma20 > ma60) & (close > ma60)\nsignal = np.where(trend, 1, 0)"},
]

# 参数扫描配置：为更多因子生成参数变体
PARAM_SCAN = {
    'MA20趋势': {'window': [10, 20, 30, 60]},
    '双均线金叉': {'fast': [5, 10], 'slow': [20, 60]},
    'RSI超卖反弹': {'low': [20, 25, 30], 'high': [70, 75, 80]},
    '5日动量': {'window': [3, 5, 10, 20]},
    '10日动量': {'window': [10, 15, 20, 30]},
    '20日动量': {'window': [20, 30, 60]},
    '放量突破': {'vol_thresh': [1.2, 1.5, 2.0]},
    '突破20日高点': {'window': [10, 20, 60]},
    '价格偏离MA20': {'dev': [0.03, 0.05, 0.08]},
    'BOLL下轨反弹': {'width': [1, 2]},
    'BOLL百分位': {'low': [0.1, 0.2], 'high': [0.8, 0.9]},
    '动量12日': {'window': [5, 12, 20]},
    'RSI中轴': {'threshold': [45, 50, 55]},
    'MACD柱状': {'threshold': [0, 0.001]},
    '趋势动量共振': {'window': [5, 10, 20]},
    'BOLL中轨趋势': {'width': [0, 0.01]},
    '成交量突破': {'vol_thresh': [1.5, 2.0, 3.0]},
}


def _load_existing_factors():
    if not os.path.exists(FACTOR_DB_PATH):
        return []
    try:
        with open(FACTOR_DB_PATH, 'r', encoding='utf-8') as f:
            return json.load(f).get('factors', [])
    except Exception:
        return []


def _load_top_tickers(n=5):
    import sqlite3
    db = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'llm_predictions.db')
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    latest = conn.execute("SELECT MAX(pred_date) FROM agentic_predictions").fetchone()[0]
    rows = conn.execute(
        "SELECT ticker, name, category, backtest_summary FROM agentic_predictions WHERE pred_date=? AND category IN ('个股','ETF') ORDER BY weighted_score DESC LIMIT ?",
        (latest, n)
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d['bt'] = parse_backtest_summary(d.get('backtest_summary', ''))
        result.append(d)
    return result


def _batch_load_data(tickers: List[str]) -> Dict[str, object]:
    """一次性加载所有标的的 OHLC + 指标数据。"""
    from core.data_layer import get_stock_data, calc_technical_indicators
    cache = {}
    for t in tickers:
        try:
            df, info = get_stock_data(t)
            df = calc_technical_indicators(df)
            cache[t] = df
        except Exception as e:
            print(f"  ⚠️ 加载 {t} 失败: {e}", file=sys.stderr)
    return cache


def _evaluate_factor_on_df(df: pd.DataFrame, code: str) -> Dict:
    """在单个 DataFrame 上执行因子代码并回测。"""
    import pandas as pd
    import numpy as np

    close = df['close']
    ma5 = df['ma5']
    ma10 = df['ma10']
    ma20 = df['ma20']
    ma60 = df['ma60']
    rsi_14 = df['rsi_14']
    macd_hist = df['macd_hist']
    momentum_5d = df['momentum_5d']
    boll_up = df['boll_up']
    boll_down = df['boll_down']
    vol_ratio = df['vol_ratio']

    namespace = {
        'np': np, 'pd': pd,
        'close': close, 'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
        'rsi_14': rsi_14, 'macd_hist': macd_hist, 'momentum_5d': momentum_5d,
        'boll_up': boll_up, 'boll_down': boll_down, 'vol_ratio': vol_ratio,
    }
    exec(code, namespace)

    signal = namespace.get('signal')
    if signal is None:
        return {'error': '未定义 signal'}

    signal = pd.Series(signal, index=df.index).fillna(0).shift(1).fillna(0)
    returns = df['close'].pct_change()
    strategy_returns = signal * returns

    total_return = strategy_returns.sum()
    vol = strategy_returns.std() * np.sqrt(252)
    sharpe = (strategy_returns.mean() / strategy_returns.std()) * np.sqrt(252) if strategy_returns.std() > 0 else 0
    cumulative = (1 + strategy_returns).cumprod()
    max_dd = ((cumulative - cumulative.cummax()) / cumulative.cummax()).min()
    positive = (strategy_returns > 0).sum()
    negative = (strategy_returns < 0).sum()
    win_rate = positive / (positive + negative) if (positive + negative) > 0 else 0
    calmar = total_return / abs(max_dd) if max_dd != 0 else 0
    trades = int((signal.diff() != 0).sum())

    return {
        'total_return': round(total_return * 100, 2),
        'annual_vol': round(vol * 100, 2),
        'sharpe': round(sharpe, 2),
        'max_drawdown': round(max_dd * 100, 2),
        'win_rate': round(win_rate * 100, 1),
        'calmar': round(calmar, 2),
        'trades': trades,
    }


def _evaluate_factor(factor: Dict, data_cache: Dict[str, pd.DataFrame]) -> Dict:
    """在多个标的上测试因子。"""
    results = []
    for ticker, df in data_cache.items():
        r = _evaluate_factor_on_df(df, factor['code'])
        r['ticker'] = ticker
        results.append(r)

    valid = [r for r in results if 'error' not in r]
    if not valid:
        return {**factor, 'evaluation': results, 'passed': False, 'score': -999}

    avg_sharpe = sum(r['sharpe'] for r in valid) / len(valid)
    avg_return = sum(r['total_return'] for r in valid) / len(valid)
    avg_drawdown = sum(r['max_drawdown'] for r in valid) / len(valid)
    avg_win_rate = sum(r['win_rate'] for r in valid) / len(valid)
    avg_calmar = sum(r['calmar'] for r in valid) / len(valid)
    avg_trades = sum(r['trades'] for r in valid) / len(valid)

    score = avg_return * max(avg_sharpe, 0) + avg_calmar * 10
    passed = avg_return > 0 and score > 0

    return {
        **factor,
        'evaluation': results,
        'avg_sharpe': round(avg_sharpe, 2),
        'avg_return': round(avg_return, 2),
        'avg_drawdown': round(avg_drawdown, 2),
        'avg_win_rate': round(avg_win_rate, 1),
        'avg_calmar': round(avg_calmar, 2),
        'avg_trades': int(avg_trades),
        'score': round(score, 2),
        'passed': passed,
    }


def _extract_code_blocks(text: str) -> List[str]:
    blocks = re.findall(r'```python\n(.*?)```', text, re.DOTALL)
    if not blocks:
        blocks = re.findall(r'```\n(.*?)```', text, re.DOTALL)
    return blocks


def _generate_llm_factors(top_tickers: List[Dict], n: int = 3) -> List[Dict]:
    """尝试让 LLM 生成多个新因子。"""
    top_context = "\n".join([
        f"{t['ticker']} {t['name']}({t['category']}): 60日收益{t['bt'].get('return_60d',0):+.1f}%, 夏普{t['bt'].get('sharpe_60d',0):.2f}"
        for t in top_tickers
    ])

    prompt = f"""你是量化因子研究员。请为 A 股设计 3 个不同的量价因子。

可用数据列：close, ma5, ma10, ma20, ma60, rsi_14, macd_hist, momentum_5d, boll_up, boll_down, vol_ratio。

当前强势股：
{top_context}

每个因子请输出：
1. 因子名（≤10字）
2. 逻辑描述（≤50字）
3. Python 代码（必须定义 signal 变量，取值为 1/-1/0；不要 import 其他模块；用 shift(1) 避免未来函数）
4. 原理（≤30字）

请用多个代码块分别输出。"""

    try:
        resp = chat([{'role': 'user', 'content': prompt}], temperature=0.8, max_tokens=2000)
    except Exception as e:
        return [{'error': f'LLM 调用异常: {e}'}]

    if not resp:
        return [{'error': 'LLM 未返回结果'}]

    blocks = _extract_code_blocks(resp)
    factors = []
    for i, code in enumerate(blocks):
        factors.append({
            'name': f'LLM_因子_{i+1}',
            'description': 'LLM 生成因子',
            'code': code,
            'rationale': '',
            'source': 'llm',
        })
    return factors


def _expand_param_variants():
    """对参数扫描配置生成真正的因子变体。"""
    variants = []
    for base in BASE_FACTOR_TEMPLATES:
        if base['name'] not in PARAM_SCAN:
            variants.append({
                'name': base['name'],
                'description': base['desc'],
                'code': base['code'] + "\nsignal = pd.Series(signal, index=close.index).shift(1).fillna(0)",
                'source': 'rule',
            })
            continue

        params = PARAM_SCAN[base['name']]
        keys = list(params.keys())
        for vals in itertools.product(*params.values()):
            kw = dict(zip(keys, vals))
            code = base['code']
            name = base['name']

            if base['name'] == 'MA20趋势':
                ma_map = {10: 'ma10', 20: 'ma20', 30: 'ma20', 60: 'ma60'}
                ma_col = ma_map.get(kw['window'], 'ma20')
                code = code.replace('ma20', ma_col)
                name = f"MA趋势_window{kw['window']}"

            elif base['name'] == '双均线金叉':
                ma_map = {5: 'ma5', 10: 'ma10', 20: 'ma20', 30: 'ma30', 60: 'ma60'}
                # 需动态构造 ma_slow，但模板中 ma5 已存在，ma20 已存在，ma30 未计算。这里只支持 5/10 vs 20/60
                if kw['fast'] == 5 and kw['slow'] in [20, 60]:
                    fast_col = ma_map[kw['fast']]
                    slow_col = ma_map[kw['slow']]
                    code = f"{fast_col}_prev, {slow_col}_prev = {fast_col}.shift(1), {slow_col}.shift(1)\nsignal = np.where(({fast_col} > {slow_col}) & ({fast_col}_prev <= {slow_col}_prev), 1, np.where(({fast_col} < {slow_col}) & ({fast_col}_prev >= {slow_col}_prev), -1, 0))"
                name = f"双均线金叉_{kw['fast']}x{kw['slow']}"

            elif base['name'] == 'RSI超卖反弹':
                code = code.replace('rsi_14 < 30', f'rsi_14 < {kw["low"]}').replace('rsi_14 > 70', f'rsi_14 > {kw["high"]}')
                name = f"RSI反转_L{kw['low']}H{kw['high']}"

            elif base['name'] == '5日动量':
                code = code.replace('close.pct_change(5)', f'close.pct_change({kw["window"]})')
                name = f"{kw['window']}日动量"

            elif base['name'] == '10日动量':
                code = code.replace('close.pct_change(10)', f'close.pct_change({kw["window"]})')
                name = f"动量_{kw['window']}日"

            elif base['name'] == '20日动量':
                code = code.replace('close.pct_change(20)', f'close.pct_change({kw["window"]})')
                name = f"动量_{kw['window']}日"

            elif base['name'] == '放量突破':
                code = code.replace('vol_ratio > 1.5', f'vol_ratio > {kw["vol_thresh"]}').replace('vol_ratio < 0.8', f'vol_ratio < {kw["vol_thresh"] * 0.5}')
                name = f"放量突破_v{kw['vol_thresh']}"

            elif base['name'] == '突破20日高点':
                code = code.replace('rolling(20)', f'rolling({kw["window"]})')
                name = f"突破{kw['window']}日高点"

            elif base['name'] == '价格偏离MA20':
                code = code.replace('-0.05', f'-{kw["dev"]}').replace('0.05', f'{kw["dev"]}')
                name = f"偏离MA20_d{kw['dev']}"

            elif base['name'] == 'BOLL下轨反弹':
                # 原始模板使用 close <= boll_down，不做变体，只是参数化宽度
                code = code.replace('close <= boll_down', f'close <= boll_down * (1 + {kw["width"] * 0.01})')
                name = f"BOLL下轨反弹_w{kw['width']}"

            elif base['name'] == 'BOLL百分位':
                code = code.replace('boll_pct < 0.1', f'boll_pct < {kw["low"]}').replace('boll_pct > 0.9', f'boll_pct > {kw["high"]}')
                name = f"BOLL百分位_l{kw['low']}h{kw['high']}"

            elif base['name'] == '动量12日':
                code = code.replace('close.pct_change(12)', f'close.pct_change({kw["window"]})')
                name = f"动量_{kw['window']}日"

            elif base['name'] == 'RSI中轴':
                code = code.replace('rsi_14 > 50', f'rsi_14 > {kw["threshold"]}').replace('rsi_14 < 50', f'rsi_14 < {kw["threshold"]}')
                name = f"RSI中轴_t{kw['threshold']}"

            elif base['name'] == 'MACD柱状':
                code = code.replace('macd_hist > 0', f'macd_hist > {kw["threshold"]}').replace('macd_hist < 0', f'macd_hist < -{kw["threshold"]}')
                name = f"MACD柱状_t{kw['threshold']}"

            elif base['name'] == 'MA60趋势':
                ma_map = {30: 'ma30', 60: 'ma60', 120: 'ma60'}
                ma_col = ma_map.get(kw['window'], 'ma60')
                code = code.replace('ma60', ma_col)
                name = f"MA趋势_window{kw['window']}"

            elif base['name'] == '趋势动量共振':
                code = code.replace('momentum_5d > 0', f'close.pct_change({kw["window"]}) > 0')
                name = f"趋势动量共振_w{kw['window']}"

            elif base['name'] == 'BOLL中轨趋势':
                # width 参数占位，不做实际替换
                name = f"BOLL中轨趋势_w{kw['width']}"

            elif base['name'] == '成交量突破':
                code = code.replace('vol_ratio > 2.0', f'vol_ratio > {kw["vol_thresh"]}')
                name = f"成交量突破_v{kw['vol_thresh']}"

            variants.append({
                'name': name,
                'description': base['desc'] + ' ' + str(kw),
                'code': code + "\nsignal = pd.Series(signal, index=close.index).shift(1).fillna(0)",
                'source': 'rule',
            })
    return variants


def _build_composite_factor(top_factors: List[Dict], data_cache: Dict[str, pd.DataFrame]):
    """将 Top 3 单因子按投票组合成一个组合因子。"""
    if len(top_factors) < 3:
        return None

    code = ""
    for i, f in enumerate(top_factors[:3]):
        sub_code = f['code'].replace('signal', f'sig{i}')
        code += f"# {f['name']}\n{sub_code}\n\n"
    code += "vote = sig0 + sig1 + sig2\n"
    code += "signal = np.where(vote >= 2, 1, np.where(vote <= -2, -1, 0))\n"
    code += "signal = pd.Series(signal, index=close.index).shift(1).fillna(0)"

    composite = {
        'name': '组合_等权投票',
        'description': 'Top3 单因子等权投票组合',
        'code': code,
        'source': 'composite',
    }
    return _evaluate_factor(composite, data_cache)


def _sanitize(obj):
    import numpy as np
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, (np.bool_, np.bool)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj


def run_factor_discovery(use_llm=False, num_llm=3, save_top_n=15):
    existing = _load_existing_factors()
    top = _load_top_tickers(20)  # 扩展到 20 只标的
    tickers = [t['ticker'] for t in top]

    print(f"[factor_discovery] 加载 {len(tickers)} 个标的，批量拉取数据中...")
    data_cache = _batch_load_data(tickers)
    if not data_cache:
        print("[factor_discovery] 没有可用数据", file=sys.stderr)
        return []

    candidates = []

    # 1. 规则基线因子 + 参数扫描变体
    rule_variants = _expand_param_variants()
    print(f"[factor_discovery] 规则因子候选: {len(rule_variants)}")
    candidates.extend(rule_variants)

    # 2. LLM 生成因子（可选）
    if use_llm:
        print("[factor_discovery] 请求 LLM 生成因子...")
        llm_factors = _generate_llm_factors(top, n=num_llm)
        for f in llm_factors:
            if 'error' not in f:
                candidates.append(f)
            else:
                print(f"[factor_discovery] LLM 生成失败: {f.get('error')}", file=sys.stderr)

    # 3. 去重
    existing_names = {x['name'] for x in existing}
    candidates = [c for c in candidates if c['name'] not in existing_names]

    # 4. 批量回测
    print(f"[factor_discovery] 开始回测 {len(candidates)} 个候选因子...")
    evaluated = []
    for c in candidates:
        ev = _evaluate_factor(c, data_cache)
        evaluated.append(ev)

    # 5. 组合因子
    passed_single = [ev for ev in evaluated if ev.get('passed')]
    passed_single.sort(key=lambda x: x['score'], reverse=True)
    if passed_single:
        composite = _build_composite_factor(passed_single, data_cache)
        if composite:
            evaluated.append(composite)

    # 6. 保存 Top N
    all_passed = [ev for ev in evaluated if ev.get('passed')]
    all_passed.sort(key=lambda x: x['score'], reverse=True)
    for ev in all_passed[:save_top_n]:
        existing.append(ev)

    os.makedirs(os.path.dirname(FACTOR_DB_PATH), exist_ok=True)
    with open(FACTOR_DB_PATH, 'w', encoding='utf-8') as f:
        json.dump({'factors': _sanitize(existing)}, f, ensure_ascii=False, indent=2)

    print(f"[factor_discovery] 通过 {len(all_passed)} 个，保存 Top {min(save_top_n, len(all_passed))}")
    return evaluated


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--llm', action='store_true', help='同时尝试 LLM 生成因子')
    parser.add_argument('--num-llm', type=int, default=3, help='LLM 生成因子数量')
    parser.add_argument('--top-n', type=int, default=10, help='保存表现最好的 N 个因子')
    args = parser.parse_args()

    results = run_factor_discovery(use_llm=args.llm, num_llm=args.num_llm, save_top_n=args.top_n)
    passed = [r for r in results if r.get('passed')]
    print(f"\n测试 {len(results)} 个因子，{len(passed)} 个通过")
    for r in sorted(passed, key=lambda x: x['score'], reverse=True)[:10]:
        print(f"\n✅ {r['name']} ({r.get('source','rule')})")
        print(f"   收益 {r['avg_return']:+.2f}% | 夏普 {r['avg_sharpe']} | 回撤 {r['avg_drawdown']}% | 胜率 {r['avg_win_rate']}% | Calmar {r['avg_calmar']} | 交易 {r['avg_trades']}")
        print(f"   逻辑: {r['description']}")
