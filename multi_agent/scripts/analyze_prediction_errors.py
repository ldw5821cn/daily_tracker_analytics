#!/usr/bin/env python3
"""预测失败分析：读取验证结果，对比成功/失败标的特征，找出系统短板。"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import defaultdict

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, MULTI_AGENT)

DB_PATH = os.path.join(MULTI_AGENT, 'data', 'llm_predictions.db')
VALIDATION_PATH = os.path.join(MULTI_AGENT, 'data', 'morning_validation.json')


def _load_validation() -> dict:
    if not os.path.exists(VALIDATION_PATH):
        return {}
    with open(VALIDATION_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def _load_predictions(pred_date: str) -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM agentic_predictions WHERE pred_date=?",
        (pred_date,)
    ).fetchall()
    conn.close()
    return {r['ticker']: dict(r) for r in rows}


def _parse_component_scores(row: dict) -> dict:
    try:
        return json.loads(row.get('component_scores', '{}'))
    except Exception:
        return {}


def main():
    val = _load_validation()
    if not val or not val.get('items'):
        print('❌ 无验证数据，请先运行 morning_validation.py')
        sys.exit(1)

    pred_date = val['pred_date']
    preds = _load_predictions(pred_date)

    correct_items = []
    wrong_items = []
    for item in val['items']:
        if item.get('note') != 'ok' or item.get('direction_correct') is None:
            continue
        row = item.copy()
        row['pred'] = preds.get(item['ticker'], {})
        if item['direction_correct']:
            correct_items.append(row)
        else:
            wrong_items.append(row)

    print(f'\n📊 预测失败分析 ({pred_date} 预测)')
    print(f'  总样本: {len(correct_items) + len(wrong_items)}')
    print(f'  正确: {len(correct_items)} | 错误: {len(wrong_items)}')
    accuracy = val['overall']['accuracy']
    print(f'  准确率: {accuracy}%\n')

    # 分类失败率
    print('📂 按分类失败率')
    cat_total = defaultdict(int)
    cat_wrong = defaultdict(int)
    for item in val['items']:
        if item.get('direction_correct') is None:
            continue
        cat = item['category']
        cat_total[cat] += 1
        if not item['direction_correct']:
            cat_wrong[cat] += 1
    for cat in cat_total:
        rate = cat_wrong[cat] / cat_total[cat] * 100
        print(f'  {cat}: 错误 {cat_wrong[cat]}/{cat_total[cat]} ({rate:.1f}%)')

    # 信号方向错误分析
    print('\n📈 按信号方向错误分布')
    sig_total = defaultdict(int)
    sig_wrong = defaultdict(int)
    for item in val['items']:
        if item.get('direction_correct') is None:
            continue
        sig = item['signal']
        sig_total[sig] += 1
        if not item['direction_correct']:
            sig_wrong[sig] += 1
    for sig in sig_total:
        rate = sig_wrong[sig] / sig_total[sig] * 100
        print(f'  {sig}: 错误 {sig_wrong[sig]}/{sig_total[sig]} ({rate:.1f}%)')

    # 特征对比
    print('\n🔍 成功 vs 失败特征对比')
    features = ['weighted_score', 'confidence']
    for feat in features:
        c_vals = [r['pred'].get(feat) for r in correct_items if r['pred'].get(feat) is not None]
        w_vals = [r['pred'].get(feat) for r in wrong_items if r['pred'].get(feat) is not None]
        if c_vals and w_vals:
            print(f'  {feat}: 正确 {sum(c_vals)/len(c_vals):.2f} vs 错误 {sum(w_vals)/len(w_vals):.2f}')

    # component scores 对比
    print('\n🧩 分项得分对比（技术/基本面/新闻）')
    comp_keys = ['technical', 'fundamental', 'news']
    for key in comp_keys:
        c_vals = []
        w_vals = []
        for r in correct_items:
            comp = _parse_component_scores(r['pred'])
            if key in comp:
                c_vals.append(comp[key])
        for r in wrong_items:
            comp = _parse_component_scores(r['pred'])
            if key in comp:
                w_vals.append(comp[key])
        if c_vals and w_vals:
            print(f'  {key}: 正确 {sum(c_vals)/len(c_vals):.2f} vs 错误 {sum(w_vals)/len(w_vals):.2f}')

    # 失败 Top10
    print('\n❌ 失败样本 Top10（按 |实际收益| 排序）')
    wrong_sorted = sorted(
        [r for r in wrong_items if r.get('return_pct') is not None],
        key=lambda x: abs(x['return_pct']),
        reverse=True
    )[:10]
    for r in wrong_sorted:
        pred = r['pred']
        print(f"  {r['ticker']:8s} {r['name'][:8]:8s} {r['signal']:8s} 预测价{pred.get('current_price',''):8s} 现价{r['today_price']:8s} 收益{r['return_pct']:+.2f}%")

    # 建议
    print('\n💡 反思与改进建议')
    if len(wrong_items) > len(correct_items):
        print('  - 整体胜率不足 50%，建议收紧信号阈值或提高置信度门槛')
    if sig_wrong.get('bullish', 0) / max(sig_total.get('bullish', 1), 1) > 0.6:
        print('  - 看多信号失败率偏高，可能市场环境为下跌/震荡，基本面/技术面滞后')
    if sig_wrong.get('bearish', 0) / max(sig_total.get('bearish', 1), 1) > 0.6:
        print('  - 看空信号失败率偏高，可能系统过度悲观或反转信号过早')
    print('  - 建议对失败标的增加日内 TickFlow 数据校验，减少滞后技术面影响')
    print('  - 建议引入宏观/资金流向 Agent，提升对大盘方向的判断')


if __name__ == '__main__':
    main()
