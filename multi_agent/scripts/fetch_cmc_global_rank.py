#!/usr/bin/env python3
"""抓取 CompaniesMarketCap 全球市值前 N 公司排名。

输出 JSON，保存到 multi_agent/data/cmc_global_rank.json。
字段：rank, name, ticker, market_cap_str, market_cap_usd, price_usd, change_pct, country
"""
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta

import requests

ROOT = '/home/liudawei/github/daily_tracker_analytics'
DATA_PATH = os.path.join(ROOT, 'multi_agent', 'data', 'cmc_global_rank.json')
TOP_N = 50
URL = 'https://companiesmarketcap.com/'


def _parse_market_cap(val: int) -> str:
    if val >= 1_000_000_000_000:
        return f"${val / 1_000_000_000_000:.3f}T"
    if val >= 1_000_000_000:
        return f"${val / 1_000_000_000:.2f}B"
    return f"${val / 1_000_000:.2f}M"


def _parse_price(val: int) -> str:
    return f"${val / 100:.2f}"


def fetch():
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    resp = requests.get(URL, headers=headers, timeout=30)
    resp.raise_for_status()
    html = resp.text

    # 提取表体
    m = re.search(r'<tbody>(.*?)</tbody>', html, re.S)
    if not m:
        raise RuntimeError('No tbody found in CMC page')
    tbody = m.group(1)

    rows = re.findall(r'<tr>(.*?)</tr>', tbody, re.S)
    items = []
    for row in rows[:TOP_N]:
        rank_m = re.search(r'data-sort="(\d+)"', row)
        name_m = re.search(r'<div class="company-name"\u003e([^\u003c]+)', row)
        code_m = re.search(r'<div class="company-code"\u003e.*?\u003cspan class="rank[^\u003e]*\u003e\u003c/span\u003e([^\u003c]+)', row)
        cap_m = re.search(r'data-sort="(\d+)"[^\u003c]*\u003cspan class="currency-symbol-left"\u003e', row)
        # price: text already contains $; we extract the textual price rather than ambiguous data-sort
        price_text_m = re.search(r'\u003ctd class="td-right" data-sort="\d+(?:\.\d+)?"\u003e\s*\u003cspan class="currency-symbol-left"\u003e\$\u003c/span\u003e([\d,.]+)\u003c/td\u003e', row)
        price_text_m = re.search(r'\u003ctd class="td-right" data-sort="\d+(?:\.\d+)?"\u003e\$([\d,.]+)\u003c/td\u003e', row) if not price_text_m else price_text_m
        # change: row shows data-sort, then span percentage. positive=green, negative=red.
        change_m = re.search(r'data-sort="(-?\d+)"\s+class="rh-sm"', row)
        country_m = re.search(r'<span class="responsive-hidden">([^\u003c]+)', row)

        if not (rank_m and name_m and code_m and cap_m):
            continue
        rank = int(rank_m.group(1))
        name = name_m.group(1).strip()
        ticker = code_m.group(1).strip()
        market_cap_usd = int(cap_m.group(1))
        # price from rendered text
        if price_text_m:
            price_usd = float(price_text_m.group(1).replace(',', ''))
        else:
            price_usd = None
        # change: row shows data-sort, then span percentage. positive=green, negative=red.
        change_raw = int(change_m.group(1)) if change_m else 0
        change_pct = change_raw / 100.0
        country = (country_m.group(1).strip() if country_m else '').replace('USA', '美国')

        items.append({
            'rank': rank,
            'name': name,
            'ticker': ticker,
            'market_cap_str': _parse_market_cap(market_cap_usd) if market_cap_usd else '',
            'market_cap_usd': market_cap_usd,
            'price_str': _parse_price(int(price_usd * 100)) if price_usd else '',
            'price_usd': price_usd,
            'change_pct': change_pct,
            'country': country,
        })

    tz = timezone(timedelta(hours=8))
    data = {
        'generated_at': datetime.now(tz).isoformat(),
        'source_url': URL,
        'top_n': len(items),
        'items': items,
    }
    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"已保存 {len(items)} 条 CMC 排名到 {DATA_PATH}")
    return data


if __name__ == '__main__':
    fetch()
