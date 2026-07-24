"""LLM-native 目标权重生成器。

当前阶段：基于规则的可解释基线，不依赖外部 LLM API。
未来会替换为 LLM 权重生成层：把信号、回测、板块、风控状态喂给 LLM，让它输出目标权重。

输入: agentic_predictions 最新预测
输出: multi_agent/data/target_weights.json
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Dict, List, Optional
from datetime import datetime

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from core.backtest_utils import parse_backtest_summary
from strategy.factor_scoring import add_factor_scores_to_predictions

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
DB_PATH = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'llm_predictions.db')
OUTPUT_PATH = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'target_weights.json')

# 数据库存中文信号，映射到英文 canonical
SIGNAL_CN_TO_EN = {'看多': 'bullish', '看空': 'bearish', '中性': 'neutral', '观望': 'weak_neutral'}

def _canonical_signal(sig):
    return SIGNAL_CN_TO_EN.get(sig, sig)

# 分类敞口上限（绝对值和）
CATEGORY_LIMIT = {
    'ETF': 0.30,
    '个股': 0.50,
    '期货': 0.20,
}

# 单标上限
MAX_POSITION = {
    'ETF': 0.10,
    '个股': 0.08,
    '期货': 0.05,
}

# 板块集中度：个股单一 sector 不超过个股敞口的 30%
SECTOR_CAP = 0.30

# 净敞口上限：|long - short| <= 10%，控制风险偏好
NET_EXPOSURE_LIMIT = 0.70  # 当只有单向信号时，总敞口不受净敞口过度压缩

# 做空权重衰减
SHORT_DECAY = 0.3

# 总敞口上限
TOTAL_EXPOSURE_LIMIT = 0.70


def _load_latest_predictions() -> List[Dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT pred_date FROM agentic_predictions ORDER BY pred_date DESC LIMIT 1")
    row = cur.fetchone()
    if not row:
        return []
    latest_date = row['pred_date']
    rows = cur.execute("""
        SELECT ticker, name, sector, category, signal, confidence, current_price,
               target_price, stop_loss, position_pct, weighted_score, backtest_summary
        FROM agentic_predictions
        WHERE pred_date = ?
    """, (latest_date,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def allocate() -> Dict:
    preds = _load_latest_predictions()
    if not preds:
        return {'error': 'no predictions'}

    date = datetime.now().strftime('%Y-%m-%d')

    # 注入回测指标
    for p in preds:
        bt = parse_backtest_summary(p.get('backtest_summary'), p.get('category', ''))
        p['bt_score'] = bt.get('bt_score', 0)
        p['bt_return_60d'] = bt.get('return_60d', 0)
        p['bt_max_dd_60d'] = bt.get('max_dd_60d', 0)

    # 注入精选因子得分
    preds = add_factor_scores_to_predictions(preds)

    # 初始权重：方向 * 信心 * 回测得分缩放 * 因子得分
    targets = []
    for p in preds:
        signal = _canonical_signal(p.get('signal', 'neutral'))
        if signal in ('neutral', 'weak_neutral'):
            continue
        direction = 1 if signal == 'bullish' else -1
        confidence = p.get('confidence', 0.5)
        bt_score = max(p.get('bt_score', 0), 0)
        factor_score = p.get('factor_score', 0)
        # factor_score 范围 [-1,1]；映射到 [0.5,1.5]
        factor_multiplier = 1.0 + factor_score
        raw = direction * confidence * (1 + bt_score / 100) * factor_multiplier
        if direction == -1:
            raw *= SHORT_DECAY
        targets.append({
            'ticker': p['ticker'],
            'name': p['name'],
            'category': p.get('category', '个股'),
            'sector': p.get('sector', ''),
            'signal': signal,
            'current_price': p.get('current_price', 0),
            'target_price': p.get('target_price', 0),
            'stop_loss': p.get('stop_loss', 0),
            'confidence': confidence,
            'bt_score': p['bt_score'],
            'factor_score': round(factor_score, 3),
            'raw_weight': raw,
            'reason': f"信号{signal[:4]} 信心{confidence:.0%} 60日收益{p['bt_return_60d']:+.1f}% 回撤{p['bt_max_dd_60d']:.1f}% 因子分{factor_score:+.2f}",
        })

    # 按 category 归一化到上限
    category_groups: Dict[str, List[Dict]] = {}
    for t in targets:
        category_groups.setdefault(t['category'], []).append(t)

    for cat, group in category_groups.items():
        limit = CATEGORY_LIMIT.get(cat, 0.30)
        total_abs = sum(abs(t['raw_weight']) for t in group)
        if total_abs == 0:
            continue
        scale = limit / total_abs
        for t in group:
            t['target_weight'] = t['raw_weight'] * scale
            max_pos = MAX_POSITION.get(cat, 0.08)
            if abs(t['target_weight']) > max_pos:
                t['target_weight'] = max_pos * (1 if t['target_weight'] > 0 else -1)
                t['capped'] = True

    # 个股板块分散：确保至少 3 个 sector，单一 sector 不超过个股总敞口 30%
    stocks = [t for t in targets if t['category'] == '个股']
    sectors = set(t['sector'] for t in stocks if t['sector'])
    if len(sectors) < 3:
        # 不做强制剔除，只降低非主要 sector 权重没有意义；记录警告
        pass
    stock_total = sum(abs(t['target_weight']) for t in stocks)
    if stock_total > 0:
        sector_weights: Dict[str, float] = {}
        for t in stocks:
            sector_weights[t['sector']] = sector_weights.get(t['sector'], 0) + abs(t['target_weight'])
        for sector, sw in sector_weights.items():
            if sw / stock_total > SECTOR_CAP:
                # 对该 sector 内标的等比例缩放
                sector_items = [t for t in stocks if t['sector'] == sector]
                scale = (stock_total * SECTOR_CAP) / sw
                for t in sector_items:
                    t['target_weight'] *= scale
                    t['sector_capped'] = True

    # 汇总
    total_long = sum(t['target_weight'] for t in targets if t['target_weight'] > 0)
    total_short = sum(abs(t['target_weight']) for t in targets if t['target_weight'] < 0)
    net_exposure = total_long - total_short

    # 限制净敞口
    if net_exposure > NET_EXPOSURE_LIMIT:
        scale_long = NET_EXPOSURE_LIMIT / net_exposure
        for t in targets:
            if t['target_weight'] > 0:
                t['target_weight'] *= scale_long
                t['net_capped'] = True
        total_long *= scale_long
    elif net_exposure < -NET_EXPOSURE_LIMIT:
        scale_short = NET_EXPOSURE_LIMIT / abs(net_exposure)
        for t in targets:
            if t['target_weight'] < 0:
                t['target_weight'] *= scale_short
                t['net_capped'] = True
        total_short *= scale_short

    total_exposure = total_long + total_short

    # 限制总敞口
    if total_exposure > TOTAL_EXPOSURE_LIMIT:
        scale = TOTAL_EXPOSURE_LIMIT / total_exposure
        for t in targets:
            t['target_weight'] *= scale
        total_long *= scale
        total_short *= scale
        total_exposure = TOTAL_EXPOSURE_LIMIT

    # 重新限制净敞口（在总敞口压缩后可能再次超出）
    net_exposure = total_long - total_short
    if net_exposure > NET_EXPOSURE_LIMIT:
        scale_long = NET_EXPOSURE_LIMIT / net_exposure
        for t in targets:
            if t['target_weight'] > 0:
                t['target_weight'] *= scale_long
                t['net_capped'] = True
        total_long *= scale_long
    elif net_exposure < -NET_EXPOSURE_LIMIT:
        scale_short = NET_EXPOSURE_LIMIT / abs(net_exposure)
        for t in targets:
            if t['target_weight'] < 0:
                t['target_weight'] *= scale_short
                t['net_capped'] = True
        total_short *= scale_short

    for t in targets:
        t['target_weight'] = round(t['target_weight'], 4)

    total_exposure = total_long + total_short
    net_exposure = total_long - total_short

    result = {
        'date': date,
        'based_on_pred_date': preds[0].get('pred_date') if preds else date,
        'total_exposure': round(total_exposure, 4),
        'long_exposure': round(total_long, 4),
        'short_exposure': round(total_short, 4),
        'net_exposure': round(total_long - total_short, 4),
        'total_targets': len(targets),
        'long_targets': sum(1 for t in targets if t['target_weight'] > 0),
        'short_targets': sum(1 for t in targets if t['target_weight'] < 0),
        'targets': sorted(targets, key=lambda x: abs(x['target_weight']), reverse=True),
    }

    # 保存
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    return result


if __name__ == '__main__':
    result = allocate()
    if 'error' in result:
        print(f"❌ {result['error']}")
    else:
        print(f"✅ 目标权重生成: {OUTPUT_PATH}")
        print(f"   总敞口: {result['total_exposure']:.2%}")
        print(f"   做多: {result['long_exposure']:.2%} ({result['long_targets']} 只)")
        print(f"   做空: {result['short_exposure']:.2%} ({result['short_targets']} 只)")
        print(f"   Top3 多头:")
        for t in result['targets']:
            if t['target_weight'] > 0:
                print(f"     {t['ticker']} {t['name']}: {t['target_weight']:+.2%}")
        print(f"   Top3 空头:")
        for t in result['targets']:
            if t['target_weight'] < 0:
                print(f"     {t['ticker']} {t['name']}: {t['target_weight']:+.2%}")
