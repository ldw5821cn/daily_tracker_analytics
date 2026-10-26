#!/usr/bin/env python3
"""
多 Agent 量化日报生成脚本 - 快速版
只分析核心标的（ETF + 部分个股），生成精简日报
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
    
    # 只分析ETF和期货，个股太多
    core_stocks = etfs + futures[:20]  # ETF全部 + 前20个期货
    
    print(f"📊 多Agent量化日报 ({datetime.now().strftime('%Y-%m-%d')})")
    print(f"总标的 {len(stocks)} 只 | 本次分析 {len(core_stocks)} 只核心标的")
    print("=" * 60)
    
    results = []
    errors = []
    
    for i, (ticker, name) in enumerate(core_stocks, 1):
        try:
            print(f"\n[{i}/{len(core_stocks)}] {name}({ticker})...")
            result = analyze_stock(ticker, name, datetime.now().strftime('%Y-%m-%d'), None, agentic=False)
            results.append(result)
            v = result['verdict']
            print(f"  → {v['rating']} | 评分{v['weighted_score']:.1f} | {v['recommendation']}")
        except Exception as e:
            print(f"  ❌ {name}({ticker}): {str(e)[:50]}")
            errors.append((ticker, name, str(e)))
            continue
    
    # 生成汇总
    print("\n" + "=" * 60)
    print("📊 多Agent量化日报汇总")
    print("=" * 60)
    
    bullish = [r for r in results if r['verdict']['net_signal'] > 0]
    bearish = [r for r in results if r['verdict']['net_signal'] < 0]
    neutral = [r for r in results if r['verdict']['net_signal'] == 0]
    
    print(f"\n看多 {len(bullish)} | 看空 {len(bearish)} | 中性 {len(neutral)} | 失败 {len(errors)}")
    
    # Top 推荐
    print("\n🏆 重点推荐 (按综合评分)")
    top = sorted(results, key=lambda x: x['verdict']['weighted_score'], reverse=True)[:10]
    for r in top:
        v = r['verdict']
        emoji = '🔥' if v['net_signal'] > 0 else '❄️' if v['net_signal'] < 0 else '➖'
        print(f"{emoji} {r['name']}({r['ticker']}) {v['weighted_score']:.1f}分 | {v['rating']} | {v['recommendation']}")
    
    # 风险提示
    print("\n⚠️ 高风险标的")
    risky = sorted(results, key=lambda x: x['risk_assessment'].get('risk_score', 0), reverse=True)[:5]
    for r in risky:
        print(f"⚠️ {r['name']}({r['ticker']}) {r['risk_assessment']['overall_risk']}")
    
    if errors:
        print(f"\n❌ 失败 {len(errors)} 只")
        for ticker, name, err in errors[:3]:
            print(f"  - {name}({ticker}): {err[:40]}...")
    
    print(f"\n📱 完整页面：https://ldw5821cn.github.io/daily_tracker_analytics/prediction.html")
    
    return results, errors


if __name__ == '__main__':
    run_daily_analysis()
