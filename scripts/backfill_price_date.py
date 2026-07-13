#!/usr/bin/env python3
"""回填 agentic_predictions 表中 price_date 为空的记录。"""
import os, sys
import sqlite3

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(REPO_ROOT, 'multi_agent', 'data', 'llm_predictions.db')

sys.path.insert(0, os.path.join(REPO_ROOT, 'multi_agent'))
from core.data_layer import get_stock_data, is_futures

def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT id, ticker, category FROM agentic_predictions WHERE price_date IS NULL OR price_date=''")
    rows = cur.fetchall()
    print(f"待回填记录数: {len(rows)}")
    updated = 0
    for r in rows:
        try:
            df, _ = get_stock_data(r['ticker'], calibrate=False)
            if df is None or len(df) == 0:
                continue
            last_date = str(df.index[-1].date()) if hasattr(df.index[-1], 'date') else str(df.index[-1])
            cur.execute("UPDATE agentic_predictions SET price_date=? WHERE id=?", (last_date, r['id']))
            updated += 1
            if updated % 20 == 0:
                print(f" 已更新 {updated}")
        except Exception as e:
            print(f"  {r['ticker']} 失败: {e}")
    conn.commit()
    conn.close()
    print(f"✅ 回填完成: {updated}/{len(rows)}")

if __name__ == '__main__':
    main()
