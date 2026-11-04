#!/usr/bin/env python3
"""Sync portfolio_state.json from recommendations.json (个股 longs top5 × 15%)."""
import json, os, sqlite3, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(REPO, 'multi_agent', 'data')

rec_path = os.path.join(DATA, 'recommendations.json')
state_path = os.path.join(DATA, 'portfolio_state.json')

with open(rec_path) as f:
    rec = json.load(f)

longs = [x for x in rec.get('longs', []) if x.get('category') == '个股'][:5]

def ticker_to_key(t):
    if not t: return None
    t = t.strip()
    if t.startswith(('6',)):
        return 'SH' + t[:6]
    if t.startswith(('4', '8')):
        return 'BJ' + t[:6]
    return 'SZ' + t[:6]

weights = {}
for x in longs:
    t = x.get('ticker') or x.get('code')
    if not t:
        name = x.get('name')
        if name:
            db = os.path.join(DATA, 'llm_predictions.db')
            conn = sqlite3.connect(db)
            cur = conn.cursor()
            cur.execute("SELECT ticker FROM agentic_predictions WHERE name=? AND pred_date=? LIMIT 1",
                        (name, rec.get('pred_date')))
            row = cur.fetchone()
            conn.close()
            if row: t = row[0]
    k = ticker_to_key(t) if t else None
    if k:
        weights[k] = 15.0

with open(state_path) as f:
    state = json.load(f)

if 'xueqiu_r' not in state:
    state['xueqiu_r'] = {}
state['xueqiu_r']['weights'] = weights
state['xueqiu_r']['scan_time'] = rec.get('pred_date', '')

with open(state_path, 'w') as f:
    json.dump(state, f, ensure_ascii=False, indent=2)

print(f"synced weights={weights}")
