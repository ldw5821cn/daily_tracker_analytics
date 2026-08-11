#!/usr/bin/env python3
"""每日资金流采集：读取 watchlist，拉取个股/ETF 资金流，保存到 warehouse.fund_flow。"""
import sys, json
from datetime import datetime
from pathlib import Path

PR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PR / "multi_agent"))

from core.warehouse import init_warehouse_db, get_warehouse_conn
from analysts.fund_flow_analyst import analyze as analyze_stock, analyze_etf


def save_fund_flow(records, date_str):
    conn = get_warehouse_conn()
    cur = conn.cursor()
    for r in records:
        cur.execute('''INSERT INTO fund_flow (date, code, name, category, net_inflow, net_ratio, main_inflow, main_ratio, retail_inflow, retail_ratio, source, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(date, code, category) DO UPDATE SET
                net_inflow=excluded.net_inflow, net_ratio=excluded.net_ratio, source=excluded.source, updated_at=excluded.updated_at''',
            (date_str, r['code'], r['name'], r['category'], r.get('net_inflow'), r.get('net_ratio'), None, None, None, None, r.get('source', 'fund_flow_analyst')))
    conn.commit()
    conn.close()


def main():
    import argparse
    import os
    from scripts.fetch_fund_flow_cache import load_latest_cache, fetch_and_cache

    parser = argparse.ArgumentParser()
    parser.add_argument('--date', help='数据日期 YYYY-MM-DD（默认取缓存文件中的日期）')
    args = parser.parse_args()

    init_warehouse_db()

    # 从缓存读取（优先），避免重复网络请求；缓存缺失时才实时拉取
    cache = load_latest_cache()
    if not cache:
        cache = fetch_and_cache()
    if not cache:
        print('[fund_flow] 无缓存数据')
        return

    date_str = args.date or cache.get('date') or datetime.now().strftime('%Y-%m-%d')
    ind_map = {str(int(x['股票代码'])).zfill(6): x for x in cache.get('individual', [])}

    with open(PR / 'multi_agent' / 'watchlist.json', 'r', encoding='utf-8') as f:
        watchlist = json.load(f)

    records = []
    for item in watchlist:
        cat = item.get('category')
        try:
            if cat == '个股':
                code = str(item['ticker']).zfill(6)
                d = ind_map.get(code)
                if d:
                    net_wan = float(d.get('净额_万元', 0) or 0)
                    amt_wan = float(d.get('成交额_万元', 0) or 0)
                    records.append({
                        'code': item['ticker'],
                        'name': item['name'],
                        'category': '个股',
                        'net_inflow': net_wan * 10000,  # 元
                        'net_ratio': (net_wan / amt_wan * 100) if amt_wan else None,
                    })
                else:
                    # 缓存无该标的：回退实时分析师
                    r = analyze_stock(item['ticker'], item['name'], item.get('sector', item.get('theme', '')))
                    data = r.get('data', {})
                    records.append({
                        'code': item['ticker'],
                        'name': item['name'],
                        'category': '个股',
                        'net_inflow': float(data.get('净额_万元', 0)) * 10000 if data.get('净额_万元') else None,
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

    save_fund_flow(records, date_str)
    print(f'[fund_flow] 保存 {len(records)} 条，日期 {date_str}')


if __name__ == '__main__':
    main()
