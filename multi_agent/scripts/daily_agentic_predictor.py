#!/usr/bin/env python3
"""每日多 Agent 预测入口：先生成宏观分析，再批量生成 watchlist 预测。"""
import sys, os, json
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, MULTI_AGENT)

import argparse

from analysts.macro_analyst import analyze as macro_analyze
from analysts.agentic_predictor import generate_for_watchlist
from scripts.daily_us_predictor import run_us_predictions
from multi_agent.core.data_loader_registry import close_cached_loaders


def main():
    parser = argparse.ArgumentParser(description='每日多 Agent 预测入口')
    parser.add_argument('--date', type=str, help='预测日期（YYYY-MM-DD）')
    parser.add_argument('--workers', type=int, default=6, help='A股并发线程数')
    parser.add_argument('--item_timeout', type=int, default=90, help='单个标的硬超时秒数')
    parser.add_argument('--us_workers', type=int, default=4, help='美股并发线程数')
    parser.add_argument('--skip_us', action='store_true', help='跳过美股预测')
    parser.add_argument('--categories', type=str, default='ETF,个股,期货', help='逗号分隔的A股类别')
    parser.add_argument('--fast', action='store_true', help='跳过基本面和新闻，仅技术面+多空辩论')
    parser.add_argument('--ultra', action='store_true', help='启用 ultra 模式（默认 False，快模式）')
    args = parser.parse_args()

    os.environ['AGENTIC_ITEM_TIMEOUT'] = str(args.item_timeout)

    print('[daily_agentic_predictor] 启动宏观分析...')
    macro_report = macro_analyze()
    print(f"[宏观] 评分 {macro_report['macro_score']} 信号 {macro_report['macro_signal']}")
    # 保存宏观报告供后续验证、反思、A/B 测试复用
    macro_path = os.path.join(MULTI_AGENT, 'data', 'macro_report.json')
    with open(macro_path, 'w', encoding='utf-8') as f:
        json.dump(macro_report, f, ensure_ascii=False, indent=2)
    print(f'[daily_agentic_predictor] 宏观报告已保存: {macro_path}')
    print('[daily_agentic_predictor] 生成 A 股 watchlist 预测...')
    cats = [c.strip() for c in args.categories.split(',')]
    result = generate_for_watchlist(
        watchlist_path=os.path.join(MULTI_AGENT, 'watchlist.json'),
        categories=cats,
        max_workers=args.workers,
        fast=args.fast,
        ultra=args.ultra,
        macro_report=macro_report,
        prediction_date=args.date,
    )
    print(f"[daily_agentic_predictor] A 股完成: {result['stats']}")
    if not args.skip_us:
        print('[daily_agentic_predictor] 生成美股预测（并行 4 workers）...')
        us_result = run_us_predictions(ultra=args.ultra, macro_report=macro_report, max_workers=args.us_workers)
        print(f"[daily_agentic_predictor] 美股完成: {us_result}")
    close_cached_loaders()


if __name__ == '__main__':
    main()
