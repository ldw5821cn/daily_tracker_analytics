#!/usr/bin/env python3
"""解禁监控模块

监控限售股解禁、大宗交易、股东增减持
提前预警解禁风险，发现大宗交易机会
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import akshare as ak
import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, MULTI_AGENT)


class LockupMonitor:
    """解禁监控器"""
    
    def __init__(self):
        self.cache_dir = os.path.join(MULTI_AGENT, 'data', 'lockup_cache')
        os.makedirs(self.cache_dir, exist_ok=True)
    
    def get_upcoming_lockups(self, days: int = 30) -> List[Dict]:
        """获取未来 N 天的解禁数据
        
        Args:
            days: 未来天数 (默认30天)
        
        Returns:
            解禁数据列表
        """
        try:
            # 使用 akshare 获取解禁数据
            end_date = (datetime.now() + timedelta(days=days)).strftime('%Y%m%d')
            start_date = datetime.now().strftime('%Y%m%d')
            
            # 获取解禁数据
            df = ak.stock_restricted_release_queue_em(
                symbol="全部股票",
                start_date=start_date,
                end_date=end_date
            )
            
            if df is None or df.empty:
                return []
            
            # 转换为字典列表
            lockups = []
            for _, row in df.iterrows():
                lockups.append({
                    'code': row.get('代码', ''),
                    'name': row.get('名称', ''),
                    'release_date': row.get('解禁时间', ''),
                    'release_shares': row.get('解禁数量', 0),
                    'market_value': row.get('实际解禁市值', 0),
                    'holder_type': row.get(' holder_type', '未知'),
                    'lockup_type': row.get('解禁类型', ''),
                })
            
            # 缓存数据
            self._cache_lockups(lockups)
            
            return lockups
            
        except Exception as e:
            print(f"Error fetching lockup data: {e}")
            # 尝试从缓存加载
            return self._load_cached_lockups()
    
    def check_stock_lockup(self, ticker: str) -> Dict:
        """检查个股解禁风险
        
        Args:
            ticker: 股票代码 (如 000001.SZ)
        
        Returns:
            解禁风险分析
        """
        code = self._normalize_code(ticker)
        
        result = {
            'ticker': ticker,
            'code': code,
            'has_upcoming_lockup': False,
            'next_lockup_date': None,
            'total_unlock_shares': 0,
            'unlock_ratio': 0,  # 解禁股占总股本比例
            'risk_level': 'low',  # low/medium/high
            'recommendation': 'hold'
        }
        
        # 获取未来解禁数据
        lockups = self.get_upcoming_lockups(days=90)
        
        # 查找该股票的解禁
        stock_lockups = [l for l in lockups if l['code'] == code]
        
        if stock_lockups:
            result['has_upcoming_lockup'] = True
            result['next_lockup_date'] = min(l['release_date'] for l in stock_lockups)
            result['total_unlock_shares'] = sum(l['release_shares'] for l in stock_lockups)
            
            # 计算解禁比例 (需要总股本数据，这里简化)
            # result['unlock_ratio'] = ...
            
            # 评估风险等级
            if result['total_unlock_shares'] > 100000000:  # > 1亿股
                result['risk_level'] = 'high'
                result['recommendation'] = 'sell_before_lockup'
            elif result['total_unlock_shares'] > 50000000:  # > 5000万股
                result['risk_level'] = 'medium'
                result['recommendation'] = 'reduce_position'
            else:
                result['risk_level'] = 'low'
                result['recommendation'] = 'hold'
        
        return result
    
    def get_daily_lockup_summary(self, date: str = None) -> Dict:
        """获取每日解禁总结"""
        date = date or datetime.now().strftime('%Y-%m-%d')
        
        lockups = self.get_upcoming_lockups(days=1)
        
        summary = {
            'date': date,
            'total_lockups': len(lockups),
            'total_unlock_value': sum(l.get('market_value', 0) for l in lockups),
            'high_risk_stocks': [],
            'medium_risk_stocks': [],
            'low_risk_stocks': []
        }
        
        for lockup in lockups:
            if lockup.get('market_value', 0) > 1000000000:  # > 10亿
                summary['high_risk_stocks'].append(lockup)
            elif lockup.get('market_value', 0) > 500000000:  # > 5亿
                summary['medium_risk_stocks'].append(lockup)
            else:
                summary['low_risk_stocks'].append(lockup)
        
        return summary
    
    def _normalize_code(self, ticker: str) -> str:
        """标准化股票代码"""
        return ticker.replace('.SH', '').replace('.SZ', '').replace('.BJ', '').strip()
    
    def _cache_lockups(self, lockups: List[Dict]):
        """缓存解禁数据"""
        cache_file = os.path.join(self.cache_dir, 
                                 f"{datetime.now().strftime('%Y%m%d')}.json")
        with open(cache_file, 'w') as f:
            json.dump(lockups, f, ensure_ascii=False, indent=2)
    
    def _load_cached_lockups(self) -> List[Dict]:
        """从缓存加载解禁数据"""
        cache_file = os.path.join(self.cache_dir,
                                 f"{datetime.now().strftime('%Y%m%d')}.json")
        if os.path.exists(cache_file):
            with open(cache_file, 'r') as f:
                return json.load(f)
        return []


def check_lockup_risk(ticker: str) -> Dict:
    """检查个股解禁风险 - 便捷函数"""
    monitor = LockupMonitor()
    return monitor.check_stock_lockup(ticker)


def get_daily_lockup_report(date: str = None) -> Dict:
    """获取每日解禁报告 - 便捷函数"""
    monitor = LockupMonitor()
    return monitor.get_daily_lockup_summary(date)


if __name__ == '__main__':
    # 测试
    print("=== 测试解禁监控 ===")
    
    monitor = LockupMonitor()
    
    # 测试个股检查
    print("\n1. 检查个股解禁风险")
    result = monitor.check_stock_lockup('000001.SZ')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    
    # 测试每日总结
    print("\n2. 获取每日解禁总结")
    summary = monitor.get_daily_lockup_summary()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
