#!/usr/bin/env python3
"""每日数据健康检查：检查 warehouse 和预测数据库的数据完整性。"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta

PR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(PR, "multi_agent"))

from core.warehouse import get_warehouse_conn

OUT = os.path.join(PR, "multi_agent", "data", "data_health_check.json")
DB = os.path.join(PR, "multi_agent", "data", "llm_predictions.db")


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _days_ago(n):
    return (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d")


def check_warehouse():
    conn = get_warehouse_conn()
    checks = {}
    try:
        for t in ["daily_bar", "feature_snapshot", "sentiment", "macro", "fund_flow"]:
            r = conn.execute(f"SELECT COUNT(*), MIN(date), MAX(date) FROM {t}").fetchone()
            checks[t] = {
                "count": r[0],
                "min_date": r[1],
                "max_date": r[2],
                "up_to_date": r[2] == _today() if r[2] else False,
                "lag_days": (_days_to_today(r[2]) if r[2] else None),
            }
        # 按 category 检查 daily_bar 最新日期
        for cat in ["个股", "ETF", "futures", "US", "index"]:
            r = conn.execute("SELECT MAX(date), COUNT(*) FROM daily_bar WHERE category=?", (cat,)).fetchone()
            checks.setdefault("daily_bar_by_category", {})[cat] = {
                "max_date": r[0],
                "count": r[1],
                "lag_days": _days_to_today(r[0]) if r[0] else None,
            }
    finally:
        conn.close()
    return checks


def _days_to_today(d):
    if not d:
        return None
    try:
        return (datetime.now() - datetime.strptime(str(d)[:10], "%Y-%m-%d")).days
    except Exception:
        return None


def check_predictions():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    checks = {}
    try:
        r = conn.execute("SELECT MAX(pred_date), COUNT(*) FROM agentic_predictions WHERE component_scores IS NOT NULL").fetchone()
        checks["latest_pred_date"] = r[0]
        checks["total_predictions"] = r[1]
        checks["pred_lag_days"] = _days_to_today(r[0])
        for cat in ["个股", "ETF", "期货", "US"]:
            r = conn.execute(
                "SELECT COUNT(*) FROM agentic_predictions WHERE category=? AND pred_date=(SELECT MAX(pred_date) FROM agentic_predictions)",
                (cat,)
            ).fetchone()
            checks.setdefault("latest_by_category", {})[cat] = r[0]
    finally:
        conn.close()
    return checks


def evaluate(checks):
    issues = []
    today = _today()

    # A 股/期货交易日：若今天是周日，允许回退到上周五（滞后 2 天）
    def _latest_trading_day(lag_allowed=0):
        dt = datetime.now()
        for _ in range(7):
            if dt.weekday() < 5:  # 周一到周五
                if lag_allowed <= 0:
                    return dt.strftime('%Y-%m-%d')
                lag_allowed -= 1
            dt -= timedelta(days=1)
        return today

    a_share_latest = _latest_trading_day(0)  # 最近交易日
    us_latest = _latest_trading_day(1) if datetime.now().weekday() >= 5 else today  # 美股周五交易到周六凌晨

    # warehouse 最新日期检查：A 股表允许周末回退
    for t, cfg in [
        ("daily_bar", 0),
        ("feature_snapshot", 1),  # feature_snapshot 可允许周末多 1 天
        ("sentiment", 0),
        ("macro", 0),
        ("fund_flow", 0),
    ]:
        max_d = checks["warehouse"].get(t, {}).get("max_date")
        lag = _days_to_today(max_d)
        if lag is None:
            issues.append(f"{t} 无数据")
            continue
        # 周末放宽：如果最近交易日是周五，滞后 2 天可接受
        effective_lag = (datetime.now() - datetime.strptime(max_d, '%Y-%m-%d')).days
        weekday_now = datetime.now().weekday()
        allowed = cfg + (2 if weekday_now in (5, 6) else 0)  # 周六日额外允许 2 天
        if effective_lag > allowed:
            issues.append(f"{t} 最新日期 {max_d}，滞后 {effective_lag} 天，预期滞后 <= {allowed} 天")

    # daily_bar category 延迟
    cat_key_map = {"期货": "futures", "US": "US", "个股": "个股", "ETF": "ETF"}
    for cat, cfg in [("期货", 1), ("US", 1), ("个股", 0), ("ETF", 0)]:
        key = cat_key_map[cat]
        max_d = checks["warehouse"].get("daily_bar_by_category", {}).get(key, {}).get("max_date")
        if not max_d:
            issues.append(f"daily_bar.{cat} 无数据")
            continue
        lag = (datetime.now() - datetime.strptime(max_d, '%Y-%m-%d')).days
        allowed = cfg + (2 if datetime.now().weekday() in (5, 6) else 0)
        if lag > allowed:
            issues.append(f"daily_bar.{cat} 最新日期 {max_d}，滞后 {lag} 天")

    # 预测最新日期
    pred_d = checks["predictions"].get("latest_pred_date")
    if pred_d:
        pred_lag = (datetime.now() - datetime.strptime(pred_d, '%Y-%m-%d')).days
        allowed = 0 + (2 if datetime.now().weekday() in (5, 6) else 0)
        if pred_lag > allowed:
            issues.append(f"最新预测日期 {pred_d}，滞后 {pred_lag} 天")

    # 可评估 5d 收益样本
    checks["trainable_status"] = _check_trainable()

    status = "ok" if not issues else "warning"
    return status, issues


def _check_trainable():
    """检查是否满足参数优化训练条件。"""
    wh = get_warehouse_conn()
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    try:
        rows = wh.execute("SELECT date, ticker, close FROM daily_bar ORDER BY ticker, date").fetchall()
        bars = {}
        for r in rows:
            bars.setdefault(r["ticker"], []).append((r["date"], r["close"]))
        ret_map = {}
        for tk, seq in bars.items():
            dates = [s[0] for s in seq]
            closes = [s[1] for s in seq]
            for i, d in enumerate(dates):
                if i + 5 < len(dates) and closes[i] and closes[i + 5]:
                    ret_map[(d, tk)] = (closes[i + 5] - closes[i]) / closes[i]

        cur = db.execute("SELECT pred_date, ticker, category FROM agentic_predictions WHERE component_scores IS NOT NULL")
        from collections import defaultdict
        by_cat = defaultdict(set)
        for r in cur.fetchall():
            if (r["pred_date"], r["ticker"]) in ret_map:
                by_cat[r["category"]].add(r["pred_date"])

        status = {}
        for cat in ["个股", "ETF", "期货", "US"]:
            n_days = len(by_cat.get(cat, set()))
            status[cat] = {
                "evaluable_days": n_days,
                "trainable": n_days >= 20,
                "note": "可训练" if n_days >= 20 else f"需继续积累 {20 - n_days} 天",
            }
        return status
    finally:
        wh.close()
        db.close()


def main():
    checks = {
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "today": _today(),
        "warehouse": check_warehouse(),
        "predictions": check_predictions(),
    }
    status, issues = evaluate(checks)
    checks["status"] = status
    checks["issues"] = issues

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(checks, f, ensure_ascii=False, indent=2)
    print(f"[data_health] status={status} saved to {OUT}")
    if issues:
        print("Issues:")
        for i in issues:
            print(f"  - {i}")
    print(json.dumps(checks, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
