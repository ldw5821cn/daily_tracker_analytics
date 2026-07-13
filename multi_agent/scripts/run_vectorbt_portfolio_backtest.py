"""用 vectorbt 跑组合回测。

输入: watchlist 价格矩阵 + 精选因子得分
输出: 多场景组合绩效指标
"""
from __future__ import annotations

import json
import os
import sys
from typing import Dict, List

import numpy as np
import pandas as pd
import vectorbt as vbt

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'multi_agent'))

from core.data_layer import get_stock_data, calc_technical_indicators
from strategy.factor_scoring import _load_selected_factors, _execute_factor_code_on_df

SELECTED_FACTORS_PATH = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'llm_factors_selected.json')
ALL_FACTORS_PATH = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'llm_factors.json')
OUTPUT_PATH = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'vectorbt_portfolio_backtest.json')


def _load_watchlist_tickers(limit: int = 50) -> List[str]:
    import sqlite3
    db = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'llm_predictions.db')
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    latest = conn.execute("SELECT MAX(pred_date) FROM agentic_predictions").fetchone()[0]
    rows = conn.execute(
        "SELECT ticker FROM agentic_predictions WHERE pred_date=? AND category IN ('个股','ETF') ORDER BY weighted_score DESC",
        (latest,)
    ).fetchall()
    conn.close()
    return [r[0] for r in rows][:limit]


def _load_price_data(tickers: List[str], min_days: int = 252) -> pd.DataFrame:
    frames = {}
    for t in tickers:
        try:
            df, _ = get_stock_data(t)
            df = calc_technical_indicators(df)
            if len(df) < min_days:
                continue
            frames[t] = df['close']
        except Exception:
            continue
    if not frames:
        return pd.DataFrame()
    price_df = pd.DataFrame(frames).dropna(axis=1, how='all').dropna(axis=0, how='all')
    return price_df.sort_index()


def _load_full_data_cache(tickers: List[str], min_days: int = 252) -> Dict[str, pd.DataFrame]:
    cache = {}
    for t in tickers:
        try:
            df, _ = get_stock_data(t)
            df = calc_technical_indicators(df)
            if len(df) < min_days:
                continue
            cache[t] = df
        except Exception:
            continue
    return cache


def _compute_scores_for_ticker(df: pd.DataFrame, factors: List[Dict]) -> pd.Series:
    votes = []
    weights = []
    for f in factors:
        try:
            signal = _execute_factor_code_on_df(f['code'], df)
            w = max(f.get('stability_score', 0), 0.01)
            votes.append(signal.values * w)
            weights.append(w)
        except Exception:
            continue
    if not votes:
        return pd.Series(np.zeros(len(df)), index=df.index)
    votes_arr = np.array(votes)
    total_w = sum(weights)
    scores = np.sum(votes_arr, axis=0) / total_w
    return pd.Series(scores, index=df.index)


def _build_daily_scores(price_df: pd.DataFrame, full_data_cache: Dict[str, pd.DataFrame],
                        factors: List[Dict]) -> pd.DataFrame:
    scores = pd.DataFrame(index=price_df.index, columns=price_df.columns, dtype=float)
    for t, df in full_data_cache.items():
        if t not in price_df.columns:
            continue
        daily_scores = _compute_scores_for_ticker(df, factors)
        scores[t] = daily_scores.reindex(price_df.index).values
    return scores


def _scores_to_weights(scores_df: pd.DataFrame, top_n: int = 10, long_only: bool = True,
                        rebalance_freq: str = 'W-FRI', max_position: float = 0.10) -> pd.DataFrame:
    """将每日得分转换为再平衡权重，只做多，降低换手。

    Args:
        rebalance_freq: 'W-FRI' 每周五再平衡，'M' 每月末再平衡，'D' 每日再平衡
        max_position: 单个标的上限
    """
    weights = pd.DataFrame(0.0, index=scores_df.index, columns=scores_df.columns)
    rebalance_dates = pd.date_range(start=scores_df.index[0], end=scores_df.index[-1], freq=rebalance_freq)
    rebalance_dates = rebalance_dates.normalize()

    active_weights = pd.Series(0.0, index=scores_df.columns)
    for idx in scores_df.index:
        if idx.normalize() in rebalance_dates or idx == scores_df.index[0]:
            row = scores_df.loc[idx].dropna()
            if len(row) < top_n:
                continue
            longs = row.nlargest(top_n).index.tolist()
            w = 1.0 / top_n
            active_weights = pd.Series(0.0, index=scores_df.columns)
            active_weights[longs] = w
            # 单标上限约束
            active_weights = active_weights.clip(upper=max_position)
            active_weights = active_weights / active_weights.sum() if active_weights.sum() > 0 else active_weights
        weights.loc[idx] = active_weights.values
    return weights


def _build_risk_managed_weights(price_df: pd.DataFrame, scores_df: pd.DataFrame, top_n: int = 10,
                                rebalance_freq: str = 'W-FRI', max_position: float = 0.10,
                                stop_loss: float = 0.08) -> pd.DataFrame:
    """在每周再平衡基础上加入个股止损：
    - 持仓标的若从最近再平衡日最高价回撤 stop_loss 则清仓
    - 组合净值从高点回撤 15% 时全部清仓（现金避险）
    """
    weights = pd.DataFrame(0.0, index=scores_df.index, columns=scores_df.columns)
    rebalance_dates = pd.date_range(start=scores_df.index[0], end=scores_df.index[-1], freq=rebalance_freq)
    rebalance_dates = rebalance_dates.normalize()

    active_weights = pd.Series(0.0, index=scores_df.columns)
    last_rebalance_high = pd.Series(np.nan, index=scores_df.columns)
    portfolio_value = 1.0
    portfolio_high = 1.0
    cash_weight = 1.0

    for idx in scores_df.index:
        if idx.normalize() in rebalance_dates or idx == scores_df.index[0]:
            row = scores_df.loc[idx].dropna()
            if len(row) >= top_n:
                longs = row.nlargest(top_n).index.tolist()
                w = 1.0 / top_n
                active_weights = pd.Series(0.0, index=scores_df.columns)
                active_weights[longs] = w
                active_weights = active_weights.clip(upper=max_position)
                total = active_weights.sum()
                active_weights = active_weights / total if total > 0 else active_weights
                last_rebalance_high = pd.Series(np.nan, index=scores_df.columns)
                for t in active_weights.index:
                    if active_weights[t] > 0 and t in price_df.columns and not np.isnan(price_df.loc[idx, t]):
                        last_rebalance_high[t] = price_df.loc[idx, t]
            cash_weight = 0.0

        # 个股止损：从最近再平衡最高价回撤 stop_loss
        for t in active_weights.index:
            if active_weights[t] > 0 and not np.isnan(last_rebalance_high.get(t, np.nan)) and t in price_df.columns:
                price = price_df.loc[idx, t]
                if not np.isnan(price):
                    last_rebalance_high[t] = max(last_rebalance_high[t], price)
                    if price <= last_rebalance_high[t] * (1 - stop_loss):
                        active_weights[t] = 0.0
        # 归一化
        if active_weights.sum() > 0:
            active_weights = active_weights / active_weights.sum()
        else:
            active_weights = pd.Series(0.0, index=scores_df.columns)

        # 组合回撤风控：从高点回撤 15% 全部转现金
        if active_weights.sum() > 0 and idx > scores_df.index[0]:
            # 用昨日持仓权重 * 今日收益 近似估算净值变化
            prev_idx = scores_df.index[scores_df.index.get_loc(idx) - 1]
            ret = 0.0
            for t in active_weights.index:
                if t in price_df.columns and not np.isnan(price_df.loc[idx, t]) and not np.isnan(price_df.loc[prev_idx, t]):
                    ret += active_weights[t] * (price_df.loc[idx, t] / price_df.loc[prev_idx, t] - 1)
            portfolio_value = portfolio_value * (1 + ret)
            portfolio_high = max(portfolio_high, portfolio_value)
            if portfolio_value < portfolio_high * 0.85:
                active_weights = pd.Series(0.0, index=scores_df.columns)
                cash_weight = 1.0

        weights.loc[idx] = active_weights.values

    return weights


def _run_vectorbt_backtest(price_df: pd.DataFrame, weights_df: pd.DataFrame,
                           freq: str = '1d', fees: float = 0.0) -> Dict:
    """用 vectorbt.Portfolio.from_orders 跑回测：size_type='targetpercent' 实现目标权重再平衡。"""
    # 对齐价格与权重
    aligned_prices, aligned_weights = price_df.align(weights_df, join='inner', axis=0)
    aligned_weights = aligned_weights.reindex(columns=aligned_prices.columns).fillna(0)

    portfolio = vbt.Portfolio.from_orders(
        aligned_prices,
        size=aligned_weights,
        size_type='targetpercent',
        direction='longonly',
        fees=fees,
        slippage=0.0,
        init_cash=100000.0,
        cash_sharing=True,
        freq=freq,
    )

    total_return = portfolio.total_return()
    ann_return = portfolio.annualized_return()
    ann_vol = portfolio.annualized_volatility()
    sharpe = portfolio.sharpe_ratio()
    max_dd = portfolio.max_drawdown()
    calmar = portfolio.calmar_ratio()
    trades = portfolio.trades.count()

    return {
        'total_return': round(float(total_return) * 100, 2),
        'annualized_return': round(float(ann_return) * 100, 2),
        'annualized_volatility': round(float(ann_vol) * 100, 2),
        'sharpe_ratio': round(float(sharpe), 2),
        'max_drawdown': round(float(max_dd) * 100, 2),
        'calmar_ratio': round(float(calmar) if calmar and not np.isnan(calmar) else 0, 2),
        'num_trades': int(trades),
        'final_value': round(float(portfolio.final_value()), 2),
    }


def run_vectorbt_backtest(top_n: int = 10, max_tickers: int = 50, transaction_cost: float = 0.001):
    tickers = _load_watchlist_tickers(limit=max_tickers)
    print(f"[vectorbt] 加载 {len(tickers)} 个标的...")
    price_df = _load_price_data(tickers, min_days=252)
    if price_df.empty:
        return {'error': 'no price data'}
    print(f"[vectorbt] 价格矩阵: {price_df.shape}")

    full_data_cache = _load_full_data_cache(tickers, min_days=252)
    print(f"[vectorbt] 完整数据缓存: {len(full_data_cache)} 个标的")

    factors = _load_selected_factors()
    if not factors:
        try:
            with open(ALL_FACTORS_PATH, 'r', encoding='utf-8') as f:
                factors = json.load(f).get('factors', [])
        except Exception:
            pass
    if not factors:
        return {'error': 'no factors'}
    print(f"[vectorbt] 使用 {len(factors)} 个因子")

    scores_df = _build_daily_scores(price_df, full_data_cache, factors)

    scenarios = {}

    # 1. 基准：每日再平衡 多空
    weights = _scores_to_weights(scores_df, top_n=top_n, long_only=False, rebalance_freq='D')
    for cost_label, fees in [('no_cost', 0.0), ('with_cost', transaction_cost)]:
        key = f"daily_long_short_{cost_label}"
        stats = _run_vectorbt_backtest(price_df, weights, freq='1d', fees=fees)
        stats['top_n'] = top_n
        stats['long_only'] = False
        stats['transaction_cost'] = fees
        scenarios[key] = stats
        print(f"  [{key}] 年化{stats['annualized_return']:+.2f}% 回撤{stats['max_drawdown']:.2f}% 夏普{stats['sharpe_ratio']}")

    # 2. 只做多 + 每周再平衡
    weights = _scores_to_weights(scores_df, top_n=top_n, long_only=True, rebalance_freq='W-FRI')
    for cost_label, fees in [('no_cost', 0.0), ('with_cost', transaction_cost)]:
        key = f"weekly_long_only_{cost_label}"
        stats = _run_vectorbt_backtest(price_df, weights, freq='1d', fees=fees)
        stats['top_n'] = top_n
        stats['long_only'] = True
        stats['transaction_cost'] = fees
        scenarios[key] = stats
        print(f"  [{key}] 年化{stats['annualized_return']:+.2f}% 回撤{stats['max_drawdown']:.2f}% 夏普{stats['sharpe_ratio']}")

    # 3. 只做多 + 每周再平衡 + 8% 个股止损 + 15% 组合回撤风控
    weights = _build_risk_managed_weights(price_df, scores_df, top_n=top_n,
                                           rebalance_freq='W-FRI', stop_loss=0.08)
    for cost_label, fees in [('no_cost', 0.0), ('with_cost', transaction_cost)]:
        key = f"weekly_long_only_risk_{cost_label}"
        stats = _run_vectorbt_backtest(price_df, weights, freq='1d', fees=fees)
        stats['top_n'] = top_n
        stats['long_only'] = True
        stats['transaction_cost'] = fees
        scenarios[key] = stats
        print(f"  [{key}] 年化{stats['annualized_return']:+.2f}% 回撤{stats['max_drawdown']:.2f}% 夏普{stats['sharpe_ratio']}")

    output = {
        'tickers': price_df.columns.tolist(),
        'top_n': top_n,
        'transaction_cost': transaction_cost,
        'scenarios': scenarios,
        'best_scenario': max(scenarios, key=lambda k: scenarios[k]['sharpe_ratio']),
    }

    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"[vectorbt] 结果已保存: {OUTPUT_PATH}")
    return output


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--top-n', type=int, default=10)
    parser.add_argument('--max-tickers', type=int, default=50)
    parser.add_argument('--transaction-cost', type=float, default=0.001)
    args = parser.parse_args()

    stats = run_vectorbt_backtest(
        top_n=args.top_n,
        max_tickers=args.max_tickers,
        transaction_cost=args.transaction_cost,
    )
    print("\n✅ vectorbt 组合回测对比")
    for k, v in stats.get('scenarios', {}).items():
        print(f"   {k}: 年化{v['annualized_return']:+.2f}% 回撤{v['max_drawdown']:.2f}% 夏普{v['sharpe_ratio']}")
