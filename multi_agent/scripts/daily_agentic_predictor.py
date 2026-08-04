#!/usr/bin/env python3
"""每日多 Agent 预测入口：先生成宏观分析，再批量生成 watchlist 预测。"""
import sys, os, json
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, MULTI_AGENT)

from analysts.macro_analyst import analyze as macro_analyze
from analysts.agentic_predictor import generate_for_watchlist
from scripts.daily_us_predictor import run_us_predictions
from multi_agent.core.data_loader_registry import close_cached_loaders


def main():
    print('[daily_agentic_predictor] 启动宏观分析...')
    macro_report = macro_analyze()
    print(f"[宏观] 评分 {macro_report['macro_score']} 信号 {macro_report['macro_signal']}")
    # 保存宏观报告供后续验证、反思、A/B 测试复用
    macro_path = os.path.join(MULTI_AGENT, 'data', 'macro_report.json')
    with open(macro_path, 'w', encoding='utf-8') as f:
        json.dump(macro_report, f, ensure_ascii=False, indent=2)
    print(f'[daily_agentic_predictor] 宏观报告已保存: {macro_path}')
    print('[daily_agentic_predictor] 生成 A 股 watchlist 预测...')
    result = generate_for_watchlist(
        watchlist_path=os.path.join(MULTI_AGENT, 'watchlist.json'),
        categories=['ETF', '个股', '期货'],
        max_workers=4,
        fast=False,
        ultra=True,
        macro_report=macro_report,
    )
    print(f"[daily_agentic_predictor] A 股完成: {result['stats']}")
    print('[daily_agentic_predictor] 生成美股预测...')
    us_result = run_us_predictions(ultra=True, macro_report=macro_report)
    print(f"[daily_agentic_predictor] 美股完成: {us_result}")
    close_cached_loaders()


if __name__ == '__main__':
    main()
