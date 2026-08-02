#!/usr/bin/env python3
"""股票代码补全工具。

提供：
- ticker → 市场（A股/港股/美股/期货/ETF）
- ticker → 标准化后缀（如 .XSHE / .XSHG）
- 名称/拼音/关键词 → ticker（基于 watchlist 索引）
- ETF / 期货 / 个股分类判断

优先使用本地 watchlist.json，可扩展到 tushare/akshare 在线搜索。
"""
import re
import json
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from functools import lru_cache

try:
    from services.config_registry import get_config
except Exception:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from services.config_registry import get_config


# A 股交易所规则
SH_PREFIXES = ('60', '68', '69', '51', '52', '56', '58', '50', '88', '11', '90')
SZ_PREFIXES = ('00', '30', '39', '15', '16', '12', '18', '13', '20')
BJ_PREFIXES = ('8', '4', '43')

# 常见港股后缀
HK_SUFFIX = re.compile(r'^(\d{4,5})\.HK$', re.I)


def _load_watchlist() -> List[Dict[str, Any]]:
    cfg = get_config()
    paths = [cfg.watchlist_path]
    # fallback to watchlist.json next to this module for compatibility
    module_dir = Path(__file__).resolve().parents[1]
    paths.append(module_dir / "watchlist.json")
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            continue
    return []


@lru_cache(maxsize=1)
def _build_index() -> Tuple[Dict[str, Dict[str, Any]], Dict[str, List[str]], Dict[str, str]]:
    """构建本地索引：ticker→item, name→tickers, alias→ticker。"""
    watchlist = _load_watchlist()
    ticker_map = {}
    name_map: Dict[str, List[str]] = {}
    alias_map: Dict[str, str] = {}

    for item in watchlist:
        ticker = item.get("ticker", "")
        name = item.get("name", "")
        if not ticker:
            continue
        ticker_map[ticker] = item
        if name:
            name_map.setdefault(name, []).append(ticker)
            alias_map[name] = ticker
            # 也加入首字母/拼音占位（后续可接 pypinyin）
            alias_map.setdefault(name.lower(), ticker)

        # 主题/sector 反向索引
        for key in ("theme", "sector"):
            val = item.get(key, "")
            if val:
                alias_map.setdefault(val, ticker)

    return ticker_map, name_map, alias_map


def refresh_index() -> None:
    _build_index.cache_clear()


def detect_market(ticker: str, category: Optional[str] = None) -> str:
    """推断 ticker 所属市场。"""
    if category:
        cat = category.lower()
        if cat == "us":
            return "us"
        if cat == "期货":
            return "futures"
        if cat == "hk":
            return "hk"
        if cat in ("etf", "index"):
            return "cn"  # ETF/指数默认按 A 股处理
    t = ticker.strip().upper()
    if t.endswith(".HK") or HK_SUFFIX.match(t):
        return "hk"
    # 纯数字：港股是 4/5 位，A 股/指数是 6 位；避免把 4 位数字误判为 A 股
    if t.isdigit():
        if len(t) in (4, 5):
            return "hk"
        # 指数/ETF 代码：000/399/930/950 等开头且为 6 位数字
        if len(t) == 6 and t.startswith(("000", "399", "930", "950")):
            return "index"
        if len(t) == 6:
            if t.startswith(SH_PREFIXES):
                return "cn_sh"
            if t.startswith(SZ_PREFIXES):
                return "cn_sz"
            if t.startswith(BJ_PREFIXES):
                return "cn_bj"
            return "cn"
        # 长度既不是 4/5 也不是 6 的纯数字，无法判断，保持未知
        return "unknown"
    if re.match(r"^[A-Z]{1,5}$", t):
        return "us"
    if len(t) >= 2 and t[0].isalpha() and t[-1].isdigit():
        return "futures"
    if t.startswith(("SH", "SZ", "BJ")) and len(t) > 2:
        return "cn"
    return "unknown"


def suffix_for_ticker(ticker: str, market: Optional[str] = None) -> str:
    """返回带交易所后缀的标准化代码，用于部分数据接口。"""
    m = market or detect_market(ticker)
    t = ticker.strip()
    if m == "cn_sh":
        return f"{t}.XSHG"
    if m == "cn_sz":
        return f"{t}.XSHE"
    if m == "cn_bj":
        return f"{t}.BJSE"
    if m == "index":
        # 沪深指数分别加后缀，其余保持原样
        if t.startswith("000") or t.startswith("950") or t.startswith("930"):
            return f"{t}.XSHG"
        if t.startswith("399"):
            return f"{t}.XSHE"
        return t
    if m == "us":
        return t.upper()
    if m == "hk":
        return t.upper() if ".HK" in t.upper() else f"{t}.HK"
    return t


def lookup_ticker(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """根据名称/代码/关键词查找候选 ticker。支持中文模糊匹配。"""
    q = query.strip()
    ticker_map, name_map, alias_map = _build_index()

    results = []
    # 精确代码
    if q in ticker_map:
        results.append(ticker_map[q])
    # 精确名称（包含大小写、拼音占位）
    for key in (q, q.lower()):
        for t in name_map.get(key, []):
            if t != q:
                results.append(ticker_map.get(t, {"ticker": t, "name": key}))
    # 别名
    t = alias_map.get(q) or alias_map.get(q.lower())
    if t and t != q:
        results.append(ticker_map.get(t, {"ticker": t, "name": q}))

    # 模糊匹配（名称、代码、主题、sector）—— 中文按包含匹配，英文/数字按 lower 包含匹配
    qlower = q.lower()
    def _match(item: Dict[str, Any]) -> bool:
        if qlower in item.get("ticker", "").lower():
            return True
        for key in ("name", "theme", "sector"):
            val = item.get(key, "") or ""
            if q in val or qlower in val.lower():
                return True
        return False

    for item in ticker_map.values():
        if _match(item) and item not in results:
            results.append(item)
        if len(results) >= limit:
            break

    # 去重
    seen = set()
    unique = []
    for item in results:
        k = item.get("ticker", "")
        if k and k not in seen:
            seen.add(k)
            item["market"] = detect_market(k, item.get("category"))
            unique.append(item)
    return unique[:limit]


def category_for_ticker(ticker: str, item: Optional[Dict[str, Any]] = None) -> str:
    """推断标的分类：个股 / ETF / 期货 / US / HK / 指数。"""
    if item and "category" in item:
        return item["category"]
    t = ticker.strip()
    market = detect_market(t)
    if market in ("hk", "us"):
        return market.upper()
    if market == "futures":
        return "期货"
    if market == "index":
        return "指数"
    if t.isdigit() and len(t) == 6 and t.startswith(("15", "51", "56", "58", "16", "12", "13", "50", "52")):
        return "ETF"
    if t.isdigit() and len(t) == 6:
        return "个股"
    return "个股"


if __name__ == "__main__":
    samples = [
        "601899", "紫金矿业", "中微公司", "000300", "AAPL", "00700.HK", "JMO",
        "515880", "M00", "688012", "399006", "500", "1234"
    ]
    for q in samples:
        market = detect_market(q)
        suffix = suffix_for_ticker(q)
        category = category_for_ticker(q)
        lookup = lookup_ticker(q, limit=1)
        print(f"{q:<12} -> market={market:<8} suffix={suffix:<18} category={category:<8} lookup={lookup}")
    print("\nlookup '紫金':", lookup_ticker("紫金", limit=3))
    print("lookup '中微':", lookup_ticker("中微", limit=3))
    print("lookup '通信':", lookup_ticker("通信", limit=3))
