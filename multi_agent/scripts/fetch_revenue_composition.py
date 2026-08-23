#!/usr/bin/env python3
"""抓取 A 股主营构成（按产品分类），写入 fundamentals_cache/<date>_revenue.json。

Usage:
    . etf_tracker/.venv/bin/activate
    python3 multi_agent/scripts/fetch_revenue_composition.py

依赖: akshare stock_zygc_em 接口，需要带交易所前缀（SZ000001 / SH600000）。
"""
import argparse
import json
import os
import sys
from datetime import datetime

import akshare as ak
import pandas as pd

ROOT = '/home/liudawei/github/daily_tracker_analytics'
CACHE_DIR = f'{ROOT}/multi_agent/data/fundamentals_cache'
UNIVERSE_CSV = f'{ROOT}/multi_agent/data/a_share_universe.csv'


def load_universe_codes(limit: int = None) -> list:
    if not os.path.exists(UNIVERSE_CSV):
        return []
    df = pd.read_csv(UNIVERSE_CSV)
    codes = df['code'].astype(str).str.zfill(6).tolist()
    if limit:
        codes = codes[:limit]
    return codes


def with_prefix(code: str) -> str:
    """根据代码规则加 SH/SZ 前缀。"""
    code = str(code).strip().zfill(6)
    if code.startswith('6') or code.startswith('5') or code.startswith('9'):
        return f'SH{code}'
    elif code.startswith('0') or code.startswith('3') or code.startswith('2') or code.startswith('4'):
        return f'SZ{code}'
    return code


def fetch_revenue_composition(symbol: str) -> dict:
    """返回 {产品: 收入比例} 字典。失败返回空。"""
    try:
        df = ak.stock_zygc_em(symbol=symbol)
        if df is None or df.empty:
            return {}
        products = df[df['分类类型'] == '按产品分类']
        if products.empty:
            return {}
        # 取最新报告期
        latest_date = products['报告日期'].max()
        latest = products[products['报告日期'] == latest_date]
        result = {}
        for _, row in latest.iterrows():
            name = row.get('主营构成', '')
            ratio = row.get('收入比例', 0)
            if name and ratio:
                result[name] = float(ratio)
        return result
    except Exception as e:
        return {"_error": str(e)[:200]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, help='仅测试前 N 只')
    parser.add_argument('--batch', type=int, default=0, help='批次号，用于分片拉取')
    parser.add_argument('--batches', type=int, default=1, help='总批次数')
    parser.add_argument('--output', default=f'{CACHE_DIR}/{datetime.now().strftime("%Y-%m-%d")}_revenue.json')
    args = parser.parse_args()

    os.makedirs(CACHE_DIR, exist_ok=True)
    codes = load_universe_codes(args.limit)
    if args.batches > 1:
        codes = codes[args.batch::args.batches]
    data = {}
    errors = 0
    for i, code in enumerate(codes, 1):
        symbol = with_prefix(code)
        result = fetch_revenue_composition(symbol)
        if isinstance(result, dict) and result and "_error" not in result:
            data[code] = result
        else:
            errors += 1
        if i % 100 == 0:
            print(f'已处理 {i}/{len(codes)}，成功 {len(data)}，失败/空 {errors}')

    output = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "count": len(data),
        "data": data,
    }
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f'已保存 {args.output}，共 {len(data)} 只')


if __name__ == '__main__':
    main()
