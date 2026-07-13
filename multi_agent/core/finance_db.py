#!/usr/bin/env python3
"""加载 FinanceDatabase 本地缓存，为美股 watchlist 标注行业/市值。"""
from __future__ import annotations
import os
import sys
import pandas as pd

MULTI_AGENT = os.path.join(os.path.dirname(__file__), '..', '..', 'multi_agent')
CACHE_DIR = os.path.join(MULTI_AGENT, 'data', 'finance_db_cache')


def load_equities_df() -> pd.DataFrame | None:
    path = os.path.join(CACHE_DIR, 'equities.bz2')
    if not os.path.exists(path):
        return None
    try:
        return pd.read_csv(path, compression='bz2', index_col=0, low_memory=False)
    except Exception as e:
        print(f'[FinanceDB] 读取失败: {e}')
        return None


def enrich_us_watchlist(watchlist: list[tuple[str, str, str]]) -> list[dict]:
    """为 watchlist 添加 sector/market_cap 等字段。"""
    df = load_equities_df()
    if df is None:
        return [{'ticker': t, 'name': n, 'sector': s, 'market_cap': '', 'industry': ''} for t, n, s in watchlist]
    enriched = []
    for ticker, name, sector in watchlist:
        info = df.loc[df.index == ticker] if ticker in df.index else None
        if info is not None and len(info) > 0:
            row = info.iloc[0]
            enriched.append({
                'ticker': ticker,
                'name': name,
                'sector': str(row.get('sector', sector)) if pd.notna(row.get('sector')) else sector,
                'industry': str(row.get('industry', '')) if pd.notna(row.get('industry')) else '',
                'market_cap': str(row.get('market_cap', '')) if pd.notna(row.get('market_cap')) else '',
                'exchange': str(row.get('exchange', '')) if pd.notna(row.get('exchange')) else '',
                'country': str(row.get('country', '')) if pd.notna(row.get('country')) else '',
            })
        else:
            enriched.append({'ticker': ticker, 'name': name, 'sector': sector, 'market_cap': '', 'industry': ''})
    return enriched


if __name__ == '__main__':
    from core.us_data import US_WATCHLIST
    for item in enrich_us_watchlist(US_WATCHLIST):
        print(item)
