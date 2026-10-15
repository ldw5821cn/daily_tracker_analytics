#!/usr/bin/env python3
"""K线形态识别模块

基于 TALib 库识别经典 K线形态，为技术分析提供形态学支持。

支持形态:
- 反转形态: 锤子线、上吊线、流星线、吞没形态、乌云盖顶、刺透形态
- 持续形态: 三只乌鸦、三兵挺进、上升/下降三法
- 十字星系列: 普通十字星、长腿十字星、墓碑十字星、蜻蜓十字星
"""
from __future__ import annotations

import sys
import os
from typing import Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

# 添加项目路径
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'multi_agent'))

# TALib 导入
try:
    import talib
    TALIB_AVAILABLE = True
except ImportError:
    TALIB_AVAILABLE = False
    print("警告: TALib 未安装，K线形态识别功能不可用")


class KLinePatternRecognizer:
    """K线形态识别器"""
    
    # 形态定义: (名称, TALib函数, 信号类型, 描述)
    PATTERNS = {
        # 反转形态 - 看涨
        'hammer': {'name': '锤子线', 'func': 'CDLHAMMER', 'signal': 'bullish', 'desc': '底部反转信号，下影线较长'},
        'inverted_hammer': {'name': '倒锤子线', 'func': 'CDLINVERTEDHAMMER', 'signal': 'bullish', 'desc': '底部反转信号，上影线较长'},
        'morning_star': {'name': '早晨之星', 'func': 'CDLMORNINGSTAR', 'signal': 'bullish', 'desc': '底部反转形态，三根K线组合'},
        'bullish_engulfing': {'name': '看涨吞没', 'func': 'CDLENGULFING', 'signal': 'bullish', 'desc': '阳线完全吞没前一根阴线'},
        'piercing': {'name': '刺透形态', 'func': 'CDLPIERCING', 'signal': 'bullish', 'desc': '阳线刺入阴线实体50%以上'},
        'three_white_soldiers': {'name': '三兵挺进', 'func': 'CDL3WHITESOLDIERS', 'signal': 'bullish', 'desc': '三根阳线，持续上涨'},
        
        # 反转形态 - 看跌
        'hanging_man': {'name': '上吊线', 'func': 'CDLHANGINGMAN', 'signal': 'bearish', 'desc': '顶部反转信号，下影线较长'},
        'shooting_star': {'name': '流星线', 'func': 'CDLSHOOTINGSTAR', 'signal': 'bearish', 'desc': '顶部反转信号，上影线较长'},
        'evening_star': {'name': '黄昏之星', 'func': 'CDLEVENINGSTAR', 'signal': 'bearish', 'desc': '顶部反转形态，三根K线组合'},
        'bearish_engulfing': {'name': '看跌吞没', 'func': 'CDLENGULFING', 'signal': 'bearish', 'desc': '阴线完全吞没前一根阳线'},
        'dark_cloud_cover': {'name': '乌云盖顶', 'func': 'CDLDARKCLOUDCOVER', 'signal': 'bearish', 'desc': '阴线刺入阳线实体50%以上'},
        'three_black_crows': {'name': '三只乌鸦', 'func': 'CDL3BLACKCROWS', 'signal': 'bearish', 'desc': '三根阴线，持续下跌'},
        'two_crows': {'name': '两只乌鸦', 'func': 'CDL2CROWS', 'signal': 'bearish', 'desc': '顶部反转，两根阴线'},
        
        # 十字星系列
        'doji': {'name': '十字星', 'func': 'CDLDOJI', 'signal': 'neutral', 'desc': '开盘价等于收盘价，市场犹豫'},
        'long_legged_doji': {'name': '长腿十字星', 'func': 'CDLLONGLEGGEDDOJI', 'signal': 'neutral', 'desc': '上下影线都很长，市场极度犹豫'},
        'gravestone_doji': {'name': '墓碑十字星', 'func': 'CDLGRAVESTONEDOJI', 'signal': 'bearish', 'desc': '顶部反转，开盘价=收盘价=最低价'},
        'dragonfly_doji': {'name': '蜻蜓十字星', 'func': 'CDLDRAGONFLYDOJI', 'signal': 'bullish', 'desc': '底部反转，开盘价=收盘价=最高价'},
        
        # 持续形态
        'rising_three': {'name': '上升三法', 'func': 'CDLRISING3METHODS', 'signal': 'bullish', 'desc': '上涨趋势中的整理形态'},
        'falling_three': {'name': '下降三法', 'func': 'CDLFALLING3METHODS', 'signal': 'bearish', 'desc': '下跌趋势中的整理'},
        'marubozu_white': {'name': '白色光头光脚', 'func': 'CDLMARUBOZU', 'signal': 'bullish', 'desc': '强势上涨，无影线'},
        'marubozu_black': {'name': '黑色光头光脚', 'func': 'CDLMARUBOZU', 'signal': 'bearish', 'desc': '强势下跌，无影线'},
    }
    
    def __init__(self):
        if not TALIB_AVAILABLE:
            raise ImportError("TALib 未安装，请运行: pip install TA-Lib")
    
    def recognize_patterns(self, df: pd.DataFrame) -> Dict:
        """识别 DataFrame 中的所有 K线形态
        
        Args:
            df: 包含 open, high, low, close 列的 DataFrame
        
        Returns:
            {
                'patterns': [形态列表],
                'latest_patterns': [最近出现的形态],
                'bullish_count': 看涨形态数量,
                'bearish_count': 看跌形态数量,
                'neutral_count': 中性形态数量,
                'signal': 'bullish'/'bearish'/'neutral',
                'strength': 0-100 信号强度
            }
        """
        required_cols = ['open', 'high', 'low', 'close']
        if not all(col in df.columns for col in required_cols):
            raise ValueError(f"DataFrame 缺少必要列: {required_cols}")
        
        # 转换数据类型
        open_prices = df['open'].astype(float).values
        high_prices = df['high'].astype(float).values
        low_prices = df['low'].astype(float).values
        close_prices = df['close'].astype(float).values
        
        results = {
            'patterns': [],
            'latest_patterns': [],
            'bullish_count': 0,
            'bearish_count': 0,
            'neutral_count': 0,
            'signal': 'neutral',
            'strength': 0
        }
        
        # 识别所有形态
        for pattern_id, pattern_info in self.PATTERNS.items():
            func_name = pattern_info['func']
            if hasattr(talib, func_name):
                func = getattr(talib, func_name)
                try:
                    # 调用 TALib 函数
                    pattern_values = func(open_prices, high_prices, low_prices, close_prices)
                    
                    # 找出非零值（出现形态的位置）
                    pattern_indices = np.where(pattern_values != 0)[0]
                    
                    if len(pattern_indices) > 0:
                        # 记录所有出现的形态
                        for idx in pattern_indices:
                            results['patterns'].append({
                                'pattern_id': pattern_id,
                                'name': pattern_info['name'],
                                'signal': pattern_info['signal'],
                                'desc': pattern_info['desc'],
                                'date': df.index[idx] if hasattr(df.index[idx], 'strftime') else str(idx),
                                'value': int(pattern_values[idx])
                            })
                        
                        # 检查最近的形态（最后3根K线）
                        recent_indices = [idx for idx in pattern_indices if idx >= len(df) - 3]
                        if recent_indices:
                            for idx in recent_indices:
                                results['latest_patterns'].append({
                                    'pattern_id': pattern_id,
                                    'name': pattern_info['name'],
                                    'signal': pattern_info['signal'],
                                    'desc': pattern_info['desc'],
                                    'date': df.index[idx] if hasattr(df.index[idx], 'strftime') else str(idx),
                                    'value': int(pattern_values[idx])
                                })
                        
                        # 统计信号
                        if pattern_info['signal'] == 'bullish':
                            results['bullish_count'] += len(pattern_indices)
                        elif pattern_info['signal'] == 'bearish':
                            results['bearish_count'] += len(pattern_indices)
                        else:
                            results['neutral_count'] += len(pattern_indices)
                            
                except Exception as e:
                    print(f"识别形态 {pattern_info['name']} 失败: {e}")
        
        # 生成综合信号
        results['signal'], results['strength'] = self._calculate_signal(results)
        
        return results
    
    def recognize_single_pattern(self, df: pd.DataFrame, pattern_id: str) -> List[Dict]:
        """识别单个形态
        
        Args:
            df: OHLC DataFrame
            pattern_id: 形态ID (如 'hammer')
        
        Returns:
            形态出现的位置列表
        """
        if pattern_id not in self.PATTERNS:
            raise ValueError(f"未知形态: {pattern_id}")
        
        pattern_info = self.PATTERNS[pattern_id]
        func_name = pattern_info['func']
        
        if not hasattr(talib, func_name):
            return []
        
        func = getattr(talib, func_name)
        
        open_prices = df['open'].astype(float).values
        high_prices = df['high'].astype(float).values
        low_prices = df['low'].astype(float).values
        close_prices = df['close'].astype(float).values
        
        try:
            pattern_values = func(open_prices, high_prices, low_prices, close_prices)
            pattern_indices = np.where(pattern_values != 0)[0]
            
            results = []
            for idx in pattern_indices:
                results.append({
                    'date': df.index[idx] if hasattr(df.index[idx], 'strftime') else str(idx),
                    'value': int(pattern_values[idx]),
                    'name': pattern_info['name'],
                    'signal': pattern_info['signal']
                })
            return results
        except Exception as e:
            print(f"识别形态 {pattern_info['name']} 失败: {e}")
            return []
    
    def _calculate_signal(self, results: Dict) -> Tuple[str, int]:
        """计算综合信号和强度"""
        bullish = results['bullish_count']
        bearish = results['bearish_count']
        total = bullish + bearish
        
        if total == 0:
            return 'neutral', 0
        
        # 计算多空比例
        bull_ratio = bullish / total
        
        # 确定信号
        if bull_ratio > 0.6:
            signal = 'bullish'
            strength = int(bull_ratio * 100)
        elif bull_ratio < 0.4:
            signal = 'bearish'
            strength = int((1 - bull_ratio) * 100)
        else:
            signal = 'neutral'
            strength = 50
        
        return signal, min(100, max(0, strength))
    
    def get_pattern_summary(self, results: Dict) -> str:
        """生成形态识别摘要"""
        lines = []
        
        if results['latest_patterns']:
            lines.append("🎯 最近出现的K线形态:")
            for p in results['latest_patterns']:
                emoji = "🟢" if p['signal'] == 'bullish' else "🔴" if p['signal'] == 'bearish' else "⚪"
                lines.append(f"  {emoji} {p['name']}: {p['desc']}")
        else:
            lines.append("最近3根K线无明确形态")
        
        lines.append("")
        lines.append(f"📊 统计: 看涨 {results['bullish_count']} | 看跌 {results['bearish_count']} | 中性 {results['neutral_count']}")
        
        signal_emoji = "🟢" if results['signal'] == 'bullish' else "🔴" if results['signal'] == 'bearish' else "⚪"
        lines.append(f"{signal_emoji} 综合信号: {results['signal']} (强度: {results['strength']}/100)")
        
        return "\n".join(lines)


# ========== 便捷函数 ==========

def recognize_kline_patterns(df: pd.DataFrame) -> Dict:
    """识别K线形态 - 便捷函数"""
    recognizer = KLinePatternRecognizer()
    return recognizer.recognize_patterns(df)


def get_pattern_summary(df: pd.DataFrame) -> str:
    """获取形态摘要 - 便捷函数"""
    recognizer = KLinePatternRecognizer()
    results = recognizer.recognize_patterns(df)
    return recognizer.get_pattern_summary(results)


# ========== 测试 ==========
if __name__ == '__main__':
    # 生成测试数据
    np.random.seed(42)
    dates = pd.date_range('2026-01-01', periods=100, freq='D')
    
    # 模拟价格数据
    base_price = 100
    prices = []
    for i in range(100):
        change = np.random.normal(0, 2)
        base_price += change
        prices.append(base_price)
    
    df = pd.DataFrame({
        'open': [p + np.random.normal(0, 0.5) for p in prices],
        'high': [p + abs(np.random.normal(0, 1)) for p in prices],
        'low': [p - abs(np.random.normal(0, 1)) for p in prices],
        'close': prices
    }, index=dates)
    
    # 测试形态识别
    print("=== 测试 K线形态识别 ===\n")
    recognizer = KLinePatternRecognizer()
    results = recognizer.recognize_patterns(df)
    
    print(recognizer.get_pattern_summary(results))
    print(f"\n共识别 {len(results['patterns'])} 个形态")
