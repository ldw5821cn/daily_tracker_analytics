#!/usr/bin/env python3
"""政策分析师 - 追踪监管和产业政策信号

基于 TradingAgents 架构，实现 A 股政策分析：
- 宏观政策 (货币/财政/汇率)
- 监管政策 (证监会/银保监会)
- 产业政策 (行业扶持/限制)
- 地方政策 (区域扶持)
- 国际政策 (中美关系/出口管制)
"""
from __future__ import annotations

import json
import os
import sys
from typing import Dict, List, Optional
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, MULTI_AGENT)

# 导入现有新闻模块
from analysts.news_analyst import analyze as news_analysis


class PolicyAnalyst:
    """政策分析师"""
    
    def __init__(self):
        self.cache_dir = os.path.join(MULTI_AGENT, 'data', 'policy_cache')
        os.makedirs(self.cache_dir, exist_ok=True)
    
    def analyze_policy_impact(self, ticker: str, name: str = "") -> Dict:
        """分析政策对个股的影响
        
        Args:
            ticker: 股票代码
            name: 股票名称
        
        Returns:
            政策分析结果
        """
        code = self._normalize_code(ticker)
        
        result = {
            'ticker': ticker,
            'code': code,
            'name': name,
            'date': datetime.now().strftime('%Y-%m-%d'),
            'policy_events': [],  # 政策事件列表
            'policy_rating': 'neutral',  # 重大利好/利好/中性/利空/重大利空
            'impact_level': 'medium',  # 强/中/弱
            'impact_duration': 'short',  # 短期/中期/长期
            'sector_impact': {},  # 板块影响
            'risk_factors': [],  # 风险因素
            'opportunities': [],  # 机会点
        }
        
        # 1. 获取相关新闻
        news_result = self._get_policy_news(code, name)
        if news_result:
            result['policy_events'] = news_result.get('policy_events', [])
            result['policy_rating'] = news_result.get('rating', 'neutral')
            result['impact_level'] = news_result.get('level', 'medium')
        
        # 2. 分析板块政策影响
        result['sector_impact'] = self._analyze_sector_policy(code, name)
        
        # 3. 生成综合评估
        result = self._generate_assessment(result)
        
        return result
    
    def _get_policy_news(self, code: str, name: str) -> Optional[Dict]:
        """获取政策相关新闻"""
        try:
            # 使用现有新闻分析模块
            news = news_analysis(code, name, datetime.now().strftime('%Y-%m-%d'))
            
            # 筛选政策相关新闻
            policy_keywords = ['政策', '监管', '证监会', '央行', '降准', '降息', '补贴', '调控', '法规']
            policy_events = []
            
            for item in news.get('news', []):
                title = item.get('title', '')
                content = item.get('content', '')
                
                # 检查是否包含政策关键词
                is_policy = any(kw in title or kw in content for kw in policy_keywords)
                if is_policy:
                    policy_events.append({
                        'date': item.get('date', ''),
                        'title': title,
                        'source': item.get('source', ''),
                        'summary': content[:200],
                    })
            
            # 评估政策影响
            rating = self._assess_policy_rating(policy_events)
            
            return {
                'policy_events': policy_events,
                'rating': rating['rating'],
                'level': rating['level'],
            }
            
        except Exception as e:
            print(f"获取政策新闻失败: {e}")
            return None
    
    def _analyze_sector_policy(self, code: str, name: str) -> Dict:
        """分析板块政策影响"""
        # 这里可以扩展为行业政策数据库查询
        # 目前返回示例数据
        return {
            'sector': '未知',
            'policy_direction': 'neutral',  # 扶持/限制/中性
            'support_policies': [],
            'restrict_policies': [],
        }
    
    def _assess_policy_rating(self, events: List[Dict]) -> Dict:
        """评估政策影响评级"""
        if not events:
            return {'rating': 'neutral', 'level': 'medium'}
        
        # 简单规则评估
        positive_keywords = ['利好', '扶持', '补贴', '鼓励', '支持', '降准', '降息']
        negative_keywords = ['利空', '限制', '调控', '监管', '处罚', '退市']
        
        positive_count = sum(1 for e in events 
                           if any(kw in e['title'] or kw in e['summary'] for kw in positive_keywords))
        negative_count = sum(1 for e in events 
                           if any(kw in e['title'] or kw in e['summary'] for kw in negative_keywords))
        
        # 判断评级
        if positive_count > negative_count + 2:
            rating = '重大利好'
            level = 'strong'
        elif positive_count > negative_count:
            rating = '利好'
            level = 'medium'
        elif negative_count > positive_count + 2:
            rating = '重大利空'
            level = 'strong'
        elif negative_count > positive_count:
            rating = '利空'
            level = 'medium'
        else:
            rating = '中性'
            level = 'weak'
        
        return {'rating': rating, 'level': level}
    
    def _generate_assessment(self, result: Dict) -> Dict:
        """生成综合评估"""
        # 根据评级生成风险和机会
        rating = result['policy_rating']
        
        if rating in ['重大利好', '利好']:
            result['opportunities'].append('政策支持力度大，可关注投资机会')
        elif rating in ['重大利空', '利空']:
            result['risk_factors'].append('政策调控风险，建议谨慎')
        
        return result
    
    def get_daily_policy_summary(self) -> Dict:
        """获取每日政策摘要"""
        # 这里可以实现全市场政策监控
        return {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'major_policies': [],
            'sector_impacts': {},
            'market_sentiment': 'neutral',
        }
    
    def _normalize_code(self, ticker: str) -> str:
        """标准化股票代码"""
        return ticker.replace('.SH', '').replace('.SZ', '').replace('.BJ', '').strip()


def analyze_policy(ticker: str, name: str = "") -> Dict:
    """政策分析 - 便捷函数"""
    analyst = PolicyAnalyst()
    return analyst.analyze_policy_impact(ticker, name)


if __name__ == '__main__':
    # 测试
    print("=== 测试政策分析师 ===")
    result = analyze_policy('000001', '平安银行')
    print(json.dumps(result, ensure_ascii=False, indent=2))
