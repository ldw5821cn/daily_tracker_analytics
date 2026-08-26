#!/usr/bin/env python3
"""用同花顺 Financial-API 抓取个股财务数据（利润表/资产负债表/现金流量表/财务指标）。

Usage:
    . etf_tracker/.venv/bin/activate
    python3 multi_agent/scripts/hithink_financials.py --code 600519.SH --report 2025-4
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import dotenv

ROOT = Path('/home/liudawei/github/daily_tracker_analytics')
CACHE_DIR = ROOT / 'multi_agent' / 'data' / 'hithink_cache' / 'financials'

sys.path.insert(0, '/tmp/hithink-finance-api/python/toolkit/fuyao/scripts')
from fuyao_client import (  # noqa: E402
    financials_income_statements as _income,
    financials_balance_sheets as _balance,
    financials_cash_flow_statements as _cashflow,
    financials_indicators as _indicators,
)


def _load_api_key() -> str:
    dotenv.load_dotenv(ROOT / '.env')
    key = os.environ.get('HITHINK_FINANCE_API_KEY') or os.environ.get('FUYAO_TOKEN') or os.environ.get('API_KEY')
    if not key:
        raise RuntimeError('未配置 HITHINK_FINANCE_API_KEY')
    return key.strip()


def fetch_financials(thscode: str, report: str | None = None) -> dict:
    os.environ.setdefault('HITHINK_FINANCE_API_KEY', _load_api_key())
    result = {
        'thscode': thscode,
        'report': report,
        'income': _income(thscode),
        'balance': _balance(thscode),
        'cashflow': _cashflow(thscode),
    }
    if report:
        result['indicators'] = _indicators(thscode, report=report)
    return result


def save(thscode: str, data: dict):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime('%Y-%m-%d')
    path = CACHE_DIR / f'{thscode.replace(".", "_")}_{today}.json'
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f'[hithink] {thscode} -> {path}')
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--code', required=True, help='thscode e.g. 600519.SH')
    parser.add_argument('--report', default=None, help='财报期 e.g. 2025-4')
    args = parser.parse_args()
    data = fetch_financials(args.code, args.report)
    save(args.code, data)


if __name__ == '__main__':
    main()
