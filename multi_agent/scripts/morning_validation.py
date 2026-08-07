#!/usr/bin/env python3
"""上午收盘后快速验证 1 日方向准确率。

读取 agentic_predictions 表中最新预测（通常是前一交易日收盘后），
用当日数据源获取上午收盘价/最新价，计算实际收益率和方向正确性。
"""
from __future__ import annotations

import json
import os
import pandas as pd
import sqlite3
import sys
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, MULTI_AGENT)

from core.data_layer import get_stock_data, get_realtime_price

DB_PATH = os.path.join(MULTI_AGENT, 'data', 'llm_predictions.db')
OUTPUT_PATH = os.path.join(MULTI_AGENT, 'data', 'morning_validation.json')
DIRECTION_THRESHOLD = 1.5  # 1日方向阈值 1.5%


def _direction_correct(signal: str, return_pct: float, threshold: float = DIRECTION_THRESHOLD) -> bool:
    """方向正确性判定。兼容中英文信号值。

    - 看多/bullish: 实际收益 > 0 即正确
    - 看空/bearish: 实际收益 < 0 即正确
    - 中性/neutral/观望: 收益在 [-threshold, threshold] 内正确
    """
    # 中英文信号映射
    _MAP = {'bullish': 'bullish', 'bearish': 'bearish', 'neutral': 'neutral',
            '观望': 'neutral', '看多': 'bullish', '看空': 'bearish', '中性': 'neutral'}
    sig = _MAP.get(signal, 'neutral')
    if sig == 'bullish':
        return return_pct > 0
    elif sig == 'bearish':
        return return_pct < 0
    else:
        return abs(return_pct) <= threshold


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _get_current_price(ticker: str, category: str, name: str, as_of_date: str = None, pred_date: str = None, price_cache: dict = None) -> float | None:
    """获取验证日收盘价。优先用批量缓存 price_cache，其次单点查询。"""
    if price_cache is not None:
        key = (ticker, as_of_date)
        if key in price_cache:
            return price_cache[key]
    try:
        if category == 'US':
            from core.us_data import get_us_price, get_us_stock_data
            df = get_us_stock_data(ticker, period='2y')
            if df is None or df.empty:
                return None
            latest_date = df.index[-1].date() if hasattr(df.index[-1], 'date') else df.index[-1]
            pred_d = pd.to_datetime(pred_date).date() if pred_date else None
            if pred_d and latest_date <= pred_d:
                return None
            return get_us_price(ticker, as_of_date=as_of_date)

        # A 股/期货统一用 get_stock_data
        df, _ = get_stock_data(ticker, period='10d', calibrate=False)
        if df is not None and not df.empty:
            latest_date = df.index[-1].date() if hasattr(df.index[-1], 'date') else df.index[-1]
            pred_d = pd.to_datetime(pred_date).date() if pred_date else None
            # 如果数据源已更新到验证日之后，直接返回最新收盘价
            if pred_d is None or latest_date > pred_d:
                return float(df['close'].iloc[-1])
            # 否则尝试用实时价格补充（腾讯实时行情）
            rt = get_realtime_price(ticker)
            if rt and rt.get('price') and rt.get('price') > 0:
                return float(rt['price'])
            return float(df['close'].iloc[-1])
    except Exception as e:
        print(f"  {ticker} 价格获取失败: {e}")
    return None


def _next_trading_date_us(date_str: str) -> str:
    """美股下一交易日：周五->周一，其他->+1。"""
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    wd = dt.weekday()
    delta = 1 if wd < 4 else (7 - wd)
    return (dt + timedelta(days=delta)).strftime('%Y-%m-%d')


def _next_trading_date_cn(date_str: str) -> str:
    """A股/期货下一交易日：周五->周一，周六/周日->周一，其他->+1。"""
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    wd = dt.weekday()
    if wd == 4:       # 周五 -> 周一
        delta = 3
    elif wd >= 5:     # 周六/周日 -> 下周一
        delta = 7 - wd
    else:
        delta = 1
    return (dt + timedelta(days=delta)).strftime('%Y-%m-%d')


def _next_trading_date_cn(date_str: str) -> str:
    """A股/期货下一交易日：周五->周一，周六/周日->周一，其他->+1。"""
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    wd = dt.weekday()
    if wd == 4:       # 周五 -> 周一
        delta = 3
    elif wd >= 5:     # 周六/周日 -> 下周一
        delta = 7 - wd
    else:
        delta = 1
    return (dt + timedelta(days=delta)).strftime('%Y-%m-%d')


def _validate_row(row: sqlite3.Row, pred_date: str, price_cache: dict = None) -> dict:
    pred_price = row['current_price']
    signal = row['signal']
    ticker = row['ticker']
    category = row['category']
    name = row['name']

    if category == 'US':
        validate_date = _next_trading_date_us(pred_date)
    elif category in ('个股', 'ETF', '期货'):
        validate_date = _next_trading_date_cn(pred_date)
    else:
        validate_date = None
    today_price = _get_current_price(ticker, category, name, as_of_date=validate_date, pred_date=pred_date, price_cache=price_cache)
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


def _build_price_cache(rows, pred_date):
    """从 warehouse.daily_bar 批量预加载下一交易日收盘价。"""
    cache = {}
    db_path = os.path.join(MULTI_AGENT, 'data', 'warehouse.db')
    if not os.path.exists(db_path):
        return cache

    # 计算每个 category 的验证日期
    dates_by_cat = {}
    for row in rows:
        cat = row['category']
        if cat not in dates_by_cat:
            if cat == 'US':
                dates_by_cat[cat] = _next_trading_date_us(pred_date)
            elif cat in ('个股', 'ETF', '期货'):
                dates_by_cat[cat] = _next_trading_date_cn(pred_date)
            else:
                dates_by_cat[cat] = None

    tickers_by_date = {}
    for row in rows:
        cat = row['category']
        vd = dates_by_cat.get(cat)
        if vd is None:
            continue
        tickers_by_date.setdefault(vd, set()).add(row['ticker'])

    try:
        conn_wh = sqlite3.connect(db_path)
        cur = conn_wh.cursor()
        for vd, tickers in tickers_by_date.items():
            placeholders = ','.join('?' * len(tickers))
            cur.execute(f"SELECT ticker, date, close FROM daily_bar WHERE date=? AND ticker IN ({placeholders})", (vd, *tickers))
            for ticker, date, close in cur.fetchall():
                if close is not None:
                    cache[(ticker, date)] = float(close)
        conn_wh.close()
    except Exception as e:
        print(f'  warehouse 批量缓存失败: {e}')
    return cache


def main():
    conn = _get_conn()
    latest = conn.execute("SELECT MAX(pred_date) FROM agentic_predictions").fetchone()[0]
    if not latest:
        print('❌ 无预测数据')
        sys.exit(1)

    # 呼出周末问题：验证日必须是已完成的交易日后的下一个完整交易日
    # 例如周六最新价格是周五收盘，应该验证周四的预测
    from datetime import datetime, timedelta
    latest_dt = datetime.strptime(latest, '%Y-%m-%d')
    wd = latest_dt.weekday()
    # 计算最新可用的下一交易日价格日期对应的验证日
    if wd == 5:         # 周六: 最新价格是周五，验证周四的预测
        pred_date = (latest_dt - timedelta(days=2)).strftime('%Y-%m-%d')
    elif wd == 6:       # 周日: 最新价格也是周五，验证周四的预测
        pred_date = (latest_dt - timedelta(days=3)).strftime('%Y-%m-%d')
    elif wd == 0:       # 周一上午: 最新价格也是周五，验证周四的预测
        pred_date = (latest_dt - timedelta(days=3)).strftime('%Y-%m-%d')
    else:
        pred_date = (latest_dt - timedelta(days=1)).strftime('%Y-%m-%d')
    # 如果选定的日期没有预测，逐日回退
    while True:
        exists = conn.execute(
            "SELECT COUNT(*) FROM agentic_predictions WHERE pred_date=?",
            (pred_date,)
        ).fetchone()[0]
        if exists > 0 or pred_date < (latest_dt - timedelta(days=7)).strftime('%Y-%m-%d'):
            break
        pred_date = (datetime.strptime(pred_date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')

    rows = conn.execute(
        "SELECT * FROM agentic_predictions WHERE pred_date=?",
        (pred_date,)
    ).fetchall()
    conn.close()

    print(f'[morning] 验证 {pred_date} 预测 vs 下一交易日收盘价，共 {len(rows)} 只...')

    price_cache = _build_price_cache(rows, pred_date)
    print(f'  从 warehouse 预加载 {len(price_cache)} 条价格')

    results = []
    for row in rows:
        r = _validate_row(row, pred_date, price_cache=price_cache)
        results.append(r)
        # 静默模式，只打印失败样本用于调试
        if r['note'] != 'ok':
            print(f"  {r['ticker']:8s} {r['signal']:8s} 无数据")

    # 统计
    by_category = {}
    by_signal = {}
    overall = {'total': 0, 'correct': 0}
    for r in results:
        if r['note'] != 'ok':
            continue
        cat = r['category']
        sig = r['signal']
        by_category.setdefault(cat, {'total': 0, 'correct': 0})
        by_category[cat]['total'] += 1
        by_signal.setdefault(sig, {'total': 0, 'correct': 0})
        by_signal[sig]['total'] += 1
        if r['direction_correct']:
            by_category[cat]['correct'] += 1
            by_signal[sig]['correct'] += 1
            overall['correct'] += 1
        overall['total'] += 1

    out = {
        'pred_date': pred_date,
        'validate_date': datetime.now().strftime('%Y-%m-%d'),
        'direction_threshold': DIRECTION_THRESHOLD,
        'overall': {
            'total': overall['total'],
            'correct': overall['correct'],
            'accuracy': round(overall['correct'] / max(overall['total'], 1) * 100, 2),
        },
        'by_category': {k: {'total': v['total'], 'correct': v['correct'], 'accuracy': round(v['correct']/max(v['total'],1)*100,2)} for k, v in by_category.items()},
        'by_signal': {k: {'total': v['total'], 'correct': v['correct'], 'accuracy': round(v['correct']/max(v['total'],1)*100,2)} for k, v in by_signal.items()},
        'items': results,
    }

    # 打印错误明细摘要
    wrong_items = [r for r in results if r['note'] == 'ok' and not r['direction_correct']][:20]
    print(f"\n  错误样本 (前20):")
    for r in wrong_items:
        print(f"  {r['ticker']:8s} {r['signal']:8s} 收益{r['return_pct']:+.2f}% | {r['category']} {r['name']}")

    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"\n[morning] 总体: {out['overall']['correct']}/{out['overall']['total']} = {out['overall']['accuracy']}%")
    for cat, stat in out['by_category'].items():
        print(f"  {cat}: {stat['correct']}/{stat['total']} = {stat['accuracy']}%")
    for sig, stat in out['by_signal'].items():
        print(f"  {sig}: {stat['correct']}/{stat['total']} = {stat['accuracy']}%")
    print(f"[morning] 详细结果保存到 {OUTPUT_PATH}")


if __name__ == '__main__':
    main()
