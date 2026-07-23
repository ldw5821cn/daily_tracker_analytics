#!/usr/bin/env python3
"""回填指数和宏观历史数据到 warehouse。"""
import sys, json
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd

PR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PR / "multi_agent"))

from core.warehouse import save_daily_bars, init_warehouse_db, save_features, get_warehouse_conn

# A 股主要指数
INDEX_SYMBOLS = {
    'sh000001': ('上证指数', 'index'),
    'sh000300': ('沪深300', 'index'),
    'sh000905': ('中证500', 'index'),
    'sz399006': ('创业板指', 'index'),
    'sh000016': ('上证50', 'index'),
    'sh000688': ('科创50', 'index'),
    'sh000852': ('中证1000', 'index'),
}


def backfill_index_bars():
    import akshare as ak
    bars = []
    for sym, (name, cat) in INDEX_SYMBOLS.items():
        try:
            df = ak.stock_zh_index_daily(symbol=sym)
            if df is None or df.empty:
                continue
            df = df.rename(columns=str.lower)
            df['date'] = pd.to_datetime(df['date'])
            for _, row in df.iterrows():
                bars.append({
                    'date': row['date'].strftime('%Y-%m-%d'),
                    'ticker': sym,
                    'category': cat,
                    'open': float(row['open']) if pd.notna(row['open']) else None,
                    'high': float(row['high']) if pd.notna(row['high']) else None,
                    'low': float(row['low']) if pd.notna(row['low']) else None,
                    'close': float(row['close']) if pd.notna(row['close']) else None,
                    'volume': float(row['volume']) if pd.notna(row['volume']) else None,
                    'turnover': None,
                    'adj_close': float(row['close']) if pd.notna(row['close']) else None,
                    'source': 'akshare_index',
                })
            print(f"  ✅ {name} {sym} 保存 {len(df)} 条")
        except Exception as e:
            print(f"  ❌ {name} {sym}: {e}")
    save_daily_bars(bars)


def backfill_macro_pmi():
    import akshare as ak
    try:
        df = ak.macro_china_pmi_yearly()
        df = df.tail(120)
        records = []
        for _, row in df.iterrows():
            d = row['日期']
            if isinstance(d, str):
                d = pd.to_datetime(d)
            records.append({
                'date': d.strftime('%Y-%m-%d'),
                'metric': 'pmi_official',
                'value': float(row['今值']) if pd.notna(row['今值']) else None,
                'detail': json.dumps({'predict': row.get('预测值'), 'prev': row.get('前值')}, ensure_ascii=False),
                'source': 'akshare',
            })
        conn = get_warehouse_conn()
        cur = conn.cursor()
        for r in records:
            cur.execute('''INSERT INTO macro (date, metric, value, detail, source, updated_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(date, metric) DO UPDATE SET value=excluded.value, detail=excluded.detail, source=excluded.source, updated_at=excluded.updated_at''',
                (r['date'], r['metric'], r['value'], r['detail'], r['source']))
        conn.commit()
        conn.close()
        print(f"  ✅ PMI 保存 {len(records)} 条")
    except Exception as e:
        print(f"  ❌ PMI: {e}")


def backfill_macro_fx():
    import akshare as ak
    try:
        df = ak.fx_spot_quote()
        # 外汇是 snapshot，只能保存当天
        pairs = {'USD/CNY': 'usdcny', 'EUR/CNY': 'eurcny', '100JPY/CNY': 'jpycny'}
        records = []
        today = datetime.now().strftime('%Y-%m-%d')
        for _, row in df.iterrows():
            pair = row['货币对']
            if pair in pairs:
                records.append({
                    'date': today,
                    'metric': pairs[pair],
                    'value': float(row['买报价']) if pd.notna(row['买报价']) else None,
                    'detail': json.dumps({'sell': row.get('卖报价')}, ensure_ascii=False),
                    'source': 'akshare_fx',
                })
        conn = get_warehouse_conn()
        cur = conn.cursor()
        for r in records:
            cur.execute('''INSERT INTO macro (date, metric, value, detail, source, updated_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(date, metric) DO UPDATE SET value=excluded.value, detail=excluded.detail, source=excluded.source, updated_at=excluded.updated_at''',
                (r['date'], r['metric'], r['value'], r['detail'], r['source']))
        conn.commit()
        conn.close()
        print(f"  ✅ FX 保存 {len(records)} 条")
    except Exception as e:
        print(f"  ❌ FX: {e}")


def main():
    init_warehouse_db()
    print('[backfill_index_bars]')
    backfill_index_bars()
    print('\n[backfill_macro_pmi]')
    backfill_macro_pmi()
    print('\n[backfill_macro_fx]')
    backfill_macro_fx()
    print('\n[done]')

if __name__ == '__main__':
    main()
