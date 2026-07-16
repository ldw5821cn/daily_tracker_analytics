#!/usr/bin/env python3
"""数据驱动的参数优化器。"""
import json, os, sqlite3, sys
from datetime import datetime
from itertools import product

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'multi_agent'))
DB_PATH = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'llm_predictions.db')
OUTPUT_PATH = os.path.join(PROJECT_ROOT, 'multi_agent', 'config', 'predictor_params.json')

WEIGHT_GRID = {'technical': [0.20, 0.25, 0.30, 0.35], 'fundamental': [0.15, 0.20, 0.25],
               'sentiment': [0.05, 0.10, 0.15], 'debate': [0.15, 0.20, 0.25, 0.30]}
THRESHOLD_GRID = {'bull': list(range(50, 66, 2)), 'bear': list(range(35, 48, 2))}

def calc_w(tech, fund, sent, dn, w):
    return max(0, min(100, tech*w["technical"]+fund*w["fundamental"]+sent*w["sentiment"]+(50+dn*8)*w["debate"]))

def sig(w, b, be):
    return "bullish" if w >= b else "bearish" if w <= be else "neutral"

def correct(s, r):
    return True if s == "neutral" else r > 0.5 if s == "bullish" else r < -0.5

def load():
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT pred_date, component_scores, horizon_1d_return FROM agentic_predictions WHERE component_scores IS NOT NULL AND horizon_1d_return IS NOT NULL").fetchall()
    conn.close()
    recs = []
    for r in rows:
        try:
            sc = json.loads(r["component_scores"]); ret = float(r["horizon_1d_return"])
        except: continue
        if not isinstance(sc, dict): continue
        t = sc.get("technical", 50)
        if isinstance(t, dict): t = 50
        recs.append({"d": r["pred_date"], "t": float(t), "f": float(sc.get("fundamental_score", 50)),
                     "s": float(sc.get("sentiment", 50)), "dn": float(sc.get("debate_net", 0)), "r": ret})
    return recs

def eval(recs, w, b, be):
    c = t = 0
    for r in recs:
        ww = calc_w(r["t"], r["f"], r["s"], r["dn"], w)
        ss = sig(ww, b, be)
        if ss == "neutral": continue
        t += 1
        if correct(ss, r["r"]): c += 1
    return {"a": c/t*100 if t else 0, "n": t}

def main():
    recs = load()
    dates = sorted(set(r["d"] for r in recs))
    tr = [r for r in recs if r["d"] in dates[:-1]]
    vr = [r for r in recs if r["d"] in dates[-1:]]
    print(f"📊 {len(recs)} recs | train={len(tr)} val={len(vr)}")

    wcs = []
    for a,b,c,d in product(*WEIGHT_GRID.values()):
        if abs(a+b+c+d-1) < 0.01: wcs.append(dict(zip(WEIGHT_GRID.keys(), (a,b,c,d))))
    total = len(wcs)*len(THRESHOLD_GRID["bull"])*len(THRESHOLD_GRID["bear"])
    print(f"⚙️ {total} combos")

    best = None
    for wi, w in enumerate(wcs):
        for b in THRESHOLD_GRID["bull"]:
            for be in THRESHOLD_GRID["bear"]:
                if b <= be+5: continue
                tre = eval(tr, w, b, be)
                if tre["n"] < 10: continue
                vre = eval(vr, w, b, be)
                if vre["n"] < 3: continue
                comb = tre["a"]*0.6 + vre["a"]*0.4
                if best is None or comb > best["score"]:
                    best = {"score": comb, "w": w, "b": b, "be": be, "tr": tre, "vr": vre}

    if best is None:
        print("❌ No valid combo found"); return
    nl, nh = best["be"]+2, best["b"]-3
    w = best["w"]
    print(f"🏆 combined={best["score"]:.1f}% w=tech{w["technical"]} fund{w["fundamental"]} sent{w["sentiment"]} debate{w["debate"]}")
    print(f"   bull={best["b"]} bear={best["be"]} neutral=[{nl},{nh}]")
    print(f"   train={best["tr"]["a"]:.1f}%({best["tr"]["n"]}) val={best["vr"]["a"]:.1f}%({best["vr"]["n"]})")

    params = {
        "weights": {"technical": w["technical"], "fundamental": w["fundamental"],
                      "sentiment": w["sentiment"], "macro": 0.0, "debate": w["debate"]},
        "threshold": {"strong_bull": best["b"]+5, "bull": best["b"], "neutral_high": nh,
                       "neutral_low": nl, "bear": best["be"], "strong_bear": best["be"]-5},
        "hard_rules": {}, "updated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "updated_by": "parameter_optimizer",
        "updated_reason": f"data-driven: train={best["tr"]["a"]:.1f}% val={best["vr"]["a"]:.1f}%",
    }
    with open(OUTPUT_PATH, "w") as f: json.dump(params, f, ensure_ascii=False, indent=2)
    print(f"✅ {OUTPUT_PATH}")

if __name__ == "__main__": main()
