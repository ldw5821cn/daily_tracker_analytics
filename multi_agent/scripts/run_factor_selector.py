"""因子精选：样本外验证 + 正交化筛选。

对 llm_factors.json 中的因子做：
1. 样本外回测（前 60% 训练，后 40% 测试）
2. 信息系数 IC / Rank IC 计算
3. 因子间相关性正交化：剔除与已选因子高相关的
4. 输出精选因子库到 llm_factors_selected.json
"""
import json
import os
import sys
from typing import List, Dict, Tuple
import numpy as np
import pandas as pd
from numba import njit
from scipy.stats import spearmanr

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'multi_agent'))

from core.data_layer import get_stock_data, calc_technical_indicators

FACTOR_DB_PATH = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'llm_factors.json')
SELECTED_DB_PATH = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'llm_factors_selected.json')


@njit
def _backtest(signal: np.ndarray, returns: np.ndarray) -> Tuple[float, float, float, int]:
    n = len(returns)
    total_ret = 0.0
    positive = 0
    negative = 0
    trades = 0
    cum = 1.0
    max_cum = 1.0
    max_dd = 0.0
    for i in range(n):
        if i > 0 and signal[i-1] != signal[i]:
            trades += 1
        ret = signal[i] * returns[i]
        total_ret += ret
        if ret > 0:
            positive += 1
        elif ret < 0:
            negative += 1
        cum *= (1.0 + ret)
        if cum > max_cum:
            max_cum = cum
        dd = (cum - max_cum) / max_cum
        if dd < max_dd:
            max_dd = dd
    win_rate = positive / (positive + negative) if (positive + negative) > 0 else 0.0
    return total_ret, max_dd, win_rate, trades


def _load_factors() -> List[Dict]:
    with open(FACTOR_DB_PATH, 'r', encoding='utf-8') as f:
        return json.load(f).get('factors', [])


def _load_top_tickers(n=20) -> List[str]:
    import sqlite3
    db = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'llm_predictions.db')
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    latest = conn.execute("SELECT MAX(pred_date) FROM agentic_predictions").fetchone()[0]
    rows = conn.execute(
        "SELECT ticker FROM agentic_predictions WHERE pred_date=? AND category IN ('个股','ETF') ORDER BY weighted_score DESC LIMIT ?",
        (latest, n)
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def _execute_factor_code_on_df(code: str, df: pd.DataFrame) -> pd.Series:
    """在已加载的 DataFrame 上执行因子代码，返回 signal 序列。"""
    close = df['close']
    ma5 = df['ma5']
    ma10 = df['ma10']
    ma20 = df['ma20']
    ma60 = df['ma60']
    rsi_14 = df['rsi_14']
    macd_hist = df['macd_hist']
    momentum_5d = df['momentum_5d']
    boll_up = df['boll_up']
    boll_down = df['boll_down']
    vol_ratio = df['vol_ratio']
    namespace = {
        'np': np, 'pd': pd,
        'close': close, 'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
        'rsi_14': rsi_14, 'macd_hist': macd_hist, 'momentum_5d': momentum_5d,
        'boll_up': boll_up, 'boll_down': boll_down, 'vol_ratio': vol_ratio,
    }
    exec(code, namespace)
    signal = namespace.get('signal')
    if signal is None:
        raise ValueError('signal not defined')
    return pd.Series(signal, index=df.index)


def _batch_load_data(tickers: List[str]) -> Dict[str, pd.DataFrame]:
    """一次性加载所有标的的数据。"""
    cache = {}
    for t in tickers:
        try:
            df, _ = get_stock_data(t)
            df = calc_technical_indicators(df)
            cache[t] = df
        except Exception as e:
            print(f"  ⚠️ 加载 {t} 失败: {e}", file=sys.stderr)
    return cache


def _out_of_sample_stats(signal: pd.Series, returns: pd.Series) -> Dict:
    """计算样本外统计：分训练/测试。过滤 nan。"""
    aligned = pd.DataFrame({'signal': signal, 'returns': returns}).dropna()
    if len(aligned) < 60:
        return {}

    n = len(aligned)
    split = int(n * 0.6)
    train_signal = aligned['signal'].iloc[:split].values.astype(np.int8)
    train_returns = aligned['returns'].iloc[:split].values.astype(np.float64)
    test_signal = aligned['signal'].iloc[split:].values.astype(np.int8)
    test_returns = aligned['returns'].iloc[split:].values.astype(np.float64)

    train_ret, train_dd, train_wr, train_trades = _backtest(train_signal, train_returns)
    test_ret, test_dd, test_wr, test_trades = _backtest(test_signal, test_returns)

    # IC: 信号与未来收益的相关性
    fut_ret = returns.shift(-1)
    ic_aligned = pd.DataFrame({'signal': signal, 'fut_ret': fut_ret}).dropna()
    if len(ic_aligned) > 30:
        ic = ic_aligned['signal'].corr(ic_aligned['fut_ret'])
        rank_ic, _ = spearmanr(ic_aligned['signal'], ic_aligned['fut_ret'])
    else:
        ic = rank_ic = 0

    return {
        'train_return': round(train_ret * 100, 2),
        'train_drawdown': round(train_dd * 100, 2),
        'train_win_rate': round(train_wr * 100, 1),
        'test_return': round(test_ret * 100, 2),
        'test_drawdown': round(test_dd * 100, 2),
        'test_win_rate': round(test_wr * 100, 1),
        'ic': round(ic, 3) if not np.isnan(ic) else 0,
        'rank_ic': round(rank_ic, 3) if not np.isnan(rank_ic) else 0,
    }


def _select_orthogonal(factors: List[Dict], corr_threshold: float = 0.7, max_selected: int = 30) -> List[Dict]:
    """按样本外表现排序，逐步加入，剔除高相关性因子。"""
    # 先按 test_return 排序
    sorted_factors = sorted(factors, key=lambda x: x.get('test_return', 0), reverse=True)
    selected = []
    for f in sorted_factors:
        if len(selected) >= max_selected:
            break
        if f.get('test_return', 0) <= 0:
            continue
        # 计算与已选因子的平均相关性
        if not selected:
            selected.append(f)
            continue
        corrs = []
        for s in selected:
            c = f.get('signal_returns_corr', 0)
            # 这里需要预先计算 signal_returns_corr
            corrs.append(abs(c))
        avg_corr = sum(corrs) / len(corrs)
        if avg_corr < corr_threshold:
            selected.append(f)
    return selected


def select_factors(save_top_n: int = 30, corr_threshold: float = 0.7):
    factors = _load_factors()
    tickers = _load_top_tickers(20)
    print(f"[factor_selector] 加载 {len(factors)} 个因子，{len(tickers)} 个标的")

    print("[factor_selector] 批量加载数据...")
    data_cache = _batch_load_data(tickers)
    if not data_cache:
        return []

    all_evaluated = []
    for idx, factor in enumerate(factors):
        oos_results = []
        for ticker, df in data_cache.items():
            try:
                signal = _execute_factor_code_on_df(factor['code'], df)
                returns = df['close'].pct_change()
                stats = _out_of_sample_stats(signal, returns)
                if not stats:
                    continue
                oos_results.append(stats)
            except Exception as e:
                continue

        if len(oos_results) < 3:
            continue

        avg_train = sum(r['train_return'] for r in oos_results) / len(oos_results)
        avg_test = sum(r['test_return'] for r in oos_results) / len(oos_results)
        avg_test_dd = sum(r['test_drawdown'] for r in oos_results) / len(oos_results)
        avg_ic = sum(r['ic'] for r in oos_results) / len(oos_results)
        avg_rank_ic = sum(r['rank_ic'] for r in oos_results) / len(oos_results)
        stability_score = avg_test * max(avg_rank_ic, 0)

        all_evaluated.append({
            **factor,
            'avg_train_return': round(avg_train, 2),
            'avg_test_return': round(avg_test, 2),
            'avg_test_drawdown': round(avg_test_dd, 2),
            'avg_ic': round(avg_ic, 3),
            'avg_rank_ic': round(avg_rank_ic, 3),
            'stability_score': round(stability_score, 2),
            'passed': avg_test > 0 and avg_rank_ic > 0,
        })

    # 正交化：按稳定性分排序，保留低相关性的因子
    passed = [f for f in all_evaluated if f['passed']]
    passed.sort(key=lambda x: x['stability_score'], reverse=True)
    selected = []
    for f in passed:
        if len(selected) >= save_top_n:
            break
        if not selected:
            selected.append(f)
            continue
        # 计算与已选因子的 signal 相关性平均值（用第一个 ticker 的 signal 作为代表）
        try:
            first_ticker = list(data_cache.keys())[0]
            first_df = data_cache[first_ticker]
            f_signal = _execute_factor_code_on_df(f['code'], first_df)
            corrs = []
            for s in selected:
                s_signal = _execute_factor_code_on_df(s['code'], first_df)
                aligned = pd.concat([f_signal, s_signal], axis=1).dropna()
                if len(aligned) > 30:
                    corrs.append(abs(aligned.iloc[:, 0].corr(aligned.iloc[:, 1])))
            avg_corr = sum(corrs) / len(corrs) if corrs else 0
            if avg_corr < corr_threshold:
                selected.append(f)
        except Exception:
            continue

    os.makedirs(os.path.dirname(SELECTED_DB_PATH), exist_ok=True)

    def _sanitize(obj):
        if isinstance(obj, dict):
            return {k: _sanitize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_sanitize(x) for x in obj]
        if isinstance(obj, (np.bool_, np.integer)):
            return bool(obj)
        if isinstance(obj, np.floating):
            return float(obj)
        return obj

    with open(SELECTED_DB_PATH, 'w', encoding='utf-8') as f:
        json.dump(_sanitize({'factors': selected, 'total': len(factors), 'passed': len(passed)}), f, ensure_ascii=False, indent=2)

    print(f"[factor_selector] 通过 {len(passed)} 个，精选 {len(selected)} 个")
    return selected


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--top-n', type=int, default=30, help='精选因子数量')
    args = parser.parse_args()

    selected = select_factors(save_top_n=args.top_n)
    for f in selected[:10]:
        print(f"\n✅ {f['name']} ({f.get('source','?')})")
        print(f"   训练收益 {f['avg_train_return']}% | 测试收益 {f['avg_test_return']}% | 测试回撤 {f['avg_test_drawdown']}%")
        print(f"   IC {f['avg_ic']} | Rank IC {f['avg_rank_ic']} | 稳定分 {f['stability_score']}")
        print(f"   {f['description']}")
