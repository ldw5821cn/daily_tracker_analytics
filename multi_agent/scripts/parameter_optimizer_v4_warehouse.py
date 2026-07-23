#!/usr/bin/env python3
"""参数优化器 V4：以真实 realized forward return 为目标优化权重与阈值。

输入:
- multi_agent/data/llm_predictions.db 中 agentic_predictions 表
- multi_agent/data/prediction_backtest.json 中 5d forward return

输出:
- multi_agent/config/predictor_params.json

优化目标:
- bullish 信号平均 5d forward return 最大化
- bearish 信号平均 5d forward return 最小化（越负越好）
- 信号方向准确率最大化
- 信号覆盖率不能过低（避免过拟合到少量样本）
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime
from itertools import product

PR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(PR, "multi_agent"))

DB = os.path.join(PR, "multi_agent", "data", "llm_predictions.db")
WH = os.path.join(PR, "multi_agent", "data", "warehouse.db")
OUT = os.path.join(PR, "multi_agent", "config", "predictor_params_warehouse_v4.json")

WG = {
    "technical": [0.20, 0.25, 0.30, 0.35, 0.40],
    "fundamental": [0.15, 0.20, 0.25, 0.30],
    "sentiment": [0.05, 0.10, 0.15, 0.20],
    "debate": [0.15, 0.20, 0.25, 0.30, 0.35],
}
TG = {
    "bull": list(range(54, 68, 2)),
    "bear": list(range(32, 46, 2)),
}
FF_STRENGTH = [0.0, 1.0, 2.0, 3.0]

RET_MAP = {}


def cw(t, f, s, dn, ff_override, w, ff_strength):
    base = t * w["technical"] + f * w["fundamental"] + s * w["sentiment"] + (50 + dn * 8) * w["debate"]
    return max(0, min(100, base + ff_override * ff_strength))


def sg(score, b, be):
    if score >= b:
        return "bullish"
    if score <= be:
        return "bearish"
    return "neutral"


def load_backtest_returns_from_warehouse():
    conn = sqlite3.connect(WH, timeout=10)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT date, ticker, close FROM daily_bar ORDER BY ticker, date").fetchall()
    conn.close()
    bars = {}
    for r in rows:
        bars.setdefault(r["ticker"], []).append((r["date"], r["close"]))
    # build next-trading-date index
    for tk, seq in bars.items():
        dates = [s[0] for s in seq]
        closes = [s[1] for s in seq]
        for i, d in enumerate(dates):
            if i + 5 < len(dates):
                if closes[i] and closes[i+5]:
                    RET_MAP[(d, tk)] = (closes[i+5] - closes[i]) / closes[i]
    return RET_MAP


def load_predictions(cat=None, ret_map=None):
    co = sqlite3.connect(DB)
    co.row_factory = sqlite3.Row
    q = """
        SELECT pred_date, ticker, category, component_scores
        FROM agentic_predictions
        WHERE component_scores IS NOT NULL
    """
    rows = co.execute(q).fetchall()
    co.close()

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

        key = (r["pred_date"], r["ticker"])
        fwd_ret = ret_map.get(key)
        if fwd_ret is None:
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
        ff_override = float(sc.get("fund_flow_override", 0))

        rs.append({
            "d": r["pred_date"],
            "t": float(t),
            "f": float(f),
            "s": float(s),
            "dn": dn,
            "ff": ff_override,
            "r": float(fwd_ret),
        })
    return rs


def evaluate(rs, w, b, be, ff_strength):
    bull_rets, bear_rets = [], []
    n_correct = n_total = 0

    for r in rs:
        score = cw(r["t"], r["f"], r["s"], r["dn"], r["ff"], w, ff_strength)
        sig = sg(score, b, be)
        if sig == "neutral":
            continue
        ret = r["r"]
        if sig == "bullish":
            bull_rets.append(ret)
            n_total += 1
            if ret > 0:
                n_correct += 1
        elif sig == "bearish":
            bear_rets.append(ret)
            n_total += 1
            if ret < 0:
                n_correct += 1

    bull_mean = sum(bull_rets) / len(bull_rets) if bull_rets else -1.0
    bear_mean = sum(bear_rets) / len(bear_rets) if bear_rets else 1.0
    direction_accuracy = n_correct / n_total * 100 if n_total else 0
    coverage = (len(bull_rets) + len(bear_rets)) / len(rs) if rs else 0

    return {
        "bullish_mean": bull_mean,
        "bearish_mean": bear_mean,
        "direction_accuracy": direction_accuracy,
        "coverage": coverage,
        "n_bull": len(bull_rets),
        "n_bear": len(bear_rets),
    }


def score(ev):
    spread = ev["bullish_mean"] - ev["bearish_mean"]
    acc = ev["direction_accuracy"]
    cov = ev["coverage"] * 100
    return 0.45 * spread + 0.30 * acc + 0.25 * cov



# V4 参数作为正则化锚点
V4_PARAMS = json.load(open(os.path.join(PR, "multi_agent", "config", "predictor_params.json")))

def _weight_reg(w, cat):
    anchor = V4_PARAMS.get(cat, V4_PARAMS.get("_default", {})).get("weights", {})
    if not anchor:
        return 0.0
    return sum((w.get(k, 0) - anchor.get(k, 0)) ** 2 for k in ["technical", "fundamental", "sentiment", "debate"])


def _threshold_reg(b, be, cat):
    anchor = V4_PARAMS.get(cat, V4_PARAMS.get("_default", {})).get("threshold", {})
    if not anchor:
        return 0.0
    return (b - anchor.get("bull", 54)) ** 2 + (be - anchor.get("bear", 44)) ** 2


def optimize_category(cat, label, ret_map):
    rs = load_predictions(cat, ret_map)
    ds = sorted(set(r["d"] for r in rs))
    n_days = len(ds)
    # 严格门槛：至少 20 个交易日，防止用短期记录数造假
    if n_days < 20 or len(rs) < 80:
        print("  %s: %d recs / %d days (skip: need >=20 days & >=80 recs)" % (label, len(rs), n_days))
        return None

    # 时间分层：前 70% train，后 30% val
    split_idx = int(n_days * 0.7)
    train_dates = set(ds[:split_idx])
    val_dates = set(ds[split_idx:])
    train_rs = [r for r in rs if r["d"] in train_dates]
    val_rs = [r for r in rs if r["d"] in val_dates]
    print("  %s: %d recs / %d days | train=%d recs/%d days | val=%d recs/%d days" % (
        label, len(rs), n_days, len(train_rs), split_idx, len(val_rs), n_days - split_idx))

    wcs = []
    for a, b, c, d in product(*WG.values()):
        if abs(a + b + c + d - 1) < 0.01:
            wcs.append(dict(zip(WG.keys(), (a, b, c, d))))

    best = None
    for w in wcs:
        for b in TG["bull"]:
            for be in TG["bear"]:
                # neutral 区间不能太窄，否则信号全是极端
                if b - be < 12:
                    continue
                for ff_strength in FF_STRENGTH:
                    te = evaluate(train_rs, w, b, be, ff_strength)
                    # 训练集每侧至少 15 条，避免过拟合到少数样本
                    if te["n_bull"] < 15 or te["n_bear"] < 15 or te["coverage"] < 0.15:
                        continue
                    ve = evaluate(val_rs, w, b, be, ff_strength)
                    if ve["n_bull"] < 5 or ve["n_bear"] < 5 or ve["coverage"] < 0.10:
                        continue
                    # 验证集不能比训练集差太多（防止过拟合）
                    if ve["direction_accuracy"] < te["direction_accuracy"] * 0.6:
                        continue
                    ts = score(te)
                    vs = score(ve)
                    # 加入 L2 正则化，奖励接近 V4 的参数
                    reg = 0.05 * _weight_reg(w, cat or "_default") + 0.02 * _threshold_reg(b, be, cat or "_default")
                    co = ts * 0.6 + vs * 0.4 - reg
                    if best is None or co > best["score"]:
                        best = {
                            "score": co,
                            "w": w,
                            "b": b,
                            "be": be,
                            "ff": ff_strength,
                            "train": te,
                            "val": ve,
                        }

    if not best:
        print("  %s: no stable params found (skip)" % label)
        return None

    w = best["w"]
    nl, nh = best["be"] + 2, best["b"] - 3
    return {
        "weights": {
            "technical": w["technical"],
            "fundamental": w["fundamental"],
            "sentiment": w["sentiment"],
            "macro": 0.0,
            "debate": w["debate"],
        },
        "threshold": {
            "strong_bull": best["b"] + 5,
            "bull": best["b"],
            "neutral_high": nh,
            "neutral_low": nl,
            "bear": best["be"],
            "strong_bear": best["be"] - 5,
        },
        "fund_flow_strength": best["ff"],
        "stats": "train=%.2f%%/%.2f%%/%.1f%% val=%.2f%%/%.2f%%/%.1f%% ff=%.1f" % (
            best["train"]["bullish_mean"] * 100,
            best["train"]["bearish_mean"] * 100,
            best["train"]["direction_accuracy"],
            best["val"]["bullish_mean"] * 100,
            best["val"]["bearish_mean"] * 100,
            best["val"]["direction_accuracy"],
            best["ff"],
        ),
        "score": round(best["score"], 2),
    }


def main():
    print("=" * 48)
    print(" Optimizer V4 - optimize on realized 5d forward return")
    print("=" * 48)
    ret_map = load_backtest_returns_from_warehouse()
    print("  loaded %d realized 5d returns from warehouse" % len(ret_map))

    rs = {"_version": 4, "updated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}
    for cat, lb in [("个股", "Stk"), ("ETF", "ETF"), ("期货", "Fut"), ("US", "US")]:
        r = optimize_category(cat, lb, ret_map)
        if r:
            rs[cat] = r
    print()
    print(" Global")
    r = optimize_category(None, "All", ret_map)
    if r:
        rs["_default"] = r
    rs["updated_by"] = "param_opt_v4_realized_return"

    with open(OUT, "w") as f:
        json.dump(rs, f, ensure_ascii=False, indent=2)
    print()
    print("=" * 50)
    print("Results:")
    for k in ["个股", "ETF", "期货", "US", "_default"]:
        if k in rs:
            print("  %s: %s" % (k, rs[k].get("stats", "N/A")))
    print("Saved: %s (review before replacing predictor_params.json)" % OUT)


if __name__ == "__main__":
    main()
