#!/usr/bin/env python3
"""
多 Agent 量化日报生成脚本
分析 watchlist 中的所有标的，生成精简日报
"""
import sys
import os
import json
from datetime import datetime

sys.path.insert(0, '/home/liudawei/github/daily_tracker_analytics/multi_agent')

from core.watchlist import get_stocks_as_tuples
from orchestrator import analyze_stock


def run_daily_analysis():
    stocks = get_stocks_as_tuples()
    
    # 分类
    etfs = [s for s in stocks if 'ETF' in s[1]]
    futures = [s for s in stocks if s[0].endswith('0') and len(s[0]) <= 4]
    stocks_only = [s for s in stocks if 'ETF' not in s[1] and not (s[0].endswith('0') and len(s[0]) <= 4)]
    
    print(f"📊 多Agent量化日报 ({datetime.now().strftime('%Y-%m-%d')})")
    print(f"共 {len(stocks)} 只标的 | ETF {len(etfs)} | 期货 {len(futures)} | 个股 {len(stocks_only)}")
    print("=" * 60)
    
    results = []
    errors = []
    
    # 分析所有标的
    for i, (ticker, name) in enumerate(stocks, 1):
        try:
            print(f"\n[{i}/{len(stocks)}] 分析 {name}({ticker})...")
            result = analyze_stock(ticker, name, datetime.now().strftime('%Y-%m-%d'), None, agentic=False)
            results.append(result)
            
            # 打印简要结果
            v = result['verdict']
            print(f"  → {v['rating']} | 综合评分{v['weighted_score']:.1f} | 建议: {v['recommendation']}")
            
        except Exception as e:
            print(f"  ❌ {name}({ticker}) 分析失败: {e}")
            errors.append((ticker, name, str(e)))
            continue
    
    # 生成汇总
    print("\n" + "=" * 60)
    print("📊 多Agent量化日报汇总")
    print("=" * 60)
    
    # 按评级分组
    bullish = [r for r in results if r['verdict']['net_signal'] > 0]
    bearish = [r for r in results if r['verdict']['net_signal'] < 0]
    neutral = [r for r in results if r['verdict']['net_signal'] == 0]
    
    print(f"\n看多 {len(bullish)} | 看空 {len(bearish)} | 中性 {len(neutral)} | 失败 {len(errors)}")
    
    # Top 推荐
    print("\n🏆 重点推荐 (按综合评分排序)")
    top = sorted(results, key=lambda x: x['verdict']['weighted_score'], reverse=True)[:10]
    for r in top:
        v = r['verdict']
        emoji = '🔥' if v['net_signal'] > 0 else '❄️' if v['net_signal'] < 0 else '➖'
        print(f"{emoji} {r['name']}({r['ticker']}) 评分{v['weighted_score']:.1f} | {v['rating']} | {v['recommendation']}")
    
    # 风险提示
    print("\n⚠️ 风险提示 (按风险评分排序)")
    risky = sorted(results, key=lambda x: x['risk_assessment'].get('risk_score', 0), reverse=True)[:5]
    for r in risky:
        print(f"⚠️ {r['name']}({r['ticker']}) 风险: {r['risk_assessment']['overall_risk']}")
    
    # 错误汇总
    if errors:
        print(f"\n❌ 分析失败 ({len(errors)}只)")
        for ticker, name, err in errors[:5]:
            print(f"  - {name}({ticker}): {err[:50]}...")
    
    print(f"\n📱 完整页面：https://ldw5821cn.github.io/daily_tracker_analytics/prediction.html")
    
    return results, errors


if __name__ == '__main__':
    run_daily_analysis()
