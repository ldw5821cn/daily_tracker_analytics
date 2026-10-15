#!/usr/bin/env python3
"""内部人交易追踪模块

基于 SkillNet a-share-insider 思路，实现 A股内部人交易追踪：
- 股东/高管增减持分析
- 内部人行为信号识别
- 与解禁监控联动
"""
from __future__ import annotations

import json
import os
import sys
from typing import Dict, List, Optional
from datetime import datetime, timedelta

import pandas as pd

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, MULTI_AGENT)


class InsiderTracker:
    """内部人交易追踪器"""
    
    def __init__(self):
        self.cache_dir = os.path.join(MULTI_AGENT, 'data', 'insider_cache')
        os.makedirs(self.cache_dir, exist_ok=True)
    
    def track_insider_trades(self, ticker: str, name: str = "") -> Dict:
        """追踪个股内部人交易
        
        Args:
            ticker: 股票代码
            name: 股票名称
        
        Returns:
            内部人交易分析结果
        """
        code = self._normalize_code(ticker)
        
        result = {
            'ticker': ticker,
            'code': code,
            'name': name,
            'date': datetime.now().strftime('%Y-%m-%d'),
            'insider_trades': [],  # 增减持明细
            'major_holders': [],  # 十大股东
            'signals': [],  # 信号列表
            'risk_assessment': {},  # 风险评估
            'recommendation': 'hold',
        }
        
        # 1. 获取增减持数据
        trades = self._get_insider_trades(code)
        if trades:
            result['insider_trades'] = trades
        
        # 2. 获取十大股东
        holders = self._get_major_holders(code)
        if holders:
            result['major_holders'] = holders
        
        # 3. 分析信号
        result['signals'] = self._analyze_signals(trades, holders)
        
        # 4. 风险评估
        result['risk_assessment'] = self._assess_risk(result['signals'])
        
        # 5. 生成建议
        result['recommendation'] = self._generate_recommendation(result)
        
        return result
    
    def get_market_insider_summary(self, days: int = 7) -> Dict:
        """获取全市场内部人交易摘要
        
        Args:
            days: 最近天数
        
        Returns:
            市场摘要
        """
        try:
            import akshare as ak
            
            # 获取增减持数据
            df = ak.stock_inner_trade_xq()
            
            if df is None or df.empty:
                return {'date': datetime.now().strftime('%Y-%m-%d'), 'trades': []}
            
            # 筛选近期数据
            df['变动日期'] = pd.to_datetime(df['变动日期'])
            cutoff = datetime.now() - timedelta(days=days)
            recent = df[df['变动日期'] >= cutoff]
            
            # 统计
            summary = {
                'date': datetime.now().strftime('%Y-%m-%d'),
                'period_days': days,
                'total_trades': len(recent),
                'increase_trades': len(recent[recent['变动方向'] == '增持']),
                'decrease_trades': len(recent[recent['变动方向'] == '减持']),
                'top_increase': recent[recent['变动方向'] == '增持'].head(10).to_dict('records'),
                'top_decrease': recent[recent['变动方向'] == '减持'].head(10).to_dict('records'),
            }
            
            return summary
            
        except Exception as e:
            print(f"获取市场内部人数据失败: {e}")
            return {'date': datetime.now().strftime('%Y-%m-%d'), 'error': str(e)}
    
    def _get_insider_trades(self, code: str) -> List[Dict]:
        """获取个股增减持数据"""
        try:
            import akshare as ak
            
            # 获取主要股东数据
            df = ak.stock_main_stock_holder(stock=code)
            
            if df is None or df.empty:
                return []
            
            # 筛选最近的股东变动
            trades = []
            for _, row in df.iterrows():
                holder_name = str(row.get('股东名称', ''))
                # 简单判断是否有变动（通过股东说明字段）
                change_info = row.get('股东说明', '')
                if pd.isna(change_info):
                    change_info = ''
                else:
                    change_info = str(change_info)
                
                trades.append({
                    'holder': holder_name,
                    'shares': float(row.get('持股数量', 0)) if pd.notna(row.get('持股数量')) else 0,
                    'ratio': float(row.get('持股比例', 0)) if pd.notna(row.get('持股比例')) else 0,
                    'holder_type': self._classify_holder(holder_name),
                    'change_info': change_info,
                    'date': str(row.get('截至日期', '')),
                })
            
            return trades
            
        except Exception as e:
            print(f"获取增减持数据失败: {e}")
            return []
    
    def _get_major_holders(self, code: str) -> List[Dict]:
        """获取十大股东"""
        try:
            import akshare as ak
            
            # 使用 akshare 获取主要股东
            df = ak.stock_main_stock_holder(stock=code)
            
            if df is None or df.empty:
                return []
            
            # 去重并排序
            df = df.drop_duplicates(subset=['股东名称'], keep='first')
            df = df.sort_values('持股比例', ascending=False)
            
            holders = []
            for idx, (_, row) in enumerate(df.head(10).iterrows()):
                holders.append({
                    'rank': idx + 1,
                    'name': str(row.get('股东名称', '')),
                    'shares': float(row.get('持股数量', 0)) if pd.notna(row.get('持股数量')) else 0,
                    'ratio': float(row.get('持股比例', 0)) if pd.notna(row.get('持股比例')) else 0,
                    'change': 0,  # akshare 接口不直接提供变动
                })
            
            return holders
            
        except Exception as e:
            print(f"获取十大股东失败: {e}")
            return []
    
    def _classify_holder(self, name: str) -> str:
        """分类股东类型"""
        if not name:
            return 'unknown'
        
        # 简单规则分类
        keywords = {
            'controlling': ['控股', '集团', '实业', '投资'],
            'executive': ['董事', '监事', '高管', '经理'],
            'institution': ['基金', '保险', '社保', 'QFII'],
            'individual': ['自然人'],
        }
        
        for holder_type, kws in keywords.items():
            if any(kw in name for kw in kws):
                return holder_type
        
        return 'other'
    
    def _analyze_signals(self, trades: List[Dict], holders: List[Dict]) -> List[Dict]:
        """分析内部人信号"""
        signals = []
        
        # 分析股东结构
        controlling_holders = [h for h in holders if '控股' in h.get('name', '') or '集团' in h.get('name', '')]
        institutional_holders = [h for h in holders if any(kw in h.get('name', '') for kw in ['基金', '保险', '社保', 'QFII'])]
        
        # 检查控股股东持股比例
        if controlling_holders:
            ctrl_ratio = sum(h.get('ratio', 0) for h in controlling_holders)
            if ctrl_ratio > 50:
                signals.append({
                    'type': 'info',
                    'signal': '控股比例高',
                    'strength': 'medium',
                    'description': f'控股股东持股 {ctrl_ratio:.1f}%，控制权稳定',
                })
        
        # 检查机构持股
        if len(institutional_holders) >= 3:
            signals.append({
                'type': 'bullish',
                'signal': '机构关注',
                'strength': 'medium',
                'description': f'{len(institutional_holders)} 家机构持股',
            })
        
        # 检查股东变动信息
        changes = [t for t in trades if t.get('change_info', '')]
        if changes:
            signals.append({
                'type': 'info',
                'signal': '股东变动',
                'strength': 'weak',
                'description': f'{len(changes)} 条股东变动记录',
            })
        
        return signals
    
    def _assess_risk(self, signals: List[Dict]) -> Dict:
        """评估风险"""
        if not signals:
            return {'level': 'low', 'factors': []}
        
        bearish_signals = [s for s in signals if s['type'] == 'bearish']
        bullish_signals = [s for s in signals if s['type'] == 'bullish']
        
        risk_level = 'low'
        if len(bearish_signals) >= 3:
            risk_level = 'high'
        elif len(bearish_signals) >= 1:
            risk_level = 'medium'
        
        return {
            'level': risk_level,
            'bearish_count': len(bearish_signals),
            'bullish_count': len(bullish_signals),
            'factors': [s['signal'] for s in bearish_signals],
        }
    
    def _generate_recommendation(self, result: Dict) -> str:
        """生成建议"""
        risk = result.get('risk_assessment', {})
        signals = result.get('signals', [])
        
        if risk.get('level') == 'high':
            return 'avoid'  # 高风险，回避
        elif risk.get('level') == 'medium':
            return 'caution'  # 谨慎
        else:
            # 检查是否有强烈看多信号
            strong_bullish = [s for s in signals if s['type'] == 'bullish' and s['strength'] == 'strong']
            if strong_bullish:
                return 'consider'  # 可考虑
            else:
                return 'hold'  # 持有
    
    def _normalize_code(self, ticker: str) -> str:
        """标准化股票代码"""
        return ticker.replace('.SH', '').replace('.SZ', '').replace('.BJ', '').strip()


def track_insider(ticker: str, name: str = "") -> Dict:
    """内部人追踪 - 便捷函数"""
    tracker = InsiderTracker()
    return tracker.track_insider_trades(ticker, name)


def get_market_summary(days: int = 7) -> Dict:
    """市场摘要 - 便捷函数"""
    tracker = InsiderTracker()
    return tracker.get_market_insider_summary(days)


if __name__ == '__main__':
    # 测试
    print("=== 测试内部人追踪 ===\n")
    
    # 测试个股
    print("1. 平安银行内部人追踪")
    result = track_insider('000001', '平安银行')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    
    # 测试市场摘要
    print("\n2. 全市场内部人摘要 (近7天)")
    summary = get_market_summary(7)
    print(json.dumps(summary, ensure_ascii=False, indent=2)[:1000])
