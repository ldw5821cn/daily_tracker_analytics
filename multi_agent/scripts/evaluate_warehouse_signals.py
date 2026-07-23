#!/usr/bin/env python3
"""用 warehouse 真实收益评估当前参数的历史表现（不覆盖参数）。"""
import json
import os
import sqlite3
import sys
from datetime import datetime

PR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(PR, "multi_agent"))

DB = os.path.join(PR, "multi_agent", "data", "llm_predictions.db")
WH = os.path.join(PR, "multi_agent", "data", "warehouse.db")
PARAMS = os.path.join(PR, "multi_agent", "config", "predictor_params.json")


def load_params():
    with open(PARAMS) as f:
        return json.load(f)


def load_returns():
    conn = sqlite3.connect(WH, timeout=10)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT date, ticker, close FROM daily_bar ORDER BY ticker, date").fetchall()
    conn.close()
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
    return ret_map


def load_predictions(cat=None):
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT pred_date, ticker, category, component_scores
        FROM agentic_predictions
        WHERE component_scores IS NOT NULL
    """).fetchall()
    conn.close()

    rs = []
    for r in rows:
        if cat and r["category"] != cat:
            continue
        try:
            sc = json.loads(r["component_scores"])
        except Exception:
            continue
        if not isinstance(sc, dict):
            continue

        t = sc.get("technical", 50)
        if isinstance(t, dict):
            t = 50
        f = sc.get("fundamental_score", sc.get("fundamental", 50))
        if isinstance(f, dict):
            f = f.get("score", 50)
        s = sc.get("sentiment", 50)
        if isinstance(s, dict):
            s = s.get("score", 50)
        dn = float(sc.get("debate_net", 0))
        ff = float(sc.get("fund_flow_override", 0))
        rs.append({
            "d": r["pred_date"],
            "t": float(t),
            "f": float(f),
            "s": float(s),
            "dn": dn,
            "ff": ff,
            "cat": r["category"],
            "ticker": r["ticker"],
        })
    return rs


def compute_weighted(r, cfg):
    w = cfg["weights"]
    base = r["t"] * w["technical"] + r["f"] * w["fundamental"] + r["s"] * w["sentiment"] + (50 + r["dn"] * 8) * w["debate"]
    ff_strength = cfg.get("fund_flow_strength", 0.0)
    return max(0, min(100, base + r["ff"] * ff_strength))


def classify(score, th):
    if score >= th["bull"]:
        return "bullish"
    if score <= th["bear"]:
        return "bearish"
    return "neutral"


def evaluate_category(rs, cfg, ret_map):
    bull_rets, bear_rets = [], []
    correct = total = 0
    for r in rs:
        score = compute_weighted(r, cfg)
        sig = classify(score, cfg["threshold"])
        if sig == "neutral":
            continue
        fwd = ret_map.get((r["d"], r["ticker"]))
        if fwd is None:
            continue
        total += 1
        if sig == "bullish":
            bull_rets.append(fwd)
            if fwd > 0:
                correct += 1
        elif sig == "bearish":
            bear_rets.append(fwd)
            if fwd < 0:
                correct += 1

    bull_mean = sum(bull_rets) / len(bull_rets) if bull_rets else None
    bear_mean = sum(bear_rets) / len(bear_rets) if bear_rets else None
    acc = correct / total * 100 if total else 0
    coverage = (len(bull_rets) + len(bear_rets)) / len(rs) if rs else 0
    return {
        "n": len(rs),
        "n_bull": len(bull_rets),
        "n_bear": len(bear_rets),
        "bullish_mean": bull_mean,
        "bearish_mean": bear_mean,
        "direction_accuracy": acc,
        "coverage": coverage,
    }


def main():
    params = load_params()
    ret_map = load_returns()
    print("=" * 60)
    print("Warehouse 真实 5d 收益回测（当前参数）")
    print("=" * 60)
    print(f"  加载 {len(ret_map)} 条 5d 收益\n")

    for cat in ["个股", "ETF", "期货", "US"]:
        rs = load_predictions(cat)
        cfg = params.get(cat, params.get("_default", {}))
        if not cfg or len(rs) < 5:
            print(f"  {cat}: 样本不足 ({len(rs)})")
            continue
        ev = evaluate_category(rs, cfg, ret_map)
        print(f"  {cat}: n={ev['n']}, bull={ev['n_bull']}, bear={ev['n_bear']}")
        if ev['bullish_mean'] is not None:
            print(f"    bullish 平均 5d 收益: {ev['bullish_mean'] * 100:.2f}%")
        else:
            print("    bullish 无信号")
        if ev['bearish_mean'] is not None:
            print(f"    bearish 平均 5d 收益: {ev['bearish_mean'] * 100:.2f}%")
        else:
            print("    bearish 无信号")
        print(f"    方向准确率: {ev['direction_accuracy']:.1f}%, 覆盖率: {ev['coverage'] * 100:.1f}%")
        print()


if __name__ == "__main__":
    main()
