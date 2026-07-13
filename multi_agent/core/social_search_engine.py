"""社交媒体 + 全网搜索舆情引擎

支持：
- Twitter/X 搜索（需配置 TWITTER_AUTH_TOKEN / TWITTER_CT0 或浏览器 Cookie）
- Exa 全网语义搜索（需 EXA_API_KEY）
- Jina AI 搜索/摘要（免费，无需 Key）

输出统一格式，并做情绪打分。
"""
from __future__ import annotations

import json
import os
import re
import ssl
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Dict, List, Optional

POSITIVE_WORDS = [
    '涨停', '大涨', '突破', '拉升', '利好', '反弹', '放量', '创新高',
    '增长', '盈利', '超预期', '买入', '增持', '回购', '分红', '政策支持',
    '龙头', '领涨', '获', '中标', '合同', '订单', '合作', '扩张',
    '扭亏', '翻红', '企稳', '回升', '加仓', '做多', ' bullish', 'strong',
    'buy', 'upgrade', 'beat', 'growth', 'rally', 'surge', 'outperform',
]

NEGATIVE_WORDS = [
    '跌停', '大跌', '暴跌', '崩盘', '利空', '减持', '卖出', '做空',
    '亏损', 'st', '退市', '警示', '处罚', '立案', '调查', '监管函',
    '下调', '评级下调', '质押', '平仓', '资金流出', '流出',
    '恐慌', '踩踏', '跌', '打压', '回调', '破位', 'bearish', 'weak',
    'sell', 'downgrade', 'miss', 'crash', 'drop', 'underperform', 'cut',
]


def _safe_get(url: str, timeout: int = 8, headers: Optional[Dict] = None) -> Optional[str]:
    try:
        req = urllib.request.Request(url, headers=headers or {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        })
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.read().decode('utf-8', errors='ignore')
    except Exception:
        return None


# ── Jina AI 搜索 ──

def search_jina(query: str, n: int = 5) -> List[Dict]:
    """用 Jina AI 免费搜索。"""
    encoded = urllib.parse.quote(query)
    url = f"https://s.jina.ai/{encoded}?n={n}"
    text = _safe_get(url, timeout=20)
    if not text:
        return []
    # Jina 返回 Markdown 列表，形如 "- [标题](url) 摘要"
    items = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith('- '):
            continue
        m = re.match(r'- \[(.*?)\]\((.*?)\)\s*(.*)', line)
        if m:
            title, link, summary = m.groups()
            items.append({
                'title': title.strip(),
                'url': link.strip(),
                'source': 'Jina Search',
                'summary': summary.strip(),
                'date': '',
            })
    return items[:n]


# ── Exa 语义搜索 ──

def search_exa(query: str, n: int = 5) -> List[Dict]:
    """用 Exa API 做语义搜索。"""
    api_key = os.getenv('EXA_API_KEY', '')
    if not api_key:
        return []
    try:
        url = 'https://api.exa.ai/search'
        data = json.dumps({
            'query': query,
            'numResults': n,
            'type': 'neural',
            'useAutoprompt': True,
        }).encode()
        req = urllib.request.Request(url, data=data, headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        }, method='POST')
        with urllib.request.urlopen(req, timeout=20) as resp:
            r = json.loads(resp.read().decode())
        items = []
        for res in r.get('results', []):
            items.append({
                'title': res.get('title', res.get('url', '')),
                'url': res.get('url', ''),
                'source': 'Exa',
                'summary': res.get('text', '')[:300],
                'date': res.get('publishedDate', '')[:10],
            })
        return items
    except Exception:
        return []


# ── Twitter/X 搜索 ──

def _twitter_cli_available() -> bool:
    try:
        subprocess.run(['twitter', '--version'], capture_output=True, check=True, timeout=10)
        return True
    except Exception:
        return False


def search_twitter(query: str, n: int = 10) -> List[Dict]:
    """用 twitter-cli 搜索推文。需要已登录 Cookie。"""
    if not _twitter_cli_available():
        return []
    try:
        r = subprocess.run(
            ['twitter', 'search', query, '--type', 'latest', '-n', str(n), '--json'],
            capture_output=True, text=True, timeout=15,
            env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
        )
        if r.returncode != 0:
            return []
        data = json.loads(r.stdout)
        tweets = data.get('tweets', []) if isinstance(data, dict) else data
        items = []
        for t in tweets[:n]:
            text = t.get('text', '') or t.get('full_text', '')
            items.append({
                'title': text[:120] + ('...' if len(text) > 120 else ''),
                'url': f"https://x.com/i/web/status/{t.get('id', '')}",
                'source': 'Twitter/X',
                'summary': text,
                'date': '',
                'author': t.get('author', {}).get('username', ''),
                'likes': t.get('likes', 0),
                'retweets': t.get('retweets', 0),
            })
        return items
    except Exception:
        return []


# ── 情绪打分 ──

def _calc_sentiment(items: List[Dict]) -> Dict:
    total_score = 0.0
    scored = 0
    pos_hits = []
    neg_hits = []
    for item in items:
        text = (item.get('title', '') + ' ' + item.get('summary', '')).lower()
        pos = [w for w in POSITIVE_WORDS if w.lower() in text]
        neg = [w for w in NEGATIVE_WORDS if w.lower() in text]
        pos_hits.extend(pos)
        neg_hits.extend(neg)
        if pos or neg:
            total_score += len(pos) - len(neg)
            scored += 1
    if scored > 0:
        avg = total_score / max(scored, 1)
        score = max(-1.0, min(1.0, avg / 5))
    else:
        score = 0.0
    return {
        'score': round(score, 2),
        'positive_keywords': list(set(pos_hits)),
        'negative_keywords': list(set(neg_hits)),
        'scored_items': scored,
    }


def get_social_sentiment(ticker: str, name: str = '', query_extra: str = '股票 A股') -> Dict:
    """聚合 Twitter、Exa、Jina 搜索结果，返回统一舆情结构。"""
    query = f"{name or ticker} {query_extra}".strip()

    # 分别拉取，避免一个源阻塞其他源
    twitter_items = []
    exa_items = []
    jina_items = []
    try:
        twitter_items = search_twitter(query, n=10)
    except Exception:
        pass
    try:
        exa_items = search_exa(query, n=5)
    except Exception:
        pass
    try:
        jina_items = search_jina(query, n=5)
    except Exception:
        pass

    all_items = twitter_items + exa_items + jina_items
    sentiment = _calc_sentiment(all_items)

    return {
        'ticker': ticker,
        'name': name,
        'query': query,
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'twitter_count': len(twitter_items),
        'exa_count': len(exa_items),
        'jina_count': len(jina_items),
        'total_count': len(all_items),
        'sentiment_score': sentiment['score'],
        'positive_keywords': sentiment['positive_keywords'],
        'negative_keywords': sentiment['negative_keywords'],
        'items': all_items[:20],
    }


def batch_social_sentiment(tickers: List[Dict], output_path: Optional[str] = None) -> Dict:
    """批量跑社交媒体舆情。tickers: [{'ticker','name','category'}]"""
    results = []
    for t in tickers:
        try:
            extra = 'ETF' if t.get('category') == 'ETF' else '股票 A股'
            r = get_social_sentiment(t['ticker'], t.get('name', ''), extra)
            results.append(r)
        except Exception as e:
            results.append({
                'ticker': t.get('ticker', ''),
                'name': t.get('name', ''),
                'error': str(e),
                'sentiment_score': 0.0,
            })

    out = {
        'date': datetime.now().strftime('%Y-%m-%d'),
        'items': results,
    }
    if output_path:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
    return out


if __name__ == '__main__':
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else '000301 东方盛虹 股票'
    r = get_social_sentiment(q)
    print(json.dumps(r, ensure_ascii=False, indent=2))
