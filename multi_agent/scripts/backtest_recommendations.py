#!/usr/bin/env python3
import json
import os
import sys
from datetime import datetime

import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, MULTI_AGENT)

from core.data_layer import get_stock_data
from core.us_data import get_us_stock_data

HIST_DIR = os.path.join(MULTI_AGENT, 'data', 'recommendation_history')
OUTPUT_PATH = os.path.join(MULTI_AGENT, 'data', 'recommendation_backtest.json')


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


def _load_history():
    files = []
    if not os.path.exists(HIST_DIR):
        return files
    for f in sorted(os.listdir(HIST_DIR)):
        if f.endswith('.json') and f != 'index.json':
            files.append(os.path.join(HIST_DIR, f))
    return files


def _compute_forward_returns(ticker, category, pred_date, pred_price, horizons=[1, 3, 5, 10]):
    try:
        if category == 'US':
            df = get_us_stock_data(ticker, period='2y')
        else:
            df, _ = get_stock_data(ticker, period='2y', calibrate=False)
        if df is None or df.empty or len(df) < 5:
            return {}, 'no_data'
        df.index = pd.to_datetime(df.index)
        pred_dt = pd.to_datetime(pred_date)
        result = {}
        for h in horizons:
            future_date = _next_trading_date(df, pred_dt, n=h)
            if future_date is None:
                result[h] = None
                continue
            future_price = _get_price_on_or_after(df, future_date)
            if future_price is None or pred_price is None or pred_price == 0:
                result[h] = None
                continue
            result[h] = (future_price - pred_price) / pred_price
        return result, 'ok'
    except Exception as e:
        return {}, 'error: ' + str(e)


def backtest_all():
    files = _load_history()
    if not files:
        print('[bt] no history')
        return
    records = []
    for path in files:
        with open(path, 'r', encoding='utf-8') as f:
            rec = json.load(f)
        pred_date = rec.get('pred_date')
        for side in ['longs', 'shorts_or_avoids']:
            for item in rec.get(side, []):
                ticker = item['ticker']
                category = item['category']
                price = item.get('price')
                fr, status = _compute_forward_returns(ticker, category, pred_date, price)
                records.append({
                    'pred_date': pred_date,
                    'ticker': ticker,
                    'name': item.get('name'),
                    'category': category,
                    'signal': item.get('signal'),
                    'side': 'long' if side == 'longs' else 'short/avoid',
                    'entry_price': price,
                    'forward_return_1d': fr.get(1),
                    'forward_return_3d': fr.get(3),
                    'forward_return_5d': fr.get(5),
                    'forward_return_10d': fr.get(10),
                    'status': status,
                })
    df = pd.DataFrame(records)

    summary = {}
    for horizon in [1, 3, 5, 10]:
        col = 'forward_return_' + str(horizon) + 'd'
        valid = df[df[col].notna()].copy()
        if valid.empty:
            continue
        by_signal = {}
        for (signal, side), g in valid.groupby(['signal', 'side']):
            by_signal[signal + '_' + side] = {
                'count': len(g),
                'mean_return': round(g[col].mean() * 100, 3),
                'median_return': round(g[col].median() * 100, 3),
                'win_rate': round((g[col] > 0).sum() / len(g) * 100, 2),
                'direction_accuracy': round(
                    (((g['signal'] == '看多') | (g['signal'] == 'bullish')) & (g[col] > 0) |
                     ((g['signal'] == '看空') | (g['signal'] == 'bearish')) & (g[col] < 0)).sum() / len(g) * 100, 2
                ),
            }
        by_category = {}
        for cat, g in valid.groupby('category'):
            by_category[cat] = {
                'count': len(g),
                'mean_return': round(g[col].mean() * 100, 3),
                'win_rate': round((g[col] > 0).sum() / len(g) * 100, 2),
            }
        summary[str(horizon) + 'd'] = {
            'total': len(valid),
            'overall_mean_return': round(valid[col].mean() * 100, 3),
            'overall_win_rate': round((valid[col] > 0).sum() / len(valid) * 100, 2),
            'by_signal': by_signal,
            'by_category': by_category,
        }

    portfolio_returns = []
    for pred_date, g in df[df['side'] == 'long'].groupby('pred_date'):
        for horizon in [1, 3, 5, 10]:
            col = 'forward_return_' + str(horizon) + 'd'
            valid = g[g[col].notna()]
            if len(valid) == 0:
                continue
            avg_ret = valid[col].mean()
            portfolio_returns.append({
                'pred_date': pred_date,
                'horizon': horizon,
                'avg_return': round(avg_ret * 100, 3),
                'n_positions': len(valid),
            })
    portfolio_df = pd.DataFrame(portfolio_returns)
    portfolio_summary = {}
    if not portfolio_df.empty:
        for horizon in [1, 3, 5, 10]:
            sub = portfolio_df[portfolio_df['horizon'] == horizon]
            if sub.empty:
                continue
            portfolio_summary[str(horizon) + 'd'] = {
                'mean_return': round(sub['avg_return'].mean(), 3),
                'median_return': round(sub['avg_return'].median(), 3),
                'win_rate': round((sub['avg_return'] > 0).sum() / len(sub) * 100, 2),
                'cumulative_return': round(sub['avg_return'].sum(), 3),
                'n_days': len(sub),
            }

    report = {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'n_records': len(df),
        'n_files': len(files),
        'summary': summary,
        'portfolio_summary': portfolio_summary,
        'records': records,
    }
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print('[bt] generated ' + OUTPUT_PATH)
    print(json.dumps({k: v for k, v in report.items() if k != 'records'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    backtest_all()

