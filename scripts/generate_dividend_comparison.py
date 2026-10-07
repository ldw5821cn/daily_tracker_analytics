#!/usr/bin/env python3
"""
高股息推荐 vs 当前持仓 可视化对比
"""

import sys
import os
sys.path.insert(0, '/home/liudawei/github/daily_tracker_analytics/multi_agent')
os.chdir('/home/liudawei/github/daily_tracker_analytics')

# 数据
recommended = [
    {'name': '招商银行', 'dividend': 5.0, 'pe': 6.86, 'pb': 0.91, 'roe': 14.5, 'score': 90, 'industry': '银行'},
    {'name': '申能股份', 'dividend': 4.2, 'pe': 11.87, 'pb': 1.17, 'roe': 11.3, 'score': 80, 'industry': '电力'},
    {'name': '宁沪高速', 'dividend': 5.5, 'pe': 14.01, 'pb': 1.58, 'roe': 13.6, 'score': 80, 'industry': '高速'},
    {'name': '陕西煤业', 'dividend': 7.5, 'pe': 12.37, 'pb': 2.50, 'roe': 21.3, 'score': 80, 'industry': '煤炭'},
    {'name': '美的集团', 'dividend': 4.5, 'pe': 13.77, 'pb': 2.89, 'roe': 21.3, 'score': 80, 'industry': '家电'},
]

current = [
    {'name': '华能国际', 'dividend': 3.5, 'pe': 9.42, 'pb': 1.71, 'roe': 13.1, 'score': 29, 'industry': '电力'},
    {'name': '平安银行', 'dividend': 5.5, 'pe': 5.17, 'pb': 0.48, 'roe': 10.1, 'score': 29, 'industry': '银行'},
    {'name': '深圳能源', 'dividend': 3.0, 'pe': 12.98, 'pb': 0.89, 'roe': 4.5, 'score': 18, 'industry': '电力'},
    {'name': '兴蓉环境', 'dividend': 3.5, 'pe': 10.27, 'pb': 1.05, 'roe': 11.5, 'score': 40, 'industry': '公用'},
    {'name': '华电国际', 'dividend': 3.8, 'pe': 10.90, 'pb': 1.16, 'roe': 11.7, 'score': 27, 'industry': '电力'},
    {'name': '浙能电力', 'dividend': 4.5, 'pe': 12.68, 'pb': 0.95, 'roe': 11.1, 'score': 33, 'industry': '电力'},
    {'name': '申能股份', 'dividend': 4.2, 'pe': 11.87, 'pb': 1.17, 'roe': 11.3, 'score': 33, 'industry': '电力'},
    {'name': '工商银行', 'dividend': 6.5, 'pe': 7.89, 'pb': 0.74, 'roe': 9.9, 'score': 29, 'industry': '银行'},
]

# 生成 HTML
html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>高股息推荐 vs 当前持仓对比</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; padding: 20px; }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        .header {{ text-align: center; color: white; margin-bottom: 30px; }}
        .header h1 {{ font-size: 2.5em; margin-bottom: 10px; }}
        .header p {{ opacity: 0.9; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(600px, 1fr)); gap: 20px; margin-bottom: 20px; }}
        .card {{ background: white; border-radius: 15px; padding: 20px; box-shadow: 0 10px 40px rgba(0,0,0,0.2); }}
        .card h2 {{ font-size: 1.3em; margin-bottom: 15px; color: #333; }}
        .chart-container {{ position: relative; height: 350px; }}
        .table-container {{ overflow-x: auto; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 0.9em; }}
        th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #eee; }}
        th {{ background: #667eea; color: white; font-weight: 600; }}
        tr:hover {{ background: #f5f5f5; }}
        .badge {{ padding: 4px 12px; border-radius: 20px; font-size: 0.85em; font-weight: 600; }}
        .badge-success {{ background: #d4edda; color: #155724; }}
        .badge-warning {{ background: #fff3cd; color: #856404; }}
        .badge-danger {{ background: #f8d7da; color: #721c24; }}
        .badge-info {{ background: #d1ecf1; color: #0c5460; }}
        .summary {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin-bottom: 20px; }}
        .stat {{ background: white; padding: 20px; border-radius: 10px; text-align: center; box-shadow: 0 5px 20px rgba(0,0,0,0.1); }}
        .stat-value {{ font-size: 2em; font-weight: bold; color: #667eea; }}
        .stat-label {{ color: #666; margin-top: 5px; }}
        .footer {{ text-align: center; color: white; opacity: 0.8; margin-top: 30px; padding: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>💰 高股息推荐 vs 当前持仓</h1>
            <p>多Agent分析结论可视化 | 生成时间: {os.popen('date +"%Y-%m-%d %H:%M"').read().strip()}</p>
        </div>

        <div class="summary">
            <div class="stat">
                <div class="stat-value">5</div>
                <div class="stat-label">推荐标的</div>
            </div>
            <div class="stat">
                <div class="stat-value">8</div>
                <div class="stat-label">当前持仓</div>
            </div>
            <div class="stat">
                <div class="stat-value">3</div>
                <div class="stat-label">靠谱推荐</div>
            </div>
            <div class="stat">
                <div class="stat-value">1</div>
                <div class="stat-label">高股息陷阱</div>
            </div>
        </div>

        <div class="grid">
            <div class="card">
                <h2>📊 股息率对比</h2>
                <div class="chart-container">
                    <canvas id="dividendChart"></canvas>
                </div>
            </div>

            <div class="card">
                <h2>📈 ROE 对比</h2>
                <div class="chart-container">
                    <canvas id="roeChart"></canvas>
                </div>
            </div>

            <div class="card">
                <h2>💰 PE/PB 对比</h2>
                <div class="chart-container">
                    <canvas id="pepbChart"></canvas>
                </div>
            </div>

            <div class="card">
                <h2>⭐ 综合评分对比</h2>
                <div class="chart-container">
                    <canvas id="scoreChart"></canvas>
                </div>
            </div>
        </div>

        <div class="card">
            <h2>📋 推荐标的详情</h2>
            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th>标的</th>
                            <th>行业</th>
                            <th>股息率</th>
                            <th>PE</th>
                            <th>PB</th>
                            <th>ROE</th>
                            <th>综合评分</th>
                            <th>建议</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td><strong>招商银行</strong></td>
                            <td>银行</td>
                            <td>5.0%</td>
                            <td>6.86</td>
                            <td>0.91</td>
                            <td>14.5%</td>
                            <td>90</td>
                            <td><span class="badge badge-success">✅ 靠谱</span></td>
                        </tr>
                        <tr>
                            <td><strong>申能股份</strong></td>
                            <td>电力</td>
                            <td>4.2%</td>
                            <td>11.87</td>
                            <td>1.17</td>
                            <td>11.3%</td>
                            <td>80</td>
                            <td><span class="badge badge-success">✅ 靠谱</span></td>
                        </tr>
                        <tr>
                            <td><strong>宁沪高速</strong></td>
                            <td>高速</td>
                            <td>5.5%</td>
                            <td>14.01</td>
                            <td>1.58</td>
                            <td>13.6%</td>
                            <td>80</td>
                            <td><span class="badge badge-warning">⚠️ 谨慎</span></td>
                        </tr>
                        <tr style="background: #fff3cd;">
                            <td><strong>陕西煤业</strong></td>
                            <td>煤炭</td>
                            <td>7.5%</td>
                            <td>12.37</td>
                            <td>2.50</td>
                            <td>21.3%</td>
                            <td>80</td>
                            <td><span class="badge badge-danger">❌ 陷阱</span></td>
                        </tr>
                        <tr>
                            <td><strong>美的集团</strong></td>
                            <td>家电</td>
                            <td>4.5%</td>
                            <td>13.77</td>
                            <td>2.89</td>
                            <td>21.3%</td>
                            <td>80</td>
                            <td><span class="badge badge-warning">⚠️ 谨慎</span></td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>

        <div class="card">
            <h2>📋 当前持仓详情</h2>
            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th>标的</th>
                            <th>行业</th>
                            <th>股息率</th>
                            <th>PE</th>
                            <th>PB</th>
                            <th>ROE</th>
                            <th>综合评分</th>
                            <th>建议</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr style="background: #d4edda;">
                            <td><strong>申能股份</strong></td>
                            <td>电力</td>
                            <td>4.2%</td>
                            <td>11.87</td>
                            <td>1.17</td>
                            <td>11.3%</td>
                            <td>33</td>
                            <td><span class="badge badge-success">✅ 保留</span></td>
                        </tr>
                        <tr style="background: #d4edda;">
                            <td><strong>浙能电力</strong></td>
                            <td>电力</td>
                            <td>4.5%</td>
                            <td>12.68</td>
                            <td>0.95</td>
                            <td>11.1%</td>
                            <td>33</td>
                            <td><span class="badge badge-success">✅ 保留</span></td>
                        </tr>
                        <tr style="background: #fff3cd;">
                            <td><strong>华能国际</strong></td>
                            <td>电力</td>
                            <td>3.5%</td>
                            <td>9.42</td>
                            <td>1.71</td>
                            <td>13.1%</td>
                            <td>29</td>
                            <td><span class="badge badge-warning">⚠️ 持有</span></td>
                        </tr>
                        <tr style="background: #fff3cd;">
                            <td><strong>平安银行</strong></td>
                            <td>银行</td>
                            <td>5.5%</td>
                            <td>5.17</td>
                            <td>0.48</td>
                            <td>10.1%</td>
                            <td>29</td>
                            <td><span class="badge badge-warning">⚠️ 换招行</span></td>
                        </tr>
                        <tr style="background: #fff3cd;">
                            <td><strong>工商银行</strong></td>
                            <td>银行</td>
                            <td>6.5%</td>
                            <td>7.89</td>
                            <td>0.74</td>
                            <td>9.9%</td>
                            <td>29</td>
                            <td><span class="badge badge-warning">⚠️ 持有</span></td>
                        </tr>
                        <tr style="background: #fff3cd;">
                            <td><strong>兴蓉环境</strong></td>
                            <td>公用</td>
                            <td>3.5%</td>
                            <td>10.27</td>
                            <td>1.05</td>
                            <td>11.5%</td>
                            <td>40</td>
                            <td><span class="badge badge-warning">⚠️ 持有</span></td>
                        </tr>
                        <tr style="background: #f8d7da;">
                            <td><strong>华电国际</strong></td>
                            <td>电力</td>
                            <td>3.8%</td>
                            <td>10.90</td>
                            <td>1.16</td>
                            <td>11.7%</td>
                            <td>27</td>
                            <td><span class="badge badge-danger">🔴 减仓</span></td>
                        </tr>
                        <tr style="background: #f8d7da;">
                            <td><strong>深圳能源</strong></td>
                            <td>电力</td>
                            <td>3.0%</td>
                            <td>12.98</td>
                            <td>0.89</td>
                            <td>4.5%</td>
                            <td>18</td>
                            <td><span class="badge badge-danger">🔴 减仓</span></td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>

        <div class="footer">
            <p>LLM-native 量化系统 | 多Agent分析 | 仅供参考，不构成投资建议</p>
        </div>
    </div>

    <script>
        // 股息率对比
        new Chart(document.getElementById('dividendChart'), {{
            type: 'bar',
            data: {{
                labels: ['招商银行', '申能股份', '宁沪高速', '陕西煤业', '美的集团', '华能国际', '平安银行', '深圳能源', '兴蓉环境', '华电国际', '浙能电力', '工商银行'],
                datasets: [{{
                    label: '推荐标的',
                    data: [5.0, 4.2, 5.5, 7.5, 4.5, null, null, null, null, null, null, null],
                    backgroundColor: 'rgba(102, 126, 234, 0.8)'
                }}, {{
                    label: '当前持仓',
                    data: [null, null, null, null, null, 3.5, 5.5, 3.0, 3.5, 3.8, 4.5, 6.5],
                    backgroundColor: 'rgba(255, 99, 132, 0.8)'
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                scales: {{
                    y: {{
                        beginAtZero: true,
                        title: {{
                            display: true,
                            text: '股息率 (%)'
                        }}
                    }}
                }}
            }}
        }});

        // ROE 对比
        new Chart(document.getElementById('roeChart'), {{
            type: 'bar',
            data: {{
                labels: ['招商银行', '申能股份', '宁沪高速', '陕西煤业', '美的集团', '华能国际', '平安银行', '深圳能源', '兴蓉环境', '华电国际', '浙能电力', '工商银行'],
                datasets: [{{
                    label: '推荐标的',
                    data: [14.5, 11.3, 13.6, 21.3, 21.3, null, null, null, null, null, null, null],
                    backgroundColor: 'rgba(75, 192, 192, 0.8)'
                }}, {{
                    label: '当前持仓',
                    data: [null, null, null, null, null, 13.1, 10.1, 4.5, 11.5, 11.7, 11.1, 9.9],
                    backgroundColor: 'rgba(255, 206, 86, 0.8)'
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                scales: {{
                    y: {{
                        beginAtZero: true,
                        title: {{
                            display: true,
                            text: 'ROE (%)'
                        }}
                    }}
                }}
            }}
        }});

        // PE/PB 对比
        new Chart(document.getElementById('pepbChart'), {{
            type: 'bar',
            data: {{
                labels: ['招商银行', '申能股份', '宁沪高速', '陕西煤业', '美的集团', '华能国际', '平安银行', '深圳能源', '兴蓉环境', '华电国际', '浙能电力', '工商银行'],
                datasets: [{{
                    label: 'PE',
                    data: [6.86, 11.87, 14.01, 12.37, 13.77, 9.42, 5.17, 12.98, 10.27, 10.90, 12.68, 7.89],
                    backgroundColor: 'rgba(54, 162, 235, 0.8)'
                }}, {{
                    label: 'PB',
                    data: [0.91, 1.17, 1.58, 2.50, 2.89, 1.71, 0.48, 0.89, 1.05, 1.16, 0.95, 0.74],
                    backgroundColor: 'rgba(153, 102, 255, 0.8)'
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                scales: {{
                    y: {{
                        beginAtZero: true
                    }}
                }}
            }}
        }});

        // 综合评分对比
        new Chart(document.getElementById('scoreChart'), {{
            type: 'radar',
            data: {{
                labels: ['招商银行', '申能股份', '宁沪高速', '陕西煤业', '美的集团'],
                datasets: [{{
                    label: '股息率',
                    data: [5.0, 4.2, 5.5, 7.5, 4.5],
                    backgroundColor: 'rgba(102, 126, 234, 0.2)',
                    borderColor: 'rgba(102, 126, 234, 1)',
                    borderWidth: 2
                }}, {{
                    label: 'ROE',
                    data: [14.5, 11.3, 13.6, 21.3, 21.3],
                    backgroundColor: 'rgba(255, 99, 132, 0.2)',
                    borderColor: 'rgba(255, 99, 132, 1)',
                    borderWidth: 2
                }}, {{
                    label: '综合评分',
                    data: [90, 80, 80, 80, 80],
                    backgroundColor: 'rgba(75, 192, 192, 0.2)',
                    borderColor: 'rgba(75, 192, 192, 1)',
                    borderWidth: 2
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                scales: {{
                    r: {{
                        beginAtZero: true
                    }}
                }}
            }}
        }});
    </script>
</body>
</html>
'''

# 保存
output_path = 'docs/high_dividend_comparison.html'
with open(output_path, 'w', encoding='utf-8') as f:
    f.write(html)

print("=" * 70)
print("✅ 高股息推荐 vs 当前持仓对比已生成")
print("=" * 70)
print(f"\n报告路径: {output_path}")
print(f"报告大小: {os.path.getsize(output_path):,} bytes")
