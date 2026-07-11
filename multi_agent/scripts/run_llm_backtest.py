"""CLI: 运行 LLM 信号回测评估，输出 JSON 报告。

Usage:
    python multi_agent/scripts/run_llm_backtest.py
    python multi_agent/scripts/run_llm_backtest.py --date 2026-07-11 --output multi_agent/data/llm_backtest_results.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE)

from multi_agent.strategy.backtest_engine import evaluate_signals, load_signals, build_report_text

OUTPUT_PATH = os.path.join(BASE, 'multi_agent', 'data', 'llm_backtest_results.json')


def main():
    parser = argparse.ArgumentParser(description='LLM 信号回测评估')
    parser.add_argument('--date', default=None, help='预测日期，默认最新')
    parser.add_argument('--output', default=os.path.join(BASE, 'multi_agent', 'data', 'llm_backtest_results.json'),
                        help='输出 JSON 路径')
    args = parser.parse_args()

    df = load_signals(args.date)
    if df.empty:
        print('error: no signals found', file=sys.stderr)
        sys.exit(1)

    result = evaluate_signals(df)
    result['output_path'] = args.output

    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(build_report_text(result))
    print(f"\n[已保存] {args.output}")


if __name__ == '__main__':
    main()
