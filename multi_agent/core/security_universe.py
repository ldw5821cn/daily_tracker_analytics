#!/usr/bin/env python3
"""统一证券库服务（基于 FinanceDatabase 本地缓存）。

把 FinanceDatabase 从"只给美股 watchlist 注解行业"升级为系统的**标的发现 + 分类标准化**层：
- 跨市场候选池：A股(country=China, Tushare 格式 000002.SZ/600xxx.SH) + 美股 + ETF
- 分类标准化：统一 sector/industry_group/market_cap/exchange/country 口径
- 代码映射：ticker <-> 带市场前缀代码(SH600519/SZ000002) <-> Tushare 格式(600519.SH)

数据缓存：multi_agent/data/finance_db_cache/{equities,etfs,funds,indices,cryptos}.bz2
（由 FinanceDatabase 官方数据生成，随仓库分发；如缺失可用 `python -c "from financedatabase import Database; Database().scrape()` 重新拉取）

用法：
    from core.security_universe import SecurityUniverse
    su = SecurityUniverse()
    # 候选池：A股金融板块、市值 50-500亿
    pool = su.discover('a_share', sector='Financials', mcap_min=5e9, mcap_max=5e10)
    # 标准化单只标的
    info = su.classify('600519.SH')
"""
from __future__ import annotations

import functools
import os

import pandas as pd

_CACHE_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'finance_db_cache')

# 市场识别规则（与系统 data_layer / xueqiu 一致）
_CN_EXCHANGES = {'SH', 'SS', 'SSE', 'SZ', 'SZSE'}   # 中国交易所代码在 index 后缀里（SS=沪=SH）

# FinanceDatabase 的市值是分类标签（不是数值），从大到小排序
_MCAP_ORDER = ['Mega Cap', 'Large Cap', 'Mid Cap', 'Small Cap', 'Micro Cap', 'Nano Cap']


def _cn_exch(exch: str) -> str:
    """统一中国交易所后缀：SS/SSE->SH，SZSE->SZ。"""
    e = (exch or '').upper()
    return {'SS': 'SH', 'SSE': 'SH', 'SZSE': 'SZ'}.get(e, e)


def _norm_index(ticker) -> str:
    """标准化 index：600519.SS->600519.SH，SH600519->600519.SH，AAPL 不变。容忍非字符串（NaN）。"""
    if not isinstance(ticker, str):
        return str(ticker)
    t = ticker.strip().upper()
    # 带市场前缀格式：SH600519 / SZ000002 -> 600519.SH / 000002.SZ
    if len(t) == 8 and t[:2] in ('SH', 'SZ', 'BJ') and t[2:].isdigit():
        exch = 'SH' if t[:2] == 'SH' else ('BJ' if t[:2] == 'BJ' else 'SZ')
        return f'{t[2:]}.{exch}'
    if '.' in t:
        code, exch = t.rsplit('.', 1)
        return f'{code}.{_cn_exch(exch)}'
    return t


def _ticker_to_prefixed(ticker: str) -> str:
    """600519.SH / 600519.SS / 600519 -> SH600519（系统 xueqiu/portfolio_state 格式）"""
    t = _norm_index(ticker)
    if '.' in t:
        code, exch = t.split('.')
        return f'{exch}{code}'
    # 无前缀：按代码段判断
    if t.startswith(('600', '601', '603', '605', '688')):
        return f'SH{t}'
    if t.startswith(('000', '001', '002', '003', '300', '301')):
        return f'SZ{t}'
    if t.startswith(('4', '8')):
        return f'BJ{t}'
    return t


def _prefixed_to_ticker(prefixed: str) -> str:
    """SH600519 -> 600519.SH（Tushare/TickFlow 格式）"""
    p = prefixed.strip().upper()
    if p[:2] in ('SH', 'SZ', 'BJ') and p[2:].isdigit():
        return f'{p[2:]}.{"SH" if p[:2]=="SH" else ("BJ" if p[:2]=="BJ" else "SZ")}'
    return p


class SecurityUniverse:
    """FinanceDatabase 统一封装：发现 + 分类 + 映射。"""

    def __init__(self, cache_dir: str | None = None):
        self.cache_dir = cache_dir or _CACHE_DIR
        self._df: pd.DataFrame | None = None

    # ---------- 数据加载 ----------
    def _load(self) -> pd.DataFrame:
        if self._df is not None:
            return self._df
        path = os.path.join(self.cache_dir, 'equities.bz2')
        if not os.path.exists(path):
            raise FileNotFoundError(
                f'FinanceDatabase 缓存缺失: {path}。'
                f'请运行: python -c "from financedatabase import Database; Database().scrape()"')
        self._df = pd.read_csv(path, compression='bz2', index_col=0, low_memory=False)
        return self._df

    @functools.lru_cache(maxsize=4)
    def _by_market(self, market: str) -> pd.DataFrame:
        df = self._load()
        # 标准化 index 后缀（SS->SH），便于统一识别 A股
        idx = pd.Series([_norm_index(i) for i in df.index], index=df.index)
        if market == 'a_share':
            # 中国 A股：index 后缀为 SH/SZ（沪主板/科创 + 深主板/创业板）
            mask = idx.str.endswith(('.SH', '.SZ'), na=False)
            sub = df[mask]
            # 用标准化后的 index 替换，保证 classify/discover 返回一致格式
            sub = sub.copy()
            sub.index = [_norm_index(i) for i in sub.index]
            return sub
        if market == 'us':
            return df[df['country'].astype(str).str.contains('United States', case=False, na=False)]
        return df

    # ---------- 核心能力 1：标的发现 ----------
    def discover(self, market: str = 'a_share', sector: str | None = None,
                 industry: str | None = None, country: str | None = None,
                 mcap_min: str | None = None, mcap_max: str | None = None,
                 exclude_delisted: bool = True, limit: int | None = None) -> pd.DataFrame:
        """按条件筛选候选池，返回标准化 DataFrame（index=ticker，A股为 600519.SH 格式）。

        market: 'a_share' | 'us' | 'all'
        mcap_min/max: 市值分类标签（'Mega Cap'/'Large Cap'/'Mid Cap'/'Small Cap'/'Micro Cap'/'Nano Cap'），
                      按从大到小过滤；如 mcap_min='Mid Cap' 表示只要 Mid 及以上。
        """
        df = self._by_market(market)
        if exclude_delisted and 'delisted' in df:
            df = df[~df['delisted'].fillna(False).astype(bool)]
        if sector:
            df = df[df['sector'].astype(str).str.contains(sector, case=False, na=False)]
        if industry:
            df = df[df['industry'].astype(str).str.contains(industry, case=False, na=False)]
        if country:
            df = df[df['country'].astype(str).str.contains(country, case=False, na=False)]
        if (mcap_min or mcap_max) and 'market_cap' in df:
            order = _MCAP_ORDER
            def rank(v):
                v = str(v)
                return order.index(v) if v in order else len(order)
            ranks = df['market_cap'].map(rank)
            if mcap_min:
                lo = order.index(mcap_min) if mcap_min in order else 0
                df = df[ranks <= lo]
            if mcap_max:
                hi = order.index(mcap_max) if mcap_max in order else len(order) - 1
                df = df[ranks >= hi]
        if limit:
            df = df.head(limit)
        return df

    # ---------- 核心能力 2：分类标准化 ----------
    def _lookup(self, df: pd.DataFrame, ticker: str):
        """在 df 里查 ticker（自动标准化 .SS/.SH 后缀）。返回命中行或 None。"""
        t = _norm_index(ticker)
        if t in df.index:
            hit = df.loc[df.index == t]
            if len(hit):
                return hit
        # 遍历标准化匹配（数据可能是 .SS 原始后缀）
        norm = { _norm_index(i): i for i in df.index }
        orig = norm.get(t)
        if orig is not None:
            hit = df.loc[df.index == orig]
            if len(hit):
                return hit
        # 尝试原样（美股等无后缀代码）
        if ticker.upper() in df.index:
            hit = df.loc[df.index == ticker.upper()]
            if len(hit):
                return hit
        return None

    def classify(self, ticker: str) -> dict:
        """标准化单只标的的分类信息。支持 600519.SH / 600519.SS / SH600519 / AAPL。"""
        df = self._load()
        t = _norm_index(ticker)
        hit = self._lookup(df, ticker)
        if hit is None:
            return {'ticker': t, 'found': False}
        row = hit.iloc[0]
        def s(col):
            v = row.get(col)
            return str(v) if pd.notna(v) else ''
        return {
            'ticker': t,
            'prefixed': _ticker_to_prefixed(t),
            'name': s('name'),
            'sector': s('sector'),
            'industry_group': s('industry_group'),
            'industry': s('industry'),
            'exchange': s('exchange'),
            'country': s('country'),
            'market_cap': s('market_cap'),   # 分类标签（Mega/Large/Mid/Small/Micro/Nano Cap）
            'isin': s('isin'),
            'found': True,
        }

    # ---------- 核心能力 3：代码映射 ----------
    def to_prefixed(self, ticker: str) -> str:
        return _ticker_to_prefixed(ticker)

    def to_tushare(self, prefixed: str) -> str:
        return _prefixed_to_ticker(prefixed)

    # ---------- 汇总统计 ----------
    def stats(self) -> dict:
        df = self._load()
        a = self._by_market('a_share')
        us = self._by_market('us')
        return {
            'total_equities': len(df),
            'a_share': len(a),
            'us': len(us),
            'a_share_with_sector': int(a['sector'].notna().sum()),
            'a_share_with_mcap': int(a['market_cap'].notna().sum()),
            'sectors': sorted(df['sector'].dropna().unique().tolist())[:15],
        }


if __name__ == '__main__':
    su = SecurityUniverse()
    print('=== 库统计 ===')
    for k, v in su.stats().items():
        print(f'  {k}: {v}')
    print('\n=== 标准化示例：600519.SH（贵州茅台）===')
    print(su.classify('600519.SH'))
    print('\n=== 候选池：A股信息技术板块 市值前 5 ===')
    pool = su.discover('a_share', sector='Information Technology', limit=5)
    print(pool[['name', 'sector', 'industry', 'market_cap']].to_string())
