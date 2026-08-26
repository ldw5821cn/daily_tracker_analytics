#!/usr/bin/env python3
"""同花顺 Financial-API 本地封装：统一读取 .env 中的 HITHINK_FINANCE_API_KEY。

Usage:
    . etf_tracker/.venv/bin/activate
    python3 multi_agent/scripts/hithink_client.py --test

需要先配置仓库根目录 .env：
    HITHINK_FINANCE_API_KEY=your_key_here
"""
import argparse
import json
import os
import sys
from pathlib import Path

import dotenv

ROOT = Path('/home/liudawei/github/daily_tracker_analytics')
FUYAO_SCRIPT = Path('/tmp/hithink-finance-api/python/toolkit/fuyao/scripts/fuyao.py')


def load_api_key() -> str:
    """优先读取仓库 .env 的 HITHINK_FINANCE_API_KEY。"""
    dotenv.load_dotenv(ROOT / '.env')
    key = os.environ.get('HITHINK_FINANCE_API_KEY') or os.environ.get('FUYAO_TOKEN') or os.environ.get('API_KEY')
    if not key:
        raise RuntimeError(
            "未配置 HITHINK_FINANCE_API_KEY。\n"
            "请访问 https://fuyao.aicubes.cn/admin 创建 Key，\n"
            "然后写入仓库根目录 .env：HITHINK_FINANCE_API_KEY=your_key"
        )
    return key.strip()


def run_fuyao(args: list) -> dict:
    """调用 fuyao.py CLI，返回 JSON。"""
    env = os.environ.copy()
    env['HITHINK_FINANCE_API_KEY'] = load_api_key()
    cmd = [sys.executable, str(FUYAO_SCRIPT)] + args
    import subprocess
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd='/tmp/hithink-finance-api', timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"fuyao.py failed: {result.stderr}\nstdout: {result.stdout[:500]}")
    return json.loads(result.stdout)


def search_ticker(q: str, limit: int = 5) -> list:
    return run_fuyao(['tickers-search', '--q', q, '--limit', str(limit), '--format', 'json'])


def prices_snapshot(thscodes: list) -> dict:
    codes = ','.join(thscodes)
    return run_fuyao(['prices-snapshot', '--thscodes', codes, '--format', 'json'])


def valuations_snapshot(thscodes: list) -> dict:
    codes = ','.join(thscodes)
    return run_fuyao(['valuations-snapshot', '--thscodes', codes, '--format', 'json'])


def limit_up_ladder() -> list:
    return run_fuyao(['special-data-limit-up-ladder', '--format', 'json'])


def hot_stock_list() -> list:
    return run_fuyao(['special-data-hot-stock-list', '--format', 'json'])


def main():
    parser = argparse.ArgumentParser(description='同花顺 Financial-API 本地测试入口')
    parser.add_argument('--test', action='store_true', help='用贵州茅台跑一组测试')
    parser.add_argument('--search', help='搜索标的')
    parser.add_argument('--snapshot', help='查询行情，逗号分隔 thscode')
    parser.add_argument('--hot', action='store_true', help='查询热股榜')
    parser.add_argument('--ladder', action='store_true', help='查询连板天梯')
    args = parser.parse_args()

    try:
        load_api_key()
        print('API Key 已配置')
    except RuntimeError as e:
        print(e)
        sys.exit(1)

    if args.test:
        print('搜索贵州茅台...')
        print(json.dumps(search_ticker('贵州茅台', 1), ensure_ascii=False, indent=2))
        print('\n查询 600519.SH 行情...')
        print(json.dumps(prices_snapshot(['600519.SH']), ensure_ascii=False, indent=2))
    elif args.search:
        print(json.dumps(search_ticker(args.search), ensure_ascii=False, indent=2))
    elif args.snapshot:
        print(json.dumps(prices_snapshot(args.snapshot.split(',')), ensure_ascii=False, indent=2))
    elif args.hot:
        print(json.dumps(hot_stock_list(), ensure_ascii=False, indent=2))
    elif args.ladder:
        print(json.dumps(limit_up_ladder(), ensure_ascii=False, indent=2))
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
