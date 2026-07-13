#!/usr/bin/env python3
"""策略场景回测：每日多空/每周只做多/止损风控，纯 pandas 实现，兼容 VectorBT 指标风格。

场景：
- daily_long_short_no_cost
- daily_long_short_with_cost
- weekly_long_only_no_cost
- weekly_long_only_with_cost
- weekly_long_only_risk_no_cost
- weekly_long_only_risk_with_cost
"""
from __future__ import annotations

import pandas as pd
import numpy as np
from typing import List, Dict


def _tech_score(df: pd.DataFrame) -> pd.Series:
    """计算技术面得分：50 基线 + 均线/MACD/RSI 贡献。"""
    score = pd.Series(50.0, index=df.index)
    if all(c in df.columns for c in ['ma5', 'ma10', 'ma20']):
        score += ((df['ma5'] > df['ma10']) & (df['ma10'] > df['ma20'])).astype(float) * 20
        score -= ((df['ma5'] < df['ma10']) & (df['ma10'] < df['ma20'])).astype(float) * 20
    if 'macd_hist' in df.columns:
        score += (df['macd_hist'] > 0).astype(float) * 15
        score -= (df['macd_hist'] < 0).astype(float) * 15
    if 'rsi_14' in df.columns:
        rsi = df['rsi_14']
        score += ((rsi > 30) & (rsi < 70)).astype(float) * 10
    return score.clip(0, 100)


def _daily_long_short(df: pd.DataFrame, cost: float = 0.0) -> pd.Series:
    """每日根据技术得分多空：得分 > 60 做多，< 40 做空，否则空仓。"""
    score = _tech_score(df)
    pos = pd.Series(0.0, index=df.index)
    pos[score > 60] = 1.0
    pos[score < 40] = -1.0
    ret = df['close'].pct_change().fillna(0) * pos.shift(1) - pos.diff().abs().fillna(0) * cost
    return ret


def _weekly_long_only(df: pd.DataFrame, cost: float = 0.0, sl_stop: float = None) -> pd.Series:
    """每周五收盘后决定下周方向，周一开盘调仓。"""
    score = _tech_score(df)
    weekly = score.resample('W-FRI').last().fillna(50)
    pos = pd.Series(0.0, index=df.index)
    for friday, s in weekly.items():
        in_pos = 1.0 if s > 60 else 0.0
        # 下周一到下周五
        mask = (df.index > friday) & (df.index <= friday + pd.Timedelta(days=7))
        pos[mask] = in_pos
    daily_ret = df['close'].pct_change().fillna(0)
    ret = daily_ret * pos.shift(1) - pos.diff().abs().fillna(0) * cost

    if sl_stop:
        # 简化止损：从建仓点回撤超过 sl_stop 则清仓，一周后重新评估
        entry_price = pd.Series(np.nan, index=df.index)
        for i in range(1, len(df)):
            if pos.iloc[i] == 1.0 and pos.iloc[i-1] == 0.0:
                entry_price.iloc[i] = df['close'].iloc[i-1]
            elif pos.iloc[i] == 1.0 and pos.iloc[i-1] == 1.0 and not np.isnan(entry_price.iloc[i-1]):
                entry_price.iloc[i] = entry_price.iloc[i-1]
        dd = (df['close'] - entry_price) / entry_price
        pos[dd < -sl_stop] = 0.0
        ret = daily_ret * pos.shift(1) - pos.diff().abs().fillna(0) * cost
    return ret


def _summarize(rets: pd.Series, scenario: str, days: int, ticker: str) -> Dict:
    total_return = float((1 + rets).prod() - 1)
    cumulative = (1 + rets).cumprod()
    peak = cumulative.cummax()
    drawdown = (cumulative - peak) / peak
    max_dd = float(drawdown.min())
    mean = float(rets.mean() * 252)
    std = float(rets.std() * np.sqrt(252))
    sharpe = float(mean / std) if std > 0 else 0.0
    trades = int((rets != 0).sum())
    return {
        'ticker': ticker,
        'scenario': scenario,
        'period_days': days,
        'total_return': round(total_return * 100, 2),
        'max_drawdown': round(max_dd * 100, 2),
        'sharpe': round(sharpe, 2),
        'trades': trades,
    }


def scenario_backtests(df: pd.DataFrame, periods=None, ticker: str = '') -> List[Dict]:
    if periods is None:
        periods = [30, 60, 90]
    if len(df) < 30:
        return []
    results = []
    for days in periods:
        if len(df) < days:
            continue
        sub = df.iloc[-days:].copy()
        results.append(_summarize(_daily_long_short(sub), 'daily_long_short_no_cost', days, ticker))
        results.append(_summarize(_daily_long_short(sub, cost=0.001), 'daily_long_short_with_cost', days, ticker))
        results.append(_summarize(_weekly_long_only(sub), 'weekly_long_only_no_cost', days, ticker))
        results.append(_summarize(_weekly_long_only(sub, cost=0.001), 'weekly_long_only_with_cost', days, ticker))
        results.append(_summarize(_weekly_long_only(sub, sl_stop=0.08), 'weekly_long_only_risk_no_cost', days, ticker))
        results.append(_summarize(_weekly_long_only(sub, cost=0.001, sl_stop=0.08), 'weekly_long_only_risk_with_cost', days, ticker))
    return results


def recommend_scenario(scenarios: List[Dict], prefer_low_risk: bool = True) -> Dict:
    if not scenarios:
        return {'scenario': 'N/A', 'score': 0, 'total_return': 0, 'max_drawdown': 0, 'sharpe': 0}

    def score(s):
        ret = s['total_return']
        dd = abs(s['max_drawdown'])
        sr = s['sharpe']
        if prefer_low_risk:
            return ret * 0.3 - dd * 0.5 + sr * 0.2
        else:
            return ret * 0.5 - dd * 0.3 + sr * 0.2

    best = max(scenarios, key=score)
    return best


SCENARIO_NAME_CN = {
    'daily_long_short_no_cost': '每日多空（无成本）',
    'daily_long_short_with_cost': '每日多空（含成本）',
    'weekly_long_only_no_cost': '每周只做多（无成本）',
    'weekly_long_only_with_cost': '每周只做多（含成本）',
    'weekly_long_only_risk_no_cost': '每周只做多+止损（无成本）',
    'weekly_long_only_risk_with_cost': '每周只做多+止损（含成本）',
}

SCENARIO_DESC = {
    'daily_long_short_no_cost': '每日按技术得分 Top 做多、Bottom 做空，无交易成本',
    'daily_long_short_with_cost': '每日按技术得分 Top 做多、Bottom 做空，扣 0.1% 单边成本',
    'weekly_long_only_no_cost': '每周五按技术得分判定下周方向，无成本',
    'weekly_long_only_with_cost': '每周五按技术得分判定下周方向，扣 0.1% 成本',
    'weekly_long_only_risk_no_cost': '每周五只做多 + 8% 止损，无成本',
    'weekly_long_only_risk_with_cost': '每周五只做多 + 8% 止损，含成本',
}


if __name__ == '__main__':
    import sys
    sys.path.insert(0, '/home/liudawei/github/daily_tracker_analytics/multi_agent')
    from core.data_layer import get_stock_data, calc_technical_indicators
    df, _ = get_stock_data('510300', calibrate=False)
    df = calc_technical_indicators(df)
    scenarios = scenario_backtests(df, ticker='510300')
    for s in scenarios[:6]:
        print(s)
    print('推荐:', recommend_scenario(scenarios))
