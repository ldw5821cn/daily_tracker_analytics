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
from collections import Counter
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
    namespace = {
        'np': np, 'pd': pd,
        'close': df['close'], 'open': df.get('open', df['close']),
        'high': df.get('high', df['close']), 'low': df.get('low', df['close']),
        'volume': df.get('volume', pd.Series(0, index=df.index)),
    }
    for col in df.columns:
        namespace[col] = df[col]
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
    if len(aligned) < 120:
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
    fut_ret = aligned['returns'].shift(-1)
    ic_aligned = pd.DataFrame({'signal': aligned['signal'], 'fut_ret': fut_ret}).dropna()
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


def _select_orthogonal(factors: List[Dict], corr_threshold: float = 0.5, max_selected: int = 30,
                       min_per_source: Dict[str, int] = None) -> List[Dict]:
    """按综合得分排序，保留低相关性的因子，并保证来源多样性。"""
    if min_per_source is None:
        min_per_source = {'rule': 8, 'auto_fe': 5, 'composite': 1}
    by_source = {}
    for f in factors:
        src = f.get('source', 'unknown')
        by_source.setdefault(src, []).append(f)
    for src in by_source:
        by_source[src].sort(key=lambda x: x.get('composite_score', 0), reverse=True)

    selected = []
    selected_signals = []

    def _avg_corr_with_selected(f: Dict) -> float:
        if not selected_signals:
            return 0.0
        corrs = []
        for sig_b in selected_signals:
            for sig_a, sig_b_ticker in zip(f.get('signals', []), sig_b):
                if sig_a is None or sig_b_ticker is None:
                    continue
                aligned = pd.concat([sig_a, sig_b_ticker], axis=1).dropna()
                if len(aligned) > 30:
                    corrs.append(abs(aligned.iloc[:, 0].corr(aligned.iloc[:, 1])))
        return sum(corrs) / len(corrs) if corrs else 0.0

    # 先满足每个来源最低配额
    for src, min_n in min_per_source.items():
        for f in by_source.get(src, [])[:min_n]:
            if len(selected) >= max_selected:
                break
            if _avg_corr_with_selected(f) < corr_threshold:
                selected.append(f)
                selected_signals.append(f.get('signals', []))
        if len(selected) >= max_selected:
            break

    # 剩余名额按综合分从高到低填补
    all_sorted = sorted(factors, key=lambda x: x.get('composite_score', 0), reverse=True)
    for f in all_sorted:
        if len(selected) >= max_selected:
            break
        if f in selected:
            continue
        if _avg_corr_with_selected(f) < corr_threshold:
            selected.append(f)
            selected_signals.append(f.get('signals', []))

    return selected


def select_factors(save_top_n: int = 30, corr_threshold: float = 0.5):
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
        signals = []
        for ticker, df in data_cache.items():
            try:
                signal = _execute_factor_code_on_df(factor['code'], df)
                returns = df['close'].pct_change()
                stats = _out_of_sample_stats(signal, returns)
                if not stats:
                    continue
                oos_results.append(stats)
                signals.append(signal)
            except Exception as e:
                continue

        if len(oos_results) < 3:
            continue

        avg_train = sum(r['train_return'] for r in oos_results) / len(oos_results)
        avg_test = sum(r['test_return'] for r in oos_results) / len(oos_results)
        avg_test_dd = sum(r['test_drawdown'] for r in oos_results) / len(oos_results)
        avg_ic = sum(r['ic'] for r in oos_results) / len(oos_results)
        avg_rank_ic = sum(r['rank_ic'] for r in oos_results) / len(oos_results)
        avg_train_dd = sum(r['train_drawdown'] for r in oos_results) / len(oos_results)
        avg_wr = sum(r['test_win_rate'] for r in oos_results) / len(oos_results)

        # 综合得分：样本外收益 + 夏普风格 + IC + 训练测试一致性
        test_sharpe = avg_test / (abs(avg_test_dd) + 1e-9)
        consistency = max(0, 1 - abs(avg_train - avg_test) / (abs(avg_train) + abs(avg_test) + 1e-9))
        ic_score = max(0, avg_rank_ic) * 100
        composite_score = (
            avg_test * 0.5 +                    # 降低收益权重
            test_sharpe * 20.0 +                # 加大夏普/回撤惩罚
            ic_score * 30.0 +                   # 加大 IC 权重
            consistency * 5.0                 # 降低一致性权重
        )
        # 通过条件：按来源差异化
        # 规则因子已有经济逻辑，要求严格；auto_fe 允许更多样本探索
        src = factor.get('source', 'unknown')
        if src == 'auto_fe':
            passed = (avg_test > -20) and (avg_test_dd > -80) and (avg_test_dd != 0)
        else:
            passed = (avg_test > -5) and (avg_test_dd > -70)

        all_evaluated.append({
            **factor,
            'avg_train_return': round(avg_train, 2),
            'avg_train_drawdown': round(avg_train_dd, 2),
            'avg_test_return': round(avg_test, 2),
            'avg_test_drawdown': round(avg_test_dd, 2),
            'avg_test_win_rate': round(avg_wr, 1),
            'avg_ic': round(avg_ic, 3),
            'avg_rank_ic': round(avg_rank_ic, 3),
            'test_sharpe': round(test_sharpe, 2),
            'consistency': round(consistency, 2),
            'composite_score': round(composite_score, 2),
            'signals': signals,
            'passed': passed,
        })

    # 正交化：按综合得分排序，保留低相关性的因子
    passed = [f for f in all_evaluated if f['passed']]
    selected = _select_orthogonal(passed, corr_threshold=corr_threshold, max_selected=save_top_n)

    # 保存前去掉 signals 序列，避免 JSON 过大
    for f in selected:
        f.pop('signals', None)
    for f in passed:
        f.pop('signals', None)

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
    print(f"[factor_selector] 通过来源分布: {dict(Counter(f.get('source', 'unknown') for f in passed))}")
    print(f"[factor_selector] 精选来源分布: {dict(Counter(f.get('source', 'unknown') for f in selected))}")
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
        print(f"   IC {f['avg_ic']} | Rank IC {f['avg_rank_ic']} | 综合分 {f['composite_score']}")
        print(f"   {f['description']}")
