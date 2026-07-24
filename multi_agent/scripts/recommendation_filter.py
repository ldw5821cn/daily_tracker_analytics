#!/usr/bin/env python3
"""推荐过滤器：把原始预测转成可执行的 Portfolio 建议。"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from typing import Dict, Optional

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, MULTI_AGENT)

from core.db import get_latest_predictions
from core.backtest_utils import inject_backtest_metrics

MAX_SINGLE_POSITION = 0.10
MAX_LONG_POSITIONS = 10
STOP_LOSS_PCT = 0.05
MACRO_BULLISH_MIN = 30  # 软风控阈值：低于此仍允许 long，但降低分配权重

# 按资产类别分散，避免单一 category 垄断全部仓位；未来交给参数优化器学习
CATEGORY_QUOTA = {
    '个股': 5,
    'ETF': 2,
    'US': 2,
    '期货': 1,
}

SIGNAL_CN = {'看多': 'bullish', '看空': 'bearish', '中性': 'neutral', 'weak_neutral': 'neutral'}


def _load_macro_report() -> Optional[Dict]:
    path = os.path.join(MULTI_AGENT, 'data', 'macro_report.json')
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def build_recommendations(pred_date: Optional[str] = None, max_long_positions: int = MAX_LONG_POSITIONS) -> Dict:
    macro = _load_macro_report()
    macro_score = macro.get('macro_score', 50) if macro else 50
    macro_signal = macro.get('macro_signal', 'neutral') if macro else 'neutral'
    allow_long = macro_score >= MACRO_BULLISH_MIN

    rows = get_latest_predictions(pred_date)
    for p in rows:
        inject_backtest_metrics(p)

    longs = []
    shorts = []
    avoids = []

    for p in rows:
        sig = p.get('signal', '中性')
        conf = p.get('confidence', 0.5)
        sig_en = SIGNAL_CN.get(sig, 'neutral')
        # 过滤低置信度信号：中性/weak_neutral 一律不进入；看多/看空需满足最低置信度
        if sig_en == 'neutral':
            continue

        price = p.get('current_price')
        if not price or price <= 0:
            continue

        if sig_en == 'bullish' and not allow_long:
            avoids.append({
                'ticker': p['ticker'], 'name': p['name'], 'category': p['category'],
                'signal': sig, 'note': f'宏观偏空({macro_score})，禁止新建多头'
            })
            continue

        avg_ret = p.get('bt_return_60d', 0) / 60 if p.get('bt_return_60d') else 0.5
        target_pct = max(abs(avg_ret) * 5, 2.0)
        target = round(price * (1 + (target_pct / 100 if sig_en == 'bullish' else -target_pct / 100)), 3)
        stop = round(price * (1 - STOP_LOSS_PCT), 3) if sig_en == 'bullish' else round(price * (1 + STOP_LOSS_PCT), 3)

        item = {
            'ticker': p['ticker'],
            'name': p['name'],
            'category': p['category'],
            'signal': sig,
            'confidence': round(p.get('confidence', 0.5), 2),
            'price': round(price, 3),
            'target': target,
            'stop': stop,
            'weighted_score': p.get('weighted_score', 50),
            'bt_return_60d': p.get('bt_return_60d', 0),
            'bt_max_dd_60d': p.get('bt_max_dd_60d', 0),
            'bt_sharpe_60d': p.get('bt_sharpe_60d', 0),
            'reasoning': p.get('reasoning', '')[:120],
        }

        if sig_en == 'bullish':
            longs.append(item)
        elif sig_en == 'bearish':
            if p['category'] == '个股':
                item['note'] = 'A股不可做空，建议规避/卖出'
            shorts.append(item)

    # 按类别分别排序，按 weighted_score 优先（避免 confidence 硬排序淹没高综合分信号）
    category_longs = {}
    for item in longs:
        category_longs.setdefault(item['category'], []).append(item)
    for cat in category_longs:
        category_longs[cat].sort(key=lambda x: (x['weighted_score'], x['confidence'], x['bt_sharpe_60d'], x['bt_return_60d']), reverse=True)

    selected = []
    for cat, quota in CATEGORY_QUOTA.items():
        selected.extend(category_longs.get(cat, [])[:quota])
    # 兜底：如果类别配额不足，从剩余高 score 中补充
    selected_tickers = {x['ticker'] for x in selected}
    remaining = [x for x in longs if x['ticker'] not in selected_tickers]
    remaining.sort(key=lambda x: (x['weighted_score'], x['confidence'], x['bt_sharpe_60d'], x['bt_return_60d']), reverse=True)
    selected = selected + remaining[:max(0, max_long_positions - len(selected))]

    longs = selected[:max_long_positions]
    longs.sort(key=lambda x: (x['weighted_score'], x['confidence'], x['bt_sharpe_60d'], x['bt_return_60d']), reverse=True)

    n = len(longs)
    pos = round(min(1.0 / n, MAX_SINGLE_POSITION), 3) if n > 0 else 0
    for item in longs:
        item['position_pct'] = pos

    return {
        'pred_date': pred_date or datetime.now().strftime('%Y-%m-%d'),
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'macro_score': macro_score,
        'macro_signal': macro_signal,
        'allow_long': allow_long,
        'longs': longs,
        'shorts_or_avoids': shorts + avoids,
        'max_single_position': MAX_SINGLE_POSITION,
        'stop_loss_pct': STOP_LOSS_PCT,
    }


def main():
    rec = build_recommendations()
    out = os.path.join(MULTI_AGENT, 'data', 'recommendations.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    print(f'[recommend] 多头 {len(rec["longs"])} 个，看空/规避 {len(rec["shorts_or_avoids"])} 个')
    print(f'[recommend] 保存到 {out}')


if __name__ == '__main__':
    main()
