#!/usr/bin/env python3
"""历史数据仓库 (Data Warehouse)

统一存储和管理：
- daily_bar: 日线行情 (open/high/low/close/volume/turnover)
- fund_flow: 资金流向 (个股/行业/概念日频净流入)
- sentiment: 情绪因子 (涨停/跌停/龙虎榜/新闻情绪)
- macro: 宏观因子 (指数/市场广度/利率/PMI等)
- features: 每日因子快照 (合并到特征宽表，供预测和训练使用)
"""
from __future__ import annotations

import os
import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any

PR = Path(__file__).resolve().parent.parent.parent
DB_PATH = PR / "multi_agent" / "data" / "warehouse.db"


def get_warehouse_conn(path: Optional[str] = None) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path or DB_PATH), exist_ok=True)
    conn = sqlite3.connect(path or DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_warehouse_db(conn: Optional[sqlite3.Connection] = None) -> sqlite3.Connection:
    conn = conn or get_warehouse_conn()
    cur = conn.cursor()

    cur.executescript("""
    CREATE TABLE IF NOT EXISTS daily_bar (
        date TEXT NOT NULL,
        ticker TEXT NOT NULL,
        category TEXT,
        open REAL,
        high REAL,
        low REAL,
        close REAL,
        volume REAL,
        turnover REAL,
        adj_close REAL,
        source TEXT,
        updated_at TEXT,
        PRIMARY KEY (date, ticker)
    );

    CREATE INDEX IF NOT EXISTS idx_daily_bar_ticker_date ON daily_bar(ticker, date);
    CREATE INDEX IF NOT EXISTS idx_daily_bar_date ON daily_bar(date);

    CREATE TABLE IF NOT EXISTS fund_flow (
        date TEXT NOT NULL,
        code TEXT NOT NULL,
        name TEXT,
        category TEXT,
        net_inflow REAL,
        net_ratio REAL,
        main_inflow REAL,
        main_ratio REAL,
        retail_inflow REAL,
        retail_ratio REAL,
        source TEXT,
        updated_at TEXT,
        PRIMARY KEY (date, code, category)
    );

    CREATE INDEX IF NOT EXISTS idx_fund_flow_date ON fund_flow(date);
    CREATE INDEX IF NOT EXISTS idx_fund_flow_code ON fund_flow(code);

    CREATE TABLE IF NOT EXISTS sentiment (
        date TEXT NOT NULL,
        ticker TEXT,
        metric TEXT NOT NULL,
        value REAL,
        detail TEXT,
        source TEXT,
        updated_at TEXT,
        PRIMARY KEY (date, ticker, metric)
    );

    CREATE INDEX IF NOT EXISTS idx_sentiment_date ON sentiment(date);
    CREATE INDEX IF NOT EXISTS idx_sentiment_metric ON sentiment(metric);

    CREATE TABLE IF NOT EXISTS macro (
        date TEXT NOT NULL,
        metric TEXT NOT NULL,
        value REAL,
        detail TEXT,
        source TEXT,
        updated_at TEXT,
        PRIMARY KEY (date, metric)
    );

    CREATE INDEX IF NOT EXISTS idx_macro_date ON macro(date);

    CREATE TABLE IF NOT EXISTS feature_snapshot (
        date TEXT NOT NULL,
        ticker TEXT NOT NULL,
        category TEXT,
        feature_json TEXT,
        signal TEXT,
        confidence REAL,
        score REAL,
        source TEXT,
        updated_at TEXT,
        PRIMARY KEY (date, ticker)
    );

    CREATE INDEX IF NOT EXISTS idx_feature_snapshot_date ON feature_snapshot(date);
    CREATE INDEX IF NOT EXISTS idx_feature_snapshot_ticker ON feature_snapshot(ticker);
    """)
    conn.commit()
    return conn


init_warehouse_db(get_warehouse_conn())


def save_daily_bars(bars: List[Dict[str, Any]]) -> Dict[str, int]:
    """批量保存日线行情。bars: list of dict with date, ticker, open, high, low, close, volume, ..."""
    if not bars:
        return {"saved": 0, "errors": 0}
    conn = get_warehouse_conn()
    cur = conn.cursor()
    stats = {"saved": 0, "errors": 0}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for b in bars:
        try:
            cur.execute("""
                INSERT INTO daily_bar (date, ticker, category, open, high, low, close, volume, turnover, adj_close, source, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date, ticker) DO UPDATE SET
                    open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close,
                    volume=excluded.volume, turnover=excluded.turnover, adj_close=excluded.adj_close,
                    source=excluded.source, updated_at=excluded.updated_at
            """, (
                b.get("date"), b.get("ticker"), b.get("category"),
                b.get("open"), b.get("high"), b.get("low"), b.get("close"),
                b.get("volume"), b.get("turnover"), b.get("adj_close"),
                b.get("source", "akshare"), now
            ))
            stats["saved"] += 1
        except Exception:
            stats["errors"] += 1
    conn.commit()
    conn.close()
    return stats


def load_bar_df(ticker: str, start: str, end: str) -> List[Dict[str, Any]]:
    conn = get_warehouse_conn()
    rows = conn.execute(
        "SELECT * FROM daily_bar WHERE ticker=? AND date >= ? AND date <= ? ORDER BY date",
        (ticker, start, end)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_features(snapshots: List[Dict[str, Any]]) -> Dict[str, int]:
    if not snapshots:
        return {"saved": 0, "errors": 0}
    conn = get_warehouse_conn()
    cur = conn.cursor()
    stats = {"saved": 0, "errors": 0}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for s in snapshots:
        try:
            cur.execute("""
                INSERT INTO feature_snapshot (date, ticker, category, feature_json, signal, confidence, score, source, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date, ticker) DO UPDATE SET
                    category=excluded.category, feature_json=excluded.feature_json,
                    signal=excluded.signal, confidence=excluded.confidence, score=excluded.score,
                    source=excluded.source, updated_at=excluded.updated_at
            """, (
                s["date"], s["ticker"], s.get("category"),
                json.dumps(s.get("features", {}), ensure_ascii=False, default=str),
                s.get("signal"), s.get("confidence"), s.get("score"),
                s.get("source", "engine"), now
            ))
            stats["saved"] += 1
        except Exception:
            stats["errors"] += 1
    conn.commit()
    conn.close()
    return stats


def load_features_as_df(date: str, category: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = get_warehouse_conn()
    if category:
        rows = conn.execute(
            "SELECT * FROM feature_snapshot WHERE date=? AND category=? ORDER BY ticker",
            (date, category)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM feature_snapshot WHERE date=? ORDER BY ticker",
            (date,)
        ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["features"] = json.loads(d["feature_json"] or "{}")
        except Exception:
            d["features"] = {}
        out.append(d)
    return out


if __name__ == "__main__":
    conn = init_warehouse_db()
    print("warehouse tables ready")
    conn.close()
