#!/usr/bin/env python3
"""从 recommendations.json 同步 portfolio_state.json 的 xueqiu_r.weights（2026-08-25 规则）。

- 取 recommendations.json 中 category=='个股' 的前 5 只，每只 15%
- key 格式 SH/SZ/BJ + 6 位代码（6 开头→SH，4/8 开头→BJ，其余→SZ）
- 写入 state['xueqiu_r']['weights'] 并更新 scan_time/note
- 无个股多头时权重置空
"""
import json
import os
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REC_PATH = os.path.join(REPO, 'multi_agent/data/recommendations.json')
STATE_PATH = os.path.join(REPO, 'multi_agent/data/portfolio_state.json')


def _key_of(code: str) -> str:
    code = str(code).strip()
    if code.startswith('6'):
        return 'SH' + code
    if code.startswith(('4', '8')):
        return 'BJ' + code
    return 'SZ' + code


def main():
    with open(REC_PATH) as f:
        rec = json.load(f)
    longs = [x for x in rec.get('longs', []) if x.get('category') == '个股']
    top5 = longs[:5]
    weights = {_key_of(x['ticker']): 15.0 for x in top5 if x.get('ticker')}
    with open(STATE_PATH) as f:
        state = json.load(f)
    state.setdefault('xueqiu_r', {})['weights'] = weights
    state['xueqiu_r']['note'] = f"synced from recommendations.json {rec.get('pred_date', '')}"
    state['xueqiu_r']['scan_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    state['scan_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(STATE_PATH, 'w') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    print(f"SYNC_DONE pred_date={rec.get('pred_date')} longs={len(longs)} weights={weights}")


if __name__ == '__main__':
    main()
