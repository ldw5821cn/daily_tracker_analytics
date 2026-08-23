#!/usr/bin/env python3
"""把产业链挖掘 Top 标的自动同步到 watchlist。

Usage:
  python3 multi_agent/scripts/supply_chain_watchlist_sync.py \
      --theme "人形机器人" --top-n 3 --score-threshold 25
"""
import argparse
import json
import os
import sys
from typing import List

ROOT = '/home/liudawei/github/daily_tracker_analytics'
sys.path.insert(0, ROOT)
sys.path.insert(0, f'{ROOT}/multi_agent')

from core.watchlist import add_stock, load_list


def load_supply_chain_top(theme: str, top_n: int = 5, score_threshold: float = 0.0):
    path = f"{ROOT}/multi_agent/data/supply_chain_{theme}.json"
    if not os.path.exists(path):
        return []
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    candidates = data.get('top_candidates', [])
    # 过滤掉无效 / 分数不足 / 非 A 股的候选
    valid = []
    for c in candidates:
        ticker = str(c.get('ticker', '')).strip()
        if not ticker.isdigit() or len(ticker) != 6:
            continue
        if c.get('final_score', 0) < score_threshold:
            continue
        valid.append(c)
    # 按 final_score 降序
    valid.sort(key=lambda x: x.get('final_score', 0), reverse=True)
    return valid[:top_n]


def sync_theme_to_watchlist(theme: str, top_n: int = 3, score_threshold: float = 25.0, dry_run: bool = False):
    added = []
    skipped = []
    existing = {s['ticker'] for s in load_list()}
    for c in load_supply_chain_top(theme, top_n, score_threshold):
        ticker = str(c.get('ticker', '')).zfill(6)
        name = c.get('company') or c.get('name', '')
        if not ticker or not name:
            skipped.append(('missing', c))
            continue
        if ticker in existing:
            skipped.append((ticker, 'already in watchlist'))
            continue
        if not dry_run:
            add_stock(
                ticker=ticker,
                name=name,
                category='个股',
                theme=theme,
                sector=c.get('segment', ''),
            )
        added.append({'ticker': ticker, 'name': name, 'theme': theme, 'score': c.get('final_score', 0)})
        existing.add(ticker)
    return added, skipped


def main():
    parser = argparse.ArgumentParser(description='产业链挖掘 Top 标的同步到 watchlist')
    parser.add_argument('--theme', required=True, help='产业链主题')
    parser.add_argument('--top-n', type=int, default=3, help='每个主题同步前 N 个')
    parser.add_argument('--score-threshold', type=float, default=25.0, help='最低 Serenity 瓶颈评分')
    parser.add_argument('--dry-run', action='store_true', help='只打印不写入')
    args = parser.parse_args()

    added, skipped = sync_theme_to_watchlist(
        args.theme, args.top_n, args.score_threshold, args.dry_run
    )

    print(f"主题：{args.theme}")
    print(f"  新增 {len(added)} 只到 watchlist")
    for a in added:
        print(f"    {a['ticker']} {a['name']} 评分 {a['score']}")
    if skipped:
        print(f"  跳过 {len(skipped)} 只")
        for s in skipped[:5]:
            print(f"    {s}")


if __name__ == '__main__':
    main()
