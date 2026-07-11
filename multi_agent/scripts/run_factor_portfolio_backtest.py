"""组合级滚动回测：用精选因子得分构建多空组合。

每天根据 factor_score 排序，做多 Top N，做空 Bottom N，
等权重再平衡，输出组合收益曲线与风险指标。
"""
from __future__ import annotations

import json
import os
import sys
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'multi_agent'))

from core.data_layer import get_stock_data, calc_technical_indicators
from strategy.factor_scoring import compute_factor_scores

SELECTED_FACTORS_PATH = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'llm_factors_selected.json')
OUTPUT_PATH = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'factor_portfolio_backtest.json')


def _load_watchlist_tickers() -> List[str]:
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
    return [r[0] for r in rows]


def _load_price_data(tickers: List[str], min_days: int = 252) -> pd.DataFrame:
    """加载所有标的收盘价，对齐到共同日期范围。"""
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
    return pd.DataFrame(frames).dropna(axis=1, how='all').dropna(axis=0, how='all')


def _compute_daily_scores(price_df: pd.DataFrame, tickers: List[str]) -> pd.DataFrame:
    """逐日计算每个标的的 factor_score。"""
    scores = pd.DataFrame(index=price_df.index, columns=price_df.columns, dtype=float)
    for t in tickers:
        if t not in price_df.columns:
            continue
        try:
            df, _ = get_stock_data(t)
            df = calc_technical_indicators(df)
            # 为每个日期生成滚动得分：取最近 60 日数据，计算该日因子得分
            # 简化：对整个数据执行因子，得到每日信号，然后滚动平均作为 score
            from strategy.factor_scoring import _load_selected_factors, _execute_factor_code_on_df
            factors = _load_selected_factors()
            daily_votes = []
            weights = []
            for f in factors:
                try:
                    signal = _execute_factor_code_on_df(f['code'], df)
                    w = max(f.get('stability_score', 0), 0.01)
                    daily_votes.append(signal.values * w)
                    weights.append(w)
                except Exception:
                    continue
            if not daily_votes:
                continue
            votes_arr = np.array(daily_votes)
            total_w = sum(weights)
            daily_scores = np.sum(votes_arr, axis=0) / total_w
            s = pd.Series(daily_scores, index=df.index).reindex(price_df.index)
            scores[t] = s
        except Exception:
            continue
    return scores


def backtest_portfolio(price_df: pd.DataFrame, scores_df: pd.DataFrame, top_n: int = 10, rebalance_freq: int = 5) -> Dict:
    """组合回测：做多 top_n 最高得分，做空 bottom_n 最低得分。"""
    returns = price_df.pct_change().shift(-1)  # t 日信号，t+1 日收益

    portfolio_values = []
    portfolio_value = 1.0
    max_value = 1.0
    max_dd = 0.0
    daily_rets = []
    positions_history = []

    for i in range(0, len(price_df) - 1, rebalance_freq):
        date = price_df.index[i]
        scores = scores_df.loc[date].dropna()
        if len(scores) < top_n * 2:
            continue
        longs = scores.nlargest(top_n).index.tolist()
        shorts = scores.nsmallest(top_n).index.tolist()

        # 等权重：每只 long 1/(2N)，short 也是 1/(2N)，总敞口 1.0
        long_weight = 1.0 / (2 * top_n)
        short_weight = -1.0 / (2 * top_n)

        for j in range(rebalance_freq):
            if i + j + 1 >= len(price_df):
                break
            next_date = price_df.index[i + j + 1]
            ret = 0.0
            for t in longs:
                if t in returns.columns and not np.isnan(returns.loc[next_date, t]):
                    ret += long_weight * returns.loc[next_date, t]
            for t in shorts:
                if t in returns.columns and not np.isnan(returns.loc[next_date, t]):
                    ret += short_weight * returns.loc[next_date, t]

            portfolio_value *= (1 + ret)
            daily_rets.append(ret)
            portfolio_values.append((next_date, portfolio_value))
            if portfolio_value > max_value:
                max_value = portfolio_value
            dd = (portfolio_value - max_value) / max_value
            if dd < max_dd:
                max_dd = dd

            positions_history.append({
                'date': str(next_date)[:10],
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
        'num_rebalances': len(positions_history) // rebalance_freq,
        'latest_positions': positions_history[-1] if positions_history else None,
        'equity_curve': [(str(d), round(v, 4)) for d, v in portfolio_values],
    }


def run_portfolio_backtest(top_n: int = 10, rebalance_freq: int = 5, max_tickers: int = 50):
    tickers = _load_watchlist_tickers()[:max_tickers]
    print(f"[portfolio_backtest] 加载 {len(tickers)} 个标的...")
    price_df = _load_price_data(tickers, min_days=252)
    if price_df.empty:
        return {'error': 'no price data'}
    print(f"[portfolio_backtest] 价格矩阵: {price_df.shape}")

    scores_df = _compute_daily_scores(price_df, tickers)
    print(f"[portfolio_backtest] 得分矩阵: {scores_df.shape}")

    stats = backtest_portfolio(price_df, scores_df, top_n=top_n, rebalance_freq=rebalance_freq)
    stats['top_n'] = top_n
    stats['rebalance_freq'] = rebalance_freq
    stats['tickers'] = price_df.columns.tolist()

    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    print(f"[portfolio_backtest] 结果已保存: {OUTPUT_PATH}")
    return stats


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--top-n', type=int, default=10)
    parser.add_argument('--rebalance-freq', type=int, default=5)
    parser.add_argument('--max-tickers', type=int, default=50)
    args = parser.parse_args()

    stats = run_portfolio_backtest(top_n=args.top_n, rebalance_freq=args.rebalance_freq, max_tickers=args.max_tickers)
    print(f"\n✅ 组合回测结果")
    for k, v in stats.items():
        if k not in ['equity_curve', 'tickers']:
            print(f"   {k}: {v}")
