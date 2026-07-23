#!/usr/bin/env python3
"""基于历史 K 线回算每日特征快照（技术面因子），不调用 LLM，纯计算。"""
from __future__ import annotations

import os
import sys
import json
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any

import pandas as pd

PR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PR / "multi_agent"))

from core.warehouse import get_warehouse_conn, save_features, init_warehouse_db
from core.data_layer import calc_technical_indicators


def _build_tech_features(df: pd.DataFrame, date: str, ticker: str, category: str) -> Dict[str, Any]:
    """为某一天计算技术面特征。"""
    if df is None or df.empty or len(df) < 60:
        return None
    df = df[df.index <= pd.Timestamp(date)].copy()
    if len(df) < 60:
        return None
    df = calc_technical_indicators(df)
    latest = df.iloc[-1]
    cp = float(latest['close'])
    if cp <= 0 or pd.isna(cp):
        return None

    score = 50
    reasons = []
    if pd.notna(latest['ma60']):
        ma60_dist = (cp / float(latest['ma60']) - 1) * 100
        if ma60_dist > 0:
            score += 8
        else:
            score -= 5
    if latest['macd_hist'] > 0: score += 6
    else: score -= 4
    if 30 < latest['rsi_14'] < 70: score += 3
    elif latest['rsi_14'] < 30: score += 4
    else: score -= 3
    vol = float(latest['annual_vol_20d']) if pd.notna(latest['annual_vol_20d']) else 0
    if vol < 30: score += 3
    elif vol > 60: score -= 2
    score = max(0, min(100, score))

    m5 = float(latest['momentum_5d']) if pd.notna(latest['momentum_5d']) else 0
    m20 = float(latest['momentum_20d']) if pd.notna(latest['momentum_20d']) else 0
    avg_daily = (m5 / 5 + m20 / 20) / 2 if m5 or m20 else 0

    return {
        'date': date,
        'ticker': ticker,
        'category': category,
        'features': {
            'close': round(cp, 3),
            'ma5': float(latest['ma5']) if pd.notna(latest['ma5']) else None,
            'ma20': float(latest['ma20']) if pd.notna(latest['ma20']) else None,
            'ma60': float(latest['ma60']) if pd.notna(latest['ma60']) else None,
            'macd_hist': float(latest['macd_hist']) if pd.notna(latest['macd_hist']) else None,
            'rsi_14': float(latest['rsi_14']) if pd.notna(latest['rsi_14']) else None,
            'boll_up': float(latest['boll_up']) if pd.notna(latest['boll_up']) else None,
            'boll_down': float(latest['boll_down']) if pd.notna(latest['boll_down']) else None,
            'momentum_5d': round(m5, 3),
            'momentum_20d': round(m20, 3),
            'avg_daily_return': round(avg_daily, 4),
            'annual_vol_20d': round(vol, 2),
        },
        'score': round(score, 1),
        'signal': 'bullish' if score >= 58 else 'bearish' if score <= 42 else 'neutral',
        'confidence': round(max(0.5, min(0.95, 0.5 + abs(score - 50) / 50 * 0.45)), 2),
        'source': 'tech_snapshot_backfill',
    }


def backfill_features(start: str, end: str, tickers: List[str] = None):
    init_warehouse_db()
    conn = get_warehouse_conn()
    try:
        if not tickers:
            rows = conn.execute(
                "SELECT DISTINCT ticker, category FROM daily_bar WHERE date BETWEEN ? AND ?",
                (start, end)
            ).fetchall()
            tickers = [(r['ticker'], r['category']) for r in rows]
        else:
            tickers = [(t, None) for t in tickers]

        dates = pd.bdate_range(start=start, end=end).strftime('%Y-%m-%d').tolist()
        print(f"[backfill_features] {len(tickers)} 个标的，{len(dates)} 个交易日")

        all_snaps = []
        for ticker, category in tickers:
            rows = conn.execute(
                "SELECT date, open, high, low, close, volume FROM daily_bar WHERE ticker=? ORDER BY date",
                (ticker,)
            ).fetchall()
            if not rows:
                continue
            df = pd.DataFrame(rows, columns=['date', 'open', 'high', 'low', 'close', 'volume'])
            df['date'] = pd.to_datetime(df['date'])
            df.set_index('date', inplace=True)
            df = df.sort_index()
            df = df.apply(pd.to_numeric, errors='coerce').dropna(subset=['close'])

            for d in dates:
                snap = _build_tech_features(df, d, ticker, category or '个股')
                if snap:
                    all_snaps.append(snap)
            print(f"  ✅ {ticker} 生成 {len([s for s in all_snaps if s['ticker'] == ticker])} 条快照")
        if all_snaps:
            stats = save_features(all_snaps)
            print(f"\n[done] 保存 {stats['saved']} 条，失败 {stats['errors']} 条")
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', type=str, default=(datetime.now() - timedelta(days=120)).strftime('%Y-%m-%d'))
    parser.add_argument('--end', type=str, default=datetime.now().strftime('%Y-%m-%d'))
    args = parser.parse_args()
    backfill_features(args.start, args.end)


if __name__ == '__main__':
    main()
