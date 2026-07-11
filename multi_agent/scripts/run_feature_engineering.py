"""自动特征工程：批量生成候选因子并回测。

从基础特征出发，通过一元变换、二元运算、窗口统计、阈值条件，
系统化生成数百到数千个候选 Alpha 因子，筛选后并入因子库。
"""
import json
import os
import sys
from typing import List, Dict, Tuple
import numpy as np
import pandas as pd
from numba import njit

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'multi_agent'))

from core.backtest_utils import parse_backtest_summary

FACTOR_DB_PATH = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'llm_factors.json')

# 基础特征名（必须与 data_layer 输出的列名一致）
BASE_FEATURES = [
    'close', 'ma5', 'ma10', 'ma20', 'ma60',
    'rsi_14', 'macd_hist', 'momentum_5d',
    'boll_up', 'boll_down', 'vol_ratio'
]

# 一元变换
UNARY_OPS = ['raw', 'diff1', 'diff5', 'pct1', 'pct5', 'zscore20', 'rank20']

# 二元运算
BINARY_OPS = ['sub', 'div', 'ratio', 'corr20']

# 信号方向
DIRECTIONS = ['long', 'short', 'both']


@njit
def _backtest_signal(signal: np.ndarray, returns: np.ndarray) -> Tuple[float, float, float, int]:
    """Numba 加速单因子单资产回测。signal 已前移。"""
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


def _build_unary_feature(series: pd.Series, op: str) -> pd.Series:
    if op == 'raw':
        return series
    if op == 'diff1':
        return series.diff(1)
    if op == 'diff5':
        return series.diff(5)
    if op == 'pct1':
        return series.pct_change(1)
    if op == 'pct5':
        return series.pct_change(5)
    if op == 'zscore20':
        return (series - series.rolling(20).mean()) / (series.rolling(20).std() + 1e-9)
    if op == 'rank20':
        return series.rolling(20).rank(pct=True)
    return series


def _build_binary_feature(a: pd.Series, b: pd.Series, op: str) -> pd.Series:
    if op == 'sub':
        return a - b
    if op == 'div':
        return a / (b + 1e-9)
    if op == 'ratio':
        return a / (b.abs() + 1e-9)
    if op == 'corr20':
        return a.rolling(20).corr(b)
    return a


def _generate_thresholds(series: pd.Series, n: int = 5) -> List[float]:
    """根据序列分布生成候选阈值。"""
    s = series.dropna()
    if len(s) < 20:
        return [0.0]
    qs = np.linspace(10, 90, n)
    return [s.quantile(q/100) for q in qs]


def _factor_to_signal(feat: pd.Series, direction: str, threshold: float, spec: Dict) -> Tuple[np.ndarray, str, Dict]:
    """根据特征值和阈值生成 -1/0/1 信号（已 shift(1)），并返回可执行代码与 spec。"""
    if direction == 'long':
        sig = np.where(feat > threshold, 1, 0)
    elif direction == 'short':
        sig = np.where(feat < threshold, -1, 0)
    else:  # both
        sig = np.where(feat > threshold, 1, np.where(feat < -threshold, -1, 0))

    sig = pd.Series(sig, index=feat.index).shift(1).fillna(0).values.astype(np.int8)

    # 构建可执行代码
    if spec['type'] == 'unary':
        f1 = spec['f1']
        op = spec['op']
        expr = f"{f1}"
        if op == 'diff1':
            expr = f"{f1}.diff(1)"
        elif op == 'diff5':
            expr = f"{f1}.diff(5)"
        elif op == 'pct1':
            expr = f"{f1}.pct_change(1)"
        elif op == 'pct5':
            expr = f"{f1}.pct_change(5)"
        elif op == 'zscore20':
            expr = f"({f1} - {f1}.rolling(20).mean()) / ({f1}.rolling(20).std() + 1e-9)"
        elif op == 'rank20':
            expr = f"{f1}.rolling(20).rank(pct=True)"
    else:  # binary
        f1, f2, op = spec['f1'], spec['f2'], spec['op']
        if op == 'sub':
            expr = f"{f1} - {f2}"
        elif op == 'div':
            expr = f"{f1} / ({f2} + 1e-9)"
        elif op == 'ratio':
            expr = f"{f1} / ({f2}.abs() + 1e-9)"
        elif op == 'corr20':
            expr = f"{f1}.rolling(20).corr({f2})"
        else:
            expr = f"{f1}"

    direction = spec['direction']
    threshold = spec['threshold']
    if direction == 'long':
        cond = f"feat > {threshold}"
    elif direction == 'short':
        cond = f"feat < {threshold}"
    else:
        cond = f"(feat > {threshold}) | (feat < {-threshold})"

    code = f"""feat = {expr}
signal = np.where({cond}, 1, np.where({'feat < -' + str(threshold) if direction == 'both' else 'False'}, -1, 0))
signal = pd.Series(signal, index=close.index).shift(1).fillna(0)
"""
    return sig, code, spec


def generate_candidate_factors(df: pd.DataFrame, max_candidates: int = 500, include_code: bool = False) -> List[Dict]:
    """在单个 DataFrame 上生成候选因子。"""
    candidates = []
    returns = df['close'].pct_change().values.astype(np.float64)
    valid_mask = ~np.isnan(returns)

    # 1. 一元特征 × 阈值 × 方向
    for fname in BASE_FEATURES:
        if fname not in df.columns:
            continue
        base = df[fname]
        for op in UNARY_OPS:
            feat = _build_unary_feature(base, op)
            feat.name = f'{fname}_{op}'
            for direction in DIRECTIONS:
                thresholds = _generate_thresholds(feat, n=3)
                for threshold in thresholds:
                    name = f"{fname}_{op}_{direction}_t{threshold:.3g}"
                    spec = {'type': 'unary', 'f1': fname, 'op': op, 'direction': direction, 'threshold': threshold}
                    signal, code, spec = _factor_to_signal(feat, direction, threshold, spec)
                    candidates.append({
                        'name': name,
                        'description': f'{fname} {op} {direction} 阈值{threshold:.3g}',
                        'code': code if include_code else '# auto-generated',
                        'spec': spec,
                        'signal': signal,
                        'returns': returns,
                        'valid_mask': valid_mask,
                    })

    # 2. 二元特征 × 阈值 × 方向
    for i, fa in enumerate(BASE_FEATURES):
        for fb in BASE_FEATURES[i+1:]:
            if fa not in df.columns or fb not in df.columns:
                continue
            for op in BINARY_OPS:
                feat = _build_binary_feature(df[fa], df[fb], op)
                feat.name = f'{fa}_{op}_{fb}'
                for direction in DIRECTIONS:
                    thresholds = _generate_thresholds(feat, n=3)
                    for threshold in thresholds:
                        name = f"{fa}_{op}_{fb}_{direction}_t{threshold:.3g}"
                        spec = {'type': 'binary', 'f1': fa, 'f2': fb, 'op': op, 'direction': direction, 'threshold': threshold}
                        signal, code, spec = _factor_to_signal(feat, direction, threshold, spec)
                        candidates.append({
                            'name': name,
                            'description': f'{fa} {op} {fb} {direction} 阈值{threshold:.3g}',
                            'code': code if include_code else '# auto-generated',
                            'spec': spec,
                            'signal': signal,
                            'returns': returns,
                            'valid_mask': valid_mask,
                        })

    # 随机采样到 max_candidates
    if len(candidates) > max_candidates:
        np.random.seed(42)
        idx = np.random.choice(len(candidates), max_candidates, replace=False)
        candidates = [candidates[i] for i in idx]

    return candidates


def evaluate_candidates(candidates: List[Dict]) -> List[Dict]:
    """对候选因子批量回测，返回带评估指标的字典。"""
    results = []
    for c in candidates:
        try:
            signal = c['signal']
            returns = c['returns']
            valid = c['valid_mask'] & (~np.isnan(returns)) & (~np.isnan(signal.astype(float)))
            if valid.sum() < 30:
                continue
            s = signal[valid]
            r = returns[valid]
            total_ret, max_dd, win_rate, trades = _backtest_signal(s, r)
            if np.isnan(total_ret) or np.isinf(total_ret):
                continue
            results.append({
                'name': c['name'],
                'description': c['description'],
                'code': c.get('code', '# auto-generated'),
                'spec': c.get('spec', {}),
                'source': 'auto_fe',
                'total_return': round(total_ret * 100, 2),
                'max_drawdown': round(max_dd * 100, 2),
                'win_rate': round(win_rate * 100, 1),
                'trades': int(trades),
            })
        except Exception:
            continue
    return results


def _load_top_tickers(n=20):
    import sqlite3
    db = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'llm_predictions.db')
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    latest = conn.execute("SELECT MAX(pred_date) FROM agentic_predictions").fetchone()[0]
    rows = conn.execute(
        "SELECT ticker, name, category FROM agentic_predictions WHERE pred_date=? AND category IN ('个股','ETF') ORDER BY weighted_score DESC LIMIT ?",
        (latest, n)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _batch_load_data(tickers):
    from core.data_layer import get_stock_data, calc_technical_indicators
    cache = {}
    for t in tickers:
        try:
            df, info = get_stock_data(t)
            df = calc_technical_indicators(df)
            cache[t] = df
        except Exception as e:
            print(f"  ⚠️ 加载 {t} 失败: {e}", file=sys.stderr)
    return cache


def _aggregate_across_assets(factor_results_by_ticker: Dict[str, List[Dict]]) -> List[Dict]:
    """聚合每个因子在多个资产上的表现。"""
    from collections import defaultdict
    grouped = defaultdict(list)
    for ticker, results in factor_results_by_ticker.items():
        for r in results:
            grouped[r['name']].append(r)

    aggregated = []
    for name, results in grouped.items():
        if len(results) < 2:
            continue
        avg_return = sum(r['total_return'] for r in results) / len(results)
        avg_dd = sum(r['max_drawdown'] for r in results) / len(results)
        avg_win = sum(r['win_rate'] for r in results) / len(results)
        avg_trades = sum(r['trades'] for r in results) / len(results)
        # 夏普用简化近似：收益 / 回撤波动
        sharpe = avg_return / (abs(avg_dd) + 1e-9) * 0.5
        calmar = avg_return / (abs(avg_dd) + 1e-9)
        score = avg_return * max(sharpe, 0)
        aggregated.append({
            'name': name,
            'description': results[0]['description'],
            'code': results[0]['code'],
            'source': 'auto_fe',
            'avg_return': round(avg_return, 2),
            'avg_drawdown': round(avg_dd, 2),
            'avg_sharpe': round(sharpe, 2),
            'avg_win_rate': round(avg_win, 1),
            'avg_calmar': round(calmar, 2),
            'avg_trades': int(avg_trades),
            'score': round(score, 2),
            'passed': avg_return > 0 and score > 0,
        })
    return aggregated


def run_auto_feature_engineering(max_candidates_per_asset=300, save_top_n=50):
    """主入口：批量生成候选因子，回测，聚合，保存 Top N。"""
    tickers = _load_top_tickers(20)
    print(f"[auto_fe] 加载 {len(tickers)} 个标的...")
    data_cache = _batch_load_data([t['ticker'] for t in tickers])
    if not data_cache:
        return []

    all_results_by_ticker = {}
    for ticker, df in data_cache.items():
        print(f"[auto_fe] 生成候选因子: {ticker}")
        candidates = generate_candidate_factors(df, max_candidates=max_candidates_per_asset)
        results = evaluate_candidates(candidates)
        all_results_by_ticker[ticker] = results
        print(f"[auto_fe]   {len(results)} 个有效候选")

    aggregated = _aggregate_across_assets(all_results_by_ticker)
    passed = [a for a in aggregated if a['passed']]
    passed.sort(key=lambda x: x['score'], reverse=True)
    top = passed[:save_top_n]

    # 合并到因子库
    existing = []
    if os.path.exists(FACTOR_DB_PATH):
        try:
            with open(FACTOR_DB_PATH, 'r', encoding='utf-8') as f:
                existing = json.load(f).get('factors', [])
        except Exception:
            pass

    existing_names = {x['name'] for x in existing}
    for f in top:
        if f['name'] not in existing_names:
            existing.append(f)

    os.makedirs(os.path.dirname(FACTOR_DB_PATH), exist_ok=True)
    with open(FACTOR_DB_PATH, 'w', encoding='utf-8') as f:
        json.dump({'factors': existing}, f, ensure_ascii=False, indent=2)

    print(f"[auto_fe] 聚合后 {len(passed)} 个正收益因子，保存 Top {len(top)}")
    return top


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidates', type=int, default=300, help='每只标的生成候选数量')
    parser.add_argument('--top-n', type=int, default=50, help='保存表现最好的 N 个')
    args = parser.parse_args()

    top = run_auto_feature_engineering(max_candidates_per_asset=args.candidates, save_top_n=args.top_n)
    for f in top[:10]:
        print(f"\n✅ {f['name']}")
        print(f"   收益 {f['avg_return']:+.2f}% | 回撤 {f['avg_drawdown']}% | 夏普 {f['avg_sharpe']} | 胜率 {f['avg_win_rate']}% | Calmar {f['avg_calmar']}")
        print(f"   {f['description']}")
