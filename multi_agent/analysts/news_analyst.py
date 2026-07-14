"""
新闻分析师 - 增强版舆情分析
数据源：akshare + 东方财富搜索API + 财联社快讯 + 热榜 + 公告(缓存)
情绪打分：关键词匹配 + LLM fallback

优化：
- 公告日终全量缓存，每个交易日只拉一次
- 社交搜索加5秒超时
- LLM 情感 fallback 解决关键词无法匹配的场景
"""
import sys
import os
import json
import re
import urllib.request
import urllib.parse
import threading
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

# 公告全量缓存（每个交易日只拉一次）
_NOTICE_CACHE = {}
_NOTICE_CACHE_LOCK = threading.Lock()

# 情绪词典
POSITIVE_WORDS = [
    '涨停', '大涨', '突破', '拉升', '利好', '反弹', '放量', '创新高',
    '增长', '盈利', '超预期', '买入', '增持', '回购', '分红', '政策支持',
    '龙头', '领涨', '获', '中标', '合同', '订单', '合作', '扩张',
    '扭亏', '翻红', '企稳', '回升', '加仓', '做多',
]

NEGATIVE_WORDS = [
    '跌停', '大跌', '暴跌', '崩盘', '利空', '减持', '卖出', '做空',
    '亏损', 'st', '退市', '警示', '处罚', '立案', '调查', '监管函',
    '下调', '评级下调', '质押', '平仓', '资金流出', '流出',
    '恐慌', '踩踏', '跌', '打压', '回调', '破位',
]

# 关键词映射：标的代码/名称 -> 搜索关键词
STOCK_KEYWORDS = {
    '601991': '大唐发电',
    '515880': '通信ETF 5G',
    '516150': '稀土ETF 稀土',
}


def _fetch_eastmoney_news(keyword, page_size=10):
    """从东方财富搜索API获取新闻"""
    try:
        encoded = urllib.parse.quote(keyword)
        url = (f"https://search-api-web.eastmoney.com/search/jsonp"
               f"?cb=jQuery&param=%7B%22uid%22%3A%22%22%2C%22keyword%22%3A%22{encoded}%22"
               f"%2C%22type%22%3A%5B%22cmsArticleWebOld%22%5D%2C%22client%22%3A%22web%22"
               f"%2C%22clientType%22%3A%22web%22%2C%22clientVersion%22%3A%22curr%22"
               f"%2C%22param%22%3A%7B%22cmsArticleWebOld%22%3A%7B%22searchScope%22%3A%22default%22"
               f"%2C%22sort%22%3A%22default%22%2C%22pageIndex%22%3A1%2C%22pageSize%22%3A{page_size}%7D%7D%7D")
        
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://www.eastmoney.com/',
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode()
        
        json_match = re.search(r'jQuery\((.*)\)', text)
        if not json_match:
            return []
        
        data = json.loads(json_match.group(1))
        articles = data.get('result', {}).get('cmsArticleWebOld', [])
        
        items = []
        for art in articles[:page_size]:
            items.append({
                'title': art.get('title', '').strip(),
                'desc': art.get('content', '')[:200] if art.get('content') else '',
                'source': '东方财富',
                'date': art.get('date', '')[:10],
                'url': art.get('url', ''),
            })
        return items
    except Exception as e:
        return []


def _fetch_newsapi(name, ticker):
    """从NewsAPI获取新闻（备用）"""
    newsapi_key = os.getenv('NEWSAPI_API_KEY', '')
    if not newsapi_key:
        return []
    
    items = []
    try:
        query = urllib.parse.quote(f"{name} 股票 A股")
        url = f"https://newsapi.org/v2/everything?q={query}&language=zh&sortBy=publishedAt&pageSize=5&apiKey={newsapi_key}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        for art in data.get('articles', [])[:5]:
            if art.get('title'):
                items.append({
                    'title': art['title'],
                    'desc': (art.get('description') or '')[:200],
                    'source': art.get('source', {}).get('name', 'NewsAPI'),
                    'date': (art.get('publishedAt') or '')[:10],
                })
    except:
        pass
    return items


def _fetch_akshare_stock_news(ticker: str, name: str = "", page_size: int = 10) -> list:
    """从 akshare 获取东方财富个股新闻。"""
    try:
        import akshare as ak
        keyword = name or ticker
        df = ak.stock_news_em(symbol=keyword)
        if df is None or df.empty:
            return []
        items = []
        for _, row in df.head(page_size).iterrows():
            items.append({
                'title': str(row.get('新闻标题', '')),
                'desc': str(row.get('新闻内容', ''))[:200],
                'source': '东方财富',
                'date': str(row.get('发布时间', ''))[:10],
                'url': str(row.get('新闻链接', '')),
                'type': 'stock_news',
            })
        return items
    except Exception as e:
        return []


def _fetch_akshare_research_report(ticker: str, name: str = "", page_size: int = 5) -> list:
    """从 akshare 获取个股研报。"""
    try:
        import akshare as ak
        df = ak.stock_research_report_em(symbol=ticker)
        if df is None or df.empty:
            return []
        items = []
        for _, row in df.head(page_size).iterrows():
            rating = str(row.get('东财评级', ''))
            items.append({
                'title': f"[{rating}] {row.get('报告名称', '')}",
                'desc': f"机构: {row.get('机构', '')} | 2026EPS: {row.get('2026-盈利预测-收益', '')} | 2026PE: {row.get('2026-盈利预测-市盈率', '')}",
                'source': '研报',
                'date': str(row.get('日期', ''))[:10],
                'url': str(row.get('报告PDF链接', '')),
                'rating': rating,
                'type': 'research_report',
            })
        return items
    except Exception as e:
        return []


def _fetch_akshare_cx_news(page_size: int = 10) -> list:
    """从 akshare 获取财联社财经快讯（全市场，可缓存）。"""
    try:
        import akshare as ak
        df = ak.stock_news_main_cx()
        if df is None or df.empty:
            return []
        items = []
        for _, row in df.head(page_size).iterrows():
            items.append({
                'title': str(row.get('summary', '')),
                'desc': '',
                'source': '财联社',
                'date': '',
                'url': str(row.get('url', '')),
                'tag': str(row.get('tag', '')),
                'type': 'cx_news',
            })
        return items
    except Exception as e:
        return []


def _fetch_akshare_hot_rank(page_size: int = 20) -> list:
    """从 akshare 获取东方财富 A 股热榜。"""
    try:
        import akshare as ak
        df = ak.stock_hot_rank_em()
        if df is None or df.empty:
            return []
        items = []
        for _, row in df.head(page_size).iterrows():
            items.append({
                'title': f"{row.get('当前排名', '')}. {row.get('股票名称', '')}({row.get('代码', '')}) 涨跌幅{row.get('涨跌幅', '')}%",
                'desc': '',
                'source': '东财热榜',
                'date': '',
                'type': 'hot_rank',
            })
        return items
    except Exception as e:
        return []


def _get_cached_notices(ticker: str, date: str = None) -> list:
    """
    获取个股公告日终数据，使用全量缓存（每个交易日只拉一次）。
    列表缓存在模块级字典中，线程安全。
    """
    if date is None:
        date = datetime.now().strftime('%Y%m%d')
    
    # 只在 A 股个股（6位数字代码）时才查公告
    if not (len(ticker) == 6 and ticker.isdigit()):
        return []
    
    cache_key = f"notices_{date}"
    with _NOTICE_CACHE_LOCK:
        if cache_key in _NOTICE_CACHE:
            all_notices = _NOTICE_CACHE[cache_key]
        else:
            try:
                import akshare as ak
                df = ak.stock_notice_report(symbol='全部', date=date)
                _NOTICE_CACHE[cache_key] = df
                all_notices = df
            except Exception:
                _NOTICE_CACHE[cache_key] = None
                return []
    
    if all_notices is None or all_notices.empty:
        return []
    
    # 过滤当前 ticker
    ticker_notices = all_notices[all_notices['代码'] == ticker]
    items = []
    for _, row in ticker_notices.head(10).iterrows():
        items.append({
            'title': str(row.get('公告标题', '')),
            'desc': f"类型: {row.get('公告类型', '')}",
            'source': '公告',
            'date': str(row.get('公告日期', '')),
            'url': str(row.get('网址', '')),
            'type': 'notice',
        })
    return items


def _rate_research_sentiment(items: list) -> dict:
    """根据研报评级统计情绪。"""
    positive = {'买入', '增持', '推荐', '强烈买入', '跑赢行业', '优于大市'}
    negative = {'卖出', '减持', '中性偏空', '跑输行业'}
    pos = neg = 0
    for it in items:
        r = str(it.get('rating', ''))
        if r in positive:
            pos += 1
        elif r in negative:
            neg += 1
    total = pos + neg
    if total == 0:
        return {'score': 0, 'pos': pos, 'neg': neg, 'total': 0}
    score = (pos - neg) / total
    return {'score': round(max(-1, min(1, score)), 2), 'pos': pos, 'neg': neg, 'total': total}


def _calc_sentiment(news_items):
    """基于新闻标题和描述的情绪打分（关键词匹配）"""
    total_score = 0
    scored_count = 0
    keyword_hits = {'positive': [], 'negative': []}
    
    for item in news_items:
        text = (item.get('title', '') + ' ' + item.get('desc', '')).lower()
        pos_hits = [w for w in POSITIVE_WORDS if w in text]
        neg_hits = [w for w in NEGATIVE_WORDS if w in text]
        
        keyword_hits['positive'].extend(pos_hits)
        keyword_hits['negative'].extend(neg_hits)
        
        if pos_hits or neg_hits:
            score = len(pos_hits) - len(neg_hits)
            total_score += score
            scored_count += 1
    
    if scored_count > 0:
        avg = total_score / max(scored_count, 1)
        sentiment = max(-1, min(1, avg / 5))
    else:
        sentiment = 0.0
    
    return {
        'score': round(sentiment, 2),
        'positive_keywords': list(set(keyword_hits['positive'])),
        'negative_keywords': list(set(keyword_hits['negative'])),
        'scored_articles': scored_count,
    }


def _llm_sentiment_fallback(ticker: str, news_items: list) -> dict:
    """
    LLM 情感 fallback：当关键词匹配无法打分时，用 LLM 做情感分析。
    只对 Top-3 新闻标题做一句话情感判定。
    """
    if not news_items:
        return {'score': 0.0, 'n_articles': 0, 'llm_fallback': False}
    
    titles = [it['title'] for it in news_items[:5] if it.get('title') and len(it['title']) > 5]
    if not titles:
        return {'score': 0.0, 'n_articles': 0, 'llm_fallback': False}
    
    try:
        from openai import OpenAI
        api_key = os.getenv('DEEPSEEK_API_KEY', '')
        if not api_key:
            return {'score': 0.0, 'n_articles': 0, 'llm_fallback': False}
        
        client = OpenAI(
            api_key=api_key,
            base_url='https://api.deepseek.com/v1',
        )
        prompt = (
            "Analyze the sentiment of these Chinese financial news headlines about a stock.\n"
            "Return ONLY a JSON: {\"sentiment\": \"positive|negative|neutral\", \"score\": -1.0 to 1.0}\n"
            "Headlines:\n" + "\n".join(f"- {t}" for t in titles)
        )
        resp = client.chat.completions.create(
            model='deepseek-chat',
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.1,
            max_tokens=100,
            timeout=10,
        )
        text = resp.choices[0].message.content.strip()
        # 提取 JSON
        json_match = re.search(r'\{[^}]*\}', text)
        if json_match:
            data = json.loads(json_match.group())
            return {'score': float(data.get('score', 0)), 'n_articles': len(titles), 'llm_fallback': True}
        return {'score': 0.0, 'n_articles': len(titles), 'llm_fallback': False}
    except Exception:
        return {'score': 0.0, 'n_articles': 0, 'llm_fallback': False}


def _social_search_fast(ticker: str, name: str = '') -> dict:
    """
    快速社交搜索（带5秒超时）。
    没有凭证时直接返回空。
    """
    # 检查是否可能获得数据
    tw_token = os.getenv('TWITTER_AUTH_TOKEN', '')
    tw_ct0 = os.getenv('TWITTER_CT0', '')
    exa_key = os.getenv('EXA_API_KEY', '')
    
    has_any_cred = bool(tw_token and tw_ct0) or bool(exa_key)
    if not has_any_cred:
        # 没有凭证，尝试一下 Jina（免费但慢）
        try:
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
            from core.social_search_engine import search_jina
            query = f"{name or ticker} 股票 A股"
            items = search_jina(query, n=3)
            if items:
                sentiment = _calc_sentiment(items)
                return {
                    'score': sentiment['score'],
                    'items': items,
                    'total_count': len(items),
                    'source': 'jina_only',
                }
        except Exception:
            pass
        return {'score': 0.0, 'items': [], 'total_count': 0, 'source': 'none'}
    
    # 有凭证：完整跑一次社交搜索（带超时）
    try:
        from core.social_search_engine import get_social_sentiment
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(get_social_sentiment, ticker, name, '股票 A股')
            result = fut.result(timeout=8)
        return {
            'score': result.get('sentiment_score', 0),
            'items': result.get('items', []),
            'total_count': result.get('total_count', 0),
            'source': 'full_search',
        }
    except Exception:
        return {'score': 0.0, 'items': [], 'total_count': 0, 'source': 'timeout'}


def analyze(ticker, name="", current_date=None):
    """
    增强版新闻/舆情分析（已优化性能）
    """
    if current_date is None:
        current_date = datetime.now().strftime('%Y-%m-%d')
    
    # 1. 获取搜索关键词
    search_keyword = STOCK_KEYWORDS.get(ticker, name or ticker)
    
    # 2. 从东方财富搜索获取新闻
    news_items = _fetch_eastmoney_news(search_keyword)

    # 3. 如果东方财富没拿到，尝试NewsAPI
    if len(news_items) < 2:
        newsapi_items = _fetch_newsapi(name, ticker)
        if newsapi_items:
            news_items = newsapi_items

    # 4. akshare 个股新闻 + 财联社快讯 + 热榜
    akshare_news = _fetch_akshare_stock_news(ticker, name)
    cx_items = _fetch_akshare_cx_news()
    hot_rank_items = _fetch_akshare_hot_rank()
    
    # 只有 A 股个股查公告（用全量缓存避免重复下载）
    notice_items = []
    if len(ticker) == 6 and ticker.isdigit():
        notice_items = _get_cached_notices(ticker)
    
    # 研报（独立情绪源）
    research_items = _fetch_akshare_research_report(ticker, name)

    # 合并去重
    seen = set()
    all_news = []
    for it in news_items + akshare_news + cx_items + hot_rank_items + notice_items:
        t = it.get('title', '')
        if t and t not in seen and '错误' not in t:
            seen.add(t)
            all_news.append(it)
    news_items = all_news if all_news else news_items

    # 5. 快速社交搜索（不阻塞）
    social = _social_search_fast(ticker, name)
    social_items = social.get('items', [])
    social_sentiment = {
        'score': social.get('score', 0),
        'source': social.get('source', 'none'),
    }

    if not news_items:
        news_items = [{'title': '暂无最新新闻', 'desc': '', 'source': '', 'date': ''}]
    
    # 6. 情绪分析（关键词匹配）
    all_for_sentiment = news_items + [{'title': i.get('title', ''), 'desc': i.get('summary', '')} for i in social_items]
    sentiment = _calc_sentiment(all_for_sentiment)
    
    # 叠加研报情绪（权重 25%）
    research_sentiment = _rate_research_sentiment(research_items)
    if research_sentiment['total'] > 0:
        sentiment['score'] = round(sentiment['score'] * 0.75 + research_sentiment['score'] * 0.25, 2)
    
    # 7. LLM 情感 fallback：如果关键词匹配没有命中任何文章，调用 LLM
    if sentiment['scored_articles'] == 0 and news_items:
        llm_result = _llm_sentiment_fallback(ticker, news_items)
        if llm_result.get('n_articles', 0) > 0:
            sentiment['score'] = llm_result['score']
            sentiment['llm_fallback'] = llm_result.get('llm_fallback', False)
    
    # 8. 提取关键主题
    keywords = list(set(
        sentiment.get('positive_keywords', []) + sentiment.get('negative_keywords', [])
    ))
    
    return {
        'analyst': '新闻分析师',
        'ticker': ticker,
        'name': name,
        'news_count': len(news_items),
        'social_count': len(social_items),
        'news_items': news_items,
        'research_items': research_items,
        'notice_items': notice_items,
        'hot_rank_items': hot_rank_items,
        'social_items': social_items[:10],
        'sentiment_score': sentiment['score'],
        'sentiment_detail': sentiment,
        'social_sentiment': social_sentiment,
        'keywords': keywords[:10],
        'summary': _generate_summary(name, news_items, sentiment, keywords, social_items),
    }


def _calc_sentiment_fallback(news_items):
    """当 social_search_engine 不可用时使用的回退情绪打分。"""
    total_score = 0
    scored_count = 0
    keyword_hits = {'positive': [], 'negative': []}
    for item in news_items:
        text = (item.get('title', '') + ' ' + item.get('desc', '')).lower()
        pos_hits = [w for w in POSITIVE_WORDS if w in text]
        neg_hits = [w for w in NEGATIVE_WORDS if w in text]
        keyword_hits['positive'].extend(pos_hits)
        keyword_hits['negative'].extend(neg_hits)
        if pos_hits or neg_hits:
            score = len(pos_hits) - len(neg_hits)
            total_score += score
            scored_count += 1
    if scored_count > 0:
        avg = total_score / max(scored_count, 1)
        sentiment = max(-1, min(1, avg / 5))
    else:
        sentiment = 0.0
    return {
        'score': round(sentiment, 2),
        'positive_keywords': list(set(keyword_hits['positive'])),
        'negative_keywords': list(set(keyword_hits['negative'])),
        'scored_articles': scored_count,
    }


def _generate_summary(name, news_items, sentiment, keywords, social_items=None):
    """生成新闻分析摘要"""
    social_items = social_items or []
    lines = []
    lines.append(f"# 新闻与舆情分析报告")
    lines.append(f"")
    
    s = sentiment['score']
    if s > 0.3:
        label = "积极 🟢"
    elif s > -0.3:
        label = "中性 🟡"
    else:
        label = "消极 🔴"
    
    lines.append(f"## 综合情绪：{label}（{s:+.2f}）")
    lines.append(f"")
    
    if sentiment.get('positive_keywords'):
        lines.append(f"**利好词**: {' '.join(sentiment['positive_keywords'][:5])}")
    if sentiment.get('negative_keywords'):
        lines.append(f"**利空词**: {' '.join(sentiment['negative_keywords'][:5])}")
    lines.append(f"**情绪打分文章**: {sentiment['scored_articles']}/{len(news_items)}篇")
    if social_items:
        lines.append(f"**社交媒体/全网条目**: {len(social_items)}条")
    lines.append(f"")
    
    if keywords:
        lines.append(f"**关键词**: {' · '.join(keywords[:8])}")
        lines.append(f"")
    
    # 新闻列表
    has_real_news = any(n.get('title', '') not in ['暂无最新新闻', ''] and '错误' not in n.get('title', '')
                        for n in news_items)
    
    if has_real_news:
        lines.append(f"### 最新资讯")
        for i, item in enumerate(news_items[:6], 1):
            title = item.get('title', '')
            if title and '错误' not in title and title not in ['暂无最新新闻', '']:
                lines.append(f"{i}. **{title.strip()}**")
                if item.get('desc'):
                    lines.append(f"   {item['desc'][:150]}")
                if item.get('source'):
                    lines.append(f"   —— {item['source']} {item.get('date', '')}")
                lines.append(f"")
    else:
        lines.append(f"暂无最新新闻数据。")

    if social_items:
        lines.append(f"")
        lines.append(f"### 社交媒体/全网搜索")
        for i, item in enumerate(social_items[:5], 1):
            title = item.get('title', '') or item.get('summary', '')[:80]
            source = item.get('source', 'social')
            lines.append(f"{i}. [{source}] {title.strip()}")
    
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    for ticker, name in [('601991','大唐发电'), ('515880','通信ETF'), ('516150','稀土ETF')]:
        print(f"\n{'='*50}")
        result = analyze(ticker, name)
        print(result['summary'])
        print(f"  情绪分: {result['sentiment_score']:+.2f} | 新闻: {result['news_count']}条")
