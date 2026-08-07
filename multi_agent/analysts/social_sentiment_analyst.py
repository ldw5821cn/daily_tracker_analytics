#!/usr/bin/env python3
"""社会情绪抓取分析器（海外社交媒体 + 搜索聚合）。

设计原则：
- 可插拔 provider，默认支持 Exa/SerpApi/Reddit/搜索聚合；未来可接 last30days-skill 引擎。
- 无凭证时返回中性（50）并记录原因，不中断主流程。
- 输出 0-100 情绪分和关键词，接入 sentiment_analyst 做加权。

环境变量（支持 .env 文件）：
  EXA_API_KEY
  SERPAPI_API_KEY

用法：
  from analysts.social_sentiment_analyst import get_social_sentiment
  score, keywords = get_social_sentiment('NVDA', name='英伟达', category='US')
"""
from __future__ import annotations

import os
import re
import json
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from collections import Counter

import requests

# 尝试加载 .env
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '.env')
    if os.path.exists(_env_path):
        load_dotenv(_env_path)
except Exception:
    pass


# ─── 工具函数 ─────────────────────────
def _safe_int(x, default=0):
    try:
        return int(float(x))
    except Exception:
        return default


def _extract_keywords(texts: List[str], topk: int = 5) -> List[str]:
    """用简单词频提取关键词（英文去停用词、保留大写缩写）。"""
    stop = {
        'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all', 'can', 'had',
        'her', 'was', 'one', 'our', 'out', 'day', 'get', 'has', 'him', 'his',
        'how', 'its', 'may', 'new', 'now', 'old', 'see', 'two', 'way', 'who',
        'boy', 'did', 'she', 'use', 'her', 'there', 'their', 'what', 'would',
        'about', 'after', 'again', 'could', 'first', 'from', 'good', 'have',
        'into', 'just', 'know', 'last', 'life', 'like', 'long', 'look', 'make',
        'many', 'more', 'most', 'much', 'never', 'only', 'other', 'over',
        'right', 'same', 'should', 'some', 'than', 'that', 'them', 'then',
        'these', 'they', 'thing', 'think', 'this', 'those', 'time', 'very',
        'well', 'were', 'what', 'when', 'where', 'which', 'while', 'with',
        'within', 'without', 'work', 'world', 'year', 'years', 'your', 'is',
        'a', 'an', 'as', 'at', 'be', 'by', 'do', 'go', 'he', 'if', 'in',
        'it', 'me', 'my', 'no', 'of', 'on', 'or', 'so', 'to', 'up', 'us',
        'we',
    }
    words = []
    for t in texts:
        # 保留中文词和英文单词/缩写
        for w in re.findall(r'[A-Za-z]{2,}|[\u4e00-\u9fff]{2,}', t or ''):
            w = w.lower()
            if w not in stop and len(w) > 2:
                words.append(w)
    counter = Counter(words)
    # 过滤过短的纯数字和过常见词
    return [w for w, _ in counter.most_common(topk)]


def _clamp_score(score: float) -> float:
    return max(0.0, min(100.0, round(score, 1)))



# ─── 通用情感评分函数 ─────────────────────────
def _sentiment_score_from_texts(texts: List[str]) -> float:
    """基于情感词频的朴素评分。"""
    pos_words = {
        'bullish', 'strong', 'growth', 'beat', 'rally', 'surge', 'outperform',
        'upgrade', 'buy', 'moon', 'rocket', 'recover', 'opportunity', 'optimistic',
        'positive', 'gain', 'rally', 'bounce', 'rises', 'soar', 'rallies',
        '看涨', '买入', '反弹', '利好', '强势', '增长', '超预期',
    }
    neg_words = {
        'bearish', 'weak', 'miss', 'crash', 'drop', 'plunge', 'underperform',
        'downgrade', 'sell', 'dump', 'recession', 'concern', 'warning', 'pessimistic',
        'negative', 'loss', 'fall', 'falls', 'decline', 'fear', 'panic',
        '看跌', '卖出', '下跌', '利空', '弱势', '衰退', '不及预期',
    }
    total = len(texts)
    if not total:
        return 50.0
    pos = neg = 0
    for t in texts:
        txt = (t or '').lower()
        pos += sum(1 for w in pos_words if w in txt)
        neg += sum(1 for w in neg_words if w in txt)
    raw = 50.0 + (pos - neg) / max(total, 1) * 15.0
    return _clamp_score(raw)


# ─── Provider 接口 ─────────────────────────
class SocialSentimentProvider:
    name: str = "base"

    def fetch(self, query: str, ticker: str, name: Optional[str]) -> Tuple[float, List[str], str]:
        """返回 (score 0-100, keywords, note)。"""
        raise NotImplementedError


class ExaSearchProvider(SocialSentimentProvider):
    name = "exa"

    def fetch(self, query: str, ticker: str, name: Optional[str]) -> Tuple[float, List[str], str]:
        key = os.environ.get('EXA_API_KEY')
        if not key:
            return 50.0, [], 'no_exa_api_key'
        url = 'https://api.exa.ai/search'
        headers = {'x-api-key': key, 'Content-Type': 'application/json'}
        start_dt = (datetime.utcnow() - timedelta(days=7)).strftime('%Y-%m-%dT%H:%M:%S') + 'Z'
        payload = {
            'query': query,
            'type': 'auto',
            'numResults': 10,
            'useAutoprompt': True,
            'startPublishedDate': start_dt,
        }
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=20)
            r.raise_for_status()
            data = r.json()
            results = data.get('results', [])
            texts = [x.get('text', x.get('title', '')) for x in results if x]
            if not texts:
                return 50.0, [], 'exa_empty_results'
            score = _sentiment_score_from_texts(texts)
            keywords = _extract_keywords(texts, topk=5)
            return _clamp_score(score), keywords, 'exa_ok'
        except Exception as e:
            return 50.0, [], f'exa_error:{type(e).__name__}'


class BraveSearchProvider(SocialSentimentProvider):
    name = "brave"

    def fetch(self, query: str, ticker: str, name: Optional[str]) -> Tuple[float, List[str], str]:
        key = os.environ.get('BRAVE_API_KEY')
        if not key:
            return 50.0, [], 'no_brave_api_key'
        url = 'https://api.search.brave.com/res/v1/news/search'
        headers = {'Accept': 'application/json', 'X-Subscription-Token': key}
        params = {'q': query, 'count': 10, 'offset': 0, 'spellcheck': 0}
        try:
            r = requests.get(url, headers=headers, params=params, timeout=20)
            r.raise_for_status()
            data = r.json()
            results = data.get('results', [])
            texts = [f"{x.get('title','')} {x.get('description','')}" for x in results]
            if not texts:
                return 50.0, [], 'brave_empty_results'
            score = _sentiment_score_from_texts(texts)
            keywords = _extract_keywords(texts, topk=5)
            return _clamp_score(score), keywords, 'brave_ok'
        except Exception as e:
            return 50.0, [], f'brave_error:{type(e).__name__}'


class SerpApiProvider(SocialSentimentProvider):
    """SerpApi 搜索（Google / Bing / 新闻）情绪抓取。"""
    name = "serpapi"

    def fetch(self, query: str, ticker: str, name: Optional[str]) -> Tuple[float, List[str], str]:
        key = os.environ.get('SERPAPI_API_KEY')
        if not key:
            return 50.0, [], 'no_serpapi_api_key'
        url = 'https://serpapi.com/search'
        params = {
            'q': query,
            'api_key': key,
            'engine': 'google',
            'tbm': 'nws',
            'num': 10,
            'tbs': 'qdr:w',  # 过去一周
        }
        try:
            r = requests.get(url, params=params, timeout=25)
            r.raise_for_status()
            data = r.json()
            results = data.get('news_results', [])
            if not results:
                results = data.get('organic_results', [])
            texts = [f"{x.get('title','')} {x.get('snippet','')} {x.get('description','')}" for x in results if x]
            if not texts:
                return 50.0, [], 'serpapi_empty_results'
            score = _sentiment_score_from_texts(texts)
            keywords = _extract_keywords(texts, topk=5)
            return _clamp_score(score), keywords, 'serpapi_ok'
        except Exception as e:
            return 50.0, [], f'serpapi_error:{type(e).__name__}'


class RedditProvider(SocialSentimentProvider):
    name = "reddit"

    def fetch(self, query: str, ticker: str, name: Optional[str]) -> Tuple[float, List[str], str]:
        """通过 Reddit JSON API 搜索（无需 key，但速率低）。"""
        try:
            term = ticker.replace('$', '')
            url = f'https://www.reddit.com/search.json'
            headers = {'User-Agent': 'Mozilla/5.0 (compatible; quant-bot/1.0)'}
            params = {'q': term, 'sort': 'new', 'limit': 25, 't': 'week'}
            r = requests.get(url, headers=headers, params=params, timeout=15)
            if r.status_code != 200:
                return 50.0, [], f'reddit_http_{r.status_code}'
            data = r.json()
            posts = data.get('data', {}).get('children', [])
            texts = []
            for p in posts:
                child = p.get('data', {})
                texts.append(f"{child.get('title','')} {child.get('selftext','')}")
            if not texts:
                return 50.0, [], 'reddit_empty'
            score = ExaSearchProvider()._sentiment_score(texts)
            keywords = _extract_keywords(texts, topk=5)
            return _clamp_score(score), keywords, 'reddit_ok'
        except Exception as e:
            return 50.0, [], f'reddit_error:{type(e).__name__}'


# ─── 聚合层 ─────────────────────────
PROVIDERS: List[SocialSentimentProvider] = [
    ExaSearchProvider(),
    SerpApiProvider(),
    RedditProvider(),
]


def _build_query(ticker: str, name: Optional[str], category: str) -> str:
    name = name or ''
    if category in ('US',):
        return f"${ticker} stock sentiment OR {name} stock news this week"
    if category in ('ETF',):
        return f"{ticker} ETF outlook sentiment"
    if category in ('个股', 'A股'):
        return f"{name} {ticker} 股票 情绪 利好 利空"
    if category in ('期货', 'futures'):
        return f"{ticker} futures price sentiment analysis"
    return f"{ticker} market sentiment"


def get_social_sentiment(
    ticker: str,
    name: Optional[str] = None,
    category: str = 'US',
    active_providers: Optional[List[str]] = None,
) -> Dict:
    """聚合多个社会情绪源，返回 dict。

    返回字段：
      - social_score: 0-100
      - keywords: list[str]
      - provider_notes: list[str]
      - source_count: int
      - has_data: bool
      - query: str
    """
    query = _build_query(ticker, name, category)
    active_providers = active_providers or ['exa', 'brave', 'reddit']

    scores = []
    all_keywords = []
    notes = []
    enabled = []

    for p in PROVIDERS:
        if p.name not in active_providers:
            continue
        enabled.append(p.name)
        score, keywords, note = p.fetch(query, ticker, name)
        notes.append(f"{p.name}:{note}")
        if note.endswith('_ok'):
            scores.append(score)
            all_keywords.extend(keywords)

    if not scores:
        return {
            'social_score': 50.0,
            'keywords': [],
            'provider_notes': notes,
            'source_count': 0,
            'has_data': False,
            'query': query,
        }

    avg_score = sum(scores) / len(scores)
    # 去重关键词并取 top 5
    keyword_counter = Counter(all_keywords)
    keywords = [k for k, _ in keyword_counter.most_common(5)]

    return {
        'social_score': _clamp_score(avg_score),
        'keywords': keywords,
        'provider_notes': notes,
        'source_count': len(scores),
        'has_data': True,
        'query': query,
    }


def get_social_sentiment_for_tickers(
    ticker_items: List[Dict[str, str]],
    category: str = 'US',
) -> Dict[str, Dict]:
    """批量获取社会情绪。

    ticker_items: [{'ticker':'NVDA','name':'英伟达'}, ...]
    """
    out = {}
    for item in ticker_items:
        ticker = item.get('ticker', '')
        name = item.get('name')
        if not ticker:
            continue
        out[ticker] = get_social_sentiment(ticker, name, category)
    return out


if __name__ == '__main__':
    # 无凭证时也会返回中性结果，不抛异常
    samples = [
        {'ticker': 'NVDA', 'name': 'NVIDIA'},
        {'ticker': 'SMH', 'name': 'VanEck Semiconductor ETF'},
        {'ticker': '中际旭创', 'name': '300308'},
    ]
    for s in samples:
        res = get_social_sentiment(s['ticker'], s.get('name'), category='US' if s['ticker'].isalpha() else '个股')
        print(s['ticker'], res)
