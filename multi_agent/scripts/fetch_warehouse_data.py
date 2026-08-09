#!/usr/bin/env python3
"""回填历史数据仓库：日线行情 + 资金流向 + 情绪 + 宏观。"""
from __future__ import annotations

import os
import sys
import json
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any

PR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PR / "multi_agent"))

from core.warehouse import init_warehouse_db, save_daily_bars, get_warehouse_conn
from core.data_loader_registry import fetch_market_data, _is_a_share, _is_etf
from core.data_layer import is_futures
from core.us_data import get_us_stock_data
import pandas as pd


def load_watchlist(path: Path) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def fetch_a_stock_history(ticker: str, start: str, end: str) -> List[Dict[str, Any]]:
    """拉取 A 股/ETF 日线历史。"""
    try:
        fetched = fetch_market_data([ticker], start, end, use_cache=True)
        df = fetched.get(ticker)
        if df is None or df.empty:
            return []
        df = df.reset_index()
        date_col = "date" if "date" in df.columns else df.columns[0]
        rows = []
        for _, row in df.iterrows():
            d = row[date_col]
            if isinstance(d, pd.Timestamp):
                d = d.strftime("%Y-%m-%d")
            rows.append({
                "date": str(d)[:10],
                "ticker": ticker,
                "open": float(row.get("open", 0)) if pd.notna(row.get("open")) else None,
                "high": float(row.get("high", 0)) if pd.notna(row.get("high")) else None,
                "low": float(row.get("low", 0)) if pd.notna(row.get("low")) else None,
                "close": float(row.get("close", 0)) if pd.notna(row.get("close")) else None,
                "volume": float(row.get("volume", 0)) if pd.notna(row.get("volume")) else None,
                "turnover": float(row.get("turnover", 0)) if pd.notna(row.get("turnover")) else None,
                "adj_close": float(row.get("close", 0)) if pd.notna(row.get("close")) else None,
                "source": "registry",
            })
        return rows
    except Exception as e:
        print(f"  ❌ {ticker}: {e}")
        return []


def fetch_us_history(ticker: str, start: str, end: str) -> List[Dict[str, Any]]:
    """拉取美股日线历史。"""
    try:
        df = get_us_stock_data(ticker, period="max")
        if df is None or df.empty:
            return []
        df = df.reset_index()
        rows = []
        for _, row in df.iterrows():
            d = row[df.columns[0]]
            if isinstance(d, pd.Timestamp):
                d = d.strftime("%Y-%m-%d")
            ds = str(d)[:10]
            if ds < start or ds > end:
                continue
            rows.append({
                "date": ds,
                "ticker": ticker,
                "open": float(row.get("Open", 0)) if pd.notna(row.get("Open")) else None,
                "high": float(row.get("High", 0)) if pd.notna(row.get("High")) else None,
                "low": float(row.get("Low", 0)) if pd.notna(row.get("Low")) else None,
                "close": float(row.get("Close", 0)) if pd.notna(row.get("Close")) else None,
                "volume": float(row.get("Volume", 0)) if pd.notna(row.get("Volume")) else None,
                "turnover": None,
                "adj_close": float(row.get("Adj Close", row.get("Close", 0))) if pd.notna(row.get("Adj Close", row.get("Close"))) else None,
                "source": "yfinance",
            })
        return rows
    except Exception as e:
        print(f"  ❌ {ticker}: {e}")
        return []


def backfill_bars(watchlist: List[Dict[str, Any]], start: str, end: str, workers: int = 4) -> Dict[str, int]:
    total = {"saved": 0, "errors": 0}
    items = [w for w in watchlist if w.get("category") in ("个股", "ETF", "US", "指数")]
    print(f"[backfill_bars] {len(items)} 个标的，{start} ~ {end}")

    def _fetch(item):
        cat = item.get("category", "个股")
        if cat == "US":
            return fetch_us_history(item["ticker"], start, end)
        if cat == "指数":
            # 复用 A 股/ETF 数据拉取路径，data_loader_registry 会识别指数代码
            return fetch_a_stock_history(item["ticker"], start, end)
        return fetch_a_stock_history(item["ticker"], start, end)

    # 串行避免被封；期货跳过
    for item in items:
        rows = _fetch(item)
        if rows:
            for r in rows:
                r["category"] = item.get("category", "个股")
            stats = save_daily_bars(rows)
            total["saved"] += stats["saved"]
            total["errors"] += stats["errors"]
            print(f"  ✅ {item['ticker']} {item['name']} 保存 {len(rows)} 条")
        else:
            print(f"  ⚠️  {item['ticker']} {item['name']} 无数据")
    return total


def show_stats():
    conn = get_warehouse_conn()
    tables = ["daily_bar", "fund_flow", "sentiment", "macro", "feature_snapshot"]
    for t in tables:
        r = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t}: {r} 条")
    dmin, dmax = conn.execute("SELECT MIN(date), MAX(date) FROM daily_bar").fetchone()
    print(f"  daily_bar 日期范围: {dmin} ~ {dmax}")
    conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=str, default=(datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d"))
    parser.add_argument("--end", type=str, default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--watchlist", type=str, default=str(PR / "multi_agent" / "watchlist.json"))
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--stats", action="store_true")
    args = parser.parse_args()

    if args.stats:
        show_stats()
        return

    init_warehouse_db()
    watchlist = load_watchlist(Path(args.watchlist))
    stats = backfill_bars(watchlist, args.start, args.end, args.workers)
    print(f"\n[done] 保存 {stats['saved']} 条，失败 {stats['errors']} 条")
    show_stats()


if __name__ == "__main__":
    main()
