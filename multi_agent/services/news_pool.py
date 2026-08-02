#!/usr/bin/env python3
"""资讯情报池。

聚合多源资讯：RSS、东方财富、财联社快讯、热榜、公告、研报。
设计为可扩展：新增 source 只需实现 _collect_{name} 并注册到 SOURCES。
结果按主题/标的索引，支持实时查询与缓存。
"""
import os
import re
import json
import hashlib
import urllib.request
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Callable, Any
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, asdict

try:
    from services.config_registry import get_config
except Exception:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from services.config_registry import get_config


@dataclass
class NewsItem:
    title: str
    source: str
    url: Optional[str] = None
    publish_time: Optional[str] = None
    summary: Optional[str] = None
    tags: List[str] = None
    sentiment: Optional[float] = None  # -1 ~ 1
    related_tickers: List[str] = None
    extra: Dict[str, Any] = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = []
        if self.related_tickers is None:
            self.related_tickers = []
        if self.extra is None:
            self.extra = {}

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# 简单缓存：内存 + 文件
_POOL_CACHE: Dict[str, Dict[str, Any]] = {}


def _cache_key(query: str, sources: List[str], hours: int) -> str:
    raw = f"{query}|{','.join(sorted(sources))}|{hours}|{datetime.utcnow().strftime('%Y-%m-%d-%H')}"
    return hashlib.md5(raw.encode()).hexdigest()


def _load_cache(key: str, ttl_minutes: int = 30) -> Optional[List[NewsItem]]:
    # 内存缓存
    entry = _POOL_CACHE.get(key)
    if entry:
        if datetime.utcnow() - entry["ts"] < timedelta(minutes=ttl_minutes):
            return [NewsItem(**d) for d in entry["items"]]
        else:
            del _POOL_CACHE[key]
    # 文件缓存
    cfg = get_config()
    cache_path = cfg.data_dir / "news_pool_cache.json"
    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            entry = data.get(key)
            if entry and datetime.utcnow() - datetime.fromisoformat(entry["ts"]) < timedelta(minutes=ttl_minutes):
                return [NewsItem(**d) for d in entry["items"]]
        except Exception:
            pass
    return None


def _save_cache(key: str, items: List[NewsItem]) -> None:
    _POOL_CACHE[key] = {
        "ts": datetime.utcnow(),
        "items": [it.to_dict() for it in items],
    }
    cfg = get_config()
    cache_path = cfg.data_dir / "news_pool_cache.json"
    data = {}
    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
    data[key] = {
        "ts": datetime.utcnow().isoformat(),
        "items": [it.to_dict() for it in items],
    }
    # 只保留最近 200 条 key
    if len(data) > 200:
        data = dict(sorted(data.items(), key=lambda x: x[1].get("ts", ""), reverse=True)[:200])
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════
# Source collectors
# ═══════════════════════════════════════════════════════

def _collect_eastmoney(query: str) -> List[NewsItem]:
    """调用东方财富搜索（复用现有 eastmoney_bridge 能力，若不可用则返回空）。"""
    try:
        from eastmoney_bridge import search_news
        results = search_news(query) or []
        items = []
        for r in results[:20]:
            items.append(NewsItem(
                title=r.get("title", ""),
                source="eastmoney",
                url=r.get("url"),
                publish_time=r.get("time"),
                summary=r.get("summary"),
                related_tickers=[query] if re.match(r"^\d{6}$", query) else [],
            ))
        return items
    except Exception:
        return []


def _collect_cx_news(query: str) -> List[NewsItem]:
    """财联社快讯（复用 news_analyst 中的 _fetch_akshare_cx_news）。"""
    try:
        from analysts.news_analyst import _fetch_akshare_cx_news
        rows = _fetch_akshare_cx_news() or []
        items = []
        for r in rows[:30]:
            title = r.get("title", "")
            if not title:
                continue
            items.append(NewsItem(
                title=title,
                source="cx_news",
                publish_time=r.get("datetime"),
                summary=r.get("content", ""),
                tags=["快讯"],
            ))
        return items
    except Exception:
        return []


def _collect_hot_rank(query: str) -> List[NewsItem]:
    """AKShare 热榜（复用 news_analyst 中的 _fetch_akshare_hot_rank）。"""
    try:
        from analysts.news_analyst import _fetch_akshare_hot_rank
        rows = _fetch_akshare_hot_rank() or []
        items = []
        for r in rows[:30]:
            title = r.get("title", "")
            if not title:
                continue
            items.append(NewsItem(
                title=title,
                source="hot_rank",
                tags=["热榜"],
            ))
        return items
    except Exception:
        return []


def _collect_rss(query: str) -> List[NewsItem]:
    """简单 RSS 抓取示例（可扩展为配置驱动）。"""
    return []  # 占位：避免依赖 feedparser


SOURCES: Dict[str, Callable[[str], List[NewsItem]]] = {
    "eastmoney": _collect_eastmoney,
    "cx_news": _collect_cx_news,
    "hot_rank": _collect_hot_rank,
    "rss": _collect_rss,
}


# ═══════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════

def collect(query: str = "", sources: Optional[List[str]] = None,
            max_workers: int = 4, timeout: float = 30.0,
            use_cache: bool = True, cache_ttl_minutes: int = 30) -> List[NewsItem]:
    """从多个来源收集资讯。

    Args:
        query: 标的代码或关键词；为空时只拉取全局快讯/热榜。
        sources: 来源列表，默认全部。
        max_workers: 并发数。
        timeout: 单来源超时。
        use_cache: 是否使用缓存。
        cache_ttl_minutes: 缓存有效期。

    Returns:
        合并去重后的 NewsItem 列表（按时间倒序）。
    """
    if sources is None:
        sources = list(SOURCES.keys())
    sources = [s for s in sources if s in SOURCES]

    if use_cache:
        key = _cache_key(query, sources, hours=cache_ttl_minutes // 60)
        cached = _load_cache(key, ttl_minutes=cache_ttl_minutes)
        if cached is not None:
            return cached

    def _fetch(src: str) -> List[NewsItem]:
        try:
            return SOURCES[src](query)
        except Exception:
            return []

    results: List[NewsItem] = []
    with ThreadPoolExecutor(max_workers=min(len(sources), max_workers)) as pool:
        futures = {pool.submit(_fetch, src): src for src in sources}
        for fut in futures:
            try:
                items = fut.result(timeout=timeout)
                results.extend(items)
            except FutureTimeoutError:
                pass

    # 去重（标题相同取第一条）
    seen = set()
    unique = []
    for it in results:
        k = it.title.strip()
        if k and k not in seen:
            seen.add(k)
            unique.append(it)

    # 按时间倒序（没有时间放最后）
    def _ts(it: NewsItem) -> str:
        return it.publish_time or "1970-01-01 00:00:00"
    unique.sort(key=_ts, reverse=True)

    if use_cache:
        _save_cache(key, unique)
    return unique


def search_pool(items: List[NewsItem], keyword: str) -> List[NewsItem]:
    """在已收集的情报池中按关键词过滤。"""
    kw = keyword.lower()
    out = []
    for it in items:
        text = f"{it.title or ''} {it.summary or ''} {' '.join(it.tags)}".lower()
        if kw in text or (it.related_tickers and keyword in it.related_tickers):
            out.append(it)
    return out


def summarize_pool(items: List[NewsItem], topk: int = 10) -> str:
    """生成简短文本摘要，供 prompt 注入。"""
    if not items:
        return "暂无相关资讯。"
    lines = [f"- [{it.source}] {it.title}" for it in items[:topk]]
    return "\n".join(lines)


if __name__ == "__main__":
    res = collect(query="601899", sources=["hot_rank"], use_cache=False)
    print(f"collected {len(res)} items")
    for it in res[:5]:
        print(f"[{it.source}] {it.title}")
