#!/usr/bin/env python3
"""
行业新闻与热点跟踪模块
获取稀土永磁、机器人、AI、芯片、存储等行业的最新动态
"""

import urllib.request
import json
import re
from datetime import datetime, timedelta
from typing import List, Dict
import html

class IndustryNewsTracker:
    """行业新闻跟踪器"""
    
    def __init__(self):
        self.sources = {
            "稀土": {
                "keywords": ["稀土", "永磁", "钕铁硼", "镨钕", "氧化镨钕", "稀土价格"],
                "etfs": ["516150", "159713", "561800"]
            },
            "机器人": {
                "keywords": ["机器人", "人形机器人", "伺服电机", "减速器", "工业机器人"],
                "etfs": ["159559", "562500", "159892"]
            },
            "AI": {
                "keywords": ["人工智能", "AI", "大模型", "算力", "ChatGPT", "AIGC"],
                "etfs": ["159819", "512930", "159819"]
            },
            "芯片": {
                "keywords": ["芯片", "半导体", "集成电路", "晶圆", "光刻", "国产替代"],
                "etfs": ["512760", "159995", "159801"]
            },
            "存储": {
                "keywords": ["存储", "内存", "DRAM", "NAND", "闪存", "长鑫", "长江存储"],
                "etfs": ["159321", "159813", "512480"]
            }
        }
    
    def fetch_news_from_api(self, keyword: str, limit: int = 10) -> List[Dict]:
        """从聚合新闻API获取新闻"""
        try:
            # 使用新浪财经新闻API
            url = f"https://search.api.sina.com.cn/?c=news&q={urllib.parse.quote(keyword)}&size={limit}&page=1"
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            resp = urllib.request.urlopen(req, timeout=10)
            data = json.loads(resp.read().decode('utf-8'))
            
            news_list = []
            if 'result' in data and 'data' in data['result']:
                for item in data['result']['data']:
                    news_list.append({
                        'title': html.unescape(item.get('title', '')),
                        'summary': html.unescape(item.get('summary', '')),
                        'url': item.get('url', ''),
                        'source': item.get('media', ''),
                        'datetime': item.get('datetime', '')
                    })
            return news_list
        except Exception as e:
            print(f"  ⚠️ 获取新闻失败 [{keyword}]: {e}")
            return []
    
    def fetch_news_from_eastmoney(self, keyword: str, limit: int = 10) -> List[Dict]:
        """从东方财富获取新闻"""
        try:
            # 东方财富新闻搜索
            url = f"https://searchapi.eastmoney.com/api/suggest?input={urllib.parse.quote(keyword)}&type=14&count={limit}"
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': 'https://so.eastmoney.com/'
            })
            resp = urllib.request.urlopen(req, timeout=10)
            data = json.loads(resp.read().decode('utf-8'))
            
            news_list = []
            if 'QuotationCodeTable' in data and 'Data' in data['QuotationCodeTable']:
                for item in data['QuotationCodeTable']['Data']:
                    news_list.append({
                        'title': item.get('Name', ''),
                        'code': item.get('Code', ''),
                        'type': item.get('Classify', '')
                    })
            return news_list
        except Exception as e:
            print(f"  ⚠️ 获取东方财富数据失败 [{keyword}]: {e}")
            return []
    
    def get_rare_earth_market_info(self) -> str:
        """获取稀土市场动态摘要"""
        # 这里可以集成 iFinD 数据获取稀土价格
        info = """### 稀土市场概况

**价格动态**:
- 氧化镨钕: 近期价格走势（需从 iFinD 获取最新数据）
- 氧化镝: 重稀土代表品种
- 氧化铽: 高性能磁材关键原料

**供需分析**:
- 供应端: 中国稀土配额、缅甸进口情况
- 需求端: 新能源汽车、风电、机器人等下游需求

**政策影响**:
- 国家稀土战略储备
- 出口管制政策变化
- 环保政策对开采的影响

**国际形势**:
- 中美贸易关系对稀土出口的影响
- 海外稀土矿山开发进展（美国MP Materials、澳大利亚Lynas等）
- 欧盟《关键原材料法案》对稀土供应链的重构
"""
        return info
    
    def get_industry_summary(self) -> str:
        """生成行业动态摘要"""
        summary = """### 热点行业动态

#### 🤖 机器人行业
- **人形机器人**: 特斯拉 Optimus 量产进展、Figure AI 融资动态
- **核心零部件**: 伺服电机、谐波减速器、力矩传感器对稀土永磁需求
- **政策催化**: 工信部人形机器人创新发展指导意见

#### 🧠 AI / 人工智能
- **算力需求**: AI 大模型训练/推理对 GPU/ASIC 芯片需求拉动
- **端侧 AI**: 手机、PC AI 化对存储芯片容量需求提升
- **相关标的**: 寒武纪、海光信息、景嘉微等国产 AI 芯片

#### 💻 芯片制造
- **国产替代**: 中芯国际先进制程进展、华为麒麟芯片回归
- **设备材料**: 光刻机、刻蚀机、薄膜沉积设备国产化
- **封测**: 长电科技、通富微电等封测厂订单情况

#### 💾 存储 / 内存
- **价格周期**: DRAM/NAND 价格触底反弹迹象
- **国产突破**: 长鑫存储 DDR5、长江存储 232层 NAND
- **HBM**: 高带宽内存需求受益于 AI 算力建设

#### 🔋 新能源（稀土下游）
- **新能源汽车**: 永磁同步电机对钕铁硼需求
- **风电**: 直驱永磁风机渗透率提升
- **节能家电**: 变频空调、冰箱压缩机磁材需求
"""
        return summary
    
    def get_daily_news_summary(self) -> str:
        """获取每日新闻摘要（用于报告）"""
        sections = []
        
        # 稀土市场
        sections.append(self.get_rare_earth_market_info())
        
        # 热点行业
        sections.append(self.get_industry_summary())
        
        return "\n\n".join(sections)


if __name__ == "__main__":
    tracker = IndustryNewsTracker()
    print(tracker.get_daily_news_summary())
