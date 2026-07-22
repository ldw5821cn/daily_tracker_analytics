#!/usr/bin/env python3
"""每日推荐归档：把 recommendations.json 按日期追加到历史记录。"""
import csv
import json
import os
import sys
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, MULTI_AGENT)

HIST_DIR = os.path.join(MULTI_AGENT, 'data', 'recommendation_history')
REC_PATH = os.path.join(MULTI_AGENT, 'data', 'recommendations.json')

os.makedirs(HIST_DIR, exist_ok=True)


def load_current_recommendations():
    with open(REC_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def archive(rec):
    pred_date = rec.get('pred_date', datetime.now().strftime('%Y-%m-%d'))
    out = os.path.join(HIST_DIR, f'{pred_date}.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    print(f'[track] archived {pred_date} to {out}')

    index_path = os.path.join(HIST_DIR, 'index.json')
    index = []
    if os.path.exists(index_path):
        with open(index_path, 'r', encoding='utf-8') as f:
            index = json.load(f)
    index = [x for x in index if x.get('pred_date') != pred_date]
    index.append({
        'pred_date': pred_date,
        'generated_at': rec.get('generated_at'),
        'longs': len(rec.get('longs', [])),
        'shorts': len(rec.get('shorts_or_avoids', [])),
        'macro_score': rec.get('macro_score'),
        'allow_long': rec.get('allow_long'),
    })
    index = sorted(index, key=lambda x: x['pred_date'])
    with open(index_path, 'w', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    portfolio = []
    for item in rec.get('longs', []):
        portfolio.append({
            'pred_date': pred_date,
            'ticker': item['ticker'],
            'name': item['name'],
            'category': item['category'],
            'signal': item['signal'],
            'price': item['price'],
            'target': item.get('target'),
            'stop': item.get('stop'),
            'position_pct': item.get('position_pct', 0),
        })
    csv_path = os.path.join(HIST_DIR, 'portfolios.csv')
    fieldnames = ['pred_date', 'ticker', 'name', 'category', 'signal', 'price', 'target', 'stop', 'position_pct']
    write_header = not os.path.exists(csv_path)
    with open(csv_path, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerows(portfolio)
    print(f'[track] appended {len(portfolio)} longs to {csv_path}')


def main():
    rec = load_current_recommendations()
    archive(rec)


if __name__ == '__main__':
    main()
