#!/usr/bin/env python3
"""基于 agentic_predictions 全库历史预测的真实回测。

对每个 pred_date 的每个预测，按 signal 计算未来 1d/3d/5d/10d 的实际收益。
数据源：get_stock_data / get_us_stock_data

核心输出：
- 按信号分组的未来收益统计（mean/median/win_rate/direction_accuracy）
- 每日推荐组合（只看多信号）的等权持有收益
- 最大回撤、夏普等基础指标
"""
import json
import os
import sys
from datetime import datetime

import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, MULTI_AGENT)

from core.data_layer import get_stock_data
from core.us_data import get_us_stock_data
from core.db import get_predictions_conn

OUTPUT_PATH = os.path.join(MULTI_AGENT, 'data', 'prediction_backtest.json')
HIST_PRICES_DIR = os.path.join(MULTI_AGENT, 'data', 'backtest_prices')
os.makedirs(HIST_PRICES_DIR, exist_ok=True)


def _load_predictions():
    conn = get_predictions_conn()
    try:
        df = pd.read_sql(
            "SELECT id, ticker, name, category, signal, pred_date, current_price, price_date, confidence, weighted_score "
            "FROM agentic_predictions WHERE pred_date IS NOT NULL AND current_price > 0 "
            "ORDER BY pred_date, ticker",
            conn
        )
    finally:
        conn.close()
    return df


def _price_cache_path(ticker):
    return os.path.join(HIST_PRICES_DIR, f'{ticker}.csv')


def _load_cached_price(ticker):
    p = _price_cache_path(ticker)
    if not os.path.exists(p):
        return None
    df = pd.read_csv(p)
    df['date'] = pd.to_datetime(df['date'])
    df.set_index('date', inplace=True)
    return df


def _cache_price(ticker, df):
    if df is None or df.empty:
        return
    p = _price_cache_path(ticker)
    df.to_csv(p)


def _get_price_df(ticker, category):
    cached = _load_cached_price(ticker)
    if cached is not None:
        return cached
    try:
        if category == 'US':
            df = get_us_stock_data(ticker, period='2y')
        else:
            df, _ = get_stock_data(ticker, period='2y', calibrate=False)
    except Exception:
        df = None
    if df is not None and not df.empty:
        df.index = pd.to_datetime(df.index)
        df = df[['close']].copy()
        _cache_price(ticker, df)
    return df


def _next_trading_date(df, start_date, n=1):
    if df is None or df.empty:
        return None
    dates = pd.to_datetime(df.index)
    start_dt = pd.to_datetime(start_date)
    valid = dates[dates > start_dt]
    if len(valid) < n:
        return None
    return valid[n - 1]


def _get_price_on_or_after(df, date):
    if df is None or df.empty:
        return None
    date = pd.to_datetime(date)
    if date in df.index:
        return float(df.loc[date, 'close'])
    future = df[df.index > date]
    if future.empty:
        return None
    return float(future.iloc[0]['close'])


def _compute_forward_return(ticker, category, pred_date, pred_price, horizon):
    df = _get_price_df(ticker, category)
    if df is None or df.empty:
        return None
    future_date = _next_trading_date(df, pred_date, n=horizon)
    if future_date is None:
        return None
    future_price = _get_price_on_or_after(df, future_date)
    if future_price is None or pred_price is None or pred_price == 0:
        return None
    return (future_price - pred_price) / pred_price


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


def backtest():
    df = _load_predictions()
    print(f'[bt] loaded {len(df)} predictions from {df["pred_date"].min()} to {df["pred_date"].max()}')

    records = []
    for _, row in df.iterrows():
        ticker = row['ticker']
        category = row['category']
        pred_date = row['pred_date']
        pred_price = row['current_price']
        for h in [1, 3, 5, 10]:
            ret = _compute_forward_return(ticker, category, pred_date, pred_price, h)
            records.append({
                'pred_date': pred_date,
                'ticker': ticker,
                'name': row['name'],
                'category': category,
                'signal': row['signal'],
                'signal_en': _signal_en(row['signal']),
                'confidence': row['confidence'],
                'weighted_score': row['weighted_score'],
                'entry_price': pred_price,
                'horizon': h,
                'forward_return': ret,
                'direction_correct': _direction_correct(row['signal'], ret) if ret is not None else None,
            })
    rdf = pd.DataFrame(records)

    # 按信号分组统计
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

    # 推荐组合：每日等权持有 bullish 信号
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
        'n_predictions': len(df),
        'n_records': len(rdf),
        'date_range': {
            'start': df['pred_date'].min(),
            'end': df['pred_date'].max(),
        },
        'summary': summary,
        'portfolio_summary': portfolio_summary,
    }
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f'[bt] saved {OUTPUT_PATH}')
    print(json.dumps({k: v for k, v in report.items() if k != 'records'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    backtest()
