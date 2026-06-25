#!/usr/bin/env python3
"""
行业新闻自动获取模块
功能：
1. 自动获取稀土、机器人、AI、芯片等行业新闻
2. 支持多数据源（新浪财经、东方财富、同花顺）
3. 新闻摘要与关键词提取
4. 情感分析（正面/负面/中性）
5. 与ETF/个股关联分析
"""

import os
import sys
import json
import pandas as pd
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import warnings
warnings.filterwarnings('ignore')

# 添加 akshare 路径 (仅在非虚拟环境时)
if not hasattr(sys, 'real_prefix') and sys.base_prefix == sys.prefix:
    sys.path.insert(0, '/home/zhihu/.linuxbrew/Cellar/python@3.10/3.10.9/lib/python3.10/site-packages')

try:
    import akshare as ak
    AKSHARE_AVAILABLE = True
except ImportError:
    AKSHARE_AVAILABLE = False


class NewsFetcher:
    """新闻获取器"""
    
    # 行业关键词映射（含上下游产业链）
    INDUSTRY_KEYWORDS = {
        "稀土永磁": [
            "稀土", "永磁", "磁材", "钕铁硼", "镨钕", "氧化镨钕", "北方稀土", "中国稀土",
            "镧铈", "钆", "钬", "重稀土", "轻稀土", "稀土矿", "稀土开采", "稀土分离",
            "磁材电机", "永磁电机", "新能源车电机", "风电电机", "节能电机",
            "稀土出口管制", "稀土配额", "稀土氧化物", "金属镨钕"
        ],
        "机器人": [
            "机器人", "工业机器人", "人形机器人", "协作机器人", "机器人概念", "减速器", "伺服电机",
            "谐波减速器", "RV减速器", "行星滚柱丝杠", "空心杯电机", "力传感器", "视觉传感器",
            "灵巧手", "具身智能", "通用人形机器人", "Optimus", "Figure", "特斯拉机器人",
            "宇树科技", "优必选", "埃斯顿", "汇川技术", "绿的谐波", "三花智控"
        ],
        "人工智能": [
            "人工智能", "AI", "大模型", "GPT", "ChatGPT", "AIGC", "机器学习", "深度学习",
            "生成式AI", "LLM", "多模态", "AI Agent", "智能体", "RAG", "算力",
            "AI芯片", "AI服务器", "液冷服务器", "光模块", "CPO", "英伟达", "NVIDIA",
            "OpenAI", "Anthropic", "Gemini", "百度文心", "阿里通义", "字节豆包", "月之暗面"
        ],
        "芯片制造": [
            "芯片", "半导体", "晶圆", "代工", "光刻", "刻蚀", "薄膜沉积", "中芯国际",
            "台积电", "先进制程", "7nm", "5nm", "3nm", "EUV光刻机", "DUV光刻机",
            "半导体设备", "半导体材料", "光刻胶", "CMP抛光液", "电子特气", "硅片",
            "晶圆厂", "IDM", "Fabless", "Chiplet", "先进封装", "SoC", "ASIC"
        ],
        "存储行业": [
            "存储", "内存", "DRAM", "NAND", "闪存", "SSD", "HBM", "长鑫存储", "长江存储",
            "存储芯片", "NOR Flash", "eMMC", "UFS", "DDR5", "DDR6", "LPDDR5",
            "HBM3", "HBM3E", "HBM4", "存储模组", "存储涨价", "存储周期", "存储拐点",
            "美光", "三星存储", "SK海力士", "西部数据", "铠侠"
        ],
        "内存制造": [
            "内存", "DRAM", "DDR", "LPDDR", "HBM", "存储芯片", "长鑫存储",
            "内存颗粒", "内存模组", "内存涨价", "DRAM周期", "内存制造", "内存芯片",
            "HBM产能", "高带宽内存", "DDR5", "DDR4", "LPDDR5X"
        ]
    }
    
    # 数据源与上游/国际标签
    NEWS_SOURCES = ["akshare", "sina", "eastmoney", "newsapi"]
    
    # NewsAPI 配置
    NEWSAPI_BASE = "https://newsapi.org/v2/everything"
    
    # 关键股票/标的映射（用于后续关联ETF成分股）
    INDUSTRY_TICKERS = {
        "稀土永磁": ["600111", "600010", "000831", "600259", "300748", "600549", "600392", "688077"],
        "机器人": ["002747", "300124", "603728", "002031", "603915", "300750", "002050", "601100"],
        "人工智能": ["300308", "002230", "300418", "688256", "603019", "688981", "300502"],
        "芯片制造": ["688981", "603893", "600584", "300661", "688012", "002371", "603501"],
        "存储行业": ["688525", "688416", "300223", "688110", "603986", "002185"],
        "内存制造": ["688525", "688416", "300223", "688110"]
    }
    
    @staticmethod
    def get_news_from_sina(keyword: str, days: int = 3) -> List[Dict]:
        """从新浪财经获取新闻（修复：绕过代理）"""
        try:
            import urllib.request
            # 修复：临时移除代理，避免容器内 https_proxy 拦截新浪
            old_proxy = os.environ.pop('https_proxy', None)
            
            # 使用新浪财经搜索API
            url = f"https://search.api.sina.com.cn/?q={urllib.parse.quote(keyword)}&c=news&sort=time&page=1&num=20"
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': 'https://finance.sina.com.cn/'
            })
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read().decode('utf-8'))
            
            # 恢复代理
            if old_proxy is not None:
                os.environ['https_proxy'] = old_proxy
            
            news_list = []
            if 'result' in data and 'list' in data['result']:
                for item in data['result']['list']:
                    news_list.append({
                        "title": item.get('title', ''),
                        "url": item.get('url', ''),
                        "source": "新浪财经",
                        "time": item.get('time', ''),
                        "summary": item.get('summary', '')
                    })
            
            return news_list
        except Exception as e:
            print(f"  新浪财经新闻获取失败: {e}")
            # 恢复代理
            if old_proxy is not None:
                os.environ['https_proxy'] = old_proxy
            return []
    
    @staticmethod
    def get_news_from_eastmoney(keyword: str, days: int = 3) -> List[Dict]:
        """从东方财富获取新闻（使用稳定 API）"""
        try:
            import urllib.request
            # 东方财富新闻搜索 - 使用更稳定的搜索API
            url = f"https://search-api-web.eastmoney.com/search/jsonp?cb=jQuery&param=%7B%22uid%22%3A%22%22%2C%22keyword%22%3A%22{urllib.parse.quote(keyword)}%22%2C%22type%22%3A%5B%22cms%22%5D%2C%22client%22%3A%22web%22%2C%22clientType%22%3A%22web%22%2C%22clientVersion%22%3A%22curr%22%2C%22param%22%3A%7B%22cms%22%3A%7B%22searchScope%22%3A%22default%22%2C%22sort%22%3A%22default%22%2C%22pageIndex%22%3A1%2C%22pageSize%22%3A10%2C%22preTag%22%3A%22%22%2C%22postTag%22%3A%22%22%7D%7D%7D"
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': 'https://www.eastmoney.com/'
            })
            resp = urllib.request.urlopen(req, timeout=15)
            text = resp.read().decode('utf-8')
            
            # 提取 JSON 从 jsonp 响应
            import re
            json_match = re.search(r'\{.*\}', text, re.DOTALL)
            if not json_match:
                return []
            data = json.loads(json_match.group())
            
            news_list = []
            articles = data.get('article', []) or data.get('list', []) or data.get('result', [])
            if isinstance(articles, list):
                for item in articles:
                    title = item.get('title', '') or item.get('original_title', '') or item.get('art_title', '')
                    content = item.get('content', '') or item.get('summary', '') or item.get('art_content', '')
                    if not title:
                        continue
                    news_list.append({
                        "title": title,
                        "url": item.get('url', '') or item.get('art_url', ''),
                        "source": "东方财富",
                        "time": str(item.get('date', '') or item.get('art_date', '') or item.get('showDate', '')),
                        "summary": content[:200] if content else ""
                    })
            
            return news_list
        except Exception as e:
            print(f"  东方财富新闻获取失败: {e}")
            return []
    
    @staticmethod
    def get_industry_news_from_akshare(industry: str = "稀土永磁", days: int = 3) -> List[Dict]:
        """使用AkShare获取行业新闻"""
        try:
            import akshare as ak
            import pandas as pd
            # 修复 pyarrow ArrowStringArray 正则转义问题：切换到 python string backend
            pd.options.mode.string_storage = 'python'
            
            # 获取财经新闻
            df = ak.stock_news_em()
            
            if len(df) == 0:
                return []
            
            # 兼容新旧列名
            title_col = '新闻标题' if '新闻标题' in df.columns else '标题'
            content_col = '新闻内容' if '新闻内容' in df.columns else '内容'
            url_col = '链接' if '链接' in df.columns else '新闻链接'
            source_col = '文章来源' if '文章来源' in df.columns else '来源'
            time_col = '发布时间' if '发布时间' in df.columns else '时间'
            
            # 筛选相关行业新闻
            keywords = NewsFetcher.INDUSTRY_KEYWORDS.get(industry, [industry])
            
            filtered_news = []
            for _, row in df.iterrows():
                title = str(row.get(title_col, ''))
                content = str(row.get(content_col, ''))
                
                # 检查是否包含关键词
                if any(kw in title or kw in content for kw in keywords):
                    filtered_news.append({
                        "title": title,
                        "url": row.get(url_col, ''),
                        "source": row.get(source_col, '东方财富'),
                        "time": str(row.get(time_col, '')),
                        "summary": content[:200] + "..." if len(content) > 200 else content
                    })
            
            return filtered_news[:20]  # 最多返回20条
        except Exception as e:
            print(f"  AkShare新闻获取失败: {e}")
            return []
    
    @staticmethod
    def get_news_from_newsapi(industry: str = "稀土永磁", days: int = 3) -> List[Dict]:
        """从 NewsAPI 获取行业新闻（补充全球视角）"""
        api_key = os.getenv("NEWSAPI_API_KEY", "")
        if not api_key:
            return []
        
        # NewsAPI 关键词映射（英文关键词，针对全球新闻）
        NEWSAPI_KEYWORDS = {
            "稀土永磁": "rare earth magnet",
            "机器人": "humanoid robot OR industrial robot",
            "人工智能": "AI OR artificial intelligence OR large language model",
            "芯片制造": "semiconductor OR chip manufacturing OR foundry",
            "存储行业": "HBM OR DRAM OR NAND flash memory",
            "内存制造": "DRAM OR HBM memory chip",
        }
        query = NEWSAPI_KEYWORDS.get(industry, industry)
        
        try:
            import urllib.request
            import urllib.parse
            url = f"{NewsFetcher.NEWSAPI_BASE}?q={urllib.parse.quote(query)}&language=en&sortBy=publishedAt&pageSize=10"
            req = urllib.request.Request(url, headers={
                'User-Agent': 'HermesETF/1.0',
                'X-Api-Key': api_key
            })
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read().decode('utf-8'))
            
            news_list = []
            for article in data.get('articles', []):
                title = article.get('title', '')
                if not title:
                    continue
                summary = article.get('description', '') or ''
                news_list.append({
                    "title": title,
                    "url": article.get('url', ''),
                    "source": f"NewsAPI/{article.get('source', {}).get('name', 'unknown')}",
                    "time": article.get('publishedAt', '')[:10],
                    "summary": summary[:200] if summary else "",
                    "content_type": "global"
                })
            
            return news_list
        except Exception as e:
            print(f"  NewsAPI 获取失败: {e}")
            return []
    
    @staticmethod
    def get_all_industry_news(industries: Optional[List[str]] = None, days: int = 3,
                               max_news_per_source: int = 10) -> Dict[str, List[Dict]]:
        """获取所有行业新闻（多源聚合）"""
        if industries is None:
            industries = list(NewsFetcher.INDUSTRY_KEYWORDS.keys())
        
        all_news = {}
        for industry in industries:
            print(f"  正在获取 {industry} 行业新闻...")
            
            aggregated = []
            seen_titles = set()
            
            def _dedup_add(news_list, source_tag):
                for item in news_list[:max_news_per_source]:
                    title = item.get('title', '')
                    if not title or title in seen_titles:
                        continue
                    seen_titles.add(title)
                    item['source_tag'] = source_tag
                    aggregated.append(item)
            
            # 多源并行获取
            try:
                _dedup_add(NewsFetcher.get_industry_news_from_akshare(industry, days), 'akshare')
            except Exception as e:
                print(f"    akshare 获取失败: {e}")
            try:
                _dedup_add(NewsFetcher.get_news_from_sina(industry, days), 'sina')
            except Exception as e:
                print(f"    sina 获取失败: {e}")
            try:
                _dedup_add(NewsFetcher.get_news_from_eastmoney(industry, days), 'eastmoney')
            except Exception as e:
                print(f"    eastmoney 获取失败: {e}")
            # NewsAPI 作为第4源（英文全球视角）
            try:
                _dedup_add(NewsFetcher.get_news_from_newsapi(industry, days), 'newsapi')
            except Exception as e:
                print(f"    newsapi 获取失败: {e}")
            
            all_news[industry] = aggregated
            print(f"  获取到 {len(aggregated)} 条去重新闻")
        
        return all_news
    
    @staticmethod
    def get_multi_industry_news(industries: Optional[List[str]] = None, days: int = 3,
                                max_news_per_source: int = 5) -> Dict[str, List[Dict]]:
        """供外部调用：获取多行业新闻（含情感标签）"""
        if industries is None:
            industries = list(NewsFetcher.INDUSTRY_KEYWORDS.keys())
        all_news = NewsFetcher.get_all_industry_news(industries, days, max_news_per_source)
        for industry, news_list in all_news.items():
            for item in news_list:
                sentiment = NewsAnalyzer.analyze_sentiment(item)
                item['sentiment'] = sentiment['sentiment']
                item['sentiment_score'] = sentiment['score']
        return all_news


class NewsAnalyzer:
    """新闻分析器"""
    
    # 情感词典
    POSITIVE_WORDS = ["上涨", "利好", "突破", "增长", "强劲", "复苏", "创新", "领先", "优势", "成功",
                       "大涨", "飙升", "涨停", "创新高", "超预期", "景气", "繁荣", "扩张"]
    
    NEGATIVE_WORDS = ["下跌", "利空", "跌破", "衰退", "疲软", "风险", "危机", "亏损", "下滑", "放缓",
                       "大跌", "暴跌", "跌停", "创新低", "不及预期", "衰退", "萎缩", "收缩"]
    
    @staticmethod
    def analyze_sentiment(news: Dict) -> Dict:
        """分析单条新闻情感"""
        title = news.get('title', '')
        summary = news.get('summary', '')
        text = title + summary
        
        positive_count = sum(1 for word in NewsAnalyzer.POSITIVE_WORDS if word in text)
        negative_count = sum(1 for word in NewsAnalyzer.NEGATIVE_WORDS if word in text)
        
        if positive_count > negative_count:
            sentiment = "positive"
            score = min(positive_count / 5, 1.0)
        elif negative_count > positive_count:
            sentiment = "negative"
            score = -min(negative_count / 5, 1.0)
        else:
            sentiment = "neutral"
            score = 0.0
        
        return {
            "sentiment": sentiment,
            "score": round(score, 2),
            "positive_words": positive_count,
            "negative_words": negative_count
        }
    
    @staticmethod
    def analyze_industry_sentiment(news_list: List[Dict]) -> Dict:
        """分析行业整体情感"""
        if not news_list:
            return {"sentiment": "neutral", "score": 0, "news_count": 0}
        
        sentiments = []
        for news in news_list:
            sentiment = NewsAnalyzer.analyze_sentiment(news)
            sentiments.append(sentiment)
        
        avg_score = sum(s['score'] for s in sentiments) / len(sentiments)
        positive_count = sum(1 for s in sentiments if s['sentiment'] == 'positive')
        negative_count = sum(1 for s in sentiments if s['sentiment'] == 'negative')
        neutral_count = len(sentiments) - positive_count - negative_count
        
        if avg_score > 0.2:
            overall = "positive"
        elif avg_score < -0.2:
            overall = "negative"
        else:
            overall = "neutral"
        
        return {
            "sentiment": overall,
            "score": round(avg_score, 2),
            "news_count": len(news_list),
            "positive_count": positive_count,
            "negative_count": negative_count,
            "neutral_count": neutral_count,
            "positive_ratio": round(positive_count / len(sentiments) * 100, 1),
            "negative_ratio": round(negative_count / len(sentiments) * 100, 1)
        }
    
    @staticmethod
    def extract_keywords(text: str) -> List[str]:
        """提取关键词"""
        # 简单的关键词提取（基于行业词典）
        keywords = []
        for industry, words in NewsFetcher.INDUSTRY_KEYWORDS.items():
            for word in words:
                if word in text and word not in keywords:
                    keywords.append(word)
        return keywords
    
    @staticmethod
    def generate_news_summary(news_list: List[Dict], max_items: int = 5) -> str:
        """生成新闻摘要"""
        if not news_list:
            return "暂无相关新闻"
        
        summary = []
        for i, news in enumerate(news_list[:max_items]):
            sentiment = NewsAnalyzer.analyze_sentiment(news)
            emoji = "📈" if sentiment['sentiment'] == 'positive' else "📉" if sentiment['sentiment'] == 'negative' else "📊"
            summary.append(f"{i+1}. {emoji} {news['title']}")
        
        return "\n".join(summary)


class IndustryNewsTracker:
    """行业新闻跟踪器"""
    
    def __init__(self, industries: List[str] = None):
        self.industries = industries or list(NewsFetcher.INDUSTRY_KEYWORDS.keys())
        self.news_fetcher = NewsFetcher()
        self.news_analyzer = NewsAnalyzer()
    
    def track_all(self, days: int = 3) -> Dict:
        """跟踪所有行业新闻"""
        print("=" * 60)
        print("正在获取行业新闻...")
        print("=" * 60)
        
        # 获取新闻
        all_news = self.news_fetcher.get_all_industry_news(self.industries, days)
        
        # 分析情感
        results = {}
        for industry, news_list in all_news.items():
            sentiment = self.news_analyzer.analyze_industry_sentiment(news_list)
            summary = self.news_analyzer.generate_news_summary(news_list)
            
            results[industry] = {
                "news_count": sentiment['news_count'],
                "sentiment": sentiment['sentiment'],
                "sentiment_score": sentiment['score'],
                "positive_ratio": sentiment['positive_ratio'],
                "negative_ratio": sentiment['negative_ratio'],
                "summary": summary,
                "news_list": news_list[:10]  # 只保存前10条
            }
            
            print(f"\n{industry}:")
            print(f"  新闻数量: {sentiment['news_count']}")
            print(f"  情感倾向: {sentiment['sentiment']} (得分: {sentiment['score']})")
            print(f"  正面占比: {sentiment['positive_ratio']}%")
            print(f"  负面占比: {sentiment['negative_ratio']}%")
            print(f"  最新动态:\n{summary}")
        
        return results
    
    def generate_market_impact_report(self, news_data: Dict) -> str:
        """生成市场影响报告"""
        report = "# 行业新闻与市场影响分析\n\n"
        report += f"> **报告日期**: {datetime.now().strftime('%Y-%m-%d')}\n\n"
        
        # 整体市场情绪
        total_score = sum(d['sentiment_score'] for d in news_data.values()) / len(news_data) if news_data else 0
        overall_sentiment = "乐观" if total_score > 0.2 else "谨慎" if total_score < -0.2 else "中性"
        
        report += f"## 整体市场情绪: {overall_sentiment} (得分: {total_score:.2f})\n\n"
        
        # 各行业分析
        for industry, data in news_data.items():
            sentiment_emoji = "📈" if data['sentiment'] == 'positive' else "📉" if data['sentiment'] == 'negative' else "📊"
            report += f"## {sentiment_emoji} {industry}\n\n"
            report += f"- 新闻数量: {data['news_count']}\n"
            report += f"- 情感倾向: {data['sentiment']} (得分: {data['sentiment_score']})\n"
            report += f"- 正面占比: {data['positive_ratio']}%\n"
            report += f"- 负面占比: {data['negative_ratio']}%\n\n"
            report += f"**最新动态**:\n\n{data['summary']}\n\n"
            report += "---\n\n"
        
        # 投资建议
        report += "## 投资建议\n\n"
        positive_industries = [k for k, v in news_data.items() if v['sentiment'] == 'positive']
        negative_industries = [k for k, v in news_data.items() if v['sentiment'] == 'negative']
        
        if positive_industries:
            report += f"### 利好行业\n"
            for industry in positive_industries:
                report += f"- {industry}: 新闻情绪积极，可关注相关标的\n"
            report += "\n"
        
        if negative_industries:
            report += f"### 利空行业\n"
            for industry in negative_industries:
                report += f"- {industry}: 新闻情绪消极，注意风险\n"
            report += "\n"
        
        report += "\n*注: 新闻分析基于公开信息，仅供参考，不构成投资建议*\n"
        
        return report


if __name__ == "__main__":
    # 测试
    tracker = IndustryNewsTracker()
    results = tracker.track_all(days=3)
    
    # 生成报告
    report = tracker.generate_market_impact_report(results)
    print("\n" + "=" * 60)
    print(report)
