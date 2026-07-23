#!/usr/bin/env python3
"""每日资金流采集：读取 watchlist，拉取个股/ETF 资金流，保存到 warehouse.fund_flow。"""
import sys, json
from datetime import datetime
from pathlib import Path

PR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PR / "multi_agent"))

from core.warehouse import init_warehouse_db, get_warehouse_conn
from analysts.fund_flow_analyst import analyze as analyze_stock, analyze_etf


def save_fund_flow(records):
    conn = get_warehouse_conn()
    cur = conn.cursor()
    today = datetime.now().strftime('%Y-%m-%d')
    for r in records:
        cur.execute('''INSERT INTO fund_flow (date, code, name, category, net_inflow, net_ratio, main_inflow, main_ratio, retail_inflow, retail_ratio, source, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(date, code, category) DO UPDATE SET
                net_inflow=excluded.net_inflow, net_ratio=excluded.net_ratio, source=excluded.source, updated_at=excluded.updated_at''',
            (today, r['code'], r['name'], r['category'], r.get('net_inflow'), r.get('net_ratio'), None, None, None, None, r.get('source', 'fund_flow_analyst')))
    conn.commit()
    conn.close()


def main():
    init_warehouse_db()
    with open(PR / 'multi_agent' / 'watchlist.json', 'r', encoding='utf-8') as f:
        watchlist = json.load(f)

    records = []
    for item in watchlist:
        cat = item.get('category')
        try:
            if cat == '个股':
                r = analyze_stock(item['ticker'], item['name'], item.get('sector', item.get('theme', '')))
                data = r.get('data', {})
                records.append({
                    'code': item['ticker'],
                    'name': item['name'],
                    'category': '个股',
                    'net_inflow': float(data.get('净额_万元', 0)) * 10000 if data.get('净额_万元') else None,  # 元
                    'net_ratio': float(data.get('净额_万元', 0)) / float(data.get('成交额_万元', 1)) * 100 if data.get('成交额_万元') else None,
                })
            elif cat == 'ETF':
                r = analyze_etf(item['ticker'], item['name'], item.get('sector', item.get('theme', '')))
                records.append({
                    'code': item['ticker'],
                    'name': item['name'],
                    'category': 'ETF',
                    'net_inflow': None,
                    'net_ratio': r.get('score', 50),
                })
        except Exception as e:
            print(f"  ⚠️ {item['ticker']} {item['name']}: {e}")

    save_fund_flow(records)
    print(f'[fund_flow] 保存 {len(records)} 条，日期 {datetime.now().strftime("%Y-%m-%d")}')


if __name__ == '__main__':
    main()
