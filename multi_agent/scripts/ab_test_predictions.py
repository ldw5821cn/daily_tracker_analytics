#!/usr/bin/env python3
"""用新参数重算历史预测信号，做 A/B 对比，无需重新调用 LLM。

读取 agentic_predictions 中指定日期的记录，用当前 agentic_predictor.py 的
WEIGHTS/THRESHOLD/宏观拦截逻辑重新计算 signal，与旧 signal 对比。
"""
from __future__ import annotations

import json
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'multi_agent'))

from multi_agent.core.db import get_predictions_conn
from multi_agent.analysts.agentic_predictor import WEIGHTS, THRESHOLD


def load_predictions_for_date(pred_date: str):
    conn = get_predictions_conn()
    try:
        cur = conn.execute(
            """SELECT ticker, name, category, signal, weighted_score, component_scores, pred_date, price_date, current_price
               FROM agentic_predictions WHERE pred_date=?""",
            (pred_date,)
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def load_macro_report(pred_date: str):
    path = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'macro_report.json')
    if not os.path.exists(path):
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def parse_component_scores(cs):
    if not cs:
        return {}
    if isinstance(cs, str):
        try:
            return json.loads(cs)
        except Exception:
            return {}
    return cs


def recalc_signal(row, macro_report):
    """用当前 WEIGHTS/THRESHOLD 重新计算 signal（不重新跑 LLM）。"""
    comp = parse_component_scores(row.get('component_scores'))
    tech = comp.get('technical', 50)
    fund = comp.get('fundamental', 50)
    sent = comp.get('sentiment', 50)
    debate_net = comp.get('debate_net', 0)
    macro_score = macro_report.get('macro_score', 50) if macro_report else 50
    macro_signal = macro_report.get('macro_signal', 'neutral') if macro_report else 'neutral'

    weighted = (
        tech * WEIGHTS['technical'] +
        fund * WEIGHTS['fundamental'] +
        sent * WEIGHTS['sentiment'] +
        macro_score * WEIGHTS['macro'] +
        (50 + debate_net * 8) * WEIGHTS['debate']
    )
    weighted = max(0, min(100, weighted))

    macro_override = 0
    if macro_report:
        raw_signal = 'bullish' if weighted >= THRESHOLD['bull'] else 'bearish' if weighted <= THRESHOLD['bear'] else 'neutral'
        if macro_signal == 'bearish' and raw_signal == 'bullish':
            weighted = THRESHOLD['bear'] - 1
            macro_override = -20
        else:
            from analysts.macro_analyst import get_macro_score_override
            macro_override = get_macro_score_override(macro_report, raw_signal)
            breadth_score = macro_report.get('market_breadth', {}).get('score', 50)
            if macro_signal == 'bearish' and breadth_score < 30:
                macro_override *= 2.0
            elif macro_signal == 'bullish' and breadth_score > 70:
                macro_override *= 1.5
            weighted = max(0, min(100, weighted + macro_override))

    if weighted >= THRESHOLD['bull']:
        signal = 'bullish'
    elif weighted <= THRESHOLD['bear']:
        signal = 'bearish'
    else:
        signal = 'neutral'

    # 与 agentic_predictor.py 保持一致：硬规则
    if macro_report and macro_report.get('macro_score', 50) < 50:
        if signal == 'bullish':
            signal = 'bearish' if tech < 55 else 'neutral'
            weighted = THRESHOLD['bear'] - 1 if tech < 55 else 50
        elif signal == 'neutral' and tech < 55:
            signal = 'bearish'
            weighted = THRESHOLD['bear'] - 1

    return signal, round(weighted, 1), macro_override


def ab_test(pred_date: str = None):
    if pred_date is None:
        rows = load_predictions_for_date('2026-07-13')
        if not rows:
            rows = load_predictions_for_date('2026-07-12')
            pred_date = '2026-07-12'
        else:
            pred_date = '2026-07-13'
    else:
        rows = load_predictions_for_date(pred_date)

    macro_report = load_macro_report(pred_date)
    print(f'[A/B] 重算日期: {pred_date}, 标数: {len(rows)}, 宏观: {macro_report.get("macro_signal")}/{macro_report.get("macro_score")}')

    transitions = {'bullish→bullish': 0, 'bullish→neutral': 0, 'bullish→bearish': 0,
                     'neutral→bullish': 0, 'neutral→neutral': 0, 'neutral→bearish': 0,
                     'bearish→bullish': 0, 'bearish→neutral': 0, 'bearish→bearish': 0}
    changed = []

    old_counts = {'bullish': 0, 'neutral': 0, 'bearish': 0}
    new_counts = {'bullish': 0, 'neutral': 0, 'bearish': 0}

    for row in rows:
        old_sig = row['signal']
        new_sig, new_score, macro_override = recalc_signal(row, macro_report)
        old_counts[old_sig] += 1
        new_counts[new_sig] += 1
        key = f'{old_sig}→{new_sig}'
        transitions[key] = transitions.get(key, 0) + 1
        if old_sig != new_sig:
            changed.append({
                'ticker': row['ticker'], 'name': row['name'], 'category': row['category'],
                'old_signal': old_sig, 'new_signal': new_sig,
                'old_score': row['weighted_score'], 'new_score': new_score,
                'macro_override': macro_override,
            })

    print('\n[旧信号分布]')
    for k, v in old_counts.items(): print(f'  {k}: {v}')
    print('\n[新信号分布]')
    for k, v in new_counts.items(): print(f'  {k}: {v}')

    print('\n[信号迁移矩阵]')
    for key in ['bullish→bullish', 'bullish→neutral', 'bullish→bearish',
                'neutral→bullish', 'neutral→neutral', 'neutral→bearish',
                'bearish→bullish', 'bearish→neutral', 'bearish→bearish']:
        print(f'  {key}: {transitions[key]}')

    print(f'\n[发生变化] {len(changed)} 个标的')
    for c in changed[:20]:
        print(f"  {c['ticker']} {c['name']} | {c['old_signal']}→{c['new_signal']} (评分 {c['old_score']}→{c['new_score']})")

    out_path = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'ab_test_signal_changes.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({
            'pred_date': pred_date,
            'macro_signal': macro_report.get('macro_signal'),
            'macro_score': macro_report.get('macro_score'),
            'old_counts': old_counts,
            'new_counts': new_counts,
            'transitions': transitions,
            'changed': changed,
        }, f, ensure_ascii=False, indent=2)
    print(f'\n[输出] {out_path}')


if __name__ == '__main__':
    ab_test()
