#!/usr/bin/env python3
"""分块运行 generate_for_watchlist，避免大批量运行时 worker 被无超时网络调用永久卡死。

用法:
  python chunk_predictor.py --chunk-file /tmp/chunk_03.json [--macro multi_agent/data/macro_report.json]
"""
import sys, os, json, argparse

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, MULTI_AGENT)

from analysts.agentic_predictor import generate_for_watchlist


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--chunk-file', required=True)
    ap.add_argument('--macro', default=None)
    args = ap.parse_args()

    with open(args.chunk_file, 'r', encoding='utf-8') as f:
        items = json.load(f)
    print(f"[chunk] {len(items)} 个标的: {[i['ticker'] for i in items][:5]}...", flush=True)

    macro_report = None
    if args.macro and os.path.exists(args.macro):
        with open(args.macro, 'r', encoding='utf-8') as f:
            macro_report = json.load(f)

    result = generate_for_watchlist(
        watchlist_path=args.chunk_file,
        categories=['ETF', '个股', '期货'],
        max_workers=4,
        fast=False,
        ultra=True,
        macro_report=macro_report,
    )
    print(f"[chunk] 完成: {result['stats']}", flush=True)


if __name__ == '__main__':
    main()
