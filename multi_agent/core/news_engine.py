"""新闻/公告数据引擎。

基于 akshare 获取东方财富个股新闻、公司公告，输出结构化 JSON。
所有东方财富 HTTP 请求统一走 data_layer._em_get 防封限流。
"""
import json
import os
import sys
from datetime import datetime, timedelta
from typing import List, Dict

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))

# 复用 data_layer 的东财防封限流入口
try:
    from .data_layer import _em_get
except ImportError:
    sys.path.insert(0, PROJECT_ROOT)
    from multi_agent.core.data_layer import _em_get


def fetch_stock_news_em(ticker: str, name: str = "", pagesize: int = 10) -> List[Dict]:
    """从东方财富获取个股新闻。"""
    try:
        import akshare as ak
        # 个股新闻：akshare 底层也走东财，无法直接替换，但 akshare 本身有缓存/节流逻辑
        df = ak.stock_news_em(symbol=ticker)
    except Exception:
        return []

    if df is None or df.empty:
        return []

    items = []
    for _, row in df.head(pagesize).iterrows():
        items.append({
            'title': str(row.get('新闻标题', '')),
            'desc': str(row.get('新闻内容', ''))[:300] if '新闻内容' in row else '',
            'source': str(row.get('文章来源', '东方财富')),
            'date': str(row.get('发布时间', ''))[:10],
            'url': str(row.get('新闻链接', '')),
        })
    return items


def fetch_stock_notice(ticker: str, pagesize: int = 5) -> List[Dict]:
    """从东方财富获取公司公告（按日期查询，暂返回空）。"""
    return []


def fetch_futures_news(name: str, pagesize: int = 5) -> List[Dict]:
    """期货新闻：通过东方财富搜索 API 获取，走 _em_get 限流。"""
    import urllib.parse, re
    try:
        encoded = urllib.parse.quote(name)
        url = (f"https://search-api-web.eastmoney.com/search/jsonp"
               f"?cb=jQuery&param=%7B%22uid%22%3A%22%22%2C%22keyword%22%3A%22{encoded}%22"
               f"%2C%22type%22%3A%5B%22cmsArticleWebOld%22%5D%2C%22client%22%3A%22web%22"
               f"%2C%22clientType%22%3A%22web%22%2C%22clientVersion%22%3A%22curr%22"
               f"%2C%22param%22%3A%7B%22cmsArticleWebOld%22%3A%7B%22searchScope%22%3A%22default%22"
               f"%2C%22sort%22%3A%22default%22%2C%22pageIndex%22%3A1%2C%22pageSize%22%3A{pagesize}%7D%7D%7D")
        r = _em_get(url, headers={
            'Referer': 'https://www.eastmoney.com/',
        }, timeout=10)
        text = r.text
        json_match = re.search(r'jQuery\((.*)\)', text)
        if not json_match:
            return []
        data = json.loads(json_match.group(1))
        articles = data.get('result', {}).get('cmsArticleWebOld', [])
        return [{
            'title': a.get('title', '').strip(),
            'desc': a.get('content', '')[:300] if a.get('content') else '',
            'source': '东方财富',
            'date': a.get('date', '')[:10],
            'url': a.get('url', ''),
        } for a in articles]
    except Exception:
        return []


def get_unstructured_data(ticker: str, name: str, category: str) -> Dict:
    """获取指定标的的新闻和公告。"""
    if category == '期货':
        news = fetch_futures_news(name)
        notices = []
    else:
        news = fetch_stock_news_em(ticker, name)
        notices = fetch_stock_notice(ticker)

    return {
        'ticker': ticker,
        'name': name,
        'category': category,
        'news': news,
        'notices': notices,
        'fetched_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
    }


if __name__ == '__main__':
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else '688981'
    name = sys.argv[2] if len(sys.argv) > 2 else '中芯国际'
    category = sys.argv[3] if len(sys.argv) > 3 else '个股'
    data = get_unstructured_data(ticker, name, category)
    print(json.dumps(data, ensure_ascii=False, indent=2)[:2000])
