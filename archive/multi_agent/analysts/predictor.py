"""
趋势预测器 - 从 advanced_predictor 抽取的三算法融合预测
线性回归 + 指数平滑 + 均线趋势 加权融合
未来1/3/5日方向预测 + 准确率回测
"""
import numpy as np
import pandas as pd
import warnings
warnings.filterwarnings('ignore')


class TrendPredictor:
    """
    趋势预测器 - 多算法融合预测
    
    算法:
    1. 线性回归预测 (短期:中期:长期 加权=0.4:0.35:0.25)
    2. 指数平滑预测 (alpha=0.3)
    3. 均线趋势预测 (MA5/MA10/MA20 斜率加权)
    
    融合权重: 线性回归0.45 : 指数平滑0.3 : 均线趋势0.25
    """
    
    @staticmethod
    def predict(df, days=5):
        """
        多算法融合预测
        
        Args:
            df: 含 close/ma5/ma10/ma20 的 DataFrame
            days: 预测天数 (1/3/5)
        
        Returns:
            dict: 预测结果
        """
        if df is None or len(df) < 30:
            return {'error': '数据不足'}
        
        l = df.iloc[-1]
        current_price = float(l['close'])
        
        # ========== 算法1: 线性回归 ==========
        x = np.arange(len(df))
        y = df['close'].values.astype(float)
        coeffs_short = np.polyfit(x[-10:].astype(float), y[-10:], 1)
        coeffs_mid = np.polyfit(x[-20:].astype(float), y[-20:], 1)
        coeffs_long = np.polyfit(x[-30:].astype(float), y[-30:], 1)
        
        future_lr = []
        for i in range(1, days + 1):
            ps = coeffs_short[0] * (len(df) + i) + coeffs_short[1]
            pm = coeffs_mid[0] * (len(df) + i) + coeffs_mid[1]
            pl = coeffs_long[0] * (len(df) + i) + coeffs_long[1]
            future_lr.append(ps * 0.4 + pm * 0.35 + pl * 0.25)
        
        # ========== 算法2: 指数平滑 ==========
        alpha = 0.3
        ema = float(l['close'])
        future_ema = []
        for i in range(1, days + 1):
            if i == 1:
                ema = alpha * float(l['close']) + (1 - alpha) * float(df.iloc[-2]['close'])
            else:
                ema = alpha * future_ema[-1] + (1 - alpha) * ema
            future_ema.append(ema)
        
        # ========== 算法3: 均线趋势 ==========
        ma5_slope = (float(l['ma5']) - float(df.iloc[-5]['ma5'])) / 5 if pd.notna(l['ma5']) and pd.notna(df.iloc[-5]['ma5']) else 0
        ma10_slope = (float(l['ma10']) - float(df.iloc[-10]['ma10'])) / 10 if pd.notna(l['ma10']) and pd.notna(df.iloc[-10]['ma10']) else 0
        ma20_slope = (float(l['ma20']) - float(df.iloc[-20]['ma20'])) / 20 if pd.notna(l['ma20']) and pd.notna(df.iloc[-20]['ma20']) else 0
        
        future_ma = []
        for i in range(1, days + 1):
            pred = current_price + (ma5_slope * 0.5 + ma10_slope * 0.3 + ma20_slope * 0.2) * i
            future_ma.append(pred)
        
        # ========== 三算法融合 ==========
        predictions = []
        for i in range(days):
            fused = future_lr[i] * 0.45 + future_ema[i] * 0.3 + future_ma[i] * 0.25
            pred_return = (fused / current_price - 1) * 100
            predictions.append({
                'day': i + 1,
                'pred_price': round(fused, 2),
                'pred_return': round(pred_return, 2),
                'direction': '涨' if pred_return > 0 else '跌' if pred_return < 0 else '平',
            })
        
        # ========== 综合方向判断 ==========
        avg_return = np.mean([p['pred_return'] for p in predictions])
        if avg_return > 1.5:
            trend = "看涨"
            confidence = min(100, abs(avg_return) * 8 + 50)
        elif avg_return > 0.5:
            trend = "偏多"
            confidence = min(80, abs(avg_return) * 10 + 30)
        elif avg_return < -1.5:
            trend = "看跌"
            confidence = min(100, abs(avg_return) * 8 + 50)
        elif avg_return < -0.5:
            trend = "偏空"
            confidence = min(80, abs(avg_return) * 10 + 30)
        else:
            trend = "震荡"
            confidence = 30
        
        return {
            'current_price': round(current_price, 2),
            'predictions': predictions,
            'avg_return': round(avg_return, 2),
            'trend': trend,
            'confidence': round(confidence, 1),
        }
    
    @staticmethod
    def backtest_accuracy(df, test_days=60):
        """
        回测预测准确率
        
        Args:
            df: 历史数据 DataFrame
            test_days: 回测天数
        
        Returns:
            dict: 1/3/5日预测准确率
        """
        if len(df) < test_days + 30:
            return {'error': '数据不足'}
        
        results = {1: {'correct': 0, 'total': 0}, 3: {'correct': 0, 'total': 0}, 5: {'correct': 0, 'total': 0}}
        
        for i in range(len(df) - test_days - 5, len(df) - 5):
            hist = df.iloc[:i]
            if len(hist) < 30:
                continue
            
            x = np.arange(len(hist))
            y = hist['close'].values.astype(float)
            coeffs = np.polyfit(x[-20:].astype(float), y[-20:], 1)
            hp = float(hist.iloc[-1]['close'])
            
            for day in [1, 3, 5]:
                pp = coeffs[0] * (len(hist) + day) + coeffs[1]
                ap = float(df.iloc[i + day]['close'])
                pred_dir = (pp - hp) > 0
                actual_dir = (ap - hp) > 0
                results[day]['total'] += 1
                if pred_dir == actual_dir:
                    results[day]['correct'] += 1
        
        return {
            'test_days': test_days,
            'accuracy': {
                '1日': round(results[1]['correct'] / max(results[1]['total'], 1) * 100, 1),
                '3日': round(results[3]['correct'] / max(results[3]['total'], 1) * 100, 1),
                '5日': round(results[5]['correct'] / max(results[5]['total'], 1) * 100, 1),
            },
            'samples': {
                '1日': results[1]['total'],
                '3日': results[3]['total'],
                '5日': results[5]['total'],
            }
        }


def get_prediction_text(ticker, name, df=None):
    """
    一键获取预测报告文本
    
    Returns: str 适合嵌入技术面分析的预测内容
    """
    from core.data_layer import get_stock_data, calc_technical_indicators
    
    if df is None:
        try:
            df, _ = get_stock_data(ticker, calibrate=False)
            df = calc_technical_indicators(df)
        except:
            return ""
    
    pred = TrendPredictor.predict(df, days=5)
    acc = TrendPredictor.backtest_accuracy(df)
    
    lines = []
    lines.append("")
    lines.append("#### 多算法趋势预测")
    lines.append("")
    
    if 'error' in pred:
        return ""
    
    lines.append(f"**{pred['trend']}** (置信度{pred['confidence']:.0f}%) | 均价预测{pred['avg_return']:+.2f}%")
    lines.append("")
    for p in pred['predictions']:
        lines.append(f"- {p['day']}日后: {p['pred_price']} ({p['pred_return']:+.2f}%)")
    
    if 'error' not in acc:
        a = acc['accuracy']
        lines.append("")
        lines.append(f"**历史回测准确率**: 1日{a['1日']}% | 3日{a['3日']}% | 5日{a['5日']}%")
    
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, '/home/zhihu/daily_tracker_analytics/etf_tracker/multi_agent')
    from core.data_layer import get_stock_data, calc_technical_indicators
    
    for ticker, name in [('601991','大唐发电'), ('515880','通信ETF'), ('516150','稀土ETF')]:
        df, _ = get_stock_data(ticker, calibrate=False)
        df = calc_technical_indicators(df)
        
        pred = TrendPredictor.predict(df, days=5)
        acc = TrendPredictor.backtest_accuracy(df)
        
        print(f"\n=== {name} ===")
        print(f"当前:{pred['current_price']} | 趋势:{pred['trend']}({pred['confidence']:.0f}%)")
        for p in pred['predictions']:
            print(f"  {p['day']}日: {p['pred_price']}({p['pred_return']:+.2f}%)")
        if 'error' not in acc:
            a = acc['accuracy']
            print(f"  准确率: 1日{a['1日']}% | 3日{a['3日']}% | 5日{a['5日']}%")
