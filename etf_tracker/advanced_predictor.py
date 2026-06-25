#!/usr/bin/env python3
"""
高级预测与预警模块 - 完整增强版
功能:
1. 趋势预测（1日/3日/5日）+ 准确率回测
2. 自动预警（评分>80、RSI>70、跌破均线、MACD死叉、背离检测）
3. 策略优化（多因子加权、动态仓位）
4. K线图生成（带均线、MACD、RSI、KDJ副图）
5. 资金流向趋势图（主力/散户/大单分布）
6. 技术指标背离检测（顶背离/底背离）
7. 多因子选股模型
8. 预测准确率验证
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

# 添加 akshare 路径 (仅在非虚拟环境时)
if not hasattr(sys, 'real_prefix') and sys.base_prefix == sys.prefix:
    sys.path.insert(0, '/home/zhihu/.linuxbrew/Cellar/python@3.10/3.10.9/lib/python3.10/site-packages')

try:
    import akshare as ak
    AKSHARE_AVAILABLE = True
except ImportError:
    AKSHARE_AVAILABLE = False


class TrendPredictor:
    """趋势预测器 - 增强版"""
    
    @staticmethod
    def add_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
        """添加技术指标（如果缺失）"""
        df = df.copy()
        
        # 计算 RSI14
        if 'rsi14' not in df.columns:
            delta = df['close'].diff()
            gain = delta.where(delta > 0, 0).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            df['rsi14'] = 100 - (100 / (1 + rs))
        
        # 计算 MA
        for ma in [5, 10, 20, 60]:
            if f'ma{ma}' not in df.columns:
                df[f'ma{ma}'] = df['close'].rolling(window=ma).mean()
        
        # 计算 MACD
        if 'macd' not in df.columns or 'macd_hist' not in df.columns:
            ema_fast = df['close'].ewm(span=12, adjust=False).mean()
            ema_slow = df['close'].ewm(span=26, adjust=False).mean()
            df['macd'] = ema_fast - ema_slow
            df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
            df['macd_hist'] = df['macd'] - df['macd_signal']
        
        return df
    
    @staticmethod
    def predict_trend(df: pd.DataFrame, days: int = 5) -> Dict:
        """预测未来趋势"""
        df = TrendPredictor.add_technical_indicators(df)
        
        if len(df) < 20:
            return {"error": "数据不足"}
        
        latest = df.iloc[-1]
        
        # 1. 线性回归预测
        x = np.arange(len(df))
        y = df['close'].values.astype(float)
        coeffs = np.polyfit(x[-20:].astype(float), y[-20:], 1)
        slope = coeffs[0]
        
        # 预测未来价格
        future_prices = []
        for i in range(1, days + 1):
            pred_price = coeffs[0] * (len(df) + i) + coeffs[1]
            future_prices.append(pred_price)
        
        # 2. 基于技术指标的预测
        # RSI 趋势
        rsi_trend = "up" if df['rsi14'].iloc[-1] > df['rsi14'].iloc[-5] else "down"
        
        # MACD 趋势
        macd_trend = "up" if df['macd_hist'].iloc[-1] > df['macd_hist'].iloc[-3] else "down"
        
        # 均线趋势
        ma_trend = "up" if df['ma5'].iloc[-1] > df['ma10'].iloc[-1] > df['ma20'].iloc[-1] else "down"
        
        # 3. 综合预测
        predictions = {}
        for day in [1, 3, 5]:
            if day <= len(future_prices):
                pred_price = future_prices[day - 1]
                current_price = latest['close']
                expected_return = (pred_price - current_price) / current_price * 100
                
                # 综合信号判断
                signals = []
                if slope > 0: signals.append("up")
                if rsi_trend == "up": signals.append("up")
                if macd_trend == "up": signals.append("up")
                if ma_trend == "up": signals.append("up")
                
                up_count = signals.count("up")
                total = len(signals)
                
                if up_count >= total * 0.75:
                    trend = "强烈看涨"
                    confidence = 0.8
                elif up_count >= total * 0.5:
                    trend = "看涨"
                    confidence = 0.6
                elif up_count >= total * 0.25:
                    trend = "震荡"
                    confidence = 0.4
                else:
                    trend = "看跌"
                    confidence = 0.3
                
                predictions[f"day_{day}"] = {
                    "predicted_price": round(pred_price, 2),
                    "expected_return": round(expected_return, 2),
                    "trend": trend,
                    "confidence": confidence,
                    "signals": {
                        "linear_slope": slope,
                        "rsi_trend": rsi_trend,
                        "macd_trend": macd_trend,
                        "ma_trend": ma_trend
                    }
                }
        
        return predictions
    
    @staticmethod
    def backtest_prediction_accuracy(df: pd.DataFrame, test_days: int = 30) -> Dict:
        """回测预测准确率 - 验证历史预测与实际走势"""
        if len(df) < test_days + 20:
            return {"error": "数据不足，无法回测"}
        
        correct_predictions = {"day_1": 0, "day_3": 0, "day_5": 0}
        total_predictions = {"day_1": 0, "day_3": 0, "day_5": 0}
        
        # 滑动窗口回测
        for i in range(len(df) - test_days - 5, len(df) - 5):
            # 使用过去数据预测
            historical = df.iloc[:i]
            if len(historical) < 20:
                continue
            
            # 线性回归预测
            x = np.arange(len(historical))
            y = historical['close'].values.astype(float)
            coeffs = np.polyfit(x[-20:].astype(float), y[-20:], 1)
            
            # 预测未来1/3/5天
            for day in [1, 3, 5]:
                pred_price = coeffs[0] * (len(historical) + day) + coeffs[1]
                actual_price = df.iloc[i + day]['close']
                actual_return = (actual_price - historical.iloc[-1]['close']) / historical.iloc[-1]['close']
                pred_return = (pred_price - historical.iloc[-1]['close']) / historical.iloc[-1]['close']
                
                # 判断方向是否正确
                if (pred_return > 0 and actual_return > 0) or (pred_return < 0 and actual_return < 0):
                    correct_predictions[f"day_{day}"] += 1
                total_predictions[f"day_{day}"] += 1
        
        # 计算准确率
        accuracy = {}
        for day in [1, 3, 5]:
            key = f"day_{day}"
            if total_predictions[key] > 0:
                accuracy[key] = {
                    "correct": correct_predictions[key],
                    "total": total_predictions[key],
                    "accuracy": round(correct_predictions[key] / total_predictions[key] * 100, 2)
                }
        
        return accuracy
    
    @staticmethod
    def optimize_strategy(df: pd.DataFrame, predictions: Dict, current_signals: Dict) -> Dict:
        """根据预测优化策略 - 增强版"""
        latest = df.iloc[-1]
        
        # 计算各指标权重
        weights = {
            "trend": 0.3,
            "rsi": 0.2,
            "macd": 0.2,
            "volume": 0.15,
            "prediction": 0.15
        }
        
        # 趋势得分
        trend_score = 0
        if latest['close'] > latest['ma5'] > latest['ma10']:
            trend_score = 1
        elif latest['close'] < latest['ma5'] < latest['ma10']:
            trend_score = -1
        
        # RSI得分
        rsi = latest['rsi14']
        rsi_score = 0
        if 30 < rsi < 50:
            rsi_score = 0.5
        elif 50 <= rsi < 70:
            rsi_score = 0.8
        elif rsi >= 70:
            rsi_score = -0.3
        elif rsi <= 30:
            rsi_score = 0.3
        
        # MACD得分
        macd_score = 0
        if latest['macd'] > latest['macd_signal'] and latest['macd_hist'] > 0:
            macd_score = 1
        elif latest['macd'] < latest['macd_signal'] and latest['macd_hist'] < 0:
            macd_score = -1
        
        # 成交量得分
        vol_ratio = latest['volume'] / latest['vol_ma20'] if latest['vol_ma20'] > 0 else 1
        vol_score = 0
        if vol_ratio > 1.5:
            vol_score = 0.5
        elif vol_ratio < 0.5:
            vol_score = -0.5
        
        # 预测得分
        pred_score = 0
        if 'day_1' in predictions:
            pred = predictions['day_1']
            if pred['trend'] in ['强烈看涨', '看涨']:
                pred_score = pred['confidence']
            elif pred['trend'] == '看跌':
                pred_score = -pred['confidence']
        
        # 综合评分
        total_score = (
            trend_score * weights['trend'] +
            rsi_score * weights['rsi'] +
            macd_score * weights['macd'] +
            vol_score * weights['volume'] +
            pred_score * weights['prediction']
        )
        
        # 生成策略建议
        if total_score > 0.6:
            action = "强烈建议买入"
            position = "80-100%"
        elif total_score > 0.3:
            action = "建议买入"
            position = "50-80%"
        elif total_score > -0.3:
            action = "持仓观望"
            position = "30-50%"
        elif total_score > -0.6:
            action = "建议减仓"
            position = "10-30%"
        else:
            action = "强烈建议卖出"
            position = "0-10%"
        
        return {
            "total_score": round(total_score, 3),
            "action": action,
            "position": position,
            "weights": weights,
            "scores": {
                "trend": trend_score,
                "rsi": rsi_score,
                "macd": macd_score,
                "volume": vol_score,
                "prediction": pred_score
            }
        }


class DivergenceDetector:
    """技术指标背离检测器"""
    
    @staticmethod
    def detect_macd_divergence(df: pd.DataFrame) -> Dict:
        """检测MACD背离"""
        # 找局部高点和低点
        highs = []
        lows = []
        
        for i in range(2, len(df) - 2):
            # 局部高点
            if df['high'].iloc[i] > df['high'].iloc[i-1] and df['high'].iloc[i] > df['high'].iloc[i+1]:
                highs.append((i, df['high'].iloc[i], df['macd'].iloc[i]))
            # 局部低点
            if df['low'].iloc[i] < df['low'].iloc[i-1] and df['low'].iloc[i] < df['low'].iloc[i+1]:
                lows.append((i, df['low'].iloc[i], df['macd'].iloc[i]))
        
        # 检测顶背离（价格新高，MACD未新高）
        top_divergence = []
        for i in range(len(highs) - 1):
            if highs[i+1][1] > highs[i][1] and highs[i+1][2] < highs[i][2]:
                top_divergence.append({
                    "type": "顶背离",
                    "price_peak": round(highs[i+1][1], 2),
                    "macd_peak": round(highs[i+1][2], 4),
                    "previous_price_peak": round(highs[i][1], 2),
                    "previous_macd_peak": round(highs[i][2], 4),
                    "date": str(df.iloc[highs[i+1][0]]['date']) if 'date' in df.columns else f"index_{highs[i+1][0]}"
                })
        
        # 检测底背离（价格新低，MACD未新低）
        bottom_divergence = []
        for i in range(len(lows) - 1):
            if lows[i+1][1] < lows[i][1] and lows[i+1][2] > lows[i][2]:
                bottom_divergence.append({
                    "type": "底背离",
                    "price_trough": round(lows[i+1][1], 2),
                    "macd_trough": round(lows[i+1][2], 4),
                    "previous_price_trough": round(lows[i][1], 2),
                    "previous_macd_trough": round(lows[i][2], 4),
                    "date": str(df.iloc[lows[i+1][0]]['date']) if 'date' in df.columns else f"index_{lows[i+1][0]}"
                })
        
        return {
            "top_divergence": top_divergence,
            "bottom_divergence": bottom_divergence,
            "has_top_divergence": len(top_divergence) > 0,
            "has_bottom_divergence": len(bottom_divergence) > 0
        }
    
    @staticmethod
    def detect_rsi_divergence(df: pd.DataFrame) -> Dict:
        """检测RSI背离"""
        highs = []
        lows = []
        
        for i in range(2, len(df) - 2):
            if df['high'].iloc[i] > df['high'].iloc[i-1] and df['high'].iloc[i] > df['high'].iloc[i+1]:
                highs.append((i, df['high'].iloc[i], df['rsi14'].iloc[i]))
            if df['low'].iloc[i] < df['low'].iloc[i-1] and df['low'].iloc[i] < df['low'].iloc[i+1]:
                lows.append((i, df['low'].iloc[i], df['rsi14'].iloc[i]))
        
        top_divergence = []
        for i in range(len(highs) - 1):
            if highs[i+1][1] > highs[i][1] and highs[i+1][2] < highs[i][2]:
                top_divergence.append({
                    "type": "RSI顶背离",
                    "price_peak": round(highs[i+1][1], 2),
                    "rsi_peak": round(highs[i+1][2], 2),
                    "date": str(df.iloc[highs[i+1][0]]['date']) if 'date' in df.columns else f"index_{highs[i+1][0]}"
                })
        
        bottom_divergence = []
        for i in range(len(lows) - 1):
            if lows[i+1][1] < lows[i][1] and lows[i+1][2] > lows[i][2]:
                bottom_divergence.append({
                    "type": "RSI底背离",
                    "price_trough": round(lows[i+1][1], 2),
                    "rsi_trough": round(lows[i+1][2], 2),
                    "date": str(df.iloc[lows[i+1][0]]['date']) if 'date' in df.columns else f"index_{lows[i+1][0]}"
                })
        
        return {
            "top_divergence": top_divergence,
            "bottom_divergence": bottom_divergence,
            "has_top_divergence": len(top_divergence) > 0,
            "has_bottom_divergence": len(bottom_divergence) > 0
        }


class AlertSystem:
    """预警系统 - 增强版"""
    
    @staticmethod
    def check_alerts(stock_data: Dict) -> List[Dict]:
        """检查预警条件"""
        alerts = []
        
        # 1. 评分预警
        score = stock_data.get('score', 0)
        max_score = stock_data.get('max_score', 5)
        score_pct = score / max_score * 100 if max_score > 0 else 0
        
        if score_pct >= 80:
            alerts.append({
                "level": "info",
                "type": "高评分",
                "message": f"{stock_data.get('name', '')} 综合评分 {score_pct:.0f}/100，技术面强势"
            })
        
        # 2. RSI 预警
        rsi = stock_data.get('rsi', 50)
        if rsi > 70:
            alerts.append({
                "level": "warning",
                "type": "RSI超买",
                "message": f"{stock_data.get('name', '')} RSI {rsi:.1f} > 70，注意回调风险"
            })
        elif rsi < 30:
            alerts.append({
                "level": "info",
                "type": "RSI超卖",
                "message": f"{stock_data.get('name', '')} RSI {rsi:.1f} < 30，潜在反弹机会"
            })
        
        # 3. 涨幅预警
        total_return = stock_data.get('total_return', 0)
        if total_return > 50:
            alerts.append({
                "level": "warning",
                "type": "短期暴涨",
                "message": f"{stock_data.get('name', '')} 30天涨幅 {total_return:+.1f}%，注意获利了结"
            })
        elif total_return < -20:
            alerts.append({
                "level": "info",
                "type": "短期暴跌",
                "message": f"{stock_data.get('name', '')} 30天跌幅 {total_return:.1f}%，关注抄底机会"
            })
        
        # 4. 资金流向预警
        fund_flow = stock_data.get('fund_flow', {})
        if isinstance(fund_flow, dict):
            main_inflow = fund_flow.get('main_inflow', 0)
            if main_inflow > 5000:  # 5000万
                alerts.append({
                    "level": "info",
                    "type": "主力大幅流入",
                    "message": f"{stock_data.get('name', '')} 主力净流入 {main_inflow:.0f}万元"
                })
            elif main_inflow < -5000:
                alerts.append({
                    "level": "warning",
                    "type": "主力大幅流出",
                    "message": f"{stock_data.get('name', '')} 主力净流出 {abs(main_inflow):.0f}万元"
                })
        
        # 5. 均线跌破预警
        latest_price = stock_data.get('latest_price', 0)
        ma5 = stock_data.get('ma5', 0)
        ma10 = stock_data.get('ma10', 0)
        ma20 = stock_data.get('ma20', 0)
        
        if latest_price > 0 and ma5 > 0 and latest_price < ma5:
            alerts.append({
                "level": "warning",
                "type": "跌破MA5",
                "message": f"{stock_data.get('name', '')} 价格 {latest_price} 跌破MA5 {ma5:.2f}"
            })
        
        if latest_price > 0 and ma10 > 0 and latest_price < ma10:
            alerts.append({
                "level": "warning",
                "type": "跌破MA10",
                "message": f"{stock_data.get('name', '')} 价格 {latest_price} 跌破MA10 {ma10:.2f}"
            })
        
        # 6. MACD死叉预警
        macd = stock_data.get('macd', 0)
        macd_signal = stock_data.get('macd_signal', 0)
        if macd < macd_signal and macd > 0:
            alerts.append({
                "level": "warning",
                "type": "MACD死叉",
                "message": f"{stock_data.get('name', '')} MACD死叉，动能减弱"
            })
        elif macd > macd_signal and macd < 0:
            alerts.append({
                "level": "info",
                "type": "MACD金叉",
                "message": f"{stock_data.get('name', '')} MACD金叉，动能增强"
            })
        
        # 7. 布林带突破预警
        bb_position = stock_data.get('bb_position', 0.5)
        if bb_position > 0.95:
            alerts.append({
                "level": "warning",
                "type": "布林带上轨突破",
                "message": f"{stock_data.get('name', '')} 价格接近布林带上轨，注意回调"
            })
        elif bb_position < 0.05:
            alerts.append({
                "level": "info",
                "type": "布林带下轨突破",
                "message": f"{stock_data.get('name', '')} 价格接近布林带下轨，潜在反弹"
            })
        
        return alerts


class MultiFactorStockSelector:
    """多因子选股模型"""
    
    @staticmethod
    def calculate_score(stock_data: Dict) -> Dict:
        """计算多因子综合评分"""
        score = 0
        max_score = 0
        factors = {}
        
        # 1. 动量因子 (30天涨幅) - 权重20%
        total_return = stock_data.get('total_return', 0)
        if total_return > 30:
            momentum_score = 1.0
        elif total_return > 10:
            momentum_score = 0.5
        elif total_return > -10:
            momentum_score = 0.0
        else:
            momentum_score = -0.5
        score += momentum_score * 0.2
        max_score += 0.2
        factors['momentum'] = momentum_score
        
        # 2. 技术因子 (RSI) - 权重20%
        rsi = stock_data.get('rsi', 50)
        if 40 < rsi < 60:
            rsi_score = 0.8
        elif 30 < rsi < 70:
            rsi_score = 0.5
        else:
            rsi_score = 0.0
        score += rsi_score * 0.2
        max_score += 0.2
        factors['rsi'] = rsi_score
        
        # 3. 资金因子 - 权重20%
        fund_flow = stock_data.get('fund_flow', {})
        if isinstance(fund_flow, dict):
            main_inflow = fund_flow.get('main_inflow', 0)
            if main_inflow > 1000:
                fund_score = 1.0
            elif main_inflow > 0:
                fund_score = 0.5
            elif main_inflow > -1000:
                fund_score = 0.0
            else:
                fund_score = -0.5
        else:
            fund_score = 0.0
        score += fund_score * 0.2
        max_score += 0.2
        factors['fund_flow'] = fund_score
        
        # 4. 趋势因子 (均线排列) - 权重20%
        latest_price = stock_data.get('latest_price', 0)
        ma5 = stock_data.get('ma5', 0)
        ma10 = stock_data.get('ma10', 0)
        ma20 = stock_data.get('ma20', 0)
        
        if latest_price > ma5 > ma10 > ma20:
            trend_score = 1.0
        elif latest_price > ma5 > ma10:
            trend_score = 0.5
        elif latest_price < ma5 < ma10 < ma20:
            trend_score = -1.0
        else:
            trend_score = 0.0
        score += trend_score * 0.2
        max_score += 0.2
        factors['trend'] = trend_score
        
        # 5. 波动因子 (ATR) - 权重20%
        atr = stock_data.get('atr', 0)
        price = stock_data.get('latest_price', 1)
        atr_ratio = atr / price if price > 0 else 0
        if 0.02 < atr_ratio < 0.05:
            vol_score = 0.8
        elif atr_ratio < 0.08:
            vol_score = 0.5
        else:
            vol_score = 0.0
        score += vol_score * 0.2
        max_score += 0.2
        factors['volatility'] = vol_score
        
        return {
            "total_score": round(score, 3),
            "max_score": round(max_score, 3),
            "score_pct": round(score / max_score * 100, 1) if max_score > 0 else 0,
            "factors": factors,
            "rating": "强烈推荐" if score > 0.6 else "推荐" if score > 0.3 else "中性" if score > -0.3 else "回避"
        }
    
    @staticmethod
    def rank_stocks(stock_list: List[Dict]) -> List[Dict]:
        """对股票列表进行多因子排序"""
        scored_stocks = []
        for stock in stock_list:
            if 'error' not in stock:
                score_result = MultiFactorStockSelector.calculate_score(stock)
                stock['multi_factor_score'] = score_result
                scored_stocks.append(stock)
        
        # 按综合评分排序
        scored_stocks.sort(key=lambda x: x['multi_factor_score']['total_score'], reverse=True)
        return scored_stocks


class DynamicPositionManager:
    """动态仓位管理器"""
    
    @staticmethod
    def calculate_position(df: pd.DataFrame, signals: Dict, predictions: Dict) -> Dict:
        """根据市场状态动态调整仓位"""
        latest = df.iloc[-1]
        
        # 基础仓位
        base_position = 0.5
        
        # 1. 波动率调整
        daily_returns = df['close'].pct_change().dropna()
        volatility = daily_returns.std() * np.sqrt(252)
        
        # 高波动降低仓位
        vol_adjustment = 0
        if volatility > 0.4:
            vol_adjustment = -0.2
        elif volatility > 0.3:
            vol_adjustment = -0.1
        elif volatility < 0.15:
            vol_adjustment = 0.1
        
        # 2. 趋势调整
        trend_adjustment = 0
        if latest['close'] > latest['ma5'] > latest['ma10'] > latest['ma20']:
            trend_adjustment = 0.15
        elif latest['close'] < latest['ma5'] < latest['ma10'] < latest['ma20']:
            trend_adjustment = -0.15
        
        # 3. 预测调整
        pred_adjustment = 0
        if 'day_1' in predictions:
            pred = predictions['day_1']
            if pred['trend'] == '强烈看涨':
                pred_adjustment = 0.15
            elif pred['trend'] == '看涨':
                pred_adjustment = 0.1
            elif pred['trend'] == '看跌':
                pred_adjustment = -0.1
            elif pred['trend'] == '强烈看跌':
                pred_adjustment = -0.15
        
        # 4. 信号评分调整
        signal_adjustment = signals.get('score', 0) / 100 * 0.2
        
        # 综合计算
        final_position = base_position + vol_adjustment + trend_adjustment + pred_adjustment + signal_adjustment
        final_position = max(0, min(1, final_position))  # 限制在0-1之间
        
        return {
            "base_position": f"{base_position*100:.0f}%",
            "volatility_adjustment": f"{vol_adjustment*100:+.0f}%",
            "trend_adjustment": f"{trend_adjustment*100:+.0f}%",
            "prediction_adjustment": f"{pred_adjustment*100:+.0f}%",
            "signal_adjustment": f"{signal_adjustment*100:+.0f}%",
            "final_position": f"{final_position*100:.0f}%",
            "volatility": round(volatility * 100, 2),
            "risk_level": "高" if volatility > 0.35 else "中" if volatility > 0.25 else "低"
        }


class AdvancedVisualizer:
    """高级可视化"""
    
    @staticmethod
    def generate_kline_chart(stock_code: str, stock_name: str, df: pd.DataFrame, output_path: str):
        """生成K线图（带均线、MACD、RSI、KDJ）"""
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            import matplotlib.dates as mdates
            from matplotlib.patches import Rectangle
            
            fig, axes = plt.subplots(5, 1, figsize=(16, 16), gridspec_kw={'height_ratios': [3, 1, 1, 1, 1]})
            
            # 1. K线图 + 均线
            ax1 = axes[0]
            
            # 绘制K线
            for idx, row in df.iterrows():
                color = 'red' if row['close'] >= row['open'] else 'green'
                
                # 实体
                height = abs(row['close'] - row['open'])
                bottom = min(row['close'], row['open'])
                rect = Rectangle((idx - 0.4, bottom), 0.8, height, 
                                facecolor=color, edgecolor=color, alpha=0.8)
                ax1.add_patch(rect)
                
                # 影线
                ax1.plot([idx, idx], [row['low'], row['high']], color=color, linewidth=0.8)
            
            # 绘制均线
            ax1.plot(df.index, df['ma5'], label='MA5', color='orange', linewidth=1)
            ax1.plot(df.index, df['ma10'], label='MA10', color='blue', linewidth=1)
            ax1.plot(df.index, df['ma20'], label='MA20', color='purple', linewidth=1)
            
            ax1.set_title(f'{stock_name} ({stock_code}) K线图', fontsize=14, fontweight='bold')
            ax1.set_ylabel('价格')
            ax1.legend(loc='upper left')
            ax1.grid(True, alpha=0.3)
            
            # 2. 成交量
            ax2 = axes[1]
            colors = ['red' if df.iloc[i]['close'] >= df.iloc[i]['open'] else 'green' 
                     for i in range(len(df))]
            ax2.bar(df.index, df['volume'], color=colors, alpha=0.6)
            ax2.set_ylabel('成交量')
            ax2.grid(True, alpha=0.3)
            
            # 3. MACD
            ax3 = axes[2]
            ax3.plot(df.index, df['macd'], label='MACD', color='blue', linewidth=1)
            ax3.plot(df.index, df['macd_signal'], label='Signal', color='orange', linewidth=1)
            
            # MACD柱状图
            macd_hist = df['macd_hist']
            colors = ['red' if h >= 0 else 'green' for h in macd_hist]
            ax3.bar(df.index, macd_hist, color=colors, alpha=0.6)
            
            ax3.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
            ax3.set_ylabel('MACD')
            ax3.legend(loc='upper left')
            ax3.grid(True, alpha=0.3)
            
            # 4. RSI
            ax4 = axes[3]
            ax4.plot(df.index, df['rsi14'], color='purple', linewidth=1.5)
            ax4.axhline(y=70, color='red', linestyle='--', alpha=0.5, label='超买线(70)')
            ax4.axhline(y=30, color='green', linestyle='--', alpha=0.5, label='超卖线(30)')
            ax4.fill_between(df.index, 30, 70, alpha=0.1, color='gray')
            ax4.set_ylabel('RSI(14)')
            ax4.set_ylim(0, 100)
            ax4.legend(loc='upper left')
            ax4.grid(True, alpha=0.3)
            
            # 5. KDJ
            ax5 = axes[4]
            ax5.plot(df.index, df['k'], label='K', color='blue', linewidth=1)
            ax5.plot(df.index, df['d'], label='D', color='orange', linewidth=1)
            ax5.plot(df.index, df['j'], label='J', color='purple', linewidth=1)
            ax5.axhline(y=80, color='red', linestyle='--', alpha=0.5, label='超买线(80)')
            ax5.axhline(y=20, color='green', linestyle='--', alpha=0.5, label='超卖线(20)')
            ax5.set_ylabel('KDJ')
            ax5.legend(loc='upper left')
            ax5.grid(True, alpha=0.3)
            
            plt.tight_layout()
            plt.savefig(output_path, dpi=150, bbox_inches='tight')
            plt.close()
            
            print(f"  ✅ K线图已保存: {output_path}")
            return True
        except Exception as e:
            print(f"  ⚠️ K线图生成失败: {e}")
            return False
    
    @staticmethod
    def generate_fund_flow_chart(stock_code: str, stock_name: str, output_path: str):
        """生成资金流向趋势图"""
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            
            import akshare as ak
            
            # 获取近10日资金流向
            df = ak.stock_individual_fund_flow(stock=stock_code, 
                                               market="sh" if stock_code.startswith('6') else "sz")
            
            if len(df) == 0:
                return False
            
            # 取最近10日
            df = df.head(10).sort_values('日期')
            
            fig, axes = plt.subplots(2, 1, figsize=(14, 10))
            
            # 1. 主力/散户资金流向
            ax1 = axes[0]
            
            dates = df['日期'].values
            main_flow = df['主力净流入-净额'].values / 10000  # 万元
            retail_flow = df['小单净流入-净额'].values / 10000
            
            x = range(len(dates))
            width = 0.35
            
            bars1 = ax1.bar([i - width/2 for i in x], main_flow, width, 
                           label='主力净流入', color='red', alpha=0.7)
            bars2 = ax1.bar([i + width/2 for i in x], retail_flow, width,
                           label='散户净流入', color='blue', alpha=0.7)
            
            ax1.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
            ax1.set_title(f'{stock_name} ({stock_code}) 资金流向趋势', fontsize=14, fontweight='bold')
            ax1.set_ylabel('净流入 (万元)')
            ax1.set_xticks(x)
            ax1.set_xticklabels(dates, rotation=45)
            ax1.legend()
            ax1.grid(True, alpha=0.3)
            
            # 2. 大单分布
            ax2 = axes[1]
            
            # 计算各类单净流入
            super_large = df['超大单净流入-净额'].values / 10000
            large = df['大单净流入-净额'].values / 10000
            medium = df['中单净流入-净额'].values / 10000
            small = df['小单净流入-净额'].values / 10000
            
            ax2.bar(x, super_large, label='超大单', color='red', alpha=0.7)
            ax2.bar(x, large, bottom=super_large, label='大单', color='orange', alpha=0.7)
            ax2.bar(x, medium, bottom=super_large+large, label='中单', color='yellow', alpha=0.7)
            ax2.bar(x, small, bottom=super_large+large+medium, label='小单', color='green', alpha=0.7)
            
            ax2.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
            ax2.set_title('订单类型分布', fontsize=14, fontweight='bold')
            ax2.set_ylabel('净流入 (万元)')
            ax2.set_xticks(x)
            ax2.set_xticklabels(dates, rotation=45)
            ax2.legend()
            ax2.grid(True, alpha=0.3)
            
            plt.tight_layout()
            plt.savefig(output_path, dpi=150, bbox_inches='tight')
            plt.close()
            
            print(f"  ✅ 资金流向图已保存: {output_path}")
            return True
        except Exception as e:
            print(f"  ⚠️ 资金流向图生成失败: {e}")
            return False
    
    @staticmethod
    def generate_divergence_chart(df: pd.DataFrame, divergence_data: Dict, output_path: str):
        """生成背离检测图表"""
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            
            fig, axes = plt.subplots(2, 1, figsize=(14, 10))
            
            # 价格图
            ax1 = axes[0]
            ax1.plot(df.index, df['close'], label='收盘价', color='blue', linewidth=1.5)
            ax1.set_title('价格走势与背离检测', fontsize=14, fontweight='bold')
            ax1.set_ylabel('价格')
            ax1.legend()
            ax1.grid(True, alpha=0.3)
            
            # 标注顶背离
            for div in divergence_data.get('top_divergence', []):
                ax1.annotate('顶背离', xy=(div['date'], div['price_peak']), 
                           xytext=(10, 10), textcoords='offset points',
                           bbox=dict(boxstyle='round,pad=0.3', facecolor='red', alpha=0.7),
                           arrowprops=dict(arrowstyle='->', color='red'))
            
            # 标注底背离
            for div in divergence_data.get('bottom_divergence', []):
                ax1.annotate('底背离', xy=(div['date'], div['price_trough']),
                           xytext=(10, -10), textcoords='offset points',
                           bbox=dict(boxstyle='round,pad=0.3', facecolor='green', alpha=0.7),
                           arrowprops=dict(arrowstyle='->', color='green'))
            
            # MACD图
            ax2 = axes[1]
            ax2.plot(df.index, df['macd'], label='MACD', color='blue', linewidth=1)
            ax2.plot(df.index, df['macd_signal'], label='Signal', color='orange', linewidth=1)
            ax2.set_title('MACD', fontsize=14, fontweight='bold')
            ax2.set_ylabel('MACD')
            ax2.legend()
            ax2.grid(True, alpha=0.3)
            
            plt.tight_layout()
            plt.savefig(output_path, dpi=150, bbox_inches='tight')
            plt.close()
            
            print(f"  ✅ 背离检测图已保存: {output_path}")
            return True
        except Exception as e:
            print(f"  ⚠️ 背离检测图生成失败: {e}")
            return False
    
    @staticmethod
    def generate_prediction_chart(df: pd.DataFrame, predictions: Dict, stock_code: str, stock_name: str, output_path: str):
        """生成预测趋势图（含历史价格+预测价格+置信区间）"""
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            
            fig, axes = plt.subplots(2, 1, figsize=(16, 12))
            
            # 1. 价格预测图
            ax1 = axes[0]
            
            # 历史价格
            hist_dates = range(len(df))
            ax1.plot(hist_dates, df['close'], label='历史价格', color='blue', linewidth=1.5)
            
            # 预测价格
            last_idx = len(df) - 1
            pred_points = []
            pred_prices = []
            pred_colors = []
            
            for day_key in ['day_1', 'day_3', 'day_5']:
                if day_key in predictions:
                    pred = predictions[day_key]
                    day_num = int(day_key.replace('day_', ''))
                    pred_idx = last_idx + day_num
                    pred_points.append(pred_idx)
                    pred_prices.append(pred['predicted_price'])
                    
                    # 根据置信度设置颜色
                    conf = pred['confidence']
                    if conf >= 0.7:
                        pred_colors.append('green')
                    elif conf >= 0.5:
                        pred_colors.append('orange')
                    else:
                        pred_colors.append('red')
            
            # 绘制预测点
            for i, (idx, price, color) in enumerate(zip(pred_points, pred_prices, pred_colors)):
                ax1.scatter([idx], [price], color=color, s=100, zorder=5)
                ax1.annotate(f'{price:.2f}', (idx, price), 
                           textcoords="offset points", xytext=(0,10), ha='center')
            
            # 连接预测线
            if len(pred_points) > 0:
                all_points = [last_idx] + pred_points
                all_prices = [df['close'].iloc[-1]] + pred_prices
                ax1.plot(all_points, all_prices, '--', color='gray', alpha=0.5, label='预测趋势')
            
            # 置信区间（简单估计）
            if len(pred_points) > 0:
                for i, (idx, price, conf) in enumerate(zip(pred_points, pred_prices, 
                                                          [predictions[k]['confidence'] for k in ['day_1', 'day_3', 'day_5'] if k in predictions])):
                    # 根据置信度计算误差范围
                    error_range = price * (1 - conf) * 0.5
                    ax1.fill_between([idx-0.5, idx+0.5], 
                                   [price - error_range, price - error_range],
                                   [price + error_range, price + error_range],
                                   alpha=0.2, color=pred_colors[i])
            
            ax1.set_title(f'{stock_name} ({stock_code}) 价格预测', fontsize=14, fontweight='bold')
            ax1.set_ylabel('价格')
            ax1.legend(loc='upper left')
            ax1.grid(True, alpha=0.3)
            
            # 2. 预测信号评分雷达图（简化为柱状图）
            ax2 = axes[1]
            
            if 'day_1' in predictions:
                signals = predictions['day_1']['signals']
                signal_items = [
                    ('RSI趋势', 1 if signals['rsi_trend'] == 'up' else -1),
                    ('MACD趋势', 1 if signals['macd_trend'] == 'up' else -1),
                    ('均线排列', signals.get('ma_score', 0)),
                    ('成交量', 1 if signals.get('vol_trend', '') == '放量' else -0.5 if signals.get('vol_trend', '') == '缩量' else 0),
                    ('布林带位置', 1 - signals.get('bb_position', 0.5) * 2)  # 越低越好
                ]
                
                names = [item[0] for item in signal_items]
                values = [item[1] for item in signal_items]
                colors = ['green' if v > 0 else 'red' if v < 0 else 'gray' for v in values]
                
                bars = ax2.barh(names, values, color=colors, alpha=0.7)
                ax2.axvline(x=0, color='black', linestyle='-', linewidth=0.5)
                ax2.set_xlim(-1.5, 1.5)
                ax2.set_title('预测信号评分', fontsize=14, fontweight='bold')
                ax2.set_xlabel('信号强度')
                ax2.grid(True, alpha=0.3)
            
            plt.tight_layout()
            plt.savefig(output_path, dpi=150, bbox_inches='tight')
            plt.close()
            
            print(f"  ✅ 预测趋势图已保存: {output_path}")
            return True
        except Exception as e:
            print(f"  ⚠️ 预测趋势图生成失败: {e}")
            return False
    
    @staticmethod
    def generate_multi_stock_dashboard(stock_data_list: List[Dict], output_path: str):
        """生成多股票监控仪表盘"""
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            
            n_stocks = len(stock_data_list)
            if n_stocks == 0:
                return False
            
            # 计算需要的行数
            n_cols = 3
            n_rows = (n_stocks + n_cols - 1) // n_cols
            
            fig, axes = plt.subplots(n_rows, n_cols, figsize=(18, 4 * n_rows))
            if n_rows == 1:
                axes = axes.reshape(1, -1)
            
            for idx, stock in enumerate(stock_data_list):
                row = idx // n_cols
                col = idx % n_cols
                ax = axes[row, col]
                
                if 'error' in stock:
                    ax.text(0.5, 0.5, f"{stock.get('name', 'Unknown')}\n数据错误", 
                           ha='center', va='center', transform=ax.transAxes)
                    ax.set_axis_off()
                    continue
                
                # 绘制迷你K线图（简化版）
                name = stock.get('name', '')
                code = stock.get('code', '')
                price = stock.get('latest_price', 0)
                ret = stock.get('total_return', 0)
                rsi = stock.get('rsi', 0)
                score = stock.get('score', 0)
                max_score = stock.get('max_score', 5)
                score_pct = score / max_score * 100 if max_score > 0 else 0
                
                # 背景色根据收益
                if ret > 20:
                    bg_color = '#ffcccc'
                elif ret > 0:
                    bg_color = '#ccffcc'
                elif ret > -10:
                    bg_color = '#ffffcc'
                else:
                    bg_color = '#ffcccc'
                
                ax.set_facecolor(bg_color)
                
                # 显示信息
                info_text = f"{name}\n{code}\n价格: {price}\n30天: {ret:+.1f}%\nRSI: {rsi:.1f}\n评分: {score_pct:.0f}"
                ax.text(0.5, 0.5, info_text, ha='center', va='center', 
                       transform=ax.transAxes, fontsize=10, fontweight='bold')
                
                # 添加颜色标记
                if score_pct >= 80:
                    ax.add_patch(plt.Rectangle((0.85, 0.85), 0.15, 0.15, 
                                              facecolor='red', alpha=0.7, transform=ax.transAxes))
                elif rsi > 70:
                    ax.add_patch(plt.Rectangle((0.85, 0.85), 0.15, 0.15, 
                                              facecolor='orange', alpha=0.7, transform=ax.transAxes))
                
                ax.set_xlim(0, 1)
                ax.set_ylim(0, 1)
                ax.set_axis_off()
            
            # 隐藏多余的子图
            for idx in range(n_stocks, n_rows * n_cols):
                row = idx // n_cols
                col = idx % n_cols
                axes[row, col].set_axis_off()
            
            plt.suptitle('个股监控仪表盘', fontsize=16, fontweight='bold', y=0.995)
            plt.tight_layout()
            plt.savefig(output_path, dpi=150, bbox_inches='tight')
            plt.close()
            
            print(f"  ✅ 多股票仪表盘已保存: {output_path}")
            return True
        except Exception as e:
            print(f"  ⚠️ 多股票仪表盘生成失败: {e}")
            return False
    
    @staticmethod
    def generate_backtest_accuracy_chart(accuracy_data: Dict, output_path: str):
        """生成预测准确率回测图表"""
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            
            fig, ax = plt.subplots(figsize=(10, 6))
            
            periods = []
            accuracies = []
            totals = []
            
            for day in [1, 3, 5]:
                key = f"day_{day}"
                if key in accuracy_data and 'accuracy' in accuracy_data[key]:
                    periods.append(f"{day}日预测")
                    accuracies.append(accuracy_data[key]['accuracy'])
                    totals.append(accuracy_data[key]['total'])
            
            if len(periods) == 0:
                ax.text(0.5, 0.5, "暂无回测数据", ha='center', va='center', 
                       transform=ax.transAxes, fontsize=14)
            else:
                colors = ['green' if a >= 60 else 'orange' if a >= 45 else 'red' for a in accuracies]
                bars = ax.bar(periods, accuracies, color=colors, alpha=0.7)
                
                # 添加数值标签
                for bar, acc, total in zip(bars, accuracies, totals):
                    height = bar.get_height()
                    ax.text(bar.get_x() + bar.get_width()/2., height,
                           f'{acc:.1f}%\n({total}次)',
                           ha='center', va='bottom', fontsize=10)
                
                ax.axhline(y=50, color='red', linestyle='--', alpha=0.5, label='随机基准(50%)')
                ax.axhline(y=60, color='green', linestyle='--', alpha=0.5, label='良好基准(60%)')
                ax.set_ylabel('准确率 (%)')
                ax.set_title('预测准确率回测', fontsize=14, fontweight='bold')
                ax.set_ylim(0, 100)
                ax.legend()
                ax.grid(True, alpha=0.3, axis='y')
            
            plt.tight_layout()
            plt.savefig(output_path, dpi=150, bbox_inches='tight')
            plt.close()
            
            print(f"  ✅ 准确率回测图已保存: {output_path}")
            return True
        except Exception as e:
            print(f"  ⚠️ 准确率回测图生成失败: {e}")
            return False
    
    @staticmethod
    def generate_sector_fund_flow_chart(sector_data: Dict, output_path: str):
        """生成板块资金流向图"""
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            
            fig, axes = plt.subplots(2, 1, figsize=(14, 10))
            
            # 1. 板块资金净流入
            ax1 = axes[0]
            
            sectors = list(sector_data.keys())
            inflows = [sector_data[s].get('main_inflow', 0) / 10000 for s in sectors]  # 万元
            
            colors = ['red' if v > 0 else 'green' for v in inflows]
            bars = ax1.barh(sectors, inflows, color=colors, alpha=0.7)
            
            ax1.axvline(x=0, color='black', linestyle='-', linewidth=0.5)
            ax1.set_xlabel('主力净流入 (万元)')
            ax1.set_title('板块资金流向', fontsize=14, fontweight='bold')
            ax1.grid(True, alpha=0.3, axis='x')
            
            # 2. 板块情绪得分
            ax2 = axes[1]
            
            sentiments = [sector_data[s].get('sentiment_score', 0) for s in sectors]
            colors2 = ['red' if v > 0.2 else 'green' if v < -0.2 else 'gray' for v in sentiments]
            
            bars2 = ax2.barh(sectors, sentiments, color=colors2, alpha=0.7)
            ax2.axvline(x=0, color='black', linestyle='-', linewidth=0.5)
            ax2.set_xlabel('情感得分')
            ax2.set_title('板块情感分析', fontsize=14, fontweight='bold')
            ax2.set_xlim(-1, 1)
            ax2.grid(True, alpha=0.3, axis='x')
            
            plt.tight_layout()
            plt.savefig(output_path, dpi=150, bbox_inches='tight')
            plt.close()
            
            print(f"  ✅ 板块资金流向图已保存: {output_path}")
            return True
        except Exception as e:
            print(f"  ⚠️ 板块资金流向图生成失败: {e}")
            return False


if __name__ == "__main__":
    # 测试预测
    from stock_analyzer import StockAnalyzer
    
    analyzer = StockAnalyzer()
    df = analyzer.get_stock_data("300054", days=60)
    df = analyzer.calculate_indicators(df)
    
    predictor = TrendPredictor()
    predictions = predictor.predict_trend(df, days=5)
    print("预测结果:")
    print(json.dumps(predictions, ensure_ascii=False, indent=2))
    
    # 测试准确率回测
    accuracy = predictor.backtest_prediction_accuracy(df, test_days=30)
    print("\n预测准确率回测:")
    print(json.dumps(accuracy, ensure_ascii=False, indent=2))
    
    # 测试背离检测
    divergence = DivergenceDetector.detect_macd_divergence(df)
    print("\nMACD背离检测:")
    print(json.dumps(divergence, ensure_ascii=False, indent=2))
    
    strategy = predictor.optimize_strategy(df, predictions, {})
    print("\n策略优化:")
    print(json.dumps(strategy, ensure_ascii=False, indent=2))
