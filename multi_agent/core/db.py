#!/usr/bin/env python3
"""统一数据库访问层（DAO）。

当前后端为 SQLite，但所有访问通过本模块，便于后续切换 PostgreSQL/MySQL/Redis 缓存。
原则：
- 保持简单，不引入 ORM。
- 每个函数都显式管理连接。
- 返回 dict 列表或标量，便于上层使用。
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from .db_guardian import ensure_db_healthy

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))

PREDICTIONS_DB = os.path.join(REPO_ROOT, 'multi_agent', 'data', 'llm_predictions.db')
FUTURES_DB = os.path.join(REPO_ROOT, 'multi_agent', 'data', 'futures_simulator.db')


def _connect(path: str, timeout: int = 10) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=timeout)
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================
# 预测数据库
# ============================================================

def init_predictions_db(conn: sqlite3.Connection) -> None:
    """初始化预测数据库表结构。"""
    # WAL 模式提升并发读写性能，避免长时间锁表
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS agentic_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            name TEXT,
            sector TEXT,
            category TEXT,
            signal TEXT NOT NULL,
            confidence REAL,
            weighted_score REAL,
            target_price REAL,
            stop_loss REAL,
            position_pct REAL,
            horizon_1d TEXT,
            horizon_3d TEXT,
            horizon_5d TEXT,
            horizon_10d TEXT,
            horizon_1d_return REAL,
            horizon_3d_return REAL,
            horizon_5d_return REAL,
            horizon_10d_return REAL,
            key_support REAL,
            key_resistance REAL,
            reasoning TEXT,
            bull_points TEXT,
            bear_points TEXT,
            component_scores TEXT,
            backtest_summary TEXT,
            current_price REAL,
            price_date TEXT,
            pred_date TEXT NOT NULL,
            pred_time TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_agentic_pred_date ON agentic_predictions(pred_date);
        CREATE INDEX IF NOT EXISTS idx_agentic_ticker ON agentic_predictions(ticker);
        CREATE INDEX IF NOT EXISTS idx_agentic_category ON agentic_predictions(category);
        CREATE INDEX IF NOT EXISTS idx_agentic_pred_date_category ON agentic_predictions(pred_date, category);
        CREATE INDEX IF NOT EXISTS idx_agentic_ticker_pred_date ON agentic_predictions(ticker, pred_date);
        CREATE INDEX IF NOT EXISTS idx_agentic_pred_date_signal ON agentic_predictions(pred_date, signal);

        CREATE TABLE IF NOT EXISTS unified_validation_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prediction_id INTEGER,
            source_table TEXT NOT NULL,
            ticker TEXT NOT NULL,
            horizon INTEGER NOT NULL,
            pred_signal TEXT,
            actual_price REAL,
            actual_return REAL,
            direction_correct INTEGER,
            confidence REAL,
            validated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS provider_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            ticker TEXT NOT NULL,
            market TEXT,
            start_date TEXT,
            end_date TEXT,
            interval TEXT,
            status TEXT NOT NULL CHECK(status IN ('success','failure','fallback')),
            rows INTEGER,
            latency_ms INTEGER,
            error_msg TEXT,
            run_date TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_provider_runs_ticker ON provider_runs(ticker);
        CREATE INDEX IF NOT EXISTS idx_provider_runs_source ON provider_runs(source);
        CREATE INDEX IF NOT EXISTS idx_provider_runs_run_date ON provider_runs(run_date);

    """)
    # 兼容旧表
    cols = [r[1] for r in conn.execute("PRAGMA table_info(agentic_predictions)")]
    if 'price_date' not in cols:
        conn.execute("ALTER TABLE agentic_predictions ADD COLUMN price_date TEXT")
    conn.commit()



def _ensure_provider_runs_table(conn: sqlite3.Connection) -> None:
    """确保 provider_runs 诊断表存在（兼容老数据库）。"""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS provider_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            ticker TEXT NOT NULL,
            market TEXT,
            start_date TEXT,
            end_date TEXT,
            interval TEXT,
            status TEXT NOT NULL CHECK(status IN ('success','failure','fallback')),
            rows INTEGER,
            latency_ms INTEGER,
            error_msg TEXT,
            run_date TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_provider_runs_ticker ON provider_runs(ticker);
        CREATE INDEX IF NOT EXISTS idx_provider_runs_source ON provider_runs(source);
        CREATE INDEX IF NOT EXISTS idx_provider_runs_run_date ON provider_runs(run_date);
    """)
    conn.commit()


def record_provider_run(
    *,
    source: str,
    ticker: str,
    market: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    interval: Optional[str] = None,
    status: str,
    rows: Optional[int] = None,
    latency_ms: Optional[int] = None,
    error_msg: Optional[str] = None,
) -> None:
    """记录一次数据源加载的运行结果。"""
    conn = _connect(PREDICTIONS_DB)
    try:
        _ensure_provider_runs_table(conn)
        conn.execute(
            """
            INSERT INTO provider_runs
            (source, ticker, market, start_date, end_date, interval, status, rows, latency_ms, error_msg)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (source, ticker, market, start_date, end_date, interval, status, rows, latency_ms, error_msg),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()

def get_provider_runs(
    ticker: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """获取最近的数据源运行记录。"""
    conn = get_predictions_conn()
    try:
        sql = "SELECT * FROM provider_runs WHERE 1=1"
        params: List[Any] = []
        if ticker:
            sql += " AND ticker=?"
            params.append(ticker)
        if source:
            sql += " AND source=?"
            params.append(source)
        sql += " ORDER BY run_date DESC LIMIT ?"
        params.append(limit)
        cur = conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

def get_predictions_conn() -> sqlite3.Connection:
    conn = _connect(PREDICTIONS_DB)
    init_predictions_db(conn)
    return conn


def save_predictions(predictions: List[Dict[str, Any]], pred_date: Optional[str] = None) -> Dict[str, int]:
    """批量保存/覆盖预测记录。同一天整批去重，并保证单日内 ticker 唯一。"""
    conn = get_predictions_conn()
    try:
        stats = {'saved': 0, 'errors': 0}
        today = pred_date or datetime.now().strftime('%Y-%m-%d')
        now = datetime.now().strftime('%H:%M')

        valid = [p for p in predictions if 'error' not in p]
        stats['errors'] = len(predictions) - len(valid)
        if not valid:
            return stats

        # 1. 输入层去重：同一 ticker 只保留第一条（watchlist 防重复）
        seen_tickers = set()
        deduped = []
        for p in valid:
            t = p['ticker']
            if t in seen_tickers:
                print(f"  ⚠️ 跳过重复输入 {t} {p.get('name','')}")
                continue
            seen_tickers.add(t)
            deduped.append(p)
        valid = deduped

        # 2. 按 pred_date + category 删除旧记录，避免多批次按品类跑时互相覆盖
        categories = sorted({p.get('category', '个股') for p in valid})
        placeholders = ','.join('?' * len(categories))
        conn.execute(
            f"DELETE FROM agentic_predictions WHERE pred_date=? AND category IN ({placeholders})",
            (today, *categories)
        )

        # 3. 批量插入
        rows = []
        for p in valid:
            try:
                rows.append((
                    p['ticker'], p['name'], p.get('sector', ''), p.get('category', '个股'),
                    p['signal'], p['confidence'], p['weighted_score'],
                    p['target_price'], p['stop_loss'], p['position_pct'],
                    p['horizon_1d'], p['horizon_3d'], p['horizon_5d'], p['horizon_10d'],
                    _to_float(p['horizon_1d_return']),
                    _to_float(p['horizon_3d_return']),
                    _to_float(p['horizon_5d_return']),
                    _to_float(p['horizon_10d_return']),
                    p['key_support'], p['key_resistance'], p['reasoning'],
                    json.dumps(p['bull_points'], ensure_ascii=False) if isinstance(p['bull_points'], (list, dict)) else p['bull_points'],
                    json.dumps(p['bear_points'], ensure_ascii=False) if isinstance(p['bear_points'], (list, dict)) else p['bear_points'],
                    json.dumps(p['component_scores'], ensure_ascii=False) if isinstance(p['component_scores'], (list, dict)) else p['component_scores'],
                    json.dumps(p['backtest_summary'], ensure_ascii=False) if isinstance(p['backtest_summary'], (list, dict)) else p['backtest_summary'],
                    p['current_price'], p.get('price_date', ''), today, now
                ))
            except Exception as e:
                print(f"准备预测失败 {p.get('ticker')}: {e}")
                stats['errors'] += 1

        if rows:
            conn.executemany("""
                INSERT INTO agentic_predictions
                (ticker, name, sector, category, signal, confidence, weighted_score,
                 target_price, stop_loss, position_pct,
                 horizon_1d, horizon_3d, horizon_5d, horizon_10d,
                 horizon_1d_return, horizon_3d_return, horizon_5d_return, horizon_10d_return,
                 key_support, key_resistance, reasoning,
                 bull_points, bear_points, component_scores, backtest_summary,
                 current_price, price_date, pred_date, pred_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)
            stats['saved'] = len(rows)
        conn.commit()
        # 写入成功后立即快照，防止数据库损坏丢失历史
        try:
            ensure_db_healthy(PREDICTIONS_DB)
        except Exception as e:
            print(f"  ⚠️ db_guardian snapshot failed: {e}")
        return stats
    finally:
        conn.close()


def _to_float(v):
    """将可能为字符串/数字的 horizon return 统一转为 float。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


# 保持向后兼容的导入
init_predictions_db(get_predictions_conn())


def get_latest_predictions(pred_date: Optional[str] = None) -> List[Dict[str, Any]]:
    """获取最新预测记录。pred_date 缺省时使用"最新完整日期"（覆盖 >=3 个主要类别）。"""
    conn = get_predictions_conn()
    try:
        if pred_date is None:
            pred_date = get_predictions_stats()['latest_pred_date']
        if not pred_date:
            return []
        cur = conn.execute("""
            SELECT ticker, name, sector, category, signal, confidence,
                   horizon_1d, horizon_3d, horizon_5d, horizon_10d,
                   current_price, price_date, target_price, stop_loss, position_pct,
                   weighted_score, reasoning, component_scores, backtest_summary
            FROM agentic_predictions
            WHERE pred_date=?
            ORDER BY weighted_score DESC
        """, (pred_date,))
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_price_date_map(pred_date: Optional[str] = None) -> Dict[str, str]:
    """返回 ticker -> price_date 映射。pred_date 缺省时使用最新完整日期。"""
    conn = get_predictions_conn()
    try:
        if pred_date is None:
            pred_date = get_predictions_stats()['latest_pred_date']
        if not pred_date:
            return {}
        cur = conn.execute(
            "SELECT ticker, price_date FROM agentic_predictions WHERE pred_date=?",
            (pred_date,)
        )
        return {r['ticker']: r['price_date'] for r in cur.fetchall()}
    finally:
        conn.close()


def get_predictions_stats() -> Dict[str, Any]:
    """返回预测统计信息。

    latest_pred_date 返回"最新完整日期"：优先取包含主要类别（个股/ETF/期货/US）中
    至少 3 个类别的最近日期；若最新日期只有单一类别（如仅 US 部分完成），
    回退到上一个完整日期，避免页面显示不完整的半成品批次。
    """
    conn = get_predictions_conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM agentic_predictions").fetchone()[0]
        row = conn.execute("SELECT MAX(pred_date) FROM agentic_predictions").fetchone()
        latest = row[0] if row else None

        # 找最新"完整"日期（覆盖 >=3 个主要类别）
        main_cats = ('个股', 'ETF', '期货', 'US')
        display = latest
        if latest:
            dates = conn.execute(
                "SELECT pred_date FROM agentic_predictions GROUP BY pred_date ORDER BY pred_date DESC LIMIT 15"
            ).fetchall()
            for (d,) in dates:
                covered = conn.execute(
                    "SELECT COUNT(DISTINCT category) FROM agentic_predictions WHERE pred_date=? AND category IN (?,?,?,?)",
                    (d, *main_cats)
                ).fetchone()[0]
                if covered >= 3:
                    display = d
                    break

        today_count = 0
        if display:
            today_count = conn.execute(
                "SELECT COUNT(*) FROM agentic_predictions WHERE pred_date=?", (display,)
            ).fetchone()[0]
        return {'total': total, 'latest_pred_date': display, 'today_count': today_count}
    finally:
        conn.close()


# ============================================================
# 期货模拟盘数据库
# ============================================================

def init_futures_db(conn: sqlite3.Connection) -> None:
    """初始化期货模拟盘数据库表结构。"""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contract TEXT NOT NULL,
            direction TEXT NOT NULL CHECK(direction IN ('long','short')),
            lots INTEGER NOT NULL DEFAULT 0,
            entry_price REAL NOT NULL,
            current_price REAL NOT NULL,
            margin_used REAL NOT NULL,
            pnl_total REAL DEFAULT 0,
            open_date TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            note TEXT
        );

        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL,
            contract TEXT NOT NULL,
            direction TEXT NOT NULL,
            action TEXT NOT NULL CHECK(action IN ('open_long','open_short','close_long','close_short')),
            lots INTEGER NOT NULL,
            price REAL NOT NULL,
            margin REAL DEFAULT 0,
            pnl REAL DEFAULT 0,
            total_value REAL,
            reason TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT NOT NULL,
            total_asset REAL NOT NULL,
            cash REAL NOT NULL,
            margin_used REAL NOT NULL,
            floating_pnl REAL NOT NULL,
            daily_return REAL,
            cumulative_return REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()


def get_futures_conn() -> sqlite3.Connection:
    conn = _connect(FUTURES_DB, timeout=10)
    init_futures_db(conn)
    return conn


def get_futures_positions(active_only: bool = True) -> List[Dict[str, Any]]:
    """获取期货持仓列表。"""
    conn = get_futures_conn()
    try:
        sql = "SELECT * FROM positions"
        if active_only:
            sql += " WHERE is_active=1"
        cur = conn.execute(sql)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_futures_config(key: str, default: Optional[str] = None) -> Optional[str]:
    conn = get_futures_conn()
    try:
        row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        return row['value'] if row else default
    finally:
        conn.close()


def set_futures_config(key: str, value: str) -> None:
    conn = get_futures_conn()
    try:
        conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
        # 期货 DB 写入后也触发健康快照
        try:
            ensure_db_healthy(FUTURES_DB)
        except Exception as e:
            print(f"  ⚠️ futures db_guardian snapshot failed: {e}")
    finally:
        conn.close()


if __name__ == '__main__':
    # 简单自检
    print('predictions stats:', get_predictions_stats())
    print('futures positions:', get_futures_positions())
