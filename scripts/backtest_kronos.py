#!/usr/bin/env python3
"""
Kronos 预测回测验证
验证历史预测准确性
"""

import sys
sys.path.insert(0, '.')

from multi_agent.kronos_predictor import KronosStockPredictor
import urllib.request
import json
import pandas as pd
from datetime import datetime, timedelta

def fetch_kline(symbol, days=120):
    """获取历史K线"""
    tencent_code = f"sh{symbol}" if symbol.startswith('6') else f"sz{symbol}"
    url = f'https://web.ifzq.gtimg.cn/appstock/app/kline/kline?param={tencent_code},day,,,{days}'
    
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    
    kline = data['data'][tencent_code].get('day', [])
    df = pd.DataFrame(kline)
    df = df.iloc[:, :6]
    df.columns = ['date', 'open', 'close', 'high', 'low', 'volume']
    
    for col in ['open', 'close', 'high', 'low', 'volume']:
        df[col] = df[col].astype(float)
    
    df['timestamps'] = pd.to_datetime(df['date'])
    return df

def backtest_prediction(symbol, name, pred_days=5, lookback=30):
    """
    回测：用历史数据的前N-5天预测后5天，对比实际走势
    """
    print(f"\n📊 回测 {name} ({symbol})...")
    
    # 获取完整历史数据
    df_full = fetch_kline(symbol, days=120)
    
    if len(df_full) < lookback + pred_days:
        print(f"   ❌ 数据不足")
        return None
    
    # 模拟历史时点（30天前）
    historical_idx = len(df_full) - pred_days - 10  # 用10天前的数据预测
    
    df_hist = df_full.iloc[:historical_idx].copy()
    df_future = df_full.iloc[historical_idx:historical_idx + pred_days].copy()
    
    current_price = df_hist['close'].iloc[-1]
    actual_prices = df_future['close'].values
    actual_return = (actual_prices[-1] - current_price) / current_price
    
    # Kronos 预测
    try:
        predictor = KronosStockPredictor(model_size="mini")
        predictor.load_model()  # 显式加载模型
        
        # 准备时间戳
        last_date = df_hist['timestamps'].iloc[-1]
        future_dates = pd.date_range(
            start=last_date + timedelta(days=1),
            periods=pred_days,
            freq='B'
        )
        
        x_timestamp = pd.Series(df_hist['timestamps'].values)
        y_timestamp = pd.Series(future_dates)
        
        pred_df = predictor.predictor.predict(
            df=df_hist[['open', 'high', 'low', 'close', 'volume']],
            x_timestamp=x_timestamp,
            y_timestamp=y_timestamp,
            pred_len=pred_days,
            T=1.0,
            top_p=0.9,
            sample_count=30
        )
        
        pred_prices = pred_df['close'].values
        pred_return = (pred_prices.mean() - current_price) / current_price
        
        # 计算准确性
        direction_match = (pred_return > 0) == (actual_return > 0)
        error = abs(pred_return - actual_return)
        
        result = {
            'symbol': symbol,
            'name': name,
            'current_price': current_price,
            'actual_prices': actual_prices.tolist(),
            'pred_prices': pred_prices.tolist(),
            'actual_return': actual_return,
            'pred_return': pred_return,
            'direction_match': direction_match,
            'error': error,
        }
        
        print(f"   当前价: {current_price:.2f}")
        print(f"   实际走势: {actual_return*100:+.2f}%")
        print(f"   Kronos预测: {pred_return*100:+.2f}%")
        print(f"   方向判断: {'✅ 正确' if direction_match else '❌ 错误'}")
        print(f"   误差: {error*100:.2f}%")
        
        return result
        
    except Exception as e:
        print(f"   ❌ 预测失败: {e}")
        return None

def main():
    print("=" * 60)
    print("🧪 Kronos 预测回测验证")
    print("=" * 60)
    
    # 测试几只股票
    test_stocks = [
        ('000001', '平安银行'),
        ('000598', '兴蓉环境'),
        ('600011', '华能国际'),
    ]
    
    results = []
    
    for symbol, name in test_stocks:
        result = backtest_prediction(symbol, name, pred_days=5, lookback=30)
        if result:
            results.append(result)
    
    # 汇总
    print("\n" + "=" * 60)
    print("📋 回测汇总")
    print("=" * 60)
    
    if results:
        accuracy = sum(1 for r in results if r['direction_match']) / len(results)
        avg_error = sum(r['error'] for r in results) / len(results)
        
        print(f"\n方向判断准确率: {accuracy*100:.0f}% ({sum(1 for r in results if r['direction_match'])}/{len(results)})")
        print(f"平均预测误差: {avg_error*100:.2f}%")
        
        print(f"\n{'标的':<12} {'实际':<10} {'预测':<10} {'方向':<6} {'误差':<8}")
        print("-" * 60)
        for r in results:
            direction = "✅" if r['direction_match'] else "❌"
            print(f"{r['name']:<12} {r['actual_return']*100:>+8.2f}% {r['pred_return']*100:>+8.2f}% {direction:<6} {r['error']*100:>6.2f}%")
    
    return results

if __name__ == '__main__':
    results = main()
