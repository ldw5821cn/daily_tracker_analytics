"""精选因子评分：对每个标的执行精选因子代码，汇总因子投票得分。"""
from __future__ import annotations

import json
import os
import sys
from typing import Dict, List

import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'multi_agent'))

from core.data_layer import get_stock_data, calc_technical_indicators

SELECTED_FACTORS_PATH = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'llm_factors_selected.json')


def _load_selected_factors() -> List[Dict]:
    try:
        with open(SELECTED_FACTORS_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get('filtered_factors') or data.get('factors', [])
    except Exception:
        return []


def _execute_factor_code_on_df(code: str, df: pd.DataFrame) -> pd.Series:
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


def compute_factor_scores(tickers: List[str]) -> Dict[str, float]:
    """对输入标的执行精选因子，返回等权投票得分。"""
    factors = _load_selected_factors()
    if not factors:
        return {}

    scores = {}
    for ticker in tickers:
        try:
            df, _ = get_stock_data(ticker)
            df = calc_technical_indicators(df)
            votes = []
            weights = []
            for f in factors:
                try:
                    signal = _execute_factor_code_on_df(f['code'], df)
                    last_signal = signal.iloc[-1]
                    if np.isnan(last_signal):
                        continue
                    votes.append(last_signal)
                    weights.append(max(f.get('stability_score', 0), 0.01))
                except Exception:
                    continue
            if not votes:
                scores[ticker] = 0.0
                continue
            votes = np.array(votes)
            weights = np.array(weights)
            weighted_sum = np.sum(votes * weights)
            total_weight = np.sum(weights)
            scores[ticker] = float(weighted_sum / total_weight) if total_weight > 0 else 0.0
        except Exception:
            scores[ticker] = 0.0
    return scores


def add_factor_scores_to_predictions(preds: List[Dict]) -> List[Dict]:
    """为预测列表注入 factor_score。"""
    tickers = [p['ticker'] for p in preds]
    scores = compute_factor_scores(tickers)
    for p in preds:
        p['factor_score'] = scores.get(p['ticker'], 0.0)
    return preds


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        tickers = sys.argv[1:]
    else:
        tickers = ['000301', '688019', '300474']
    scores = compute_factor_scores(tickers)
    for t, s in sorted(scores.items(), key=lambda x: abs(x[1]), reverse=True):
        print(f"{t}: {s:+.3f}")
