#!/usr/bin/env python3
"""回填期货和美股历史日线到 warehouse。"""
import sys, json
from datetime import datetime
from pathlib import Path

PR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PR / "multi_agent"))

import pandas as pd
import akshare as ak
from core.warehouse import init_warehouse_db, save_daily_bars


def backfill_futures(tickers, start='2025-07-01', end=None):
    end = end or datetime.now().strftime('%Y%m%d')
    bars = []
    for sym in tickers:
        try:
            df = ak.futures_zh_daily_sina(symbol=sym)
            if df is None or len(df) == 0:
                continue
            df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
            sdate = f'{start[:4]}-{start[4:6]}-{start[6:]}'
            edate = f'{end[:4]}-{end[4:6]}-{end[6:]}'
            df = df[(df['date'] >= sdate) & (df['date'] <= edate)]
            for _, r in df.iterrows():
                bars.append({
                    'date': r['date'], 'ticker': sym, 'category': 'futures',
                    'open': r['open'], 'high': r['high'], 'low': r['low'],
                    'close': r['close'], 'volume': r['volume'], 'turnover': None,
                    'source': 'akshare_futures_sina',
                })
            print(f'  {sym}: {len(df)} bars')
        except Exception as e:
            print(f'  ⚠️ {sym}: {e}')
    return bars


def backfill_us(tickers, start='2025-07-01', end=None):
    end = end or datetime.now().strftime('%Y-%m-%d')
    bars = []
    for sym in tickers:
        try:
            df = ak.stock_us_daily(symbol=sym, adjust='')
            if df is None or len(df) == 0:
                continue
            df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
            df = df[(df['date'] >= start) & (df['date'] <= end)]
            for _, r in df.iterrows():
                bars.append({
                    'date': r['date'], 'ticker': sym, 'category': 'us',
                    'open': r['open'], 'high': r['high'], 'low': r['low'],
                    'close': r['close'], 'volume': r['volume'], 'turnover': None,
                    'source': 'akshare_us_daily',
                })
            print(f'  {sym}: {len(df)} bars')
        except Exception as e:
            print(f'  ⚠️ {sym}: {e}')
    return bars


def main():
    init_warehouse_db()

    with open(PR / 'multi_agent' / 'watchlist.json', 'r', encoding='utf-8') as f:
        watchlist = json.load(f)

    futures = [w['ticker'] for w in watchlist if w.get('category') == '期货']
    us = [w['ticker'] for w in watchlist if w.get('category') == 'US']

    print(f'[futures] {len(futures)} tickers')
    fb = backfill_futures(futures, start='2025-07-01')
    print(f'[us] {len(us)} tickers')
    ub = backfill_us(us, start='2025-07-01')

    all_bars = fb + ub
    stats = save_daily_bars(all_bars)
    print(f'[warehouse] 保存 {stats["saved"]} 条，失败 {stats["errors"]} 条')


if __name__ == '__main__':
    main()
