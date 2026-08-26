#!/usr/bin/env python3
"""拉取同花顺 Financial-API 特色数据：涨停池、跌停池、炸板池、连板天梯、热股榜、龙虎榜。

Usage:
    . etf_tracker/.venv/bin/activate
    python3 multi_agent/scripts/hithink_special_data.py
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import dotenv

ROOT = Path('/home/liudawei/github/daily_tracker_analytics')
CACHE_DIR = ROOT / 'multi_agent' / 'data' / 'hithink_cache'

sys.path.insert(0, '/tmp/hithink-finance-api/python/toolkit/fuyao/scripts')
from fuyao_client import (  # noqa: E402
    special_data_limit_up_pool as _limit_up_pool,
    special_data_limit_down_pool as _limit_down_pool,
    special_data_limit_break_pool as _limit_break_pool,
    special_data_limit_up_ladder as _limit_up_ladder,
    special_data_hot_stock_list as _hot_stock_list,
    special_data_dragon_tiger_list as _dragon_tiger_list,
)


def _load_api_key() -> str:
    dotenv.load_dotenv(ROOT / '.env')
    key = os.environ.get('HITHINK_FINANCE_API_KEY') or os.environ.get('FUYAO_TOKEN') or os.environ.get('API_KEY')
    if not key:
        raise RuntimeError('未配置 HITHINK_FINANCE_API_KEY')
    return key.strip()


def save_json(name: str, data: dict):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime('%Y-%m-%d')
    path = CACHE_DIR / f'{name}_{today}.json'
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f'[hithink] {name} -> {path} ({len(data.get("item", [])) if "item" in data else "-"} items)')
    return path


def fetch_all():
    os.environ.setdefault('HITHINK_FINANCE_API_KEY', _load_api_key())
    save_json('limit_up_pool', _limit_up_pool())
    save_json('limit_down_pool', _limit_down_pool())
    save_json('limit_break_pool', _limit_break_pool())
    save_json('limit_up_ladder', _limit_up_ladder())
    save_json('hot_stock_list', _hot_stock_list())
    save_json('dragon_tiger_list', _dragon_tiger_list())


if __name__ == '__main__':
    fetch_all()
