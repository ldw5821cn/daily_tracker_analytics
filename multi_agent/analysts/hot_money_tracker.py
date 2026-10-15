#!/usr/bin/env python3
"""游资追踪分析师：追踪主力资金、龙虎榜、量价异动

基于现有数据缓存：
- multi_agent/data/lhb_cache/{date}.json (龙虎榜)
- multi_agent/data/fund_flow_cache.json (资金流向)
- multi_agent/data/hot_stock_list_{date}.json (热门股票)

提供游资动向分析、席位追踪、涨停分析等功能。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, MULTI_AGENT)

# 导入现有数据模块
from scripts.fetch_lhb_cache import fetch_lhb as _fetch_lhb
from scripts.fetch_fund_flow_cache import load_latest_cache


class HotMoneyTracker:
    """游资追踪分析师"""
    
    def __init__(self):
        self.lhb_cache_dir = os.path.join(MULTI_AGENT, 'data', 'lhb_cache')
        self.fund_flow_cache = os.path.join(MULTI_AGENT, 'data', 'fund_flow_cache.json')
    
    def analyze_stock(self, ticker: str, date: str = None) -> Dict:
        """分析个股的游资动向
        
        Args:
            ticker: 股票代码 (如 000001.SZ)
            date: 分析日期 (默认最近交易日)
        
        Returns:
            游资分析结果
        """
        code = self._normalize_code(ticker)
        
        result = {
            'ticker': ticker,
            'code': code,
            'date': date or datetime.now().strftime('%Y-%m-%d'),
            'hot_money_score': 0,  # 0-100 游资活跃度评分
            'signals': [],  # 游资信号列表
            'lhb_data': None,  # 龙虎榜数据
            'fund_flow': None,  # 资金流向数据
            'recommendation': 'neutral'  # bullish/bearish/neutral
        }
        
        # 1. 获取龙虎榜数据
        lhb_data = self._get_lhb_data(code, result['date'])
        if lhb_data:
            result['lhb_data'] = lhb_data
            result['signals'].append('lhb_listed')
            result['hot_money_score'] += 30
        
        # 2. 获取资金流向数据
        fund_flow = self._get_fund_flow(code)
        if fund_flow:
            result['fund_flow'] = fund_flow
            net_inflow = fund_flow.get('net_inflow', 0)
            if net_inflow > 100000000:  # 净流入 > 1亿
                result['signals'].append('large_inflow')
                result['hot_money_score'] += 25
            elif net_inflow > 50000000:  # 净流入 > 5000万
                result['signals'].append('medium_inflow')
                result['hot_money_score'] += 15
        
        # 3. 计算综合评分
        result['hot_money_score'] = min(100, result['hot_money_score'])
        
        # 4. 生成建议
        if result['hot_money_score'] >= 70:
            result['recommendation'] = 'bullish'
        elif result['hot_money_score'] >= 40:
            result['recommendation'] = 'neutral'
        else:
            result['recommendation'] = 'bearish'
        
        return result
    
    def analyze_sector(self, sector: str) -> Dict:
        """分析板块的游资动向"""
        # TODO: 实现板块分析
        return {'sector': sector, 'status': 'not_implemented'}
    
    def _normalize_code(self, ticker: str) -> str:
        """标准化股票代码"""
        return ticker.replace('.SH', '').replace('.SZ', '').replace('.BJ', '').strip()
    
    def _get_lhb_data(self, code: str, date: str) -> Optional[Dict]:
        """获取龙虎榜数据"""
        try:
            cache_file = os.path.join(self.lhb_cache_dir, f'{date}.json')
            if os.path.exists(cache_file):
                with open(cache_file, 'r') as f:
                    data = json.load(f)
                    # 查找该股票的数据
                    for item in data.get('top_buyers', []):
                        if item.get('code') == code:
                            return item
        except Exception as e:
            print(f"Error loading LHB data: {e}")
        return None
    
    def _get_fund_flow(self, code: str) -> Optional[Dict]:
        """获取资金流向数据"""
        try:
            cache = load_latest_cache()
            for item in cache.get('individual', []):
                if str(item.get('股票代码', '')) == code or int(item.get('股票代码', 0)) == int(code):
                    return {
                        'net_inflow': item.get('净额_万元', 0) * 10000,  # 转换为元
                        'buy_amount': item.get('买入_万元', 0) * 10000,
                        'sell_amount': item.get('卖出_万元', 0) * 10000,
                        'turnover_rate': item.get('换手率', 0)
                    }
        except Exception as e:
            print(f"Error loading fund flow: {e}")
        return None
    
    def get_daily_summary(self, date: str = None) -> Dict:
        """获取每日游资动向总结"""
        date = date or datetime.now().strftime('%Y-%m-%d')
        
        summary = {
            'date': date,
            'total_lhb_stocks': 0,
            'hot_sectors': [],
            'top_inflow_stocks': [],
            'top_outflow_stocks': [],
            'market_sentiment': 'neutral'
        }
        
        # 加载龙虎榜数据
        try:
            cache_file = os.path.join(self.lhb_cache_dir, f'{date}.json')
            if os.path.exists(cache_file):
                with open(cache_file, 'r') as f:
                    data = json.load(f)
                    summary['total_lhb_stocks'] = data.get('total_count', 0)
                    summary['inst_net_buy_total'] = data.get('inst_net_buy_total', 0)
        except Exception as e:
            print(f"Error loading daily summary: {e}")
        
        # 加载资金流向数据
        try:
            cache = load_latest_cache()
            # 按净流入排序
            individual = cache.get('individual', [])
            sorted_by_inflow = sorted(individual, key=lambda x: x.get('净额_万元', 0), reverse=True)
            
            summary['top_inflow_stocks'] = [
                {'code': item.get('股票代码'), 'name': item.get('股票名称'), 'net_inflow': item.get('净额_万元')}
                for item in sorted_by_inflow[:10]
            ]
            
            summary['top_outflow_stocks'] = [
                {'code': item.get('股票代码'), 'name': item.get('股票名称'), 'net_outflow': item.get('净额_万元')}
                for item in sorted_by_inflow[-10:]
            ]
        except Exception as e:
            print(f"Error loading fund flow summary: {e}")
        
        return summary


def create_hot_money_tracker():
    """创建游资追踪分析师实例"""
    return HotMoneyTracker()


def analyze_hot_money(ticker: str, date: str = None) -> Dict:
    """便捷函数：分析个股游资动向"""
    tracker = create_hot_money_tracker()
    return tracker.analyze_stock(ticker, date)


if __name__ == '__main__':
    # 测试
    tracker = create_hot_money_tracker()
    
    # 测试个股分析
    print("=== 测试个股分析 ===")
    result = tracker.analyze_stock('000001.SZ')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    
    # 测试每日总结
    print("\n=== 测试每日总结 ===")
    summary = tracker.get_daily_summary()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
