#!/usr/bin/env python3
"""美股每日预测入口：用 akshare 新浪美股数据跑 US_WATCHLIST。"""
from __future__ import annotations

import os
import sys
import json
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, MULTI_AGENT)

from analysts import agentic_predictor
from core.us_data import US_WATCHLIST
from core.db import save_predictions


def run_us_predictions(ultra: bool = True, macro_report: dict = None):
    preds = []
    for ticker, name, sector in US_WATCHLIST:
        print(f'[US] {ticker} {name}')
        p = agentic_predictor.predict_one(
            ticker=ticker, name=name, sector=sector, category='US',
            ultra=ultra, macro_report=macro_report
        )
        if p and 'error' not in p:
            preds.append(p)
        else:
            print(f'  ❌ 失败: {p.get("error")}')

    if not preds:
        return {'saved': 0, 'errors': len(US_WATCHLIST)}

    # 保存到数据库，category 为 US
    result = save_predictions(preds)
    print(f'[US daily] 完成: {result}')
    return result


if __name__ == '__main__':
    # 尝试读取今日宏观报告
    macro_path = os.path.join(MULTI_AGENT, 'data', 'macro_report.json')
    macro_report = {}
    if os.path.exists(macro_path):
        with open(macro_path, 'r', encoding='utf-8') as f:
            macro_report = json.load(f)
    run_us_predictions(ultra=True, macro_report=macro_report)
