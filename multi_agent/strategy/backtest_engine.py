"""
LLM 信号组合回测评估引擎。

当前约束：
- agentic_predictions 只有最近 1-2 天信号，无法做完整 vectorbt 滚动回测。
- 因此先用每行自带的 backtest_summary 做信号分组评估。
- 等积累 1-3 个月历史信号后，可启用 run_backtest() 做真正的组合级回测。

职责：
1. 读取 agentic_predictions 信号
2. 解析 backtest_summary 历史回测指标
3. 按 bullish/bearish/neutral 分组统计
4. 输出评估报告（JSON/CLI/页面）
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

BASE = '/home/liudawei/github/daily_tracker_analytics'
DB_PATH = os.path.join(BASE, 'multi_agent', 'data', 'llm_predictions.db')


def load_signals(pred_date: Optional[str] = None) -> pd.DataFrame:
    """从 agentic_predictions 读取最新信号。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        if pred_date is None:
            row = conn.execute("SELECT MAX(pred_date) FROM agentic_predictions").fetchone()
            pred_date = row[0]
        df = pd.read_sql_query(
            """
            SELECT ticker, name, category, sector, signal, confidence,
                   current_price, target_price, stop_loss, pred_date,
                   backtest_summary, weighted_score
            FROM agentic_predictions
            WHERE pred_date = ?
            """,
            conn,
            params=(pred_date,),
        )
    finally:
        conn.close()
    df['pred_date'] = pd.to_datetime(df['pred_date'])
    return df


def _parse_backtest_summary(row) -> Dict[str, float]:
    """解析 backtest_summary JSON，提取 60d/30d 收益、最大回撤、夏普。"""
    raw = row.get('backtest_summary') if isinstance(row, dict) else getattr(row, 'backtest_summary', None)
    if not raw:
        return {}
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return {}
    periods = data.get('periods', []) if isinstance(data, dict) else []
    result = {}
    for p in periods:
        period = p.get('period', '')
        # 映射中文周期到天数
        if '30' in period:
            days = 30
        elif '60' in period:
            days = 60
        elif '90' in period:
            days = 90
        elif '120' in period:
            days = 120
        else:
            continue
        key = f"return_{days}d"
        result[key] = p.get('total_return', p.get('return', 0))
        result[f"max_drawdown_{days}d"] = p.get('max_drawdown', 0)
        result[f"sharpe_{days}d"] = p.get('sharpe', 0)
    return result


def evaluate_signals(df: pd.DataFrame) -> Dict[str, Dict]:
    """按信号方向和 category 分组统计回测指标。"""
    metrics = []
    for _, row in df.iterrows():
        bt = _parse_backtest_summary(row)
        metrics.append({
            'ticker': row['ticker'],
            'category': row['category'],
            'signal': row['signal'],
            'confidence': row['confidence'],
            'return_60d': bt.get('return_60d', 0),
            'return_30d': bt.get('return_30d', 0),
            'max_drawdown_60d': bt.get('max_drawdown_60d', 0),
            'sharpe_60d': bt.get('sharpe_60d', 0),
        })
    mdf = pd.DataFrame(metrics)
    if mdf.empty:
        return {}

    # 全局按信号分组
    overall = mdf.groupby('signal').agg(
        count=('ticker', 'count'),
        avg_return_60d=('return_60d', 'mean'),
        avg_return_30d=('return_30d', 'mean'),
        avg_max_drawdown_60d=('max_drawdown_60d', 'mean'),
        avg_sharpe_60d=('sharpe_60d', 'mean'),
        win_rate_60d=('return_60d', lambda x: (x > 0).mean() * 100),
    ).round(2)

    # 按 category + 信号分组
    by_category = {}
    for cat in mdf['category'].unique():
        sub = mdf[mdf['category'] == cat]
        by_category[cat] = sub.groupby('signal').agg(
            count=('ticker', 'count'),
            avg_return_60d=('return_60d', 'mean'),
            avg_return_30d=('return_30d', 'mean'),
            avg_max_drawdown_60d=('max_drawdown_60d', 'mean'),
            avg_sharpe_60d=('sharpe_60d', 'mean'),
            win_rate_60d=('return_60d', lambda x: (x > 0).mean() * 100),
        ).round(2).to_dict('index')

    return {
        'overall': overall.to_dict('index'),
        'by_category': by_category,
        'signal_count': mdf['signal'].value_counts().to_dict(),
        'pred_date': df['pred_date'].iloc[0].strftime('%Y-%m-%d'),
    }


def build_report_text(result: Dict) -> str:
    """生成人类可读的 Markdown 评估报告。"""
    lines = []
    lines.append(f"# LLM 信号评估报告 ({result.get('pred_date', '')})")
    lines.append("")
    lines.append("## 信号分布")
    for sig, cnt in result.get('signal_count', {}).items():
        lines.append(f"- {sig}: {cnt}")
    lines.append("")

    lines.append("## 整体回测表现（按信号分组）")
    lines.append("| 信号 | 数量 | 60日收益 | 30日收益 | 60日回撤 | 60日夏普 | 60日胜率 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for sig, vals in result.get('overall', {}).items():
        lines.append(
            f"| {sig} | {vals.get('count', 0)} | "
            f"{vals.get('avg_return_60d', 0):.2f}% | "
            f"{vals.get('avg_return_30d', 0):.2f}% | "
            f"{vals.get('avg_max_drawdown_60d', 0):.2f}% | "
            f"{vals.get('avg_sharpe_60d', 0):.2f} | "
            f"{vals.get('win_rate_60d', 0):.1f}% |"
        )
    lines.append("")

    for cat, groups in result.get('by_category', {}).items():
        lines.append(f"## {cat} 回测表现")
        lines.append("| 信号 | 数量 | 60日收益 | 30日收益 | 60日回撤 | 60日夏普 | 60日胜率 |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for sig, vals in groups.items():
            lines.append(
                f"| {sig} | {vals.get('count', 0)} | "
                f"{vals.get('avg_return_60d', 0):.2f}% | "
                f"{vals.get('avg_return_30d', 0):.2f}% | "
                f"{vals.get('avg_max_drawdown_60d', 0):.2f}% | "
                f"{vals.get('avg_sharpe_60d', 0):.2f} | "
                f"{vals.get('win_rate_60d', 0):.1f}% |"
            )
        lines.append("")

    lines.append(
        "_注：以上为基于历史回测指标（backtest_summary）的聚合评估，"
        "并非真实 forward 组合回测。待积累 1-3 个月历史信号后，"
        "可用 vectorbt 跑完整组合回测。_"
    )
    return "\n".join(lines)


# ── 预留：真正的 vectorbt 组合回测 ──

def run_backtest(weights_df: pd.DataFrame, prices_df: pd.DataFrame) -> Dict:
    """vectorbt 组合回测占位。需要至少 1-3 个月历史信号才能启用。"""
    import vectorbt as vbt

    weights_wide = weights_df.pivot(index='date', columns='ticker', values='weight').fillna(0)
    weights_wide = weights_wide.reindex(prices_df.index).fillna(0)
    weights_wide = weights_wide[prices_df.columns]

    portfolio = vbt.Portfolio.from_orders(
        close=prices_df,
        size=weights_wide,
        size_type='targetpercent',
        freq='1d',
        init_cash=100_000,
        fees=0.0003,
        slippage=0.0005,
    )
    stats = portfolio.stats()
    return {
        'total_return': float(stats.get('Total Return [%]', 0)),
        'sharpe': float(stats.get('Sharpe Ratio', 0)),
        'max_drawdown': float(stats.get('Max Drawdown [%]', 0)),
        'calmar': float(stats.get('Calmar Ratio', 0)),
        'win_rate': float(stats.get('Win Rate [%]', 0)),
        'trades': int(stats.get('Total Trades', 0)),
    }


def main():
    df = load_signals()
    result = evaluate_signals(df)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("\n" + "=" * 60 + "\n")
    print(build_report_text(result))


if __name__ == '__main__':
    main()
