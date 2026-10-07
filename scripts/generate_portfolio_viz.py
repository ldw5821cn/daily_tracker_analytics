#!/usr/bin/env python3
"""
持仓可视化报告生成器
生成 HTML 图表展示持仓分析结果
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

# 持仓
holdings = [
    {'symbol': '000001', 'name': '平安银行', 'shares': 100, 'color': '#FF6B6B'},
    {'symbol': '000027', 'name': '深圳能源', 'shares': 200, 'color': '#4ECDC4'},
    {'symbol': '000598', 'name': '兴蓉环境', 'shares': 200, 'color': '#45B7D1'},
    {'symbol': '600011', 'name': '华能国际', 'shares': 600, 'color': '#96CEB4'},
    {'symbol': '600027', 'name': '华电国际', 'shares': 400, 'color': '#FECA57'},
    {'symbol': '600023', 'name': '浙能电力', 'shares': 500, 'color': '#48DBFB'},
    {'symbol': '600642', 'name': '申能股份', 'shares': 200, 'color': '#FF9FF3'},
    {'symbol': '601398', 'name': '工商银行', 'shares': 300, 'color': '#54A0FF'},
]

print("=" * 70)
print("📊 生成持仓可视化报告")
print("=" * 70)

# 获取数据
print("\n【1/3】获取数据...")
client = HiThinkFinanceClient()
val_analyzer = ValuationAnalyzer()
fin_analyzer = FinancialAnalyzer()

# 行情数据
quotes = []
for h in holdings:
    try:
        thscode = client.to_thscode(h['symbol'])
        quote = client.get_quote(thscode)
        if quote:
            quote['symbol'] = h['symbol']
            quote['name'] = h['name']
            quote['shares'] = h['shares']
            quote['color'] = h['color']
            quotes.append(quote)
    except Exception as e:
        print(f"  ⚠️ {h['name']}: {e}")

# 估值数据
valuation_results = val_analyzer.analyze_portfolio(holdings)

# 财务数据
financial_results = fin_analyzer.analyze_portfolio(holdings)

print(f"  ✅ 行情: {len(quotes)} 只")
print(f"  ✅ 估值: {len(valuation_results)} 只")
print(f"  ✅ 财务: {len(financial_results)} 只")

# 生成 HTML
print("\n【2/3】生成可视化 HTML...")

html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>📊 持仓可视化分析 - {os.popen('date +%Y-%m-%d').read().strip()}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }}
        
        .container {{
            max-width: 1400px;
            margin: 0 auto;
        }}
        
        .header {{
            background: white;
            border-radius: 20px;
            padding: 30px;
            margin-bottom: 20px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.1);
        }}
        
        .header h1 {{
            color: #333;
            font-size: 2em;
            margin-bottom: 10px;
        }}
        
        .header p {{
            color: #666;
            font-size: 1.1em;
        }}
        
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }}
        
        .stat-card {{
            background: white;
            border-radius: 15px;
            padding: 20px;
            box-shadow: 0 5px 20px rgba(0,0,0,0.08);
            text-align: center;
            transition: transform 0.3s;
        }}
        
        .stat-card:hover {{
            transform: translateY(-5px);
        }}
        
        .stat-card .value {{
            font-size: 2em;
            font-weight: bold;
            color: #667eea;
        }}
        
        .stat-card .label {{
            color: #666;
            margin-top: 5px;
        }}
        
        .charts-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(500px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }}
        
        .chart-card {{
            background: white;
            border-radius: 15px;
            padding: 25px;
            box-shadow: 0 5px 20px rgba(0,0,0,0.08);
        }}
        
        .chart-card h3 {{
            color: #333;
            margin-bottom: 20px;
            font-size: 1.3em;
        }}
        
        .chart-container {{
            position: relative;
            height: 300px;
        }}
        
        .table-card {{
            background: white;
            border-radius: 15px;
            padding: 25px;
            box-shadow: 0 5px 20px rgba(0,0,0,0.08);
            margin-bottom: 20px;
        }}
        
        table {{
            width: 100%;
            border-collapse: collapse;
        }}
        
        th, td {{
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #eee;
        }}
        
        th {{
            background: #f8f9fa;
            font-weight: bold;
            color: #333;
        }}
        
        tr:hover {{
            background: #f8f9fa;
        }}
        
        .badge {{
            display: inline-block;
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.85em;
            font-weight: bold;
        }}
        
        .badge-success {{
            background: #d4edda;
            color: #155724;
        }}
        
        .badge-warning {{
            background: #fff3cd;
            color: #856404;
        }}
        
        .badge-danger {{
            background: #f8d7da;
            color: #721c24;
        }}
        
        .badge-info {{
            background: #d1ecf1;
            color: #0c5460;
        }}
        
        .footer {{
            text-align: center;
            color: white;
            margin-top: 30px;
            padding: 20px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📊 持仓可视化分析</h1>
            <p>生成时间: {os.popen('date +"%Y-%m-%d %H:%M:%S"').read().strip()} | 数据来源: HiThink Financial-API</p>
        </div>
        
        <div class="stats-grid">
            <div class="stat-card">
                <div class="value">8</div>
                <div class="label">持仓标的</div>
            </div>
            <div class="stat-card">
                <div class="value" id="total-value">--</div>
                <div class="label">总市值（元）</div>
            </div>
            <div class="stat-card">
                <div class="value" id="avg-score">--</div>
                <div class="label">平均财务评分</div>
            </div>
            <div class="stat-card">
                <div class="value" id="undervalued-count">--</div>
                <div class="label">低估标的数</div>
            </div>
        </div>
        
        <div class="charts-grid">
            <div class="chart-card">
                <h3>📈 持仓市值分布</h3>
                <div class="chart-container">
                    <canvas id="marketValueChart"></canvas>
                </div>
            </div>
            
            <div class="chart-card">
                <h3>📊 财务评分对比</h3>
                <div class="chart-container">
                    <canvas id="scoreChart"></canvas>
                </div>
            </div>
            
            <div class="chart-card">
                <h3>💰 PE/PB 估值对比</h3>
                <div class="chart-container">
                    <canvas id="valuationChart"></canvas>
                </div>
            </div>
            
            <div class="chart-card">
                <h3>📉 ROE vs 营收增长</h3>
                <div class="chart-container">
                    <canvas id="roeGrowthChart"></canvas>
                </div>
            </div>
        </div>
        
        <div class="table-card">
            <h3>📋 持仓明细表</h3>
            <table id="holdings-table">
                <thead>
                    <tr>
                        <th>排名</th>
                        <th>标的</th>
                        <th>现价</th>
                        <th>市值</th>
                        <th>PE</th>
                        <th>PB</th>
                        <th>ROE</th>
                        <th>营收增长</th>
                        <th>综合评分</th>
                        <th>建议</th>
                    </tr>
                </thead>
                <tbody id="table-body">
                </tbody>
            </table>
        </div>
        
        <div class="footer">
            <p>📊 LLM-native 量化系统 | HiThink Financial-API | 数据仅供参考，不构成投资建议</p>
        </div>
    </div>
    
    <script>
        // 数据
        const holdingsData = {json.dumps([{
            'name': q['name'],
            'symbol': q['symbol'],
            'price': q.get('last_price', 0) or q.get('close_price', 0),
            'change': q.get('price_change_ratio_pct', 0) or q.get('change_ratio', 0),
            'shares': q['shares'],
            'market_value': (q.get('last_price', 0) or q.get('close_price', 0)) * q['shares'],
            'color': q.get('color', '#667eea')
        } for q in quotes], ensure_ascii=False)};
        
        const valuationData = {json.dumps([{
            'name': m.name,
            'pe': m.pe_ttm,
            'pb': m.pb_mrq,
            'status': m.valuation_status
        } for m in valuation_results], ensure_ascii=False)};
        
        const financialData = {json.dumps([{
            'name': ind.name,
            'roe': ind.roe,
            'revenue_growth': ind.revenue_growth,
            'score': ind.overall_score
        } for ind in financial_results], ensure_ascii=False)};
        
        // 计算统计数据
        const totalValue = holdingsData.reduce((sum, h) => sum + h.market_value, 0);
        const avgScore = financialData.reduce((sum, f) => sum + (f.score || 0), 0) / financialData.length;
        const undervaluedCount = valuationData.filter(v => v.status === 'undervalued').length;
        
        document.getElementById('total-value').textContent = totalValue.toLocaleString();
        document.getElementById('avg-score').textContent = avgScore.toFixed(0);
        document.getElementById('undervalued-count').textContent = undervaluedCount;
        
        // 1. 市值分布饼图
        new Chart(document.getElementById('marketValueChart'), {{
            type: 'pie',
            data: {{
                labels: holdingsData.map(h => h.name),
                datasets: [{{
                    data: holdingsData.map(h => h.market_value),
                    backgroundColor: holdingsData.map(h => h.color),
                    borderWidth: 2,
                    borderColor: '#fff'
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{
                        position: 'right'
                    }},
                    tooltip: {{
                        callbacks: {{
                            label: function(context) {{
                                const value = context.raw;
                                const percentage = ((value / totalValue) * 100).toFixed(1);
                                return context.label + ': ¥' + value.toLocaleString() + ' (' + percentage + '%)';
                            }}
                        }}
                    }}
                }}
            }}
        }});
        
        // 2. 财务评分柱状图
        const sortedByScore = [...financialData].sort((a, b) => (b.score || 0) - (a.score || 0));
        new Chart(document.getElementById('scoreChart'), {{
            type: 'bar',
            data: {{
                labels: sortedByScore.map(f => f.name),
                datasets: [{{
                    label: '综合评分',
                    data: sortedByScore.map(f => f.score || 0),
                    backgroundColor: sortedByScore.map(f => {{
                        const score = f.score || 0;
                        if (score >= 40) return '#28a745';
                        if (score >= 30) return '#ffc107';
                        if (score >= 20) return '#fd7e14';
                        return '#dc3545';
                    }}),
                    borderRadius: 8
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                scales: {{
                    y: {{
                        beginAtZero: true,
                        max: 100
                    }}
                }}
            }}
        }});
        
        // 3. PE/PB 对比图
        new Chart(document.getElementById('valuationChart'), {{
            type: 'bar',
            data: {{
                labels: valuationData.map(v => v.name),
                datasets: [
                    {{
                        label: 'PE (TTM)',
                        data: valuationData.map(v => v.pe),
                        backgroundColor: '#667eea',
                        borderRadius: 5,
                        yAxisID: 'y'
                    }},
                    {{
                        label: 'PB (MRQ)',
                        data: valuationData.map(v => v.pb),
                        backgroundColor: '#764ba2',
                        borderRadius: 5,
                        yAxisID: 'y1'
                    }}
                ]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                scales: {{
                    y: {{
                        type: 'linear',
                        display: true,
                        position: 'left',
                        title: {{
                            display: true,
                            text: 'PE'
                        }}
                    }},
                    y1: {{
                        type: 'linear',
                        display: true,
                        position: 'right',
                        title: {{
                            display: true,
                            text: 'PB'
                        }},
                        grid: {{
                            drawOnChartArea: false
                        }}
                    }}
                }}
            }}
        }});
        
        // 4. ROE vs 营收增长散点图
        new Chart(document.getElementById('roeGrowthChart'), {{
            type: 'scatter',
            data: {{
                datasets: financialData.map(f => ({{
                    label: f.name,
                    data: [{{
                        x: f.revenue_growth || 0,
                        y: f.roe || 0
                    }}],
                    backgroundColor: holdingsData.find(h => h.name === f.name)?.color || '#667eea',
                    pointRadius: 12,
                    pointHoverRadius: 15
                }}))
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                scales: {{
                    x: {{
                        title: {{
                            display: true,
                            text: '营收增长率 (%)'
                        }}
                    }},
                    y: {{
                        title: {{
                            display: true,
                            text: 'ROE (%)'
                        }}
                    }}
                }},
                plugins: {{
                    tooltip: {{
                        callbacks: {{
                            label: function(context) {{
                                const dataset = context.dataset;
                                return dataset.label + ': ROE ' + context.parsed.y.toFixed(1) + '%, 增长 ' + context.parsed.x.toFixed(1) + '%';
                            }}
                        }}
                    }}
                }}
            }}
        }});
        
        // 生成明细表
        const combinedData = holdingsData.map(h => {{
            const val = valuationData.find(v => v.name === h.name) || {{}};
            const fin = financialData.find(f => f.name === h.name) || {{}};
            return {{
                ...h,
                pe: val.pe,
                pb: val.pb,
                valuation_status: val.status,
                roe: fin.roe,
                revenue_growth: fin.revenue_growth,
                financial_score: fin.score
            }};
        }}).sort((a, b) => (b.financial_score || 0) - (a.financial_score || 0));
        
        const tableBody = document.getElementById('table-body');
        combinedData.forEach((item, index) => {{
            const score = item.financial_score || 0;
            let scoreBadge = '';
            if (score >= 40) scoreBadge = '<span class="badge badge-success">🟢 ' + score.toFixed(0) + '</span>';
            else if (score >= 30) scoreBadge = '<span class="badge badge-warning">🟡 ' + score.toFixed(0) + '</span>';
            else if (score >= 20) scoreBadge = '<span class="badge badge-danger">🟠 ' + score.toFixed(0) + '</span>';
            else scoreBadge = '<span class="badge badge-danger">🔴 ' + score.toFixed(0) + '</span>';
            
            let advice = '';
            if (score >= 40 && (item.revenue_growth || 0) > 5) advice = '<span class="badge badge-success">✅ 优质持仓</span>';
            else if (score >= 30) advice = '<span class="badge badge-warning">🟡 持有观察</span>';
            else if (score >= 20) advice = '<span class="badge badge-danger">🟠 考虑减仓</span>';
            else advice = '<span class="badge badge-danger">🔴 建议减仓</span>';
            
            const row = `
                <tr>
                    <td>${{index + 1}}</td>
                    <td><strong>${{item.name}}</strong></td>
                    <td>¥${{item.price?.toFixed(2) || '--'}}</td>
                    <td>¥${{item.market_value?.toLocaleString() || '--'}}</td>
                    <td>${{item.pe?.toFixed(2) || '--'}}</td>
                    <td>${{item.pb?.toFixed(2) || '--'}}</td>
                    <td>${{item.roe?.toFixed(1) || '--'}}%</td>
                    <td>${{item.revenue_growth ? (item.revenue_growth > 0 ? '+' : '') + item.revenue_growth.toFixed(1) + '%' : '--'}}</td>
                    <td>${{scoreBadge}}</td>
                    <td>${{advice}}</td>
                </tr>
            `;
            tableBody.innerHTML += row;
        }});
    </script>
</body>
</html>
"""

# 保存 HTML
output_path = Path('docs/portfolio_visualization.html')
with open(output_path, 'w', encoding='utf-8') as f:
    f.write(html_content)

print(f"  ✅ HTML 报告已生成: {output_path}")
print(f"     文件大小: {output_path.stat().st_size:,} bytes")

print("\n" + "=" * 70)
print("✅ 可视化报告生成完成")
print("=" * 70)
