#!/usr/bin/env python3
"""参数稳定性评估：按时间窗口切片，仅评估有真实 5d 收益的样本。"""
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
OUT = os.path.join(PR, "multi_agent", "data", "parameter_stability.json")


def load_params():
    with open(PARAMS) as f:
        return json.load(f)


def load_bars_by_ticker():
    conn = sqlite3.connect(WH, timeout=10)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT date, ticker, close, category FROM daily_bar ORDER BY ticker, date").fetchall()
    conn.close()
    bars = {}
    for r in rows:
        bars.setdefault(r["ticker"], []).append((r["date"], r["close"], r["category"]))
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
    if max_dates.get("futures"):
        max_dates["期货"] = max_dates["futures"]
    return max_dates


def load_predictions():
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
            "ticker": r["ticker"],
            "cat": r["category"],
            "t": float(t), "f": float(f), "s": float(s),
            "dn": dn, "ff": ff,
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


def evaluate_window(rows, cfg, ret_map):
    evaluable = [r for r in rows if (r["d"], r["ticker"]) in ret_map]
    bull_rets, bear_rets = [], []
    correct = total = 0
    n_bull = n_bear = 0
    for r in evaluable:
        score = compute_weighted(r, cfg)
        sig = classify(score, cfg["threshold"])
        if sig == "neutral":
            continue
        fwd = ret_map[(r["d"], r["ticker"])]
        total += 1
        if sig == "bullish":
            n_bull += 1
            bull_rets.append(fwd)
            if fwd > 0:
                correct += 1
        else:
            n_bear += 1
            bear_rets.append(fwd)
            if fwd < 0:
                correct += 1
    acc = correct / total * 100 if total else 0
    bull_mean = sum(bull_rets) / len(bull_rets) if bull_rets else None
    bear_mean = sum(bear_rets) / len(bear_rets) if bear_rets else None
    return {
        "n": len(rows),
        "n_evaluable": len(evaluable),
        "coverage": total / len(evaluable) if evaluable else 0,
        "n_bull": n_bull,
        "n_bear": n_bear,
        "accuracy": acc,
        "bull_mean": round(bull_mean * 100, 2) if bull_mean is not None else None,
        "bear_mean": round(bear_mean * 100, 2) if bear_mean is not None else None,
    }


def main():
    params = load_params()
    bars_by_ticker = load_bars_by_ticker()
    ret_map = build_returns(bars_by_ticker)
    cat_max = category_max_dates(bars_by_ticker)
    preds = load_predictions()

    # 只保留有真实 5d 收益的预测，避免跨类别延迟污染
    evaluable_preds = [p for p in preds if (p["d"], p["ticker"]) in ret_map]
    all_dates = sorted(set(p["d"] for p in evaluable_preds))
    if len(all_dates) < 3:
        print(f"可评估日期不足: {len(all_dates)} 天，无法评估稳定性")
        return

    by_cat = defaultdict(list)
    for p in evaluable_preds:
        by_cat[p["cat"]].append(p)

    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "pred_date_range": [all_dates[0], all_dates[-1]],
        "n_days": len(all_dates),
        "n_predictions": len(evaluable_preds),
        "warehouse_max_dates": cat_max,
        "categories": {},
    }

    for cat in ["个股", "ETF", "期货", "US"]:
        rs = by_cat.get(cat, [])
        cfg = params.get(cat, params.get("_default", {}))
        if not cfg or len(rs) < 5:
            report["categories"][cat] = {"status": "insufficient_data"}
            continue

        n = len(all_dates)
        windows = []
        if n >= 8:
            windows = ["前半段", all_dates[:n // 2]], ["后半段", all_dates[n // 2:]]
        if n >= 12:
            windows = [
                ["第一段", all_dates[:n // 3]],
                ["第二段", all_dates[n // 3:2 * n // 3]],
                ["第三段", all_dates[2 * n // 3:]],
            ]
        if not windows:
            windows = [["全部", all_dates]]

        window_results = []
        for name, dates in windows:
            subset = [r for r in rs if r["d"] in dates]
            ev = evaluate_window(subset, cfg, ret_map)
            window_results.append({"name": name, "dates": [dates[0], dates[-1]], **ev})

        # 只统计有可评估样本的窗口
        valid_windows = [w for w in window_results if w["n_evaluable"] >= 5]
        accs = [w["accuracy"] for w in valid_windows]
        covs = [w["coverage"] for w in valid_windows]
        mean_acc = sum(accs) / len(accs) if accs else 0
        std_acc = (sum((a - mean_acc) ** 2 for a in accs) / len(accs)) ** 0.5 if accs else 0
        cv_acc = std_acc / mean_acc if mean_acc else 0
        mean_cov = sum(covs) / len(covs) if covs else 0
        std_cov = (sum((c - mean_cov) ** 2 for c in covs) / len(covs)) ** 0.5 if covs else 0
        cv_cov = std_cov / mean_cov if mean_cov else 0

        trainable = len(all_dates) >= 30 and cv_acc < 0.5 and cv_cov < 0.5 and mean_cov >= 0.2
        report["categories"][cat] = {
            "status": "ok",
            "windows": window_results,
            "mean_accuracy": round(mean_acc, 1),
            "accuracy_cv": round(cv_acc, 2),
            "mean_coverage": round(mean_cov * 100, 1),
            "coverage_cv": round(cv_cov, 2),
            "trainable": trainable,
            "note": "数据量足够且跨窗口稳定" if trainable else "数据不足或跨窗口不稳定，暂不训练",
        }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"[parameter_stability] 保存到 {OUT}")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
