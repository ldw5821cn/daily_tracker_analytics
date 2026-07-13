#!/usr/bin/env python3
"""上午收盘后快速验证 1 日方向准确率。

读取 agentic_predictions 表中最新预测（通常是前一交易日收盘后），
用当日数据源获取上午收盘价/最新价，计算实际收益率和方向正确性。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, MULTI_AGENT)

from core.data_layer import get_stock_data, get_realtime_price

DB_PATH = os.path.join(MULTI_AGENT, 'data', 'llm_predictions.db')
OUTPUT_PATH = os.path.join(MULTI_AGENT, 'data', 'morning_validation.json')
DIRECTION_THRESHOLD = 1.5  # 1日方向阈值 1.5%


def _direction_correct(signal: str, return_pct: float, threshold: float = DIRECTION_THRESHOLD) -> bool:
    """方向正确性判定。

    - bullish: 实际收益 > 0 即正确（允许小幅上涨，避免阈值过滤掉温和上涨）
    - bearish: 实际收益 < 0 即正确
    - neutral: 实际收益在 [-threshold, threshold] 区间内正确
    """
    if signal == 'bullish':
        return return_pct > 0
    elif signal == 'bearish':
        return return_pct < 0
    else:
        return abs(return_pct) <= threshold


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _get_current_price(ticker: str, category: str, name: str) -> float | None:
    """获取最新收盘价（验证日收盘价）。"""
    try:
        if category == '期货':
            df, _ = get_stock_data(ticker, period='10d', calibrate=False)
            if df is not None and not df.empty:
                return float(df['close'].iloc[-1])
        else:
            df, _ = get_stock_data(ticker, period='10d', calibrate=False)
            if df is not None and not df.empty:
                return float(df['close'].iloc[-1])
    except Exception as e:
        print(f"  {ticker} 价格获取失败: {e}")
    return None


def _validate_row(row: sqlite3.Row) -> dict:
    pred_price = row['current_price']
    signal = row['signal']
    ticker = row['ticker']
    category = row['category']
    name = row['name']

    today_price = _get_current_price(ticker, category, name)
    if today_price is None or pred_price is None or pred_price == 0:
        return {
            'ticker': ticker, 'name': name, 'category': category,
            'signal': signal, 'pred_price': pred_price,
            'today_price': None, 'return_pct': None,
            'direction_correct': None, 'note': 'no_data',
        }

    return_pct = (today_price - pred_price) / pred_price * 100
    correct = _direction_correct(signal, return_pct)

    return {
        'ticker': ticker, 'name': name, 'category': category,
        'signal': signal, 'pred_price': round(pred_price, 3),
        'today_price': round(today_price, 3),
        'return_pct': round(return_pct, 3),
        'direction_correct': correct,
        'note': 'ok',
    }


def main():
    conn = _get_conn()
    latest = conn.execute("SELECT MAX(pred_date) FROM agentic_predictions").fetchone()[0]
    if not latest:
        print('❌ 无预测数据')
        sys.exit(1)

    rows = conn.execute(
        "SELECT * FROM agentic_predictions WHERE pred_date=?",
        (latest,)
    ).fetchall()
    conn.close()

    print(f'[morning] 验证 {latest} 预测 vs 今日上午收盘价，共 {len(rows)} 只...')
    results = []
    for row in rows:
        r = _validate_row(row)
        results.append(r)
        if r['note'] == 'ok':
            print(f"  {r['ticker']:8s} {r['signal']:8s} 预测{r['pred_price']:.2f} 现{r['today_price']:.2f} 收益{r['return_pct']:+.2f}% 正确{'✓' if r['direction_correct'] else '✗'}")

    # 统计
    by_category = {}
    overall = {'total': 0, 'correct': 0}
    for r in results:
        if r['note'] != 'ok':
            continue
        cat = r['category']
        by_category.setdefault(cat, {'total': 0, 'correct': 0})
        by_category[cat]['total'] += 1
        if r['direction_correct']:
            by_category[cat]['correct'] += 1
            overall['correct'] += 1
        overall['total'] += 1

    out = {
        'pred_date': latest,
        'validate_date': datetime.now().strftime('%Y-%m-%d'),
        'direction_threshold': DIRECTION_THRESHOLD,
        'overall': {
            'total': overall['total'],
            'correct': overall['correct'],
            'accuracy': round(overall['correct'] / max(overall['total'], 1) * 100, 2),
        },
        'by_category': {k: {'total': v['total'], 'correct': v['correct'], 'accuracy': round(v['correct']/max(v['total'],1)*100,2)} for k, v in by_category.items()},
        'items': results,
    }

    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"\n[morning] 总体: {out['overall']['correct']}/{out['overall']['total']} = {out['overall']['accuracy']}%")
    for cat, stat in out['by_category'].items():
        print(f"  {cat}: {stat['correct']}/{stat['total']} = {stat['accuracy']}%")
    print(f"[morning] 详细结果保存到 {OUTPUT_PATH}")


if __name__ == '__main__':
    main()
