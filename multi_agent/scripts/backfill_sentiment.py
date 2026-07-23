#!/usr/bin/env python3
"""回填涨停/跌停/龙虎榜历史情绪数据到 warehouse。"""
import sys, json, time
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd

PR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PR / "multi_agent"))

from core.warehouse import init_warehouse_db, get_warehouse_conn


def _save_sentiment(records):
    conn = get_warehouse_conn()
    cur = conn.cursor()
    for r in records:
        cur.execute('''INSERT INTO sentiment (date, ticker, metric, value, detail, source, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(date, ticker, metric) DO UPDATE SET value=excluded.value, detail=excluded.detail, source=excluded.source, updated_at=excluded.updated_at''',
            (r['date'], r['ticker'], r['metric'], r['value'], r['detail'], r['source']))
    conn.commit()
    conn.close()


def backfill_zt_pool(start: str, end: str):
    import akshare as ak
    dates = pd.bdate_range(start=start, end=end).strftime('%Y%m%d').tolist()
    records = []
    for d in dates:
        try:
            df = ak.stock_zt_pool_em(date=d)
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                records.append({
                    'date': pd.to_datetime(d).strftime('%Y-%m-%d'),
                    'ticker': str(row['代码']).zfill(6),
                    'metric': 'zt_pool',
                    'value': float(row['涨跌幅']),
                    'detail': json.dumps({'name': row.get('名称'), 'limit_boards': row.get('连板数'), 'amount': row.get('成交额'), 'industry': row.get('所属行业')}, ensure_ascii=False, default=str),
                    'source': 'akshare_zt',
                })
            print(f"  ✅ {d} 涨停 {len(df)} 条")
        except Exception as e:
            print(f"  ⚠️ {d} zt: {e}")
        time.sleep(0.2)
    _save_sentiment(records)
    return len(records)


def backfill_dt_pool(start: str, end: str):
    import akshare as ak
    dates = pd.bdate_range(start=start, end=end).strftime('%Y%m%d').tolist()
    records = []
    for d in dates:
        try:
            df = ak.stock_zt_pool_dtgc_em(date=d)
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                records.append({
                    'date': pd.to_datetime(d).strftime('%Y-%m-%d'),
                    'ticker': str(row['代码']).zfill(6),
                    'metric': 'dt_pool',
                    'value': float(row['涨跌幅']),
                    'detail': json.dumps({'name': row.get('名称'), 'amount': row.get('成交额'), 'industry': row.get('所属行业')}, ensure_ascii=False, default=str),
                    'source': 'akshare_dt',
                })
            print(f"  ✅ {d} 跌停 {len(df)} 条")
        except Exception as e:
            print(f"  ⚠️ {d} dt: {e}")
        time.sleep(0.2)
    _save_sentiment(records)
    return len(records)


def backfill_lhb(start: str, end: str):
    import akshare as ak
    dates = pd.bdate_range(start=start, end=end).strftime('%Y%m%d').tolist()
    records = []
    for d in dates:
        try:
            df = ak.stock_lhb_detail_daily_sina(date=d)
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                records.append({
                    'date': pd.to_datetime(d).strftime('%Y-%m-%d'),
                    'ticker': str(row['股票代码']).zfill(6),
                    'metric': 'lhb',
                    'value': float(row['对应值']) if pd.notna(row['对应值']) else 0,
                    'detail': json.dumps({'name': row.get('股票名称'), 'close': row.get('收盘价'), 'amount': row.get('成交额'), 'indicator': row.get('指标')}, ensure_ascii=False, default=str),
                    'source': 'akshare_lhb',
                })
            print(f"  ✅ {d} 龙虎榜 {len(df)} 条")
        except Exception as e:
            print(f"  ⚠️ {d} lhb: {e}")
        time.sleep(0.2)
    _save_sentiment(records)
    return len(records)


def main():
    init_warehouse_db()
    end = datetime.now().strftime('%Y-%m-%d')
    start = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
    print(f'[backfill sentiment] {start} ~ {end}')
    n1 = backfill_zt_pool(start, end)
    n2 = backfill_dt_pool(start, end)
    n3 = backfill_lhb(start, end)
    print(f'[done] zt={n1}, dt={n2}, lhb={n3}')


if __name__ == '__main__':
    main()
