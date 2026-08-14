#!/usr/bin/env python3
"""最小 tvkit 接入验证：匿名拉取单只股票日线，并尝试写入 warehouse 格式。"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from datetime import datetime, timezone

from tvkit.api.chart.ohlcv import OHLCV
from tvkit.symbols import normalize_symbol

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(REPO_ROOT, "multi_agent", "data", "warehouse.db")


def _to_warehouse_row(ticker: str, exchange: str, bar) -> dict:
    """将 tvkit OHLCVBar 转为本仓库 daily_bar 字段。"""
    ts = datetime.fromtimestamp(bar.timestamp, tz=timezone.utc)
    return {
        "ticker": ticker.upper(),
        "category": "US",
        "date": ts.strftime("%Y-%m-%d"),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "source": f"tvkit_{exchange.lower()}",
    }


async def fetch_symbol(exchange_symbol: str, bars_count: int = 5) -> list[dict]:
    norm = normalize_symbol(exchange_symbol)
    exchange, ticker = norm.split(":", 1)
    async with OHLCV() as client:
        bars = await client.get_historical_ohlcv(
            exchange_symbol=norm,
            interval="1D",
            bars_count=bars_count,
        )
    print(f"[{exchange_symbol}] 返回 {len(bars)} 条日线")
    return [_to_warehouse_row(ticker, exchange, b) for b in bars]


async def main() -> None:
    rows = await fetch_symbol("NASDAQ:AAPL", bars_count=5)
    print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))

    # 可选：写入 warehouse（dry-run 模式，先不写）
    if os.path.exists(DB_PATH):
        print(f"\n检测到仓库: {DB_PATH}")
        conn = sqlite3.connect(DB_PATH)
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='daily_bar'")
        exists = cur.fetchone() is not None
        conn.close()
        print(f"daily_bar 表存在: {exists}")
    else:
        print(f"\n未找到仓库: {DB_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
