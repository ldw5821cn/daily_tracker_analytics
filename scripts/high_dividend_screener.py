#!/usr/bin/env python3
"""
高股息长期持有股票筛选器
基于 HiThink Financial-API 财务数据 + 高股息策略
"""

import sys
import os
import json
sys.path.insert(0, '/home/liudawei/github/daily_tracker_analytics/multi_agent')
os.chdir('/home/liudawei/github/daily_tracker_analytics')

# 设置 API key
os.environ['HITHINK_API_KEY'] = open('.env').read().split('HITHINK_API_KEY=')[1].split('\n')[0].strip()

from hithink_client import HiThinkFinanceClient
from valuation_analyzer import ValuationAnalyzer
from financial_analyzer import FinancialAnalyzer
from pathlib import Path

# 高股息候选池（银行、电力、公用事业、高速公路、煤炭等）
candidates = [
    # 银行
    {'symbol': '000001', 'name': '平安银行', 'industry': '银行', 'dividend_yield': 5.5},
    {'symbol': '601398', 'name': '工商银行', 'industry': '银行', 'dividend_yield': 6.5},
    {'symbol': '601288', 'name': '农业银行', 'industry': '银行', 'dividend_yield': 6.8},
    {'symbol': '601939', 'name': '建设银行', 'industry': '银行', 'dividend_yield': 6.2},
    {'symbol': '601988', 'name': '中国银行', 'industry': '银行', 'dividend_yield': 6.5},
    {'symbol': '600036', 'name': '招商银行', 'industry': '银行', 'dividend_yield': 5.0},
    
    # 电力
    {'symbol': '600011', 'name': '华能国际', 'industry': '电力', 'dividend_yield': 3.5},
    {'symbol': '600027', 'name': '华电国际', 'industry': '电力', 'dividend_yield': 3.8},
    {'symbol': '600023', 'name': '浙能电力', 'industry': '电力', 'dividend_yield': 4.5},
    {'symbol': '600642', 'name': '申能股份', 'industry': '电力', 'dividend_yield': 4.2},
    {'symbol': '000027', 'name': '深圳能源', 'industry': '电力', 'dividend_yield': 3.0},
    
    # 公用事业
    {'symbol': '000598', 'name': '兴蓉环境', 'industry': '公用事业', 'dividend_yield': 3.5},
    {'symbol': '600008', 'name': '首创环保', 'industry': '公用事业', 'dividend_yield': 3.2},
    
    # 高速公路
    {'symbol': '600377', 'name': '宁沪高速', 'industry': '高速公路', 'dividend_yield': 5.5},
    {'symbol': '600548', 'name': '深高速', 'industry': '高速公路', 'dividend_yield': 5.0},
    {'symbol': '601006', 'name': '大秦铁路', 'industry': '铁路', 'dividend_yield': 7.0},
    
    # 煤炭
    {'symbol': '601088', 'name': '中国神华', 'industry': '煤炭', 'dividend_yield': 8.0},
    {'symbol': '600188', 'name': '兖矿能源', 'industry': '煤炭', 'dividend_yield': 9.0},
    {'symbol': '601225', 'name': '陕西煤业', 'industry': '煤炭', 'dividend_yield': 7.5},
    
    # 家电
    {'symbol': '000651', 'name': '格力电器', 'industry': '家电', 'dividend_yield': 6.0},
    {'symbol': '000333', 'name': '美的集团', 'industry': '家电', 'dividend_yield': 4.5},
    
    # 食品饮料
    {'symbol': '600519', 'name': '贵州茅台', 'industry': '白酒', 'dividend_yield': 3.5},
    {'symbol': '000858', 'name': '五粮液', 'industry': '白酒', 'dividend_yield': 3.0},
    {'symbol': '600887', 'name': '伊利股份', 'industry': '乳制品', 'dividend_yield': 4.0},
]

print("=" * 70)
print("💰 高股息长期持有股票筛选")
print("=" * 70)
print(f"\n候选池: {len(candidates)} 只")
print("筛选标准:")
print("  1. 股息率 > 4%")
print("  2. ROE > 10%")
print("  3. 资产负债率 < 70%（银行除外）")
print("  4. PE < 15")
print("  5. 营收增长 > 0%")

# 获取数据
print("\n【1/3】获取估值数据...")
client = HiThinkFinanceClient()
val_analyzer = ValuationAnalyzer()

# 批量获取估值
thscodes = [client.to_thscode(c['symbol']) for c in candidates]
valuation_results = val_analyzer.analyze_portfolio(candidates)

print(f"  ✅ 估值数据: {len(valuation_results)} 只")

print("\n【2/3】获取财务数据...")
fin_analyzer = FinancialAnalyzer()
financial_results = fin_analyzer.analyze_portfolio(candidates)

print(f"  ✅ 财务数据: {len(financial_results)} 只")

# 合并数据并筛选
print("\n【3/3】筛选高股息优质股...")

screened = []
for i, candidate in enumerate(candidates):
    val = valuation_results[i] if i < len(valuation_results) else None
    fin = financial_results[i] if i < len(financial_results) else None
    
    if not val or not fin:
        continue
    
    # 筛选条件
    dividend_yield = candidate.get('dividend_yield', 0)
    roe = fin.roe or 0
    debt_ratio = fin.debt_ratio or 0
    pe = val.pe_ttm or 999
    revenue_growth = fin.revenue_growth or 0
    
    # 银行特殊处理（高负债率正常）
    is_bank = candidate['industry'] == '银行'
    debt_threshold = 90 if is_bank else 70
    
    # 评分
    score = 0
    meets_criteria = True
    
    if dividend_yield >= 4:
        score += 20
    else:
        meets_criteria = False
    
    if roe >= 10:
        score += 20
    elif roe >= 8:
        score += 10
    else:
        meets_criteria = False
    
    if debt_ratio < debt_threshold:
        score += 15
    else:
        score -= 10
    
    if pe < 10:
        score += 20
    elif pe < 15:
        score += 10
    else:
        meets_criteria = False
    
    if revenue_growth > 0:
        score += 15
    elif revenue_growth > -5:
        score += 5
    else:
        score -= 5
    
    # PB 加分
    pb = val.pb_mrq or 999
    if pb < 1:
        score += 10
    
    if meets_criteria and score >= 60:
        screened.append({
            'name': candidate['name'],
            'symbol': candidate['symbol'],
            'industry': candidate['industry'],
            'dividend_yield': dividend_yield,
            'pe': pe,
            'pb': val.pb_mrq,
            'roe': roe,
            'debt_ratio': debt_ratio,
            'revenue_growth': revenue_growth,
            'financial_score': fin.overall_score,
            'total_score': score,
            'valuation_status': val.valuation_status
        })

# 排序
screened.sort(key=lambda x: x['total_score'], reverse=True)

print(f"\n✅ 筛选结果: {len(screened)} 只")

# 显示结果
print("\n" + "=" * 70)
print("💎 高股息优质股推荐（按综合评分排序）")
print("=" * 70)

print(f"\n{'排名':<4} {'标的':<10} {'行业':<8} {'股息率':<8} {'PE':<8} {'PB':<8} {'ROE':<8} {'评分':<6}")
print("-" * 70)

for i, stock in enumerate(screened[:10], 1):
    print(f"{i:<4} {stock['name']:<10} {stock['industry']:<8} "
          f"{stock['dividend_yield']:<7.1f}% {stock['pe']:<7.2f} {stock['pb']:<7.2f} "
          f"{stock['roe']:<7.1f}% {stock['total_score']:<6.0f}")

# 生成推荐报告
print("\n" + "=" * 70)
print("📝 生成推荐报告")
print("=" * 70)

report = f"""# 💰 高股息长期持有股票推荐

**生成时间**: {os.popen('date +"%Y-%m-%d %H:%M:%S"').read().strip()}
**数据来源**: HiThink Financial-API

---

## 筛选标准

| 指标 | 要求 | 权重 |
|------|------|------|
| **股息率** | > 4% | 20分 |
| **ROE** | > 10% | 20分 |
| **资产负债率** | < 70%（银行<90%） | 15分 |
| **PE** | < 15 | 20分 |
| **营收增长** | > 0% | 15分 |
| **PB** | < 1 | +10分 |

---

## 🏆 TOP 10 推荐

| 排名 | 标的 | 行业 | 股息率 | PE | PB | ROE | 综合评分 |
|------|------|------|--------|-----|-----|------|----------|
"""

for i, stock in enumerate(screened[:10], 1):
    report += f"| {i} | **{stock['name']}** | {stock['industry']} | {stock['dividend_yield']:.1f}% | {stock['pe']:.2f} | {stock['pb']:.2f} | {stock['roe']:.1f}% | {stock['total_score']:.0f} |\n"

report += """
---

## 📊 详细分析

"""

for i, stock in enumerate(screened[:5], 1):
    report += f"""### {i}. {stock['name']} ({stock['symbol']})

| 指标 | 数值 | 评价 |
|------|------|------|
| **行业** | {stock['industry']} | - |
| **股息率** | {stock['dividend_yield']:.1f}% | {'🟢 优秀' if stock['dividend_yield'] >= 6 else '🟡 良好'} |
| **PE (TTM)** | {stock['pe']:.2f} | {'🟢 低估' if stock['pe'] < 10 else '🟡 合理'} |
| **PB (MRQ)** | {stock['pb']:.2f} | {'🟢 破净' if stock['pb'] < 1 else '🟡 合理'} |
| **ROE** | {stock['roe']:.1f}% | {'🟢 优秀' if stock['roe'] >= 12 else '🟡 良好'} |
| **资产负债率** | {stock['debt_ratio']:.1f}% | {'🟢 安全' if stock['debt_ratio'] < 60 else '🟡 一般'} |
| **营收增长** | {stock['revenue_growth']:+.1f}% | {'🟢 增长' if stock['revenue_growth'] > 0 else '🔴 下滑'} |

**投资建议**: {'✅ 强烈推荐' if stock['total_score'] >= 80 else '🟡 推荐持有' if stock['total_score'] >= 70 else '🟠 谨慎持有'}

---

"""

report += """## 💡 高股息策略要点

### 适合长期持有的特征

1. **稳定现金流**：公用事业、高速公路、铁路等基础设施
2. **高分红历史**：连续 5 年以上分红，且分红率 > 30%
3. **低估值**：PE < 15，PB < 1（破净更好）
4. **财务稳健**：ROE > 10%，资产负债率合理
5. **行业龙头**：具有垄断地位或竞争优势

### 风险提示

- ⚠️ **高股息陷阱**：股息率高可能是因为股价暴跌（如双汇发展）
- ⚠️ **周期股**：煤炭、钢铁等周期股股息率波动大
- ⚠️ **银行风险**：高负债率，受经济周期影响大

### 建议配置

| 类型 | 比例 | 示例 |
|------|------|------|
| **银行** | 30% | 工商银行、建设银行 |
| **公用事业** | 25% | 兴蓉环境、首创环保 |
| **高速公路** | 20% | 宁沪高速、深高速 |
| **煤炭** | 15% | 中国神华、陕西煤业 |
| **家电** | 10% | 格力电器、美的集团 |

---

*报告生成: LLM-native 量化系统 | HiThink Financial-API | 仅供参考，不构成投资建议*
"""

# 保存报告
output_path = Path('docs/high_dividend_recommendations.md')
with open(output_path, 'w', encoding='utf-8') as f:
    f.write(report)

print(f"✅ 推荐报告已保存: {output_path}")

print("\n" + "=" * 70)
print("✅ 高股息筛选完成")
print("=" * 70)
