#!/usr/bin/env python3
"""参数稳定性评估：按时间窗口切片，检查当前参数在不同日期段的稳定性。"""
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
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
    bull_rets, bear_rets = [], []
    correct = total = 0
    n_bull = n_bear = 0
    for r in rows:
        score = compute_weighted(r, cfg)
        sig = classify(score, cfg["threshold"])
        if sig == "neutral":
            continue
        fwd = ret_map.get((r["d"], r["ticker"]))
        if fwd is None:
            continue
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
        "coverage": total / len(rows) if rows else 0,
        "n_bull": n_bull,
        "n_bear": n_bear,
        "accuracy": acc,
        "bull_mean": round(bull_mean * 100, 2) if bull_mean is not None else None,
        "bear_mean": round(bear_mean * 100, 2) if bear_mean is not None else None,
    }


def main():
    params = load_params()
    ret_map = load_returns()
    preds = load_predictions()
    all_dates = sorted(set(p["d"] for p in preds))
    if len(all_dates) < 3:
        print(f"预测日期不足: {len(all_dates)} 天，无法评估稳定性")
        return

    # 按类别分组
    by_cat = defaultdict(list)
    for p in preds:
        by_cat[p["cat"]].append(p)

    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "pred_date_range": [all_dates[0], all_dates[-1]],
        "n_days": len(all_dates),
        "n_predictions": len(preds),
        "categories": {},
    }

    for cat in ["个股", "ETF", "期货", "US"]:
        rs = by_cat.get(cat, [])
        cfg = params.get(cat, params.get("_default", {}))
        if not cfg or len(rs) < 5:
            report["categories"][cat] = {"status": "insufficient_data"}
            continue

        # 按日期切分为 2-3 个窗口
        n = len(all_dates)
        windows = []
        if n >= 8:
            windows = [
                ("前半段", all_dates[:n // 2]),
                ("后半段", all_dates[n // 2:]),
            ]
        if n >= 12:
            windows = [
                ("第一段", all_dates[:n // 3]),
                ("第二段", all_dates[n // 3:2 * n // 3]),
                ("第三段", all_dates[2 * n // 3:]),
            ]
        if not windows:
            windows = [("全部", all_dates)]

        window_results = []
        for name, dates in windows:
            subset = [r for r in rs if r["d"] in dates]
            ev = evaluate_window(subset, cfg, ret_map)
            window_results.append({"name": name, "dates": [dates[0], dates[-1]], **ev})

        # 稳定性评分：准确率变异系数、覆盖率变异系数、bull_mean 范围
        accs = [w["accuracy"] for w in window_results if w["n"] >= 5]
        covs = [w["coverage"] for w in window_results if w["n"] >= 5]
        mean_acc = sum(accs) / len(accs) if accs else 0
        std_acc = (sum((a - mean_acc) ** 2 for a in accs) / len(accs)) ** 0.5 if accs else 0
        cv_acc = std_acc / mean_acc if mean_acc else 0
        mean_cov = sum(covs) / len(covs) if covs else 0
        std_cov = (sum((c - mean_cov) ** 2 for c in covs) / len(covs)) ** 0.5 if covs else 0
        cv_cov = std_cov / mean_cov if mean_cov else 0

        # 如果数据足够且稳定性好，才建议训练
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
