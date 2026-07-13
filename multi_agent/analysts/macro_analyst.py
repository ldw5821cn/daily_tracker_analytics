#!/usr/bin/env python3
"""
宏观市场分析师 (Market Macro Analyst)

负责获取和判断整体市场环境：
- 大盘指数（沪深300、中证500、上证指数）趋势
- 市场情绪（涨跌停家数、涨跌比）
- 北向资金流向（如可获取）
- 人民币汇率/美债/大宗商品（可选）
- 板块轮动热度

输出统一的宏观评分和信号，供基金经理 Agent 在裁决时参考。
"""
from __future__ import annotations

import sys
import os
import json
import warnings
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

warnings.filterwarnings('ignore')

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, MULTI_AGENT)

from core.data_layer import get_stock_data, is_futures, is_etf, is_stock
import pandas as pd

# 大盘指数代码
INDEX_TICKERS = {
    '000300': '沪深300',
    '000905': '中证500',
    '000001': '上证指数',
    '399006': '创业板指',
    '399673': '创业板50',
}


def _safe_float(val, default=0.0):
    try:
        return float(val)
    except Exception:
        return default


def _get_index_data(ticker: str, period: str = '30d') -> Optional[pd.DataFrame]:
    """获取指数数据。"""
    try:
        df, _ = get_stock_data(ticker, period=period, calibrate=False)
        if df is not None and len(df) >= 5:
            return df
    except Exception:
        return None
    return None


def _calc_index_score(ticker: str, name: str) -> Dict:
    """计算单个指数的技术得分。"""
    df = _get_index_data(ticker, '60d')
    if df is None or len(df) < 20:
        return {'ticker': ticker, 'name': name, 'score': 50, 'trend': 'unknown', 'close': 0, 'ma20': 0, 'ma60': 0, 'return_1d': 0, 'return_5d': 0, 'return_20d': 0}

    latest = df.iloc[-1]
    prev = df.iloc[-2]
    close = float(latest['close'])
    ma20 = float(df['close'].rolling(20).mean().iloc[-1])
    ma60 = float(df['close'].rolling(60).mean().iloc[-1])

    ret_5d = (close / float(df['close'].iloc[-6]) - 1) * 100 if len(df) >= 6 else 0
    ret_20d = (close / float(df['close'].iloc[-21]) - 1) * 100 if len(df) >= 21 else 0

    score = 50
    if close > ma20: score += 8
    if close > ma60: score += 8
    if ma20 > ma60: score += 6
    if ret_5d > 0: score += 4
    if ret_20d > 0: score += 4

    # 最近 1 日涨跌
    day_change = (close / float(prev['close']) - 1) * 100
    if day_change > 1: score += 3
    elif day_change < -1: score -= 3

    score = max(0, min(100, score))

    trend = 'bullish' if score >= 65 else 'bearish' if score <= 35 else 'neutral'
    return {
        'ticker': ticker, 'name': name, 'score': round(score, 1),
        'trend': trend, 'close': round(close, 2),
        'ma20': round(ma20, 2), 'ma60': round(ma60, 2),
        'return_1d': round(day_change, 2),
        'return_5d': round(ret_5d, 2), 'return_20d': round(ret_20d, 2),
    }


def _get_market_breadth() -> Dict:
    """市场广度：基于沪深300/中证500的涨跌情况估算。"""
    breadth = {'advances': 0, 'declines': 0, 'score': 50}
    for ticker, name in INDEX_TICKERS.items():
        df = _get_index_data(ticker, '5d')
        if df is not None and len(df) >= 2:
            today = float(df['close'].iloc[-1])
            yesterday = float(df['close'].iloc[-2])
            if today > yesterday:
                breadth['advances'] += 1
            else:
                breadth['declines'] += 1
    total = breadth['advances'] + breadth['declines']
    if total > 0:
        breadth['score'] = round(breadth['advances'] / total * 100, 1)
    return breadth


def analyze(current_date: Optional[str] = None) -> Dict:
    """
    宏观市场分析主入口。
    返回宏观评分、市场方向和关键指标。
    """
    if current_date is None:
        current_date = datetime.now().strftime('%Y-%m-%d')

    index_scores = []
    for ticker, name in INDEX_TICKERS.items():
        s = _calc_index_score(ticker, name)
        s['name'] = name
        index_scores.append(s)

    breadth = _get_market_breadth()

    # 综合宏观得分（等权平均指数得分 + 市场广度）
    avg_index_score = sum(s['score'] for s in index_scores) / len(index_scores) if index_scores else 50
    macro_score = round(avg_index_score * 0.7 + breadth['score'] * 0.3, 1)

    # 宏观信号：帮助经理判断
    if macro_score >= 65:
        macro_signal = 'bullish'
    elif macro_score <= 35:
        macro_signal = 'bearish'
    else:
        macro_signal = 'neutral'

    # 生成报告
    summary_lines = [
        f"# 宏观市场分析报告 ({current_date})",
        "",
        f"综合宏观评分: {macro_score}/100 | 信号: {macro_signal}",
        "",
        "## 大盘指数",
    ]
    for s in index_scores:
        summary_lines.append(
            f"- {s.get('name', s['ticker'])}({s['ticker']}): 评分{s['score']}, 趋势{s['trend']}, "
            f"1日{s['return_1d']:+.2f}%, 5日{s['return_5d']:+.2f}%, 20日{s['return_20d']:+.2f}%"
        )
    summary_lines.append("")
    summary_lines.append(f"## 市场广度")
    summary_lines.append(f"- 指数上涨: {breadth['advances']} / 下跌: {breadth['declines']}")
    summary_lines.append(f"- 广度得分: {breadth['score']}")

    return {
        'analyst': '宏观市场分析师',
        'macro_score': macro_score,
        'macro_signal': macro_signal,
        'index_scores': index_scores,
        'market_breadth': breadth,
        'summary': "\n".join(summary_lines),
    }


def get_macro_score_override(macro_report: Dict, individual_signal: str) -> float:
    """
    返回对单个标的信号的修正分数。
    - 如果宏观看多，bullish 信号加分，bearish 信号减分
    - 如果宏观看空，bearish 信号加分，bullish 信号减分
    - 如果宏观中性，修正小
    """
    macro_signal = macro_report.get('macro_signal', 'neutral')
    macro_score = macro_report.get('macro_score', 50)
    # 距离中性（50）越远，修正幅度越大
    strength = abs(macro_score - 50) / 50  # 0~1
    if macro_signal == 'bullish':
        return 5 * strength if individual_signal == 'bullish' else -5 * strength if individual_signal == 'bearish' else 0
    elif macro_signal == 'bearish':
        return 5 * strength if individual_signal == 'bearish' else -5 * strength if individual_signal == 'bullish' else 0
    return 0


if __name__ == '__main__':
    r = analyze()
    print(r['summary'])
    print(f"\nmacro_score: {r['macro_score']} | signal: {r['macro_signal']}")
    print('修正示例 bullish:', get_macro_score_override(r, 'bullish'))
    print('修正示例 bearish:', get_macro_score_override(r, 'bearish'))
