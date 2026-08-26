#!/usr/bin/env python3
"""用同花顺 Financial-API 批量获取 A 股估值快照，写入 fundamentals_cache。

可作为 fetch_fundamentals_cache.py 的替代/补充数据源。
Usage:
    . etf_tracker/.venv/bin/activate
    python3 multi_agent/scripts/hithink_fundamentals_cache.py
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import dotenv

ROOT = Path('/home/liudawei/github/daily_tracker_analytics')
CACHE_DIR = ROOT / 'multi_agent' / 'data' / 'fundamentals_cache'
UNIVERSE_CSV = ROOT / 'multi_agent' / 'data' / 'a_share_universe_sz.csv'

sys.path.insert(0, '/tmp/hithink-finance-api/python/toolkit/fuyao/scripts')
from fuyao_client import (  # noqa: E402
    a_share_valuations_snapshot as _valuations_snapshot,
    prices_snapshot as _prices_snapshot,
    tickers_list as _tickers_list,
)


def _load_api_key() -> str:
    dotenv.load_dotenv(ROOT / '.env')
    key = os.environ.get('HITHINK_FINANCE_API_KEY') or os.environ.get('FUYAO_TOKEN') or os.environ.get('API_KEY')
    if not key:
        raise RuntimeError('未配置 HITHINK_FINANCE_API_KEY')
    return key.strip()


def load_universe_codes() -> List[str]:
    """从同花顺 Financial-API 拉取全 A 股代码列表。"""
    import os
    os.environ.setdefault('HITHINK_FINANCE_API_KEY', _load_api_key())
    items = _tickers_list(asset_type='a-share', limit=10000)
    codes = [str(i.get('ticker', '')).zfill(6) for i in items if str(i.get('ticker', '')).isdigit()]
    return sorted(set(codes))


def to_thscode(code: str) -> str:
    code = str(code).zfill(6)
    # 北交所 920/430/830/899 等挂 .BJ，hithink 列表 ticker 可能按 SH/SZ 返回但行情不支持
    if code.startswith(('92', '43', '83', '87', '88')):
        return f'{code}.BJ'
    if code.startswith(('68', '69', '9', '5')) or (code.startswith('6') and not code.startswith('60')):
        return f'{code}.SH'
    if code.startswith('6'):
        return f'{code}.SH'
    return f'{code}.SZ'


def hithink_fundamentals(codes: List[str], batch_size: int = 40) -> Dict[str, dict]:
    """批量拉取行情快照 + 估值快照，合并输出。"""
    os.environ.setdefault('HITHINK_FINANCE_API_KEY', _load_api_key())
    out: Dict[str, dict] = {}
    total = len(codes)
    for i in range(0, total, batch_size):
        batch = codes[i:i + batch_size]
        thscodes = [to_thscode(c) for c in batch]
        try:
            prices = _prices_snapshot(thscodes=thscodes)
            vals = _valuations_snapshot(thscodes=thscodes)
            # 构建 thscode -> price/val 索引
            price_map = {p.get('thscode'): p for p in prices if p.get('thscode')}
            val_map = {v.get('thscode'): v for v in vals.get('item', []) if v.get('thscode')}
            for c in batch:
                ths = to_thscode(c)
                p = price_map.get(ths, {})
                v = val_map.get(ths, {})
                if not p and not v:
                    continue
                out[c] = {
                    'name': p.get('name') or v.get('name'),
                    'close': p.get('last_price'),
                    'open': p.get('open_price'),
                    'high': p.get('high_price'),
                    'low': p.get('low_price'),
                    'volume': p.get('volume'),
                    'turnover': p.get('turnover'),
                    'price_change': p.get('price_change'),
                    'price_change_ratio_pct': p.get('price_change_ratio_pct'),
                    'market_cap': v.get('total_market_cap'),
                    'float_market_cap': v.get('float_market_cap'),
                    'pe_ratio': v.get('pe_ttm') or v.get('pe_dynamic'),
                    'pb_ratio': v.get('pb_mrq'),
                    'ps_ratio': v.get('ps_ttm'),
                    'pcf_ratio': v.get('pcf_ttm'),
                    'dividend_yield': v.get('dividend_yield'),
                    'source': 'hithink',
                }
        except Exception as e:
            print(f'[hithink] 批次 {i//batch_size + 1} 失败: {e}')
        if (i + batch_size) % 200 == 0 or (i + batch_size) >= total:
            print(f'[hithink] 已处理 {min(i + batch_size, total)}/{total}，成功 {len(out)}')
    return out


def merge_into_fundamentals_cache(hithink_data: Dict[str, dict]) -> Dict:
    """把 hithink 数据合并到现有 fundamentals_cache。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime('%Y-%m-%d')
    cache_path = CACHE_DIR / f'{today}.json'

    existing = {'date': today, 'fundamentals': {}}
    if cache_path.exists():
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                existing = json.load(f)
        except Exception:
            pass

    fundamentals = existing.setdefault('fundamentals', {})
    for code, data in hithink_data.items():
        if code not in fundamentals:
            fundamentals[code] = {}
        # hithink 数据优先覆盖
        fundamentals[code].update(data)
        fundamentals[code]['hithink_updated'] = today

    with open(cache_path, 'w', encoding='utf-8') as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    print(f'[hithink] 已合并 {len(hithink_data)} 条到 {cache_path}')
    return existing


def main():
    codes = load_universe_codes()
    print(f'[hithink] 目标标的 {len(codes)} 只')
    data = hithink_fundamentals(codes)
    merge_into_fundamentals_cache(data)


if __name__ == '__main__':
    main()
