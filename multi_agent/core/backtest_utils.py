#!/usr/bin/env python3
"""统一回测指标解析工具。

用于把 multi_period_backtest 返回的 JSON summary 转换成可排序的指标和综合分。
避免在日报、页面生成脚本里重复定义解析逻辑。
"""
import json
from typing import Dict, Tuple


def parse_backtest_summary(backtest_summary) -> Dict:
    """解析回测汇总，返回关键指标：60日收益、回撤、夏普、综合得分。"""
    if isinstance(backtest_summary, str):
        try:
            backtest_summary = json.loads(backtest_summary)
        except Exception:
            backtest_summary = {}
    if not backtest_summary or not isinstance(backtest_summary, dict):
        return {'return_60d': 0, 'max_dd_60d': 0, 'sharpe_60d': 0, 'bt_score': 0}

    periods = backtest_summary.get('periods', [])
    r30 = next((p for p in periods if p.get('period') == '近30天'), {})
    r60 = next((p for p in periods if p.get('period') == '近60天'), {})

    ret60 = r60.get('return', 0)
    dd60 = r60.get('max_drawdown', 0)
    sharpe60 = r60.get('sharpe', 0)
    ret30 = r30.get('return', 0)

    # 截断异常收益，防止复权/退市导致排序被拉高
    ret60 = max(min(ret60, 150), -150)
    ret30 = max(min(ret30, 100), -100)
    bt_score = ret60 * 0.5 + ret30 * 0.3 - abs(dd60) * 0.2

    return {
        'return_60d': r60.get('return', 0),   # 原始收益，展示用
        'max_dd_60d': dd60,
        'sharpe_60d': sharpe60,
        'bt_score': bt_score,                    # 排序用
    }


def inject_backtest_metrics(pred: Dict) -> Dict:
    """给预测字典注入回测指标，返回原字典（已修改）。"""
    bt = parse_backtest_summary(pred.get('backtest_summary'))
    pred.update({
        'bt_return_60d': bt['return_60d'],
        'bt_max_dd_60d': bt['max_dd_60d'],
        'bt_sharpe_60d': bt['sharpe_60d'],
        'bt_score': bt['bt_score'],
    })
    return pred


def sort_by_backtest(preds, reverse=True):
    """按回测综合分排序预测列表。"""
    for p in preds:
        inject_backtest_metrics(p)
    return sorted(preds, key=lambda x: x.get('bt_score', 0), reverse=reverse)
