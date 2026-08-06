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
OUTPUT_PATH = os.path.join(MULTI_AGENT, 'data', 'prediction_error_analysis.json')


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
    comp_keys = ['technical', 'sentiment', 'fundamental_score', 'debate_net']
    comp_names = {'technical': '技术面', 'sentiment': '新闻情绪', 'fundamental_score': '基本面', 'debate_net': '多空辩论净得分'}
    for key in comp_keys:
        c_vals = []
        w_vals = []
        for r in correct_items:
            comp = _parse_component_scores(r['pred'])
            v = comp.get(key)
            if isinstance(v, (int, float)):
                c_vals.append(v)
        for r in wrong_items:
            comp = _parse_component_scores(r['pred'])
            v = comp.get(key)
            if isinstance(v, (int, float)):
                w_vals.append(v)
        if c_vals and w_vals:
            print(f'  {comp_names.get(key, key)}: 正确 {sum(c_vals)/len(c_vals):.2f} vs 错误 {sum(w_vals)/len(w_vals):.2f}')

    # 因子背离度：基本面 - 技术面；正值表示基本面比技术面更乐观
    print('\n⚡ 基本面-技术面背离度（fundamental_score - technical）')
    def _divergence(r):
        comp = _parse_component_scores(r['pred'])
        f = comp.get('fundamental_score')
        t = comp.get('technical')
        if isinstance(f, (int, float)) and isinstance(t, (int, float)):
            return f - t
        return None

    c_div = [v for r in correct_items if (v := _divergence(r)) is not None]
    w_div = [v for r in wrong_items if (v := _divergence(r)) is not None]
    if c_div and w_div:
        print(f'  正确样本平均背离: {sum(c_div)/len(c_div):.2f}')
        print(f'  错误样本平均背离: {sum(w_div)/len(w_div):.2f}')
        # 按信号细分
        for sig in ['看多', '看空', '中性', '观望']:
            sig_wrong_div = [v for r in wrong_items if r['signal']==sig and (v := _divergence(r)) is not None]
            if sig_wrong_div:
                print(f'  {sig}错误样本平均背离: {sum(sig_wrong_div)/len(sig_wrong_div):.2f} (n={len(sig_wrong_div)})')

    # 失败 Top10
    print('\n❌ 失败样本 Top10（按 |实际收益| 排序）')
    wrong_sorted = sorted(
        [r for r in wrong_items if r.get('return_pct') is not None],
        key=lambda x: abs(x['return_pct']),
        reverse=True
    )[:10]
    for r in wrong_sorted:
        pred = r['pred']
        cp = pred.get('current_price', 0) or 0
        print(f"  {r['ticker']:8s} {r['name'][:8]:8s} {r['signal']:8s} 预测价{cp:8.2f} 现价{r['today_price']:8.2f} 收益{r['return_pct']:+.2f}%")

    # 建议
    suggestions = []
    if len(wrong_items) > len(correct_items):
        suggestions.append('整体胜率不足 50%，建议收紧信号阈值或提高置信度门槛')
    if sig_wrong.get('bullish', 0) / max(sig_total.get('bullish', 1), 1) > 0.6:
        suggestions.append('看多信号失败率偏高，可能市场环境为下跌/震荡，基本面/技术面滞后')
    if sig_wrong.get('bearish', 0) / max(sig_total.get('bearish', 1), 1) > 0.6:
        suggestions.append('看空信号失败率偏高，可能系统过度悲观或反转信号过早')
    suggestions.append('建议对失败标的增加日内 TickFlow 数据校验，减少滞后技术面影响')
    suggestions.append('建议引入宏观/资金流向 Agent，提升对大盘方向的判断')

    print('\n💡 反思与改进建议')
    for s in suggestions:
        print(f'  - {s}')

    analysis = {
        'pred_date': pred_date,
        'validation_date': val.get('validation_date'),
        'summary': {
            'total': len(correct_items) + len(wrong_items),
            'correct': len(correct_items),
            'wrong': len(wrong_items),
            'accuracy': accuracy,
        },
        'by_category': {cat: {'total': cat_total[cat], 'wrong': cat_wrong[cat], 'error_rate': round(cat_wrong[cat] / cat_total[cat] * 100, 2)} for cat in cat_total},
        'by_signal': {sig: {'total': sig_total[sig], 'wrong': sig_wrong[sig], 'error_rate': round(sig_wrong[sig] / sig_total[sig] * 100, 2)} for sig in sig_total},
        'feature_compare': {},
        'component_compare': {},
        'wrong_top10': [],
        'correct_top10': [],
        'suggestions': suggestions,
    }

    for feat in features:
        c_vals = [r['pred'].get(feat) for r in correct_items if r['pred'].get(feat) is not None]
        w_vals = [r['pred'].get(feat) for r in wrong_items if r['pred'].get(feat) is not None]
        if c_vals and w_vals:
            analysis['feature_compare'][feat] = {
                'correct_avg': round(sum(c_vals) / len(c_vals), 2),
                'wrong_avg': round(sum(w_vals) / len(w_vals), 2),
            }

    for key in comp_keys:
        c_vals = []
        w_vals = []
        for r in correct_items:
            comp = _parse_component_scores(r['pred'])
            if key in comp and isinstance(comp[key], (int, float)):
                c_vals.append(comp[key])
        for r in wrong_items:
            comp = _parse_component_scores(r['pred'])
            if key in comp and isinstance(comp[key], (int, float)):
                w_vals.append(comp[key])
        if c_vals and w_vals:
            analysis['component_compare'][comp_names.get(key, key)] = {
                'correct_avg': round(sum(c_vals) / len(c_vals), 2),
                'wrong_avg': round(sum(w_vals) / len(w_vals), 2),
            }

    # 加入背离度特征到输出
    analysis['divergence_analysis'] = {}
    if c_div and w_div:
        analysis['divergence_analysis']['fundamental_technical_divergence'] = {
            'correct_avg': round(sum(c_div) / len(c_div), 2),
            'wrong_avg': round(sum(w_div) / len(w_div), 2),
            'by_wrong_signal': {},
        }
        for sig in ['看多', '看空', '中性', '观望']:
            sig_wrong_div = [v for r in wrong_items if r['signal'] == sig and (v := _divergence(r)) is not None]
            if sig_wrong_div:
                analysis['divergence_analysis']['fundamental_technical_divergence']['by_wrong_signal'][sig] = {
                    'avg': round(sum(sig_wrong_div) / len(sig_wrong_div), 2),
                    'n': len(sig_wrong_div),
                }

    analysis['wrong_top10'] = [
        {
            'ticker': r['ticker'],
            'name': r['name'],
            'category': r['category'],
            'signal': r['signal'],
            'return_pct': r['return_pct'],
            'pred_price': r['pred'].get('current_price'),
            'today_price': r['today_price'],
            'weighted_score': r['pred'].get('weighted_score'),
            'confidence': r['pred'].get('confidence'),
            'reasoning': r['pred'].get('reasoning', '')[:200],
        }
        for r in wrong_sorted
    ]

    correct_sorted = sorted(
        [r for r in correct_items if r.get('return_pct') is not None],
        key=lambda x: abs(x['return_pct']),
        reverse=True,
    )[:10]
    analysis['correct_top10'] = [
        {
            'ticker': r['ticker'],
            'name': r['name'],
            'category': r['category'],
            'signal': r['signal'],
            'return_pct': r['return_pct'],
            'pred_price': r['pred'].get('current_price'),
            'today_price': r['today_price'],
            'weighted_score': r['pred'].get('weighted_score'),
            'confidence': r['pred'].get('confidence'),
        }
        for r in correct_sorted
    ]

    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(analysis, f, ensure_ascii=False, indent=2)
    print(f'\n✅ 分析结果已保存: {OUTPUT_PATH}')

    return analysis


if __name__ == '__main__':
    main()
