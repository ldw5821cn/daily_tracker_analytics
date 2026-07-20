#!/usr/bin/env python3
"""参数优化器 V3：按类别优化权重 + 阈值，并学习资金流修正强度。

输入: multi_agent/data/llm_predictions.db 中 agentic_predictions 表
输出: multi_agent/config/predictor_params.json

相比 V2，新增：
- 把 component_scores.fund_flow_override 作为独立修正项纳入回测评分
- 学习 fund_flow_strength（缩放乘数），自动决定资金流修正力度
- 继续禁止把 macro 作为权重项；宏观仅通过 macro_override 自然修正
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
OUT = os.path.join(PR, "multi_agent", "config", "predictor_params.json")

# 权重搜索空间
WG = {
    "technical": [0.20, 0.25, 0.30, 0.35],
    "fundamental": [0.15, 0.20, 0.25],
    "sentiment": [0.05, 0.10, 0.15],
    "debate": [0.15, 0.20, 0.25, 0.30],
}
TG = {
    "bull": list(range(50, 66, 2)),
    "bear": list(range(35, 48, 2)),
}
# 资金流修正强度搜索空间
FF_STRENGTH = [0.0, 1.0, 2.0, 3.0, 4.0]


def cw(t, f, s, dn, ff_override, w, ff_strength):
    """计算加权评分，并应用资金流修正。"""
    base = t * w["technical"] + f * w["fundamental"] + s * w["sentiment"] + (50 + dn * 8) * w["debate"]
    # 资金流修正：override 已经是 [-10, 10]，乘以 ff_strength 缩放
    return max(0, min(100, base + ff_override * ff_strength))


def sg(score, b, be):
    if score >= b:
        return "bullish"
    if score <= be:
        return "bearish"
    return "neutral"


def cr(signal, ret):
    if signal == "neutral":
        return False
    return (ret > 0.0) if signal == "bullish" else (ret < 0.0)


def ld(cat=None):
    co = sqlite3.connect(DB)
    co.row_factory = sqlite3.Row
    q = """
        SELECT pred_date, category, component_scores, horizon_1d_return
        FROM agentic_predictions
        WHERE component_scores IS NOT NULL
          AND horizon_1d_return IS NOT NULL
    """
    rows = co.execute(q).fetchall()
    co.close()

    rs = []
    for r in rows:
        if cat and r["category"] != cat:
            continue
        try:
            sc = json.loads(r["component_scores"])
            rt = float(r["horizon_1d_return"])
        except Exception:
            continue
        if not isinstance(sc, dict):
            continue

        t = sc.get("technical", 50)
        if isinstance(t, dict):
            t = 50
        # 兼容旧字段名
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
            "r": rt,
            "cat": r["category"],
        })
    return rs


def ev(rs, w, b, be, ff_strength):
    c = t = 0
    for r in rs:
        ww = cw(r["t"], r["f"], r["s"], r["dn"], r["ff"], w, ff_strength)
        ss = sg(ww, b, be)
        if ss == "neutral":
            continue
        t += 1
        if cr(ss, r["r"]):
            c += 1
    return {"a": c / t * 100 if t else 0, "n": t}


def opt(cat, lb):
    rs = ld(cat)
    if len(rs) < 20:
        print("  %s: %d recs (skip)" % (lb, len(rs)))
        return None

    ds = sorted(set(r["d"] for r in rs))
    tr = [r for r in rs if r["d"] in ds[:-1]]
    vr = [r for r in rs if r["d"] in ds[-1:]]
    print("  %s: %d recs | train=%d val=%d" % (lb, len(rs), len(tr), len(vr)))

    # 生成合法权重组合（和约等于 1）
    wcs = []
    for a, b, c, d in product(*WG.values()):
        if abs(a + b + c + d - 1) < 0.01:
            wcs.append(dict(zip(WG.keys(), (a, b, c, d))))

    best = None
    for w in wcs:
        for b in TG["bull"]:
            for be in TG["bear"]:
                if b <= be + 5:
                    continue
                for ff_strength in FF_STRENGTH:
                    te = ev(tr, w, b, be, ff_strength)
                    if te["n"] < 5:
                        continue
                    ve = ev(vr, w, b, be, ff_strength)
                    if ve["n"] < 2:
                        continue
                    co = te["a"] * 0.6 + ve["a"] * 0.4
                    if best is None or co > best["score"]:
                        best = {
                            "score": co,
                            "w": w,
                            "b": b,
                            "be": be,
                            "ff": ff_strength,
                            "tr": te,
                            "vr": ve,
                        }

    if not best:
        return None

    nl, nh = best["be"] + 2, best["b"] - 3
    w = best["w"]
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
        "stats": "train=%.1f%%(%d) val=%.1f%%(%d) ff_strength=%.1f" % (
            best["tr"]["a"], best["tr"]["n"], best["vr"]["a"], best["vr"]["n"], best["ff"]
        ),
        "score": round(best["score"], 1),
    }


def main():
    print("=" * 48)
    print(" Optimizer V3 - category-specific + fund_flow_strength")
    print("=" * 48)
    rs = {"_version": 2, "updated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}
    for cat, lb in [("个股", "Stk"), ("ETF", "ETF"), ("期货", "Fut"), ("US", "US")]:
        r = opt(cat, lb)
        if r:
            rs[cat] = r
    print()
    print(" Global")
    r = opt(None, "All")
    if r:
        rs["_default"] = r
    rs["updated_by"] = "param_opt_v3"
    with open(OUT, "w") as f:
        json.dump(rs, f, ensure_ascii=False, indent=2)
    print()
    print("=" * 50)
    print("Results:")
    for k in ["个股", "ETF", "期货", "US", "_default"]:
        if k in rs:
            print("  %s: %s" % (k, rs[k].get("stats", "N/A")))
    print("Saved: %s" % OUT)


if __name__ == "__main__":
    main()
