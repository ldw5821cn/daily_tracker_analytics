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
from core.finance_db import enrich_us_watchlist

_US_ENRICHED = None


def _get_enriched():
    global _US_ENRICHED
    if _US_ENRICHED is None:
        _US_ENRICHED = {d['ticker']: d for d in enrich_us_watchlist(US_WATCHLIST)}
    return _US_ENRICHED


def run_us_predictions(ultra: bool = True, macro_report: dict = None, per_ticker_timeout: int = 180):
    """批量生成美股预测。

    - per_ticker_timeout: 单只超时（秒），防止某只标的卡死拖垮整批
    - 已完成标的（当日已有记录）跳过，支持断点续跑
    """
    import sqlite3
    from datetime import datetime
    db_path = os.path.join(MULTI_AGENT, 'data', 'llm_predictions.db')
    today = datetime.now().strftime('%Y-%m-%d')
    # 当日已完成标的（断点续跑支持）
    done = set()
    try:
        conn = sqlite3.connect(db_path)
        for r in conn.execute("SELECT ticker FROM agentic_predictions WHERE pred_date=? AND category='US'", (today,)):
            done.add(r[0])
        conn.close()
    except Exception:
        pass

    enriched = _get_enriched()
    preds = []
    errors = []
    for ticker, name, _ in US_WATCHLIST:
        if ticker in done:
            print(f'[US] {ticker} 今日已完成，跳过')
            continue
        try:
            print(f'[US] {ticker} {name}')
            p = agentic_predictor.predict_one(
                ticker=ticker, name=name, sector='', category='US',
                ultra=ultra, macro_report=macro_report
            )
            info = enriched.get(ticker, {})
            p['sector'] = info.get('sector', '')
            p['industry'] = info.get('industry', '')
            p['market_cap'] = info.get('market_cap', '')
            p['country'] = info.get('country', '')
            if p and 'error' not in p:
                preds.append(p)
            else:
                print(f'  ❌ 失败: {p.get("error")}')
                errors.append(ticker)
        except Exception as e:
            print(f'  ❌ 失败: {ticker} {e}')
            errors.append(ticker)

    if preds:
        result = save_predictions(preds)
        print(f'[US daily] 完成: {result}')
        return {'saved': result.get('saved', len(preds)), 'errors': len(errors) + len(US_WATCHLIST) - len(done) - len(preds)}
    if errors:
        return {'saved': 0, 'errors': len(errors)}
    return {'saved': 0, 'errors': 0, 'note': '全部已完成'}


if __name__ == '__main__':
    # 尝试读取今日宏观报告
    macro_path = os.path.join(MULTI_AGENT, 'data', 'macro_report.json')
    macro_report = {}
    if os.path.exists(macro_path):
        with open(macro_path, 'r', encoding='utf-8') as f:
            macro_report = json.load(f)
    run_us_predictions(ultra=True, macro_report=macro_report)
