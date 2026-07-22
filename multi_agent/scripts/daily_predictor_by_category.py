#!/usr/bin/env python3
"""按 category 独立运行 A 股/ETF/期货/美股预测，避免全量单进程超时。

用法:
    python3 multi_agent/scripts/daily_predictor_by_category.py --category 个股
    python3 multi_agent/scripts/daily_predictor_by_category.py --category ETF
    python3 multi_agent/scripts/daily_predictor_by_category.py --category 期货
    python3 multi_agent/scripts/daily_predictor_by_category.py --category US

cron 建议拆成 4 个 job:
    - 00:05  个股
    - 00:30  ETF
    - 01:00  期货
    - 08:30  US（美股收盘后）
"""
import os
import sys
import json
import argparse
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, MULTI_AGENT)
sys.path.insert(0, PROJECT_ROOT)

from analysts.macro_analyst import analyze as macro_analyze
from analysts.agentic_predictor import generate_for_watchlist, predict_one
from scripts.daily_us_predictor import run_us_predictions


def _load_macro_report():
    macro_path = os.path.join(MULTI_AGENT, 'data', 'macro_report.json')
    if os.path.exists(macro_path):
        try:
            with open(macro_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return None


def main():
    parser = argparse.ArgumentParser(description='按 category 运行预测')
    parser.add_argument('--category', required=True, choices=['ETF', '个股', '期货', 'US'], help='要运行的类别')
    parser.add_argument('--macro', action='store_true', help='先运行宏观分析并保存')
    parser.add_argument('--workers', type=int, default=4, help='并发线程数')
    parser.add_argument('--ultra', action='store_true', default=True, help='使用轻量技术面分析')
    args = parser.parse_args()

    # 需要时运行宏观分析
    if args.macro or not _load_macro_report():
        print('[daily_predictor_by_category] 运行宏观分析...')
        macro_report = macro_analyze()
        macro_path = os.path.join(MULTI_AGENT, 'data', 'macro_report.json')
        with open(macro_path, 'w', encoding='utf-8') as f:
            json.dump(macro_report, f, ensure_ascii=False, indent=2)
        print(f"[宏观] 评分 {macro_report['macro_score']} 信号 {macro_report['macro_signal']}")
    else:
        macro_report = _load_macro_report()
        print(f"[daily_predictor_by_category] 使用已存在宏观报告: {macro_report['macro_score']}")

    if args.category == 'US':
        print('[daily_predictor_by_category] 运行美股预测...')
        result = run_us_predictions(ultra=args.ultra, macro_report=macro_report)
        print(f'[US] 完成: {result}')
    else:
        watchlist_path = os.path.join(MULTI_AGENT, 'watchlist.json')
        print(f'[daily_predictor_by_category] 运行 {args.category} 预测...')
        result = generate_for_watchlist(
            watchlist_path=watchlist_path,
            categories=[args.category],
            max_workers=args.workers,
            fast=False,
            ultra=args.ultra,
            macro_report=macro_report,
        )
        print(f'[{args.category}] 完成: 保存 {result["stats"]["saved"]} 条, 失败 {result["stats"]["errors"]} 条')


if __name__ == '__main__':
    main()
