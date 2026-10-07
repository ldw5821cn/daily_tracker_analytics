#!/usr/bin/env python3
"""
持仓完整分析脚本
"""

import sys
import os
sys.path.insert(0, '/home/liudawei/github/daily_tracker_analytics/multi_agent')
os.chdir('/home/liudawei/github/daily_tracker_analytics')

# 设置 API key
os.environ['HITHINK_API_KEY'] = open('.env').read().split('HITHINK_API_KEY=')[1].split('\n')[0].strip()

from hithink_client import HiThinkFinanceClient
from valuation_analyzer import ValuationAnalyzer
from financial_analyzer import FinancialAnalyzer

# 持仓
holdings = [
    {'symbol': '000001', 'name': '平安银行', 'shares': 100},
    {'symbol': '000027', 'name': '深圳能源', 'shares': 200},
    {'symbol': '000598', 'name': '兴蓉环境', 'shares': 200},
    {'symbol': '600011', 'name': '华能国际', 'shares': 600},
    {'symbol': '600027', 'name': '华电国际', 'shares': 400},
    {'symbol': '600023', 'name': '浙能电力', 'shares': 500},
    {'symbol': '600642', 'name': '申能股份', 'shares': 200},
    {'symbol': '601398', 'name': '工商银行', 'shares': 300},
]

print("=" * 70)
print("📊 当前持仓完整分析")
print("=" * 70)

# ========== 1. 实时行情 ==========
print("\n【1/4】实时行情...")
client = HiThinkFinanceClient()

# 使用行情快照接口
quotes = []
for h in holdings:
    try:
        thscode = client.to_thscode(h['symbol'])
        quote = client.get_quote(thscode)
        if quote:
            quote['symbol'] = h['symbol']
            quote['name'] = h['name']
            quote['shares'] = h['shares']
            quotes.append(quote)
    except Exception as e:
        print(f"  ⚠️ {h['name']} 行情获取失败: {e}")

print(f"\n{'标的':<10} {'现价':<8} {'涨跌幅':<8} {'市值估算':<10}")
print("-" * 50)
total_value = 0
for quote in quotes:
    price = quote.get('last_price', 0) or quote.get('close_price', 0) or quote.get('latest_price', 0)
    change = quote.get('price_change_ratio_pct', 0) or quote.get('change_ratio', 0)
    shares = quote.get('shares', 0)
    market_value = price * shares
    total_value += market_value
    print(f"{quote['name']:<10} {price:<8.2f} {change:>+7.2f}% {market_value:<10.0f}")
print(f"\n总估算市值: {total_value:,.0f} 元")

# ========== 2. 估值分析 ==========
print("\n【2/4】估值分析...")
val_analyzer = ValuationAnalyzer()
valuation_results = val_analyzer.analyze_portfolio(holdings)

print(f"\n{'标的':<10} {'PE':<8} {'PB':<8} {'状态':<10}")
print("-" * 50)
for m in valuation_results:
    pe_str = f"{m.pe_ttm:.2f}" if m.pe_ttm else "N/A"
    pb_str = f"{m.pb_mrq:.2f}" if m.pb_mrq else "N/A"
    status = {"undervalued": "🟢 低估", "fair": "🟡 合理", "overvalued": "🔴 高估"}.get(m.valuation_status, "⚪ 未知")
    print(f"{m.name:<10} {pe_str:<8} {pb_str:<8} {status:<10}")

# ========== 3. 财务分析 ==========
print("\n【3/4】财务分析...")
fin_analyzer = FinancialAnalyzer()
financial_results = fin_analyzer.analyze_portfolio(holdings)

print(f"\n{'标的':<10} {'ROE':<8} {'营收增长':<10} {'综合评分':<8}")
print("-" * 50)
for ind in financial_results:
    roe_str = f"{ind.roe:.1f}%" if ind.roe else "N/A"
    growth_str = f"{ind.revenue_growth:+.1f}%" if ind.revenue_growth else "N/A"
    score_str = f"{ind.overall_score:.0f}" if ind.overall_score else "N/A"
    print(f"{ind.name:<10} {roe_str:<8} {growth_str:<10} {score_str:<8}")

# ========== 4. 综合评分 ==========
print("\n【4/4】综合评分...")

# 合并所有维度
combined_scores = []
for i, h in enumerate(holdings):
    score_data = {'name': h['name'], 'symbol': h['symbol'], 'shares': h['shares']}
    
    # 估值评分
    if i < len(valuation_results):
        v = valuation_results[i]
        score_data['pe'] = v.pe_ttm
        score_data['pb'] = v.pb_mrq
        score_data['valuation_status'] = v.valuation_status
    
    # 财务评分
    if i < len(financial_results):
        f = financial_results[i]
        score_data['roe'] = f.roe
        score_data['revenue_growth'] = f.revenue_growth
        score_data['financial_score'] = f.overall_score
    
    combined_scores.append(score_data)

# 排序
scored = [s for s in combined_scores if s.get('financial_score')]
scored.sort(key=lambda x: x['financial_score'], reverse=True)

print(f"\n{'排名':<4} {'标的':<10} {'财务评分':<10} {'ROE':<8} {'估值状态':<10} {'建议':<15}")
print("-" * 70)
for i, s in enumerate(scored, 1):
    roe_str = f"{s.get('roe', 0):.1f}%" if s.get('roe') else "N/A"
    status = {"undervalued": "🟢 低估", "fair": "🟡 合理", "overvalued": "🔴 高估"}.get(s.get('valuation_status'), "⚪ 未知")
    
    # 简单建议
    score = s.get('financial_score', 0)
    growth = s.get('revenue_growth', 0) or 0
    if score >= 40 and growth > 5:
        advice = "✅ 优质持仓"
    elif score >= 30:
        advice = "🟡 持有观察"
    elif score >= 20:
        advice = "🟠 考虑减仓"
    else:
        advice = "🔴 建议减仓"
    
    print(f"{i:<4} {s['name']:<10} {score:<10.0f} {roe_str:<8} {status:<10} {advice:<15}")

print("\n" + "=" * 70)
print("✅ 分析完成")
print("=" * 70)
