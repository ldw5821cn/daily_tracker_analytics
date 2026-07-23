#!/usr/bin/env python3
"""模型表现日报：用 warehouse 真实 5d 收益评估当前参数，生成 JSON 供页面使用。"""
import json
import os
import sqlite3
import sys
from datetime import datetime
from collections import defaultdict

PR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(PR, "multi_agent"))

DB = os.path.join(PR, "multi_agent", "data", "llm_predictions.db")
WH = os.path.join(PR, "multi_agent", "data", "warehouse.db")
PARAMS = os.path.join(PR, "multi_agent", "config", "predictor_params.json")
OUT = os.path.join(PR, "multi_agent", "data", "model_performance.json")


def load_params():
    with open(PARAMS) as f:
        return json.load(f)


def load_bars_by_ticker():
    conn = sqlite3.connect(WH, timeout=10)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT date, ticker, close, category FROM daily_bar ORDER BY ticker, date").fetchall()
    conn.close()
    bars = defaultdict(list)
    for r in rows:
        bars[r["ticker"]].append((r["date"], r["close"], r["category"]))
    return bars


def build_returns(bars_by_ticker):
    ret_map = {}
    for tk, seq in bars_by_ticker.items():
        dates = [s[0] for s in seq]
        closes = [s[1] for s in seq]
        for i, d in enumerate(dates):
            if i + 5 < len(dates) and closes[i] and closes[i + 5]:
                ret_map[(d, tk)] = (closes[i + 5] - closes[i]) / closes[i]
    return ret_map


def category_max_dates(bars_by_ticker):
    max_dates = {}
    for tk, seq in bars_by_ticker.items():
        cat = seq[0][2] if seq else None
        if cat:
            max_dates[cat] = max(max_dates.get(cat, ""), seq[-1][0])
    # 统一映射到预测表中的中文类别名
    if max_dates.get("futures"):
        max_dates["期货"] = max_dates["futures"]
    return max_dates


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


def evaluate_category(rs, cfg, ret_map, cat_max_date):
    """评估类别，只使用有足够未来收益的预测。"""
    bull_rets, bear_rets = [], []
    correct = total = 0
    skipped_no_fwd = 0

    if not cat_max_date:
        return {"status": "no_warehouse_data"}

    # 只评估 warehouse 已有 5 日后收盘价的预测
    evaluable = [r for r in rs if (r["d"], r["ticker"]) in ret_map]
    for r in evaluable:
        score = compute_weighted(r, cfg)
        sig = classify(score, cfg["threshold"])
        if sig == "neutral":
            continue
        fwd = ret_map[(r["d"], r["ticker"])]
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
    coverage = (len(bull_rets) + len(bear_rets)) / len(evaluable) if evaluable else 0
    return {
        "status": "ok",
        "n": len(rs),
        "n_evaluable": len(evaluable),
        "skipped_no_forward": len(rs) - len(evaluable),
        "n_bull": len(bull_rets),
        "n_bear": len(bear_rets),
        "bullish_mean": round(bull_mean * 100, 2) if bull_mean is not None else None,
        "bearish_mean": round(bear_mean * 100, 2) if bear_mean is not None else None,
        "direction_accuracy": round(acc, 1),
        "coverage": round(coverage * 100, 1),
        "warehouse_max_date": cat_max_date,
    }


def main():
    params = load_params()
    bars_by_ticker = load_bars_by_ticker()
    ret_map = build_returns(bars_by_ticker)
    cat_max = category_max_dates(bars_by_ticker)

    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "pred_date": datetime.now().strftime("%Y-%m-%d"),
        "ret_map_count": len(ret_map),
        "warehouse_max_dates": cat_max,
        "categories": {},
    }

    for cat in ["个股", "ETF", "期货", "US"]:
        rs = load_predictions(cat)
        cfg = params.get(cat, params.get("_default", {}))
        if not cfg or len(rs) < 5:
            report["categories"][cat] = {"status": "insufficient_data", "n": len(rs)}
            continue
        ev = evaluate_category(rs, cfg, ret_map, cat_max.get(cat))
        report["categories"][cat] = ev

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"[model_performance] 保存到 {OUT}")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
