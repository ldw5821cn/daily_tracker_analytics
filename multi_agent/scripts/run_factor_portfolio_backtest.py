"""组合级滚动回测：用精选因子得分构建多空/只做多组合。

支持：
- 多空 / 只做多
- 交易成本（默认双边 0.1%）
- 滚动训练因子精选（按年滚动，用过去一年数据重新选因子）
- 输出三种配置的结果对比
"""
from __future__ import annotations

import json
import os
import sys
from typing import Dict, List

import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'multi_agent'))

from core.data_layer import get_stock_data, calc_technical_indicators
from strategy.factor_scoring import _load_selected_factors, _execute_factor_code_on_df

SELECTED_FACTORS_PATH = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'llm_factors_selected.json')
ALL_FACTORS_PATH = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'llm_factors.json')
OUTPUT_PATH = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'factor_portfolio_backtest.json')


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


def _load_full_data_cache(tickers: List[str], min_days: int = 252) -> Dict[str, pd.DataFrame]:
    """加载所有标的完整 OHLCV 数据并缓存。"""
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
    price_df = price_df.sort_index()
    return price_df


def _compute_scores_for_ticker(df: pd.DataFrame, factors: List[Dict]) -> np.ndarray:
    """为单只标的计算加权因子得分。"""
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
        return np.zeros(len(df))
    votes_arr = np.array(votes)
    total_w = sum(weights)
    return np.sum(votes_arr, axis=0) / total_w


def _compute_daily_scores(price_df: pd.DataFrame, full_data_cache: Dict[str, pd.DataFrame],
                          factors: List[Dict]) -> pd.DataFrame:
    """基于完整数据缓存，为所有标的计算每日因子得分。"""
    scores = pd.DataFrame(index=price_df.index, columns=price_df.columns, dtype=float)
    for t, df in full_data_cache.items():
        if t not in price_df.columns:
            continue
        daily_scores = _compute_scores_for_ticker(df, factors)
        s = pd.Series(daily_scores, index=df.index).reindex(price_df.index)
        scores[t] = s.values
    return scores


def _select_factors_rolling(price_df: pd.DataFrame, full_data_cache: Dict[str, pd.DataFrame],
                            all_factors: List[Dict], current_date: pd.Timestamp,
                            lookback_days: int = 252, top_n: int = 15) -> List[Dict]:
    """基于过去一年数据，用缓存价格矩阵选出 long-only 平均收益最高的因子。"""
    start_date = current_date - pd.Timedelta(days=lookback_days)
    window_df = price_df.loc[(price_df.index >= start_date) & (price_df.index <= current_date)]
    if len(window_df) < 60:
        return all_factors[:top_n]

    factor_returns = {f['name']: [] for f in all_factors}
    for t, df in full_data_cache.items():
        if t not in window_df.columns:
            continue
        df = df.loc[(df.index >= start_date) & (df.index <= current_date)]
        if len(df) < 60:
            continue
        ret = df['close'].pct_change().shift(-1)
        for f in all_factors:
            try:
                signal = _execute_factor_code_on_df(f['code'], df)
                aligned = pd.DataFrame({'signal': signal, 'returns': ret}).dropna()
                if len(aligned) < 30:
                    continue
                long_ret = aligned[aligned['signal'] > 0]['returns'].mean()
                if not np.isnan(long_ret):
                    factor_returns[f['name']].append(long_ret)
            except Exception:
                continue

    factor_scores = []
    for f in all_factors:
        rets = factor_returns[f['name']]
        if not rets:
            continue
        avg_ret = np.mean(rets)
        factor_scores.append((avg_ret, f))
    factor_scores.sort(key=lambda x: x[0], reverse=True)
    return [f for _, f in factor_scores[:top_n]]


def backtest_portfolio(price_df: pd.DataFrame, scores_df: pd.DataFrame, top_n: int = 10,
                       rebalance_freq: int = 5, long_only: bool = False,
                       transaction_cost: float = 0.0) -> Dict:
    returns = price_df.pct_change().shift(-1)
    portfolio_value = 1.0
    max_value = 1.0
    max_dd = 0.0
    daily_rets = []
    portfolio_values = []
    positions_history = []
    prev_positions = set()

    for i in range(0, len(price_df) - 1, rebalance_freq):
        date = price_df.index[i]
        scores = scores_df.loc[date].dropna()
        if len(scores) < top_n * (1 if long_only else 2):
            continue
        longs = scores.nlargest(top_n).index.tolist()
        shorts = [] if long_only else scores.nsmallest(top_n).index.tolist()

        current_positions = set(longs + shorts)
        turnover = len(current_positions - prev_positions) / len(current_positions) if current_positions else 0
        prev_positions = current_positions
        cost_per_rebalance = turnover * transaction_cost * 2  # 双边

        long_weight = 1.0 / top_n if long_only else 1.0 / (2 * top_n)
        short_weight = 0.0 if long_only else -1.0 / (2 * top_n)

        for j in range(rebalance_freq):
            if i + j + 1 >= len(price_df):
                break
            next_date = price_df.index[i + j + 1]
            ret = 0.0
            for t in longs:
                r = returns.loc[next_date, t]
                if not np.isnan(r):
                    ret += long_weight * r
            for t in shorts:
                r = returns.loc[next_date, t]
                if not np.isnan(r):
                    ret += short_weight * r
            if j == 0:
                ret -= cost_per_rebalance

            portfolio_value *= (1 + ret)
            daily_rets.append(ret)
            portfolio_values.append((str(next_date)[:10], portfolio_value))
            if portfolio_value > max_value:
                max_value = portfolio_value
            dd = (portfolio_value - max_value) / max_value
            if dd < max_dd:
                max_dd = dd

        positions_history.append({
            'date': str(date)[:10],
            'longs': longs,
            'shorts': shorts,
        })

    if not daily_rets:
        return {'error': 'no daily returns'}

    daily_rets = np.array(daily_rets)
    total_return = portfolio_value - 1.0
    ann_return = (1 + total_return) ** (252 / len(daily_rets)) - 1
    ann_vol = np.std(daily_rets) * np.sqrt(252)
    sharpe = ann_return / ann_vol if ann_vol > 0 else 0
    calmar = ann_return / abs(max_dd) if max_dd != 0 else 0

    return {
        'total_return': round(total_return * 100, 2),
        'annualized_return': round(ann_return * 100, 2),
        'max_drawdown': round(max_dd * 100, 2),
        'annualized_volatility': round(ann_vol * 100, 2),
        'sharpe_ratio': round(sharpe, 2),
        'calmar_ratio': round(calmar, 2),
        'num_trading_days': len(daily_rets),
        'num_rebalances': len(positions_history),
        'latest_positions': positions_history[-1] if positions_history else None,
        'equity_curve': portfolio_values[-100:],
    }


def run_portfolio_backtest(top_n: int = 10, rebalance_freq: int = 5, max_tickers: int = 50,
                           transaction_cost: float = 0.001, rolling: bool = False):
    tickers = _load_watchlist_tickers(limit=max_tickers)
    print(f"[portfolio_backtest] 加载 {len(tickers)} 个标的...")
    price_df = _load_price_data(tickers, min_days=252)
    if price_df.empty:
        return {'error': 'no price data'}
    print(f"[portfolio_backtest] 价格矩阵: {price_df.shape}")

    full_data_cache = _load_full_data_cache(tickers, min_days=252)
    print(f"[portfolio_backtest] 完整数据缓存: {len(full_data_cache)} 个标的")

    all_factors = _load_selected_factors()
    if not all_factors:
        all_factors = []
        try:
            with open(ALL_FACTORS_PATH, 'r', encoding='utf-8') as f:
                all_factors = json.load(f).get('factors', [])
        except Exception:
            pass

    if not all_factors:
        return {'error': 'no factors'}

    if rolling:
        print("[portfolio_backtest] 使用滚动因子精选...")
        unique_years = sorted(set(price_df.index.year))
        yearly_factors = {}
        for year in unique_years:
            if year == price_df.index[0].year:
                continue
            date = pd.Timestamp(f'{year}-01-01')
            yearly_factors[year] = _select_factors_rolling(price_df, full_data_cache, all_factors, date,
                                                           lookback_days=252, top_n=15)
            print(f"  {year}: 选出 {len(yearly_factors[year])} 个因子")

        scores_df = pd.DataFrame(index=price_df.index, columns=price_df.columns, dtype=float)
        for t, df in full_data_cache.items():
            if t not in price_df.columns:
                continue
            s = pd.Series(np.nan, index=price_df.index)
            for year in unique_years:
                factors = yearly_factors.get(year, all_factors[:15])
                mask = price_df.index.year == year
                daily_scores = _compute_scores_for_ticker(df, factors)
                ds = pd.Series(daily_scores, index=df.index)
                s.loc[mask] = ds.reindex(price_df.index[mask]).values
            scores_df[t] = s.values
    else:
        scores_df = _compute_daily_scores(price_df, full_data_cache, all_factors)

    print(f"[portfolio_backtest] 得分矩阵: {scores_df.shape}")

    results = {}
    config = {
        'long_short': {'long_only': False, 'cost': 0.0},
        'long_only': {'long_only': True, 'cost': 0.0},
        'long_short_cost': {'long_only': False, 'cost': transaction_cost},
        'long_only_cost': {'long_only': True, 'cost': transaction_cost},
    }
    for name, cfg in config.items():
        stats = backtest_portfolio(price_df, scores_df, top_n=top_n, rebalance_freq=rebalance_freq,
                                   long_only=cfg['long_only'], transaction_cost=cfg['cost'])
        stats['top_n'] = top_n
        stats['rebalance_freq'] = rebalance_freq
        stats['long_only'] = cfg['long_only']
        stats['transaction_cost'] = cfg['cost']
        results[name] = stats
        print(f"  [{name}] 年化{stats['annualized_return']:+.2f}% 回撤{stats['max_drawdown']:.2f}% 夏普{stats['sharpe_ratio']}")

    output = {
        'tickers': price_df.columns.tolist(),
        'top_n': top_n,
        'rebalance_freq': rebalance_freq,
        'transaction_cost': transaction_cost,
        'rolling': rolling,
        'scenarios': results,
        'best_scenario': max(results, key=lambda k: results[k]['sharpe_ratio']),
    }

    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"[portfolio_backtest] 结果已保存: {OUTPUT_PATH}")
    return output


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--top-n', type=int, default=10)
    parser.add_argument('--rebalance-freq', type=int, default=5)
    parser.add_argument('--max-tickers', type=int, default=50)
    parser.add_argument('--transaction-cost', type=float, default=0.001)
    parser.add_argument('--rolling', action='store_true')
    args = parser.parse_args()

    stats = run_portfolio_backtest(
        top_n=args.top_n,
        rebalance_freq=args.rebalance_freq,
        max_tickers=args.max_tickers,
        transaction_cost=args.transaction_cost,
        rolling=args.rolling
    )
    print("\n✅ 组合回测对比")
    for k, v in stats.get('scenarios', {}).items():
        print(f"   {k}: 年化{v['annualized_return']:+.2f}% 回撤{v['max_drawdown']:.2f}% 夏普{v['sharpe_ratio']}")
