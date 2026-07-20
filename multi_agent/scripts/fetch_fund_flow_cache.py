#!/usr/bin/env python3
"""预拉取 akshare 资金流向数据并缓存为 JSON，供 fund_flow_analyst 使用。
避免每次预测时重复拉取 5000+ 条数据，避免东财接口被封。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, MULTI_AGENT)

import akshare as ak
import pandas as pd

CACHE_DIR = os.path.join(MULTI_AGENT, 'data', 'fund_flow_cache')


def _parse_amount(x) -> float:
    """把 '1.23亿' / '456.78万' / '-12.3万' 转成万元浮点数。"""
    if pd.isna(x) or x == '' or x is None:
        return 0.0
    s = str(x).strip().replace(',', '')
    sign = -1 if s.startswith('-') else 1
    s = s.replace('-', '').replace('+', '')
    if s.endswith('亿'):
        return sign * float(s[:-1]) * 10000
    elif s.endswith('万'):
        return sign * float(s[:-1])
    elif s.endswith('%'):
        return sign * float(s[:-1])
    try:
        return sign * float(s)
    except Exception:
        return 0.0


def fetch_and_cache() -> Dict:
    """拉取个股、行业、概念、大单资金流向并缓存。"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    today = datetime.now().strftime('%Y-%m-%d')
    result = {'date': today, 'individual': [], 'industry': [], 'concept': [], 'big_deal': []}

    try:
        df = ak.stock_fund_flow_individual()
        df['净额_万元'] = df['净额'].apply(_parse_amount)
        df['成交额_万元'] = df['成交额'].apply(_parse_amount)
        df['涨跌幅_pct'] = df['涨跌幅'].apply(_parse_amount)
        df['换手率_pct'] = df['换手率'].apply(_parse_amount)
        result['individual'] = df[['股票代码', '股票简称', '最新价', '涨跌幅_pct', '换手率_pct',
                                     '净额_万元', '成交额_万元']].to_dict('records')
    except Exception as e:
        result['individual_error'] = str(e)

    try:
        df = ak.stock_fund_flow_industry()
        df['净额_亿元'] = df['净额'].apply(lambda x: _parse_amount(str(x)))
        df['涨跌幅_pct'] = df['行业-涨跌幅'].apply(_parse_amount)
        result['industry'] = df[['行业', '行业指数', '涨跌幅_pct', '净额_亿元']].to_dict('records')
    except Exception as e:
        result['industry_error'] = str(e)

    try:
        df = ak.stock_fund_flow_concept()
        df['净额_亿元'] = df['净额'].apply(lambda x: _parse_amount(str(x)))
        df['涨跌幅_pct'] = df['行业-涨跌幅'].apply(_parse_amount)
        result['concept'] = df[['行业', '行业指数', '涨跌幅_pct', '净额_亿元']].to_dict('records')
    except Exception as e:
        result['concept_error'] = str(e)

    try:
        df = ak.stock_fund_flow_big_deal()
        # 取前 500 条即可
        result['big_deal'] = df.head(500).to_dict('records')
    except Exception as e:
        result['big_deal_error'] = str(e)

    out = os.path.join(CACHE_DIR, f'{today}.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f'[fund_flow] cached {len(result["individual"])} individual, {len(result["industry"])} industry, {len(result["concept"])} concept, {len(result["big_deal"])} big_deal to {out}')
    return result


def load_latest_cache() -> Optional[Dict]:
    """加载最新缓存。"""
    if not os.path.exists(CACHE_DIR):
        return None
    files = sorted([f for f in os.listdir(CACHE_DIR) if f.endswith('.json')], reverse=True)
    if not files:
        return None
    with open(os.path.join(CACHE_DIR, files[0]), 'r', encoding='utf-8') as f:
        return json.load(f)


if __name__ == '__main__':
    fetch_and_cache()
