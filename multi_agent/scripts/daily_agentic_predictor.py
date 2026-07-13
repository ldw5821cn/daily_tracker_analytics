#!/usr/bin/env python3
"""每日多 Agent 预测入口：先生成宏观分析，再批量生成 watchlist 预测。"""
import sys, os
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, MULTI_AGENT)

from analysts.macro_analyst import analyze as macro_analyze
from analysts.agentic_predictor import generate_for_watchlist


def main():
    print('[daily_agentic_predictor] 启动宏观分析...')
    macro_report = macro_analyze()
    print(f"[宏观] 评分 {macro_report['macro_score']} 信号 {macro_report['macro_signal']}")
    print('[daily_agentic_predictor] 生成 watchlist 预测...')
    result = generate_for_watchlist(
        watchlist_path=os.path.join(MULTI_AGENT, 'watchlist.json'),
        categories=['ETF', '个股', '期货'],
        max_workers=4,
        fast=True,  # 跳过基本面/新闻，提速；日常 cron 可改为 False 以使用完整分析
        ultra=True,
        macro_report=macro_report,
    )
    print(f"[daily_agentic_predictor] 完成: {result['stats']}")


if __name__ == '__main__':
    main()
