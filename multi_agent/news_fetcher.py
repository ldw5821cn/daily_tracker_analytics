#!/usr/bin/env python3
"""
多源新闻抓取模块 - P1 大V复盘

数据源：
1. 东方财富公告（公开 API）
2. 新浪财经滚动新闻（公开 API）
3. 财联社电报（备用）

注意：雪球内容 API 需要有效 cookies（目前 400016 过期），
      待 cookies 更新后可添加 XueqiuFetcher

用法：
    from news_fetcher import NewsFetcher
    
    fetcher = NewsFetcher()
    news = fetcher.fetch_all(keywords=['AI', '算力', '半导体'], limit=20)
    # 返回统一格式的新闻列表
"""

import requests
import json
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict
from enum import Enum


class NewsSource(Enum):
    """新闻来源"""
    EASTMONEY = "eastmoney"
    SINA = "sina"
    CLS = "cls"
    XUEQIU = "xueqiu"


@dataclass
class NewsItem:
    """
    统一新闻格式
    
    Attributes:
        source: 来源（eastmoney/sina/cls/xueqiu）
        source_id: 原始 ID
        title: 标题
        content: 内容摘要
        url: 原文链接
        publish_time: 发布时间
        keywords: 匹配的关键词
        sentiment_hint: 情绪提示（optional/positive/negative）
    """
    source: str
    source_id: str
    title: str
    content: str
    url: str
    publish_time: str
    keywords: List[str] = None
    sentiment_hint: str = "optional"
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    def matches_keywords(self, keywords: List[str]) -> bool:
        """检查是否匹配关键词"""
        if not keywords:
            return True
        text = (self.title + " " + self.content).lower()
        for kw in keywords:
            if kw.lower() in text:
                if self.keywords is None:
                    self.keywords = []
                if kw not in self.keywords:
                    self.keywords.append(kw)
                return True
        return False


class EastMoneyFetcher:
    """东方财富公告抓取"""
    
    BASE_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"
    
    HEADERS = {
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }
    
    def fetch(self, limit: int = 20, stock_code: str = None) -> List[NewsItem]:
        """
        抓取东方财富公告
        
        Args:
            limit: 数量限制
            stock_code: 可选，指定股票代码（如 000001）
            
        Returns:
            NewsItem 列表
        """
        try:
            params = {
                'sr': -1,
                'page_size': limit,
                'page_index': 1,
                'ann_type': 'A',
                'client_source': 'web'
            }
            if stock_code:
                params['stock_list'] = stock_code
            
            resp = requests.get(self.BASE_URL, params=params, headers=self.HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            
            items = []
            if 'data' in data and 'list' in data['data']:
                for item in data['data']['list']:
                    # 解析时间
                    notice_date = item.get('notice_date', '')
                    if notice_date:
                        try:
                            dt = datetime.fromisoformat(notice_date.replace('Z', '+00:00'))
                            publish_time = dt.strftime('%Y-%m-%d %H:%M:%S')
                        except:
                            publish_time = notice_date
                    else:
                        publish_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    
                    # 构建 URL
                    art_code = item.get('art_code', '')
                    url = f"https://data.eastmoney.com/notices/detail/{art_code}.html" if art_code else ""
                    
                    news_item = NewsItem(
                        source=NewsSource.EASTMONEY.value,
                        source_id=art_code or str(item.get('notice_date', '')),
                        title=item.get('title', ''),
                        content=item.get('title', ''),  # 公告只有标题
                        url=url,
                        publish_time=publish_time
                    )
                    items.append(news_item)
            
            return items
            
        except Exception as e:
            print(f"❌ 东方财富抓取失败: {e}")
            return []


class SinaFetcher:
    """新浪财经滚动新闻抓取"""
    
    BASE_URL = "https://feed.mix.sina.com.cn/api/roll/get"
    
    HEADERS = {
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    }
    
    # 栏目 ID 映射
    CHANNELS = {
        '财经': '2516',
        '股票': '2517',
        '要闻': '2515',
    }
    
    def fetch(self, limit: int = 20, channel: str = '财经') -> List[NewsItem]:
        """
        抓取新浪财经滚动新闻
        
        Args:
            limit: 数量限制
            channel: 栏目（财经/股票/要闻）
            
        Returns:
            NewsItem 列表
        """
        try:
            lid = self.CHANNELS.get(channel, '2516')
            params = {
                'pageid': 153,
                'lid': lid,
                'num': limit,
                'page': 1
            }
            
            resp = requests.get(self.BASE_URL, params=params, headers=self.HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            
            items = []
            if 'result' in data and 'data' in data['result']:
                for item in data['result']['data']:
                    # 解析时间戳
                    ctime = item.get('ctime', '')
                    if ctime and ctime.isdigit():
                        try:
                            dt = datetime.fromtimestamp(int(ctime))
                            publish_time = dt.strftime('%Y-%m-%d %H:%M:%S')
                        except:
                            publish_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    else:
                        publish_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    
                    news_item = NewsItem(
                        source=NewsSource.SINA.value,
                        source_id=item.get('docid', ''),
                        title=item.get('title', ''),
                        content=item.get('intro', '') or item.get('title', ''),
                        url=item.get('url', ''),
                        publish_time=publish_time
                    )
                    items.append(news_item)
            
            return items
            
        except Exception as e:
            print(f"❌ 新浪财经抓取失败: {e}")
            return []


class XueqiuFetcher:
    """
    雪球内容抓取（需要有效 cookies）
    
    注意：当前 cookies 已过期（400016 错误），待更新后可启用
    """
    
    def __init__(self, cookies: str = None):
        self.cookies = cookies
        self.headers = {
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Referer': 'https://xueqiu.com/',
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Cookie': cookies or '',
        }
    
    def fetch_user_articles(self, user_id: str, limit: int = 10) -> List[NewsItem]:
        """
        抓取指定用户的文章（需要 cookies）
        
        Args:
            user_id: 雪球用户 ID
            limit: 数量限制
            
        Returns:
            NewsItem 列表
        """
        if not self.cookies:
            print("⚠️ 雪球 cookies 未配置，跳过抓取")
            return []
        
        try:
            url = f"https://xueqiu.com/statuses/original/timeline.json?user_id={user_id}&page=1&count={limit}"
            resp = requests.get(url, headers=self.headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            
            items = []
            if 'statuses' in data:
                for status in data['statuses']:
                    # 解析时间戳
                    created_at = status.get('created_at', 0)
                    try:
                        dt = datetime.fromtimestamp(created_at / 1000)
                        publish_time = dt.strftime('%Y-%m-%d %H:%M:%S')
                    except:
                        publish_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    
                    # 提取文本内容
                    text = status.get('text', '')
                    if text:
                        # 去除 HTML 标签
                        text = re.sub(r'<[^>]+>', '', text)
                        text = text.strip()
                    
                    news_item = NewsItem(
                        source=NewsSource.XUEQIU.value,
                        source_id=str(status.get('id', '')),
                        title=status.get('title', '') or text[:50] + '...',
                        content=text[:500] if text else '',
                        url=f"https://xueqiu.com{status.get('target', '')}",
                        publish_time=publish_time
                    )
                    items.append(news_item)
            
            return items
            
        except Exception as e:
            print(f"❌ 雪球抓取失败: {e}")
            return []


class NewsFetcher:
    """
    多源新闻聚合抓取器
    
    自动聚合多个数据源，统一格式，支持关键词过滤
    """
    
    def __init__(self, xueqiu_cookies: str = None):
        self.eastmoney = EastMoneyFetcher()
        self.sina = SinaFetcher()
        self.xueqiu = XueqiuFetcher(xueqiu_cookies) if xueqiu_cookies else None
    
    def fetch_all(
        self,
        keywords: List[str] = None,
        limit_per_source: int = 20,
        sources: List[str] = None
    ) -> List[NewsItem]:
        """
        抓取所有数据源
        
        Args:
            keywords: 关键词过滤（如 ['AI', '算力', '半导体']）
            limit_per_source: 每个源的数量限制
            sources: 指定源（默认全部）
            
        Returns:
            过滤后的 NewsItem 列表，按时间倒序
        """
        all_items = []
        
        # 默认使用所有可用源
        if sources is None:
            sources = ['eastmoney', 'sina']
            if self.xueqiu and self.xueqiu.cookies:
                sources.append('xueqiu')
        
        # 东方财富
        if 'eastmoney' in sources:
            items = self.eastmoney.fetch(limit=limit_per_source)
            all_items.extend(items)
        
        # 新浪财经
        if 'sina' in sources:
            items = self.sina.fetch(limit=limit_per_source, channel='财经')
            all_items.extend(items)
            items = self.sina.fetch(limit=limit_per_source, channel='股票')
            all_items.extend(items)
        
        # 雪球（如果有 cookies）
        if 'xueqiu' in sources and self.xueqiu:
            # 示例：抓取几个知名大V
            # 实际使用时应配置关注列表
            pass
        
        # 关键词过滤
        if keywords:
            filtered = []
            for item in all_items:
                if item.matches_keywords(keywords):
                    filtered.append(item)
            all_items = filtered
        
        # 按时间倒序排序
        all_items.sort(key=lambda x: x.publish_time, reverse=True)
        
        return all_items
    
    def fetch_by_stock(
        self,
        stock_code: str,
        limit: int = 10
    ) -> List[NewsItem]:
        """
        抓取指定股票相关的新闻
        
        Args:
            stock_code: 股票代码（如 000001）
            limit: 数量限制
            
        Returns:
            NewsItem 列表
        """
        items = []
        
        # 东方财富公告
        em_items = self.eastmoney.fetch(limit=limit, stock_code=stock_code)
        items.extend(em_items)
        
        # 按时间排序
        items.sort(key=lambda x: x.publish_time, reverse=True)
        
        return items[:limit]


# ============================================================
# 大V关注列表配置（待 cookies 更新后启用）
# ============================================================

# 知名大V雪球用户 ID（示例）
WATCHLIST_XUEQIU = {
    # 'user_id': '昵称',
    # '7886890620': '雪球官方',
    # 待添加...
}

# 关键词分组（用于观点聚合）
KEYWORD_GROUPS = {
    'AI/算力': ['AI', '算力', '人工智能', '大模型', 'AIGC', 'ChatGPT', 'GPU', '服务器'],
    '半导体': ['半导体', '芯片', '晶圆', '光刻', 'EDA', '存储', '封测'],
    '新能源': ['新能源', '光伏', '风电', '储能', '锂电', '电动车', '充电桩'],
    '消费': ['消费', '白酒', '食品饮料', '免税', '零售', '餐饮'],
    '医药': ['医药', '创新药', '医疗器械', 'CXO', '疫苗', '生物'],
    '金融': ['银行', '券商', '保险', '金融科技', '支付'],
    '地产': ['地产', '房地产', '物业', '建材', '家电'],
    '红利': ['红利', '高股息', '分红', '央企', '国企'],
}


# ============================================================
# 测试
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("🧪 多源新闻抓取模块测试")
    print("=" * 60)
    
    fetcher = NewsFetcher()
    
    # 测试1: 抓取所有新闻
    print("\n【测试1】抓取财经新闻")
    news = fetcher.fetch_all(limit_per_source=5)
    print(f"✅ 获取到 {len(news)} 条新闻")
    for i, item in enumerate(news[:3], 1):
        print(f"\n  {i}. [{item.source}] {item.title[:40]}...")
        print(f"     时间: {item.publish_time}")
    
    # 测试2: 关键词过滤
    print("\n【测试2】关键词过滤（AI/算力）")
    news = fetcher.fetch_all(keywords=['AI', '算力'], limit_per_source=10)
    print(f"✅ 过滤后 {len(news)} 条新闻")
    for i, item in enumerate(news[:3], 1):
        kws = ', '.join(item.keywords or [])
        print(f"\n  {i}. [{item.source}] {item.title[:40]}...")
        print(f"     关键词: {kws}")
    
    # 测试3: 股票相关新闻
    print("\n【测试3】股票相关新闻（000001 平安银行）")
    news = fetcher.fetch_by_stock('000001', limit=5)
    print(f"✅ 获取到 {len(news)} 条相关新闻")
    for i, item in enumerate(news[:3], 1):
        print(f"\n  {i}. {item.title[:50]}...")
    
    print("\n" + "=" * 60)
    print("✅ 测试完成")
