#!/usr/bin/env python3
"""批量生成社交媒体/全网搜索舆情数据。

读取 watchlist.json 或 agentic_predictions 表，为每个标的调用 social_search_engine，
结果保存到 multi_agent/data/social_sentiment.json，供日报和页面使用。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, MULTI_AGENT)

from core.social_search_engine import get_social_sentiment

WATCHLIST_PATH = os.path.join(MULTI_AGENT, 'data', 'watchlist.json')
OUTPUT_PATH = os.path.join(MULTI_AGENT, 'data', 'social_sentiment.json')
DB_PATH = os.path.join(MULTI_AGENT, 'data', 'llm_predictions.db')


def _load_watchlist() -> list:
    if os.path.exists(WATCHLIST_PATH):
        with open(WATCHLIST_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []


def _load_predictions_latest() -> list:
    if not os.path.exists(DB_PATH):
        return []
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    latest = conn.execute("SELECT MAX(pred_date) FROM agentic_predictions").fetchone()[0]
    rows = conn.execute(
        "SELECT ticker, name, category FROM agentic_predictions WHERE pred_date=?",
        (latest,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _analyze_one(t: dict) -> dict:
    try:
        extra = 'ETF' if t.get('category') == 'ETF' else '股票 A股'
        return get_social_sentiment(t['ticker'], t.get('name', ''), query_extra=extra)
    except Exception as e:
        return {
            'ticker': t.get('ticker', ''),
            'name': t.get('name', ''),
            'error': str(e),
            'sentiment_score': 0.0,
            'total_count': 0,
        }


def main():
    tickers = _load_watchlist()
    if not tickers:
        tickers = _load_predictions_latest()
    if not tickers:
        print('❌ 无标的可分析')
        sys.exit(1)

    print(f'[social] 批量分析 {len(tickers)} 只标的，并发 8...')
    results = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_analyze_one, t): t for t in tickers}
        for i, fut in enumerate(as_completed(futures), 1):
            t = futures[fut]
            try:
                r = fut.result(timeout=30)
            except Exception as e:
                r = {'ticker': t.get('ticker', ''), 'name': t.get('name', ''), 'error': str(e), 'sentiment_score': 0.0, 'total_count': 0}
            results.append(r)
            if i % 10 == 0 or i == len(tickers):
                print(f'  完成 {i}/{len(tickers)}')

    out = {
        'date': __import__('datetime').datetime.now().strftime('%Y-%m-%d'),
        'items': results,
    }
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    total = len(results)
    with_data = sum(1 for i in results if i.get('total_count', 0) > 0)
    avg_score = sum(i.get('sentiment_score', 0) for i in results) / max(total, 1)
    print(f'[social] 完成，结果保存到 {OUTPUT_PATH}')
    print(f'[social] 有数据 {with_data}/{total}，平均情绪 {avg_score:+.2f}')


if __name__ == '__main__':
    main()
