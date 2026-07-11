#!/usr/bin/env python3
"""根据目标权重对期货模拟盘进行再平衡。

逻辑：
1. 从 target_weights.json 读取期货目标（只取 category=='期货'）。
2. 按目标权重排序，在期货预算（权益 × 20%）内选最多的品种，每个品种 1 手。
3. 对当前持仓：不在目标中的平仓，方向不对的平仓，目标手数差异调整。
4. 所有开仓/加仓经过 RiskGuard 审核。
"""

from __future__ import annotations

import json
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'multi_agent'))

from futures_simulator import (
    get_positions_summary, open_position, close_position,
    calc_margin, CONTRACT_SPECS, DEFAULT_CAPITAL, get_cash, DB_PATH
)

TARGET_WEIGHTS_PATH = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'target_weights.json')


def _contract_from_ticker(ticker: str) -> str:
    """把 OI0/CF0 等映射到 OI/CF。"""
    if ticker.endswith('0') and len(ticker) >= 2:
        return ticker[:-1]
    return ticker


def _load_futures_targets():
    if not os.path.exists(TARGET_WEIGHTS_PATH):
        return []
    with open(TARGET_WEIGHTS_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return [t for t in data.get('targets', []) if t.get('category') == '期货']


def rebalance(dry_run: bool = True) -> Dict:
    summary = get_positions_summary()
    equity = summary['total_asset']
    cash = summary['cash']
    budget = equity * 0.50  # 期货预算 50% 权益，剩余现金保持流动性
    max_contracts = 5

    targets = _load_futures_targets()
    if not targets:
        return {'error': '无期货目标权重'}

    # 按目标权重绝对值排序
    targets = sorted(targets, key=lambda x: abs(x['target_weight']), reverse=True)

    selected = []
    used_budget = 0.0
    for t in targets:
        contract = _contract_from_ticker(t['ticker'])
        if contract not in CONTRACT_SPECS:
            continue
        price = t.get('current_price', 0) or 0
        if price <= 0:
            q = None
            try:
                from futures_simulator import fetch_futures_price
                q = fetch_futures_price(contract)
            except Exception:
                pass
            price = q['close'] if q else 0
        if price <= 0:
            continue
        margin_per_lot = calc_margin(contract, price, 1)
        # 单笔保证金不能超过权益 15%（RiskGuard 上限），否则跳过
        if margin_per_lot > equity * 0.15:
            continue
        if used_budget + margin_per_lot > budget or len(selected) >= max_contracts:
            continue
        selected.append({
            'ticker': t['ticker'],
            'contract': contract,
            'direction': 'long' if t['target_weight'] > 0 else 'short',
            'price': price,
            'margin_per_lot': margin_per_lot,
            'target_weight': t['target_weight'],
        })
        used_budget += margin_per_lot

    # 当前持仓
    current = {p['contract']: p for p in summary.get('positions', [])}
    target_map = {s['contract']: s for s in selected}

    actions = []

    # 1. 平仓不在目标或方向不对的
    for contract, pos in current.items():
        if contract not in target_map:
            actions.append({
                'contract': contract,
                'action': 'close',
                'direction': pos['direction'],
                'lots': pos['lots'],
                'reason': '不在目标列表',
            })
        else:
            target_dir = target_map[contract]['direction']
            current_dir = 'long' if pos['direction'] == '多' else 'short'
            if current_dir != target_dir:
                actions.append({
                    'contract': contract,
                    'action': 'close',
                    'direction': current_dir,
                    'lots': pos['lots'],
                    'reason': f'方向切换 目标{target_dir}',
                })
            elif pos['lots'] > 1:
                actions.append({
                    'contract': contract,
                    'action': 'close',
                    'direction': current_dir,
                    'lots': pos['lots'] - 1,
                    'reason': '目标 1 手，减仓',
                })

    # 2. 开仓目标中不存在的，或方向对但数量不足（暂不考虑加仓，每个 1 手）
    for s in selected:
        contract = s['contract']
        if contract not in current:
            actions.append({
                'contract': contract,
                'action': 'open',
                'direction': s['direction'],
                'lots': 1,
                'price': s['price'],
                'reason': f'目标权重{s["target_weight"]:+.2%}',
            })

    # 执行（或 dry-run）
    executed = []
    if not dry_run:
        # 先平仓，释放保证金
        for a in actions:
            if a['action'] == 'close':
                # 获取当前价格，避免 price 处理异常
                cur_price = current[a['contract']]['current_price']
                r = close_position(a['contract'], a['direction'], a['lots'], price=cur_price, reason=a['reason'])
                executed.append({'action': a, 'result': r})
        # 再开仓
        for a in actions:
            if a['action'] == 'open':
                r = open_position(a['contract'], a['direction'], a['lots'], a['price'], note=a['reason'])
                executed.append({'action': a, 'result': r})

    return {
        'dry_run': dry_run,
        'equity': equity,
        'cash': cash,
        'budget': budget,
        'selected_targets': selected,
        'current_positions': list(current.values()),
        'actions': actions,
        'executed': executed,
    }


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='期货目标权重再平衡')
    parser.add_argument('--execute', action='store_true', help='执行调仓（默认仅预览）')
    args = parser.parse_args()

    result = rebalance(dry_run=not args.execute)
    if 'error' in result:
        print(f"❌ {result['error']}")
        sys.exit(1)

    print(f"{'[DRY RUN]' if result['dry_run'] else '[EXECUTED]'} 期货再平衡")
    print(f"权益: {result['equity']:.2f}  现金: {result['cash']:.2f}  预算: {result['budget']:.2f}")
    print(f"目标持仓 ({len(result['selected_targets'])} 只):")
    for s in result['selected_targets']:
        print(f"  {s['contract']:6s} {s['direction']:5s} 1手 保证金{s['margin_per_lot']:.0f} 目标{s['target_weight']:+.2%}")
    print(f"\n当前持仓:")
    for p in result['current_positions']:
        print(f"  {p['contract']:6s} {p['direction']:3s} {p['lots']}手")
    print(f"\n计划操作 ({len(result['actions'])} 笔):")
    for a in result['actions']:
        print(f"  {a['action']:6s} {a['contract']:6s} {a['direction']:5s} {a['lots']}手  {a['reason']}")
    if not result['dry_run']:
        print(f"\n执行结果:")
        for e in result['executed']:
            status = '✅' if e['result'].get('success') else '❌'
            print(f"  {status} {e['action']['action']} {e['action']['contract']}: {e['result']}")
        print(f"\n调仓后状态:")
        print(json.dumps(get_positions_summary(), ensure_ascii=False, indent=2, default=str))
