#!/usr/bin/env python3
"""基于 agentic_predictions 全库历史预测的真实回测（warehouse 数据源）。"""
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from collections import defaultdict

import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, MULTI_AGENT)

from core.warehouse import get_warehouse_conn
from core.db import get_predictions_conn

OUTPUT_PATH = os.path.join(MULTI_AGENT, 'data', 'prediction_backtest.json')
LEGACY_CACHE_DIR = os.path.join(MULTI_AGENT, 'data', 'backtest_prices')


def _load_predictions():
    conn = get_predictions_conn()
    try:
        df = pd.read_sql(
            "SELECT id, ticker, name, category, signal, pred_date, current_price, price_date, "
            "confidence, weighted_score, target_price, stop_loss "
            "FROM agentic_predictions WHERE pred_date IS NOT NULL AND current_price > 0 "
            "ORDER BY pred_date, ticker",
            conn
        )
    finally:
        conn.close()
    return df


def _load_warehouse_prices():
    conn = get_warehouse_conn()
    try:
        rows = conn.execute(
            "SELECT date, ticker, open, high, low, close, category FROM daily_bar ORDER BY ticker, date"
        ).fetchall()
    finally:
        conn.close()
    bars = defaultdict(list)
    for r in rows:
        bars[r['ticker']].append((r['date'], r['open'], r['high'], r['low'], r['close'], r['category']))
    return bars


def _build_return_map(bars, cost_by_category=None):
    """构建 horizon forward return map，并扣除交易成本。

    cost_by_category: 不同资产类别的双边交易成本（默认 A股0.20%、ETF0.13%、期货0.10%、US0.08%）。
    """
    if cost_by_category is None:
        cost_by_category = {
            "个股": 0.0020,
            "ETF": 0.0013,
            "期货": 0.0010,
            "US": 0.0008,
            "futures": 0.0010,
        }
    ret_map = {}
    for tk, seq in bars.items():
        dates = [s[0] for s in seq]
        closes = [s[4] for s in seq]
        cat = seq[0][5] if seq else "个股"
        cost = cost_by_category.get(cat, cost_by_category.get("个股", 0.0020))
        for i, d in enumerate(dates):
            for h in [1, 3, 5, 10]:
                if i + h < len(dates) and closes[i] and closes[i + h]:
                    raw_ret = (closes[i + h] - closes[i]) / closes[i]
                    ret_map[(h, d, tk)] = raw_ret - cost
    return ret_map


def _build_bar_map(bars):
    """把 warehouse 日线序列映射为 (ticker, date) -> {open, high, low, close}。"""
    bar_map = {}
    for tk, seq in bars.items():
        for s in seq:
            d, o, h, l, c, _ = s
            bar_map[(tk, d)] = {'open': o, 'high': h, 'low': l, 'close': c}
    return bar_map


def _forward_bars(bar_map, ticker, pred_date, horizon):
    """取出 pred_date 之后 horizon 个交易日的 OHLC 序列。"""
    seq = sorted((d, bar_map[(ticker, d)]) for d, _ in bar_map if _[0] == ticker and d > pred_date)
    bars = []
    for d, b in seq:
        if all(v is not None for v in (b['open'], b['high'], b['low'], b['close'])):
            bars.append({'date': d, 'open': b['open'], 'high': b['high'], 'low': b['low'], 'close': b['close']})
    return bars[:horizon]


def _signal_en(signal):
    if signal in ('看多', 'bullish'):
        return 'bullish'
    if signal in ('看空', 'bearish'):
        return 'bearish'
    return 'neutral'


def _direction_correct(signal, ret):
    s = _signal_en(signal)
    if s == 'bullish':
        return ret > 0
    if s == 'bearish':
        return ret < 0
    return abs(ret) <= 0.015


def _evaluate_targets(signal_en, entry_price, stop_loss, take_profit, forward_bars, neutral_band_pct=0.015):
    """基于 forward K 线评估止盈止损首触结果与模拟收益。

    支持 bullish/bearish/neutral 三种信号：
    - bullish: 低点<=stop 止损，高点>=target 止盈；否则持有到最后一根 K 线。
    - bearish: 反向做空，高点>=stop 止损，低点<=target 止盈。
    - neutral: 用 entry*(1±band) 作为上下边界，超出即视为"打破区间"。
    """
    result = {
        'hit_stop_loss': None,
        'hit_take_profit': None,
        'first_hit': None,
        'first_hit_date': None,
        'first_hit_trading_days': None,
        'simulated_exit_price': None,
        'simulated_return_pct': None,
    }
    if signal_en not in ('bullish', 'bearish', 'neutral') or not forward_bars:
        return result
    if entry_price is None or entry_price <= 0:
        return result

    # A-share T+1: forward_bars[0] is the prediction-day bar, real entry is next open (bars[1]).
    # We approximate by using forward_bars[1] open if available, otherwise close.
    entry = forward_bars[1].get('open') if len(forward_bars) > 1 else None
    if entry is None or entry <= 0:
        entry = forward_bars[0].get('open') or forward_bars[0].get('close') or entry_price
    if entry is None or entry <= 0:
        entry = entry_price

    # Apply bilateral trading cost depending on signal direction.
    # cost covers commission + tax + slippage for round trip.
    cost = 0.0020  # default A-share individual stock
    # Note: simulated_return_pct currently does not know category; keep default.
    # For bearish short, cost is symmetric.

    if signal_en == 'neutral':
        upper = entry * (1 + neutral_band_pct)
        lower = entry * (1 - neutral_band_pct)
        for i, bar in enumerate(forward_bars):
            if i == 0:
                continue
            high = bar.get('high')
            low = bar.get('low')
            if low is None or high is None:
                continue
            hit_upper = high >= upper
            hit_lower = low <= lower
            if hit_upper and hit_lower:
                result['hit_stop_loss'] = True
                result['hit_take_profit'] = True
                result['first_hit'] = 'ambiguous'
                result['first_hit_date'] = bar.get('date')
                result['first_hit_trading_days'] = i
                result['simulated_exit_price'] = entry
                break
            if hit_upper:
                result['hit_stop_loss'] = False
                result['hit_take_profit'] = True
                result['first_hit'] = 'take_profit'
                result['first_hit_date'] = bar.get('date')
                result['first_hit_trading_days'] = i
                result['simulated_exit_price'] = upper
                break
            if hit_lower:
                result['hit_stop_loss'] = True
                result['hit_take_profit'] = False
                result['first_hit'] = 'stop_loss'
                result['first_hit_date'] = bar.get('date')
                result['first_hit_trading_days'] = i
                result['simulated_exit_price'] = lower
                break
        else:
            last_close = forward_bars[-1].get('close')
            if last_close is not None:
                result['hit_stop_loss'] = False
                result['hit_take_profit'] = False
                result['first_hit'] = 'none'
                result['first_hit_date'] = forward_bars[-1].get('date')
                result['first_hit_trading_days'] = len(forward_bars)
                result['simulated_exit_price'] = last_close
        if result['simulated_exit_price'] is not None:
            raw = (result['simulated_exit_price'] - entry) / entry
            result['simulated_return_pct'] = (raw - cost) * 100
        return result

    # bullish / bearish
    if signal_en == 'bullish':
        sl = stop_loss if stop_loss and stop_loss > 0 else entry * 0.95
        tp = take_profit if take_profit and take_profit > 0 else entry * 1.10
    else:
        # bearish: 做空，止损在 entry 上方，止盈在 entry 下方
        sl = stop_loss if stop_loss and stop_loss > 0 else entry * 1.05
        tp = take_profit if take_profit and take_profit > 0 else entry * 0.90

    for i, bar in enumerate(forward_bars):
        if i == 0:
            continue
        low = bar.get('low')
        high = bar.get('high')
        if low is None or high is None:
            continue
        if signal_en == 'bullish':
            hit_sl = low <= sl
            hit_tp = high >= tp
        else:
            hit_sl = high >= sl
            hit_tp = low <= tp
        if hit_sl and hit_tp:
            result['hit_stop_loss'] = True
            result['hit_take_profit'] = True
            result['first_hit'] = 'ambiguous'
            result['first_hit_date'] = bar.get('date')
            result['first_hit_trading_days'] = i
            result['simulated_exit_price'] = sl
            break
        if hit_sl:
            result['hit_stop_loss'] = True
            result['hit_take_profit'] = False
            result['first_hit'] = 'stop_loss'
            result['first_hit_date'] = bar.get('date')
            result['first_hit_trading_days'] = i
            result['simulated_exit_price'] = sl
            break
        if hit_tp:
            result['hit_stop_loss'] = False
            result['hit_take_profit'] = True
            result['first_hit'] = 'take_profit'
            result['first_hit_date'] = bar.get('date')
            result['first_hit_trading_days'] = i
            result['simulated_exit_price'] = tp
            break
    else:
        last_close = forward_bars[-1].get('close')
        if last_close is not None:
            result['hit_stop_loss'] = False
            result['hit_take_profit'] = False
            result['first_hit'] = 'none'
            result['first_hit_date'] = forward_bars[-1].get('date')
            result['first_hit_trading_days'] = len(forward_bars)
            result['simulated_exit_price'] = last_close

    if result['simulated_exit_price'] is not None:
        raw = (result['simulated_exit_price'] - entry) / entry
        result['simulated_return_pct'] = (raw - cost) * 100
    return result


def backtest():
    df = _load_predictions()
    if df.empty:
        print('[bt] agentic_predictions is empty, writing empty report')
        report = {
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'data_source': 'warehouse.daily_bar',
            'n_predictions': 0,
            'n_records': 0,
            'date_range': {'start': None, 'end': None},
            'summary': {},
            'portfolio_summary': {},
            'records': [],
        }
        with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f'[bt] saved empty {OUTPUT_PATH}')
        return
    print(f'[bt] loaded {len(df)} predictions from {df["pred_date"].min()} to {df["pred_date"].max()}')

    bars = _load_warehouse_prices()
    ret_map = _build_return_map(bars)
    bar_map = _build_bar_map(bars)
    print(f'[bt] loaded {len(bars)} tickers from warehouse, return map size={len(ret_map)}')

    records = []
    for _, row in df.iterrows():
        ticker = row['ticker']
        pred_date = row['pred_date']
        signal_en = _signal_en(row['signal'])
        # 预取该预测之后最长 horizon 的 forward bars，供各 horizon 复用
        max_h = 10
        fb = _forward_bars(bar_map, ticker, pred_date, max_h)
        target_eval = _evaluate_targets(
            signal_en, row['current_price'], row['stop_loss'], row['target_price'], fb
        )
        for h in [1, 3, 5, 10]:
            ret = ret_map.get((h, pred_date, ticker))
            rec = {
                'pred_date': pred_date,
                'ticker': ticker,
                'name': row['name'],
                'category': row['category'],
                'signal': row['signal'],
                'signal_en': signal_en,
                'confidence': row['confidence'],
                'weighted_score': row['weighted_score'],
                'entry_price': row['current_price'],
                'target_price': row['target_price'],
                'stop_loss': row['stop_loss'],
                'horizon': h,
                'forward_return': ret,
                'direction_correct': _direction_correct(row['signal'], ret) if ret is not None else None,
                'hit_stop_loss': target_eval['hit_stop_loss'],
                'hit_take_profit': target_eval['hit_take_profit'],
                'first_hit': target_eval['first_hit'],
                'first_hit_date': target_eval['first_hit_date'],
                'first_hit_trading_days': target_eval['first_hit_trading_days'],
                'simulated_exit_price': target_eval['simulated_exit_price'],
                'simulated_return_pct': target_eval['simulated_return_pct'],
            }
            records.append(rec)
    rdf = pd.DataFrame(records)

    summary = {}
    for h in [1, 3, 5, 10]:
        sub = rdf[(rdf['horizon'] == h) & (rdf['forward_return'].notna())].copy()
        if sub.empty:
            continue
        by_signal = {}
        for sig, g in sub.groupby('signal_en'):
            by_signal[sig] = {
                'count': len(g),
                'mean_return': round(g['forward_return'].mean() * 100, 3),
                'median_return': round(g['forward_return'].median() * 100, 3),
                'std': round(g['forward_return'].std() * 100, 3),
                'win_rate': round((g['forward_return'] > 0).sum() / len(g) * 100, 2),
                'direction_accuracy': round(g['direction_correct'].sum() / len(g) * 100, 2),
            }
        by_category = {}
        for cat, g in sub.groupby('category'):
            by_category[cat] = {
                'count': len(g),
                'mean_return': round(g['forward_return'].mean() * 100, 3),
                'win_rate': round((g['forward_return'] > 0).sum() / len(g) * 100, 2),
                'direction_accuracy': round(g['direction_correct'].sum() / len(g) * 100, 2),
            }
        summary[f'{h}d'] = {
            'total': len(sub),
            'overall_mean_return': round(sub['forward_return'].mean() * 100, 3),
            'overall_median_return': round(sub['forward_return'].median() * 100, 3),
            'overall_win_rate': round((sub['forward_return'] > 0).sum() / len(sub) * 100, 2),
            'overall_direction_accuracy': round(sub['direction_correct'].sum() / len(sub) * 100, 2),
            'by_signal': by_signal,
            'by_category': by_category,
        }

    portfolio = []
    for pred_date, g in rdf[rdf['signal_en'] == 'bullish'].groupby('pred_date'):
        for h in [1, 3, 5, 10]:
            sub = g[(g['horizon'] == h) & (g['forward_return'].notna())]
            if len(sub) == 0:
                continue
            portfolio.append({
                'pred_date': pred_date,
                'horizon': h,
                'avg_return': round(sub['forward_return'].mean() * 100, 3),
                'n_positions': len(sub),
                'mean_confidence': round(sub['confidence'].mean(), 3) if sub['confidence'].notna().any() else None,
            })
    port_df = pd.DataFrame(portfolio)
    portfolio_summary = {}
    if not port_df.empty:
        for h in [1, 3, 5, 10]:
            sub = port_df[port_df['horizon'] == h]
            if sub.empty:
                continue
            returns = sub['avg_return'].dropna()
            portfolio_summary[f'{h}d'] = {
                'n_days': len(sub),
                'mean_return': round(returns.mean(), 3),
                'median_return': round(returns.median(), 3),
                'std': round(returns.std(), 3),
                'win_rate': round((returns > 0).sum() / len(returns) * 100, 2),
                'cumulative_return': round(returns.sum(), 3),
                'max_drawdown': round((returns.cumsum() - returns.cumsum().cummax()).min(), 3),
                'sharpe': round(returns.mean() / (returns.std() + 1e-9) * (252 / len(returns)) ** 0.5, 3) if returns.std() > 0 else 0,
            }

    report = {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'data_source': 'warehouse.daily_bar',
        'n_predictions': len(df),
        'n_records': len(rdf),
        'date_range': {
            'start': df['pred_date'].min(),
            'end': df['pred_date'].max(),
        },
        'summary': summary,
        'portfolio_summary': portfolio_summary,
        'records': rdf[rdf['forward_return'].notna()].to_dict('records'),
    }
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f'[bt] saved {OUTPUT_PATH}')
    print(json.dumps({k: v for k, v in report.items() if k != 'records'}, ensure_ascii=False, indent=2))

    if os.path.exists(LEGACY_CACHE_DIR):
        try:
            shutil.rmtree(LEGACY_CACHE_DIR)
            print(f'[bt] removed legacy cache {LEGACY_CACHE_DIR}')
        except Exception as e:
            print(f'[bt] warning: could not remove legacy cache: {e}')


if __name__ == '__main__':
    backtest()
