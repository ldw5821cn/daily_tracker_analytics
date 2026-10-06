#!/usr/bin/env python3
"""
批量预测用户持仓股票
"""

import sys
sys.path.insert(0, '.')

from multi_agent.kronos_predictor import KronosStockPredictor

# 用户持仓（来自东财截图）
HOLDINGS = [
    {'code': '000001', 'name': '平安银行', 'shares': 100},
    {'code': '000598', 'name': '兴蓉环境', 'shares': 200},
    {'code': '000027', 'name': '深圳能源', 'shares': 200},
    {'code': '600011', 'name': '华能国际', 'shares': 300},
    {'code': '600027', 'name': '华电国际', 'shares': 400},
    {'code': '600023', 'name': '浙能电力', 'shares': 500},
    {'code': '600642', 'name': '申能股份', 'shares': 200},
    {'code': '601398', 'name': '工商银行', 'shares': 300},
]

def main():
    print("=" * 60)
    print("🚀 Kronos 批量预测 - 用户持仓")
    print("=" * 60)
    
    predictor = KronosStockPredictor(model_size="mini")
    results = []
    
    for stock in HOLDINGS:
        symbol = stock['code']
        name = stock['name']
        
        print(f"\n📊 {name} ({symbol})...")
        
        try:
            result = predictor.predict(symbol, pred_days=5, sample_count=30)
            
            if 'error' not in result:
                results.append({
                    'name': name,
                    'symbol': symbol,
                    'shares': stock['shares'],
                    'current_price': result['current_price'],
                    'expected_return': result['expected_return'],
                    'volatility': result['volatility'],
                    'bull_prob': result['bull_prob'],
                    'bear_prob': result['bear_prob'],
                    'signal': result['signal'],
                    'pred_prices': result['pred_prices'],
                })
                
                print(f"   现价: {result['current_price']:.2f}")
                print(f"   预期收益: {result['expected_return']*100:+.2f}%")
                print(f"   上涨概率: {result['bull_prob']*100:.0f}%")
                print(f"   信号: {result['signal']}")
            else:
                print(f"   ❌ {result['error']}")
                
        except Exception as e:
            print(f"   ❌ 预测失败: {e}")
    
    # 汇总
    print("\n" + "=" * 60)
    print("📋 预测汇总")
    print("=" * 60)
    
    # 按预期收益排序
    results.sort(key=lambda x: x['expected_return'], reverse=True)
    
    print(f"\n{'排名':<4} {'标的':<12} {'现价':<8} {'预期收益':<10} {'上涨概率':<10} {'信号':<6}")
    print("-" * 60)
    
    for i, r in enumerate(results, 1):
        print(f"{i:<4} {r['name']:<12} {r['current_price']:<8.2f} "
              f"{r['expected_return']*100:>+8.2f}% {r['bull_prob']*100:>8.0f}% {r['signal']:<6}")
    
    # 信号统计
    buy_signals = [r for r in results if r['signal'] == 'BUY']
    sell_signals = [r for r in results if r['signal'] == 'SELL']
    hold_signals = [r for r in results if r['signal'] == 'HOLD']
    
    print(f"\n📊 信号分布:")
    print(f"   🔴 买入信号: {len(buy_signals)} 只")
    print(f"   🟡 持有信号: {len(hold_signals)} 只")
    print(f"   🟢 卖出信号: {len(sell_signals)} 只")
    
    # 保存结果
    import json
    from datetime import datetime
    
    output = {
        'date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'predictions': results,
        'summary': {
            'total': len(results),
            'buy': len(buy_signals),
            'hold': len(hold_signals),
            'sell': len(sell_signals),
        }
    }
    
    output_file = 'docs/kronos_predictions.json'
    with open(output_file, 'w') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    print(f"\n💾 结果已保存: {output_file}")
    
    return results

if __name__ == '__main__':
    results = main()
