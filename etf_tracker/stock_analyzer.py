#!/usr/bin/env python3
"""
个股深度分析模块
包含：技术指标、资金流向、超大单分析、可视化图表
"""

import os
import sys
import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
import warnings
warnings.filterwarnings('ignore')

# 添加 akshare 路径
# 添加 akshare 路径 (仅在非虚拟环境时)
if not hasattr(sys, 'real_prefix') and sys.base_prefix == sys.prefix:
    sys.path.insert(0, '/home/zhihu/.linuxbrew/Cellar/python@3.10/3.10.9/lib/python3.10/site-packages')

try:
    import akshare as ak
    AKSHARE_AVAILABLE = True
except ImportError:
    AKSHARE_AVAILABLE = False


class StockAnalyzer:
    """个股深度分析器"""
    
    @staticmethod
    def get_stock_data(stock_code: str, days: int = 60) -> pd.DataFrame:
        """获取个股历史数据"""
        import akshare as ak
        
        start_date = (datetime.now() - timedelta(days=days*2)).strftime('%Y%m%d')
        df = ak.stock_zh_a_hist(symbol=stock_code, period="daily", 
                               start_date=start_date, adjust="qfq")
        
        df = df.rename(columns={
            '日期': 'date', '开盘': 'open', '收盘': 'close',
            '最高': 'high', '最低': 'low', '成交量': 'volume', '成交额': 'amount'
        })
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)
        
        if len(df) > days:
            df = df.tail(days).reset_index(drop=True)
        
        return df
    
    @staticmethod
    def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
        """计算技术指标"""
        # RSI
        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['rsi14'] = 100 - (100 / (1 + rs))
        
        # MACD
        ema_fast = df['close'].ewm(span=12, adjust=False).mean()
        ema_slow = df['close'].ewm(span=26, adjust=False).mean()
        df['macd'] = ema_fast - ema_slow
        df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
        df['macd_hist'] = df['macd'] - df['macd_signal']
        
        # 均线
        df['ma5'] = df['close'].rolling(window=5).mean()
        df['ma10'] = df['close'].rolling(window=10).mean()
        df['ma20'] = df['close'].rolling(window=20).mean()
        df['ma60'] = df['close'].rolling(window=60).mean()
        
        # 布林带
        df['bb_middle'] = df['close'].rolling(window=20).mean()
        bb_std = df['close'].rolling(window=20).std()
        df['bb_upper'] = df['bb_middle'] + (bb_std * 2)
        df['bb_lower'] = df['bb_middle'] - (bb_std * 2)
        
        # KDJ
        low_list = df['low'].rolling(window=9, min_periods=9).min()
        high_list = df['high'].rolling(window=9, min_periods=9).max()
        rsv = (df['close'] - low_list) / (high_list - low_list) * 100
        df['k'] = rsv.ewm(com=2, adjust=False).mean()
        df['d'] = df['k'].ewm(com=2, adjust=False).mean()
        df['j'] = 3 * df['k'] - 2 * df['d']
        
        # ATR
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df['atr14'] = tr.rolling(window=14).mean()
        
        return df
    
    @staticmethod
    def get_fund_flow(stock_code: str) -> Dict:
        """获取资金流向数据"""
        try:
            import akshare as ak
            
            # 获取近5日资金流向
            df = ak.stock_individual_fund_flow(symbol=stock_code, market="sh" if stock_code.startswith('6') else "sz")
            
            if len(df) == 0:
                return {"error": "无数据"}
            
            latest = df.iloc[0]
            
            # 解析资金流向数据
            main_inflow = 0
            retail_inflow = 0
            
            # 尝试不同的列名格式
            if '主力净流入-净额' in latest:
                main_inflow = float(latest['主力净流入-净额']) / 10000
            elif '主力净流入' in latest:
                main_inflow = float(latest['主力净流入']) / 10000
                
            if '小单净流入-净额' in latest:
                retail_inflow = float(latest['小单净流入-净额']) / 10000
            elif '小单净流入' in latest:
                retail_inflow = float(latest['小单净流入']) / 10000
            
            # 超大单、大单数据
            super_large = 0
            large = 0
            medium = 0
            small = 0
            
            if '超大单净流入-净额' in latest:
                super_large = float(latest['超大单净流入-净额']) / 10000
            if '大单净流入-净额' in latest:
                large = float(latest['大单净流入-净额']) / 10000
            if '中单净流入-净额' in latest:
                medium = float(latest['中单净流入-净额']) / 10000
            if '小单净流入-净额' in latest:
                small = float(latest['小单净流入-净额']) / 10000
            
            return {
                "date": str(latest.get('日期', '')),
                "main_inflow": round(main_inflow, 2),
                "retail_inflow": round(retail_inflow, 2),
                "super_large": round(super_large, 2),
                "large": round(large, 2),
                "medium": round(medium, 2),
                "small": round(small, 2),
                "signal": "主力流入" if main_inflow > 0 else "主力流出",
                "strength": "强" if abs(main_inflow) > 1000 else "中" if abs(main_inflow) > 500 else "弱"
            }
        except Exception as e:
            return {"error": str(e)}
    
    @staticmethod
    def analyze_stock(stock_code: str, stock_name: str, days: int = 60) -> Dict:
        """全面分析个股"""
        print(f"  📊 正在深度分析 {stock_name} ({stock_code})...")
        
        # 1. 获取数据
        df = StockAnalyzer.get_stock_data(stock_code, days)
        df = StockAnalyzer.calculate_indicators(df)
        
        # 2. 获取资金流向
        fund_flow = StockAnalyzer.get_fund_flow(stock_code)
        
        # 3. 计算回测指标
        latest = df.iloc[-1]
        first = df.iloc[0]
        
        total_return = (latest['close'] - first['close']) / first['close'] * 100
        daily_returns = df['close'].pct_change().dropna()
        volatility = daily_returns.std() * np.sqrt(252) * 100
        
        # 最大回撤
        cumulative = (1 + daily_returns).cumprod()
        peak = cumulative.expanding().max()
        drawdown = (cumulative - peak) / peak
        max_drawdown = drawdown.min() * 100
        
        # 夏普比率
        excess_return = daily_returns.mean() * 252 - 0.02
        sharpe = excess_return / (daily_returns.std() * np.sqrt(252)) if daily_returns.std() != 0 else 0
        
        # 胜率
        win_rate = (daily_returns > 0).sum() / len(daily_returns) * 100
        
        # 技术指标状态
        rsi = latest['rsi14']
        rsi_status = "超买" if rsi > 70 else "超卖" if rsi < 30 else "中性"
        
        macd_bull = latest['macd'] > latest['macd_signal'] and latest['macd_hist'] > 0
        macd_bear = latest['macd'] < latest['macd_signal'] and latest['macd_hist'] < 0
        macd_status = "金叉" if macd_bull else "死叉" if macd_bear else "纠缠"
        
        ma_bull = latest['close'] > latest['ma5'] > latest['ma10'] > latest['ma20']
        ma_bear = latest['close'] < latest['ma5'] < latest['ma10'] < latest['ma20']
        ma_status = "多头排列" if ma_bull else "空头排列" if ma_bear else "震荡"
        
        kdj_j = latest['j']
        kdj_status = "超买" if kdj_j > 80 else "超卖" if kdj_j < 20 else "中性"
        
        # 综合评分
        score = 0
        if total_return > 0: score += 1
        if rsi > 30 and rsi < 70: score += 1
        if macd_bull: score += 1
        if ma_bull: score += 1
        if latest['close'] > latest['bb_middle']: score += 1
        
        return {
            "code": stock_code,
            "name": stock_name,
            "latest_price": round(latest['close'], 2),
            "total_return": round(total_return, 2),
            "volatility": round(volatility, 2),
            "max_drawdown": round(max_drawdown, 2),
            "sharpe_ratio": round(sharpe, 3),
            "win_rate": round(win_rate, 2),
            "rsi": round(rsi, 2),
            "rsi_status": rsi_status,
            "macd_status": macd_status,
            "ma_status": ma_status,
            "kdj_j": round(kdj_j, 2),
            "kdj_status": kdj_status,
            "bb_position": round((latest['close'] - latest['bb_lower']) / (latest['bb_upper'] - latest['bb_lower']), 3),
            "atr": round(latest['atr14'], 3),
            "volume": int(latest['volume']),
            "fund_flow": fund_flow,
            "score": score,
            "max_score": 5,
            "trend": "上涨" if total_return > 0 else "下跌"
        }


class StockVisualizer:
    """个股可视化"""
    
    @staticmethod
    def generate_comparison_chart(stocks_data: List[Dict], output_path: str):
        """生成个股对比图表"""
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            import matplotlib.dates as mdates
            
            fig, axes = plt.subplots(3, 1, figsize=(14, 12))
            
            # 1. 收益率对比柱状图
            ax1 = axes[0]
            names = [s['name'] for s in stocks_data if 'error' not in s]
            returns = [s['total_return'] for s in stocks_data if 'error' not in s]
            colors = ['green' if r > 0 else 'red' for r in returns]
            
            bars = ax1.bar(names, returns, color=colors, alpha=0.7)
            ax1.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
            ax1.set_title('30天收益率对比', fontsize=14, fontweight='bold')
            ax1.set_ylabel('收益率 (%)')
            ax1.tick_params(axis='x', rotation=45)
            
            # 添加数值标签
            for bar, ret in zip(bars, returns):
                height = bar.get_height()
                ax1.text(bar.get_x() + bar.get_width()/2., height,
                        f'{ret:+.1f}%', ha='center', va='bottom', fontsize=9)
            
            # 2. 波动率 vs 夏普比率散点图
            ax2 = axes[1]
            volatilities = [s['volatility'] for s in stocks_data if 'error' not in s]
            sharpes = [s['sharpe_ratio'] for s in stocks_data if 'error' not in s]
            
            scatter = ax2.scatter(volatilities, sharpes, s=100, alpha=0.6, c=returns, cmap='RdYlGn')
            for i, name in enumerate(names):
                ax2.annotate(name, (volatilities[i], sharpes[i]), 
                           xytext=(5, 5), textcoords='offset points', fontsize=8)
            
            ax2.set_xlabel('年化波动率 (%)')
            ax2.set_ylabel('夏普比率')
            ax2.set_title('风险收益特征', fontsize=14, fontweight='bold')
            ax2.axhline(y=0, color='black', linestyle='--', alpha=0.3)
            ax2.grid(True, alpha=0.3)
            plt.colorbar(scatter, ax=ax2, label='收益率 (%)')
            
            # 3. 综合评分雷达图
            ax3 = axes[2]
            
            # 简化为评分柱状图
            scores = [s['score'] for s in stocks_data if 'error' not in s]
            max_scores = [s['max_score'] for s in stocks_data if 'error' not in s]
            score_ratios = [s/ms*100 if ms > 0 else 0 for s, ms in zip(scores, max_scores)]
            
            bars = ax3.barh(names, score_ratios, color='skyblue', alpha=0.7)
            ax3.set_xlim(0, 100)
            ax3.set_title('综合技术评分', fontsize=14, fontweight='bold')
            ax3.set_xlabel('评分 (满分100)')
            
            # 添加数值标签
            for bar, score in zip(bars, score_ratios):
                width = bar.get_width()
                ax3.text(width, bar.get_y() + bar.get_height()/2.,
                        f'{score:.0f}', ha='left', va='center', fontsize=9)
            
            plt.tight_layout()
            plt.savefig(output_path, dpi=150, bbox_inches='tight')
            plt.close()
            
            print(f"  ✅ 对比图表已保存: {output_path}")
            return True
        except Exception as e:
            print(f"  ⚠️ 图表生成失败: {e}")
            return False


if __name__ == "__main__":
    # 测试
    analyzer = StockAnalyzer()
    result = analyzer.analyze_stock("300054", "鼎龙股份")
    print(json.dumps(result, ensure_ascii=False, indent=2))
