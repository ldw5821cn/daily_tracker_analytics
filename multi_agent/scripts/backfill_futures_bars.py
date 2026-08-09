#!/usr/bin/env python3
"""每日期货主连日线回填到 warehouse。

数据源：新浪期货 K 线（core.futures.get_futures_kline_data）
标的选择：core.futures.FUTURES_MAP
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any

PR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PR / "multi_agent"))

from core.futures import FUTURES_MAP, get_futures_kline_data
from core.warehouse import init_warehouse_db, save_daily_bars, get_warehouse_conn


def backfill_futures(start: str, end: str) -> Dict[str, Any]:
    init_warehouse_db()
    total_saved = 0
    total_errors = 0
    latest_dates: Dict[str, str] = {}

    for code, name in FUTURES_MAP:
        try:
            df = get_futures_kline_data(code)
            if df is None or df.empty or len(df) < 20:
                print(f"  ⚠️ {code} {name} 无数据")
                continue

            rows = []
            for date_idx, row in df.iterrows():
                ds = date_idx.strftime('%Y-%m-%d')
                if ds < start or ds > end:
                    continue
                rows.append({
                    'date': ds,
                    'ticker': code,
                    'category': 'futures',
                    'open': float(row['open']) if 'open' in row else None,
                    'high': float(row['high']) if 'high' in row else None,
                    'low': float(row['low']) if 'low' in row else None,
                    'close': float(row['close']) if 'close' in row else None,
                    'volume': float(row['volume']) if 'volume' in row else None,
                    'turnover': None,
                    'adj_close': float(row['close']) if 'close' in row else None,
                    'source': 'sina_futures',
                })
            if not rows:
                print(f"  ⚠️ {code} {name} 在 {start}~{end} 无数据")
                continue

            stats = save_daily_bars(rows)
            total_saved += stats['saved']
            total_errors += stats['errors']
            latest_dates[code] = max(r['date'] for r in rows)
            print(f"  ✅ {code} {name} 保存 {len(rows)} 条，最新 {latest_dates[code]}")
        except Exception as e:
            print(f"  ❌ {code} {name}: {e}")
            total_errors += 1

    return {
        'saved': total_saved,
        'errors': total_errors,
        'latest_dates': latest_dates,
    }


def main():
    start = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    end = datetime.now().strftime('%Y-%m-%d')
    stats = backfill_futures(start, end)
    print(f"\n[done] 保存 {stats['saved']} 条，失败 {stats['errors']} 条")


if __name__ == '__main__':
    main()
