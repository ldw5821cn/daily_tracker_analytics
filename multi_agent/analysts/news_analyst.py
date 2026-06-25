"""
新闻分析师 - 增强版舆情分析
数据源：东方财富搜索API + NewsAPI(备) + 关键词情绪打分
"""
import sys
import os
sys.path.insert(0, '/home/zhihu/daily_tracker_analytics/etf_tracker/multi_agent')

import json
import re
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')


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
    '下调', '评级下调', '减持', '质押', '平仓', '资金流出', '流出',
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
        
        # 提取JSON
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
        return [{'title': f'东方财富API错误: {e}', 'desc': '', 'source': '', 'date': ''}]


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


def _calc_sentiment(news_items):
    """基于新闻标题和描述的情绪打分"""
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
    
    # 归一化到 -1 ~ 1
    if scored_count > 0:
        avg = total_score / max(scored_count, 1)
        sentiment = max(-1, min(1, avg / 5))  # 除以5降低单篇极端值影响
    else:
        sentiment = 0.0
    
    return {
        'score': round(sentiment, 2),
        'positive_keywords': list(set(keyword_hits['positive'])),
        'negative_keywords': list(set(keyword_hits['negative'])),
        'scored_articles': scored_count,
    }


def analyze(ticker, name="", current_date="2026-07-02"):
    """
    增强版新闻/舆情分析
    """
    # 1. 获取搜索关键词
    search_keyword = STOCK_KEYWORDS.get(ticker, name or ticker)
    
    # 2. 从东方财富获取新闻
    news_items = _fetch_eastmoney_news(search_keyword)
    
    # 3. 如果东方财富没拿到，尝试NewsAPI
    if len(news_items) < 2 or '错误' in news_items[0].get('title', ''):
        newsapi_items = _fetch_newsapi(name, ticker)
        if newsapi_items:
            news_items = newsapi_items
    
    if not news_items:
        news_items = [{'title': '暂无最新新闻', 'desc': '', 'source': '', 'date': ''}]
    
    # 4. 情绪分析
    sentiment = _calc_sentiment(news_items)
    
    # 5. 提取关键主题
    keywords = list(set(
        sentiment['positive_keywords'] + sentiment['negative_keywords']
    ))
    
    return {
        'analyst': '新闻分析师',
        'ticker': ticker,
        'name': name,
        'news_count': len(news_items),
        'news_items': news_items,
        'sentiment_score': sentiment['score'],
        'sentiment_detail': sentiment,
        'keywords': keywords[:10],
        'summary': _generate_summary(name, news_items, sentiment, keywords),
    }


def _generate_summary(name, news_items, sentiment, keywords):
    """生成新闻分析摘要"""
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
    
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    for ticker, name in [('601991','大唐发电'), ('515880','通信ETF'), ('516150','稀土ETF')]:
        print(f"\n{'='*50}")
        result = analyze(ticker, name)
        print(result['summary'])
        print(f"  情绪分: {result['sentiment_score']:+.2f} | 新闻: {result['news_count']}条")
