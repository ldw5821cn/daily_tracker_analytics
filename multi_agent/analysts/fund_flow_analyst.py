#!/usr/bin/env python3
"""资金流分析器：基于 akshare 资金流向缓存，给个股/行业/概念打分。

使用 multi_agent/scripts/fetch_fund_flow_cache.py 预拉取的缓存，
避免每次调用东财接口，提高稳定性。
"""
from __future__ import annotations

import json
import os
import sys
from typing import Dict, List, Optional, Tuple

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, MULTI_AGENT)

from scripts.fetch_fund_flow_cache import load_latest_cache, fetch_and_cache


def _load_cache() -> Dict:
    cache = load_latest_cache()
    if not cache:
        cache = fetch_and_cache()
    return cache or {}


def _normalize_ticker(ticker: str) -> str:
    """统一去掉市场后缀。"""
    return ticker.replace('.SH', '').replace('.SZ', '').replace('.BJ', '').replace('/US', '').strip()


def get_stock_fund_flow(ticker: str, market: str = 'sh') -> Optional[Dict]:
    """从缓存读取个股资金流向。"""
    cache = _load_cache()
    code = _normalize_ticker(ticker)
    for item in cache.get('individual', []):
        if str(item.get('股票代码', '')) == code:
            return item
    return None


def get_sector_fund_flow_rank(top_n: int = 10) -> List[Dict]:
    """返回行业资金净流入排行。"""
    cache = _load_cache()
    return sorted(cache.get('industry', []), key=lambda x: x.get('净额_亿元', 0), reverse=True)[:top_n]


def compute_stock_score(ff: Optional[Dict]) -> float:
    """基于个股资金流向打分（0-100）。"""
    if not ff:
        return 50.0
    net = ff.get('净额_万元', 0)
    turnover = ff.get('成交额_万元', 1)
    change = ff.get('涨跌幅_pct', 0)
    turnover_rate = ff.get('换手率_pct', 0)

    if turnover <= 0:
        return 50.0

    net_ratio = net / turnover * 100

    score = 50.0
    # 主力净流入占比
    if net_ratio > 5:
        score += min(net_ratio, 15)
    elif net_ratio < -5:
        score -= min(abs(net_ratio), 15)

    # 涨跌幅适度：大涨但主力净流出 = 诱多，扣分
    if change > 5 and net_ratio < -2:
        score -= 10
    elif change < -5 and net_ratio > 2:
        score += 5  # 错杀反弹

    # 换手率：过高（>15%）且净流出 = 出货
    if turnover_rate > 15 and net_ratio < -3:
        score -= 8
    elif 1 < turnover_rate < 15 and net_ratio > 3:
        score += 3

    return max(0, min(100, score))


def get_sector_score(sector_name: str) -> float:
    """根据行业名称从缓存获取资金分数。"""
    cache = _load_cache()
    for item in cache.get('industry', []):
        if item.get('行业') == sector_name or sector_name in item.get('行业', ''):
            net = item.get('净额_亿元', 0)
            # 简单映射：净流入为+5~+15，净流出为-5~-15
            # 行业净额通常 ±100 亿，缩放
            return max(0, min(100, 50 + max(min(net / 3.0, 15), -15)))
    return 50.0


def get_concept_score(concept_name: str) -> float:
    """根据概念名称从缓存获取资金分数。"""
    cache = _load_cache()
    for item in cache.get('concept', []):
        if item.get('行业') == concept_name or concept_name in item.get('行业', ''):
            net = item.get('净额_亿元', 0)
            return max(0, min(100, 50 + max(min(net / 3.0, 15), -15)))
    return 50.0


def analyze(ticker: str, name: str = '', sector: str = '') -> Dict:
    """个股资金流向分析主入口。"""
    ff = get_stock_fund_flow(ticker)
    stock_score = compute_stock_score(ff)
    sector_score = get_sector_score(sector) if sector else 50.0

    # 综合分数：个股 70% + 行业 30%
    combined = round(stock_score * 0.7 + sector_score * 0.3, 1)

    return {
        'ticker': ticker,
        'name': name,
        'score': combined,
        'stock_score': stock_score,
        'sector_score': sector_score,
        'data': ff or {},
        'reasons': [
            f"个股资金分{stock_score:.1f}",
            f"行业资金分{sector_score:.1f}",
        ],
    }


if __name__ == '__main__':
    r = analyze('600028', '中国石化', '油气开采及服务')
    print(r)
