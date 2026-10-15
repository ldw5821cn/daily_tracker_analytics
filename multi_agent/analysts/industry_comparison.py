#!/usr/bin/env python3
"""行业对比分析师 - 板块轮动和个股行业地位分析

基于 TradingAgents 架构，实现行业对比分析：
- 个股 vs 行业表现
- 行业资金流向
- 板块轮动趋势
"""
from __future__ import annotations

import json
import os
import sys
from typing import Dict, List, Optional
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, MULTI_AGENT)

# 导入现有数据模块
from scripts.fetch_fund_flow_cache import load_latest_cache


class IndustryComparisonAnalyst:
    """行业对比分析师"""
    
    def __init__(self):
        self.cache_dir = os.path.join(MULTI_AGENT, 'data', 'industry_cache')
        os.makedirs(self.cache_dir, exist_ok=True)
    
    def compare_with_industry(self, ticker: str, name: str = "") -> Dict:
        """个股与行业对比
        
        Args:
            ticker: 股票代码
            name: 股票名称
        
        Returns:
            行业对比结果
        """
        code = self._normalize_code(ticker)
        
        result = {
            'ticker': ticker,
            'code': code,
            'name': name,
            'date': datetime.now().strftime('%Y-%m-%d'),
            'industry': '',  # 所属行业
            'industry_performance': {},  # 行业表现
            'stock_vs_industry': {},  # 个股 vs 行业
            'sector_rank': 0,  # 行业内排名
            'sector_percentile': 0,  # 行业内百分位
            'recommendation': 'hold',  # 建议
        }
        
        # 1. 获取行业数据
        industry_data = self._get_industry_data()
        if industry_data:
            result['industry_performance'] = industry_data
        
        # 2. 对比个股与行业
        stock_data = self._get_stock_data(code)
        if stock_data and industry_data:
            result['stock_vs_industry'] = self._compare_metrics(stock_data, industry_data)
            result['recommendation'] = self._generate_recommendation(result)
        
        return result
    
    def analyze_sector_rotation(self) -> Dict:
        """分析板块轮动
        
        Returns:
            板块轮动分析
        """
        cache = load_latest_cache()
        
        # 获取行业资金流向
        industry_flow = cache.get('industry', [])
        concept_flow = cache.get('concept', [])
        
        # 分析板块热度
        hot_sectors = sorted(industry_flow, 
                           key=lambda x: x.get('净额_万元', 0), 
                           reverse=True)[:10]
        
        cold_sectors = sorted(industry_flow, 
                            key=lambda x: x.get('净额_万元', 0))[:10]
        
        return {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'hot_sectors': [
                {'name': s.get('行业', ''), 'net_inflow': s.get('净额_万元', 0)}
                for s in hot_sectors
            ],
            'cold_sectors': [
                {'name': s.get('行业', ''), 'net_outflow': s.get('净额_万元', 0)}
                for s in cold_sectors
            ],
            'rotation_signal': self._detect_rotation(hot_sectors, cold_sectors),
        }
    
    def _get_industry_data(self) -> Optional[Dict]:
        """获取行业数据"""
        try:
            cache = load_latest_cache()
            industry_data = cache.get('industry', [])
            
            if not industry_data:
                return None
            
            # 计算行业平均涨跌幅、资金流向等
            avg_change = sum(item.get('涨跌幅_pct', 0) for item in industry_data) / len(industry_data)
            total_inflow = sum(item.get('净额_万元', 0) for item in industry_data)
            
            return {
                'avg_change': avg_change,
                'total_inflow': total_inflow,
                'sector_count': len(industry_data),
                'sectors': industry_data,
            }
            
        except Exception as e:
            print(f"获取行业数据失败: {e}")
            return None
    
    def _get_stock_data(self, code: str) -> Optional[Dict]:
        """获取个股数据"""
        # 这里可以从数据库或缓存获取
        # 目前返回示例数据
        return {
            'code': code,
            'change_pct': 0,  # 涨跌幅
            'fund_flow': 0,  # 资金流向
            'turnover': 0,  # 换手率
        }
    
    def _compare_metrics(self, stock: Dict, industry: Dict) -> Dict:
        """对比个股与行业指标"""
        comparison = {}
        
        # 对比涨跌幅
        if 'change_pct' in stock and 'avg_change' in industry:
            diff = stock['change_pct'] - industry['avg_change']
            comparison['performance_vs_industry'] = {
                'stock': stock['change_pct'],
                'industry': industry['avg_change'],
                'diff': diff,
                'assessment': 'outperform' if diff > 0 else 'underperform',
            }
        
        return comparison
    
    def _generate_recommendation(self, result: Dict) -> str:
        """生成投资建议"""
        comparison = result.get('stock_vs_industry', {})
        performance = comparison.get('performance_vs_industry', {})
        
        if performance.get('assessment') == 'outperform':
            return 'buy'  # 跑赢行业，建议买入
        elif performance.get('assessment') == 'underperform':
            return 'sell'  # 跑输行业，建议卖出
        else:
            return 'hold'
    
    def _detect_rotation(self, hot: List, cold: List) -> str:
        """检测板块轮动信号"""
        if not hot or not cold:
            return 'no_clear_rotation'
        
        # 简单判断：如果热点板块净流入远大于冷门板块，说明轮动明显
        hot_inflow = sum(s.get('net_inflow', 0) for s in hot)
        cold_outflow = sum(abs(s.get('net_outflow', 0)) for s in cold)
        
        if hot_inflow > cold_outflow * 2:
            return 'strong_rotation'
        elif hot_inflow > cold_outflow:
            return 'moderate_rotation'
        else:
            return 'weak_rotation'
    
    def _normalize_code(self, ticker: str) -> str:
        """标准化股票代码"""
        return ticker.replace('.SH', '').replace('.SZ', '').replace('.BJ', '').strip()


def compare_with_industry(ticker: str, name: str = "") -> Dict:
    """行业对比 - 便捷函数"""
    analyst = IndustryComparisonAnalyst()
    return analyst.compare_with_industry(ticker, name)


def analyze_sector_rotation() -> Dict:
    """板块轮动分析 - 便捷函数"""
    analyst = IndustryComparisonAnalyst()
    return analyst.analyze_sector_rotation()


if __name__ == '__main__':
    # 测试
    print("=== 测试行业对比分析师 ===\n")
    
    # 测试个股对比
    print("1. 个股与行业对比")
    result = compare_with_industry('000001', '平安银行')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    
    print("\n2. 板块轮动分析")
    rotation = analyze_sector_rotation()
    print(json.dumps(rotation, ensure_ascii=False, indent=2))
