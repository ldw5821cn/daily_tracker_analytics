#!/usr/bin/env python3
"""
持仓股票趋势分析
生成价格走势 + 技术指标可视化
"""

import sys
import os
sys.path.insert(0, '/home/liudawei/github/daily_tracker_analytics/multi_agent')
os.chdir('/home/liudawei/github/daily_tracker_analytics')

import json
from pathlib import Path

# 持仓
holdings = [
    {'symbol': '600011', 'name': '华能国际', 'shares': 600},
    {'symbol': '000001', 'name': '平安银行', 'shares': 100},
    {'symbol': '000027', 'name': '深圳能源', 'shares': 200},
    {'symbol': '000598', 'name': '兴蓉环境', 'shares': 200},
    {'symbol': '600027', 'name': '华电国际', 'shares': 400},
    {'symbol': '600023', 'name': '浙能电力', 'shares': 500},
    {'symbol': '600642', 'name': '申能股份', 'shares': 200},
    {'symbol': '601398', 'name': '工商银行', 'shares': 300},
]

# 获取历史数据
from hithink_client import HiThinkFinanceClient
client = HiThinkFinanceClient()

all_data = {}
print("获取历史数据...")
for h in holdings:
    try:
        thscode = client.to_thscode(h['symbol'])
        # 获取 60 天历史数据（使用字符串日期格式）
        import time
        end_date = time.strftime('%Y-%m-%d')
        start_date = time.strftime('%Y-%m-%d', time.localtime(time.time() - 60 * 24 * 3600))
        data = client.get_kline(thscode, start_date, end_date, interval='1d')
        if data and len(data) > 0:
            # 处理时间格式
            dates = []
            closes = []
            volumes = []
            for d in data:
                # 时间可能是毫秒时间戳
                t = d.get('time', d.get('date', ''))
                if isinstance(t, int):
                    # 毫秒时间戳转日期
                    import datetime
                    dt = datetime.datetime.fromtimestamp(t / 1000)
                    dates.append(dt.strftime('%m-%d'))
                else:
                    dates.append(str(t)[5:10] if len(str(t)) >= 10 else str(t))
                closes.append(d.get('close_price', d.get('close', 0)))
                volumes.append(d.get('volume', 0))
            
            all_data[h['name']] = {
                'symbol': h['symbol'],
                'dates': dates,
                'closes': closes,
                'volumes': volumes,
            }
            print(f"  ✅ {h['name']}: {len(data)} 天")
        else:
            print(f"  ⚠️ {h['name']}: 无数据")
    except Exception as e:
        print(f"  ❌ {h['name']}: {e}")

# 生成 HTML
html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>持仓股票趋势分析</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; padding: 20px; }}
        .container {{ max-width: 1600px; margin: 0 auto; }}
        .header {{ text-align: center; color: white; margin-bottom: 30px; }}
        .header h1 {{ font-size: 2.5em; margin-bottom: 10px; }}
        .header p {{ opacity: 0.9; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(700px, 1fr)); gap: 20px; margin-bottom: 20px; }}
        .card {{ background: white; border-radius: 15px; padding: 20px; box-shadow: 0 10px 40px rgba(0,0,0,0.2); }}
        .card h2 {{ font-size: 1.2em; margin-bottom: 15px; color: #333; display: flex; justify-content: space-between; align-items: center; }}
        .badge {{ padding: 4px 12px; border-radius: 20px; font-size: 0.8em; font-weight: 600; }}
        .badge-up {{ background: #d4edda; color: #155724; }}
        .badge-down {{ background: #f8d7da; color: #721c24; }}
        .badge-flat {{ background: #fff3cd; color: #856404; }}
        .chart-container {{ position: relative; height: 300px; }}
        .stats {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin-bottom: 20px; }}
        .stat {{ background: white; padding: 20px; border-radius: 10px; text-align: center; box-shadow: 0 5px 20px rgba(0,0,0,0.1); }}
        .stat-value {{ font-size: 1.8em; font-weight: bold; }}
        .stat-label {{ color: #666; margin-top: 5px; font-size: 0.9em; }}
        .positive {{ color: #dc3545; }}
        .negative {{ color: #28a745; }}
        .footer {{ text-align: center; color: white; opacity: 0.8; margin-top: 30px; padding: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📈 持仓股票趋势分析</h1>
            <p>近60日价格走势 | 生成时间: {os.popen('date +"%Y-%m-%d %H:%M"').read().strip()}</p>
        </div>

        <div class="stats">
            <div class="stat">
                <div class="stat-value">{len(all_data)}</div>
                <div class="stat-label">持仓标的</div>
            </div>
            <div class="stat">
                <div class="stat-value positive">--</div>
                <div class="stat-label">平均涨幅</div>
            </div>
            <div class="stat">
                <div class="stat-value">--</div>
                <div class="stat-label">最强标的</div>
            </div>
            <div class="stat">
                <div class="stat-value">--</div>
                <div class="stat-label">最弱标的</div>
            </div>
        </div>

        <div class="grid">
'''

# 为每只股票生成图表
colors = [
    'rgba(102, 126, 234, 1)', 'rgba(255, 99, 132, 1)', 'rgba(75, 192, 192, 1)',
    'rgba(255, 206, 86, 1)', 'rgba(153, 102, 255, 1)', 'rgba(255, 159, 64, 1)',
    'rgba(54, 162, 235, 1)', 'rgba(199, 199, 199, 1)'
]

for i, (name, data) in enumerate(all_data.items()):
    color = colors[i % len(colors)]
    dates_json = json.dumps(data['dates'])
    closes_json = json.dumps(data['closes'])
    
    # 计算涨跌幅
    if len(data['closes']) >= 2 and data['closes'][0] > 0:
        change = (data['closes'][-1] - data['closes'][0]) / data['closes'][0] * 100
        change_str = f"+{change:.1f}%" if change >= 0 else f"{change:.1f}%"
        badge_class = 'badge-up' if change >= 0 else 'badge-down'
    else:
        change_str = '--'
        badge_class = 'badge-flat'
    
    html += f'''
            <div class="card">
                <h2>
                    <span>{name} ({data['symbol']})</span>
                    <span class="badge {badge_class}">{change_str}</span>
                </h2>
                <div class="chart-container">
                    <canvas id="chart_{i}"></canvas>
                </div>
            </div>
    '''

html += '''
        </div>

        <div class="footer">
            <p>LLM-native 量化系统 | HiThink Financial-API | 仅供参考，不构成投资建议</p>
        </div>
    </div>

    <script>
'''

# 生成 Chart.js 配置
for i, (name, data) in enumerate(all_data.items()):
    color = colors[i % len(colors)]
    dates_json = json.dumps(data['dates'])
    closes_json = json.dumps(data['closes'])
    
    html += f'''
        new Chart(document.getElementById('chart_{i}'), {{
            type: 'line',
            data: {{
                labels: {dates_json},
                datasets: [{{
                    label: '{name}',
                    data: {closes_json},
                    borderColor: '{color}',
                    backgroundColor: '{color.replace("1)", "0.1)")}',
                    borderWidth: 2,
                    fill: true,
                    tension: 0.3,
                    pointRadius: 0,
                    pointHoverRadius: 5
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                interaction: {{
                    mode: 'index',
                    intersect: false
                }},
                plugins: {{
                    legend: {{
                        display: false
                    }},
                    tooltip: {{
                        callbacks: {{
                            label: function(context) {{
                                return '{name}: ¥' + context.parsed.y.toFixed(2);
                            }}
                        }}
                    }}
                }},
                scales: {{
                    x: {{
                        display: true,
                        ticks: {{
                            maxTicksLimit: 8,
                            font: {{
                                size: 10
                            }}
                        }}
                    }},
                    y: {{
                        display: true,
                        ticks: {{
                            callback: function(value) {{
                                return '¥' + value.toFixed(2);
                            }}
                        }}
                    }}
                }}
            }}
        }});
    '''

html += '''
    </script>
</body>
</html>
'''

# 保存
output_path = 'docs/holdings_trend.html'
with open(output_path, 'w', encoding='utf-8') as f:
    f.write(html)

print("\n" + "=" * 70)
print("✅ 持仓趋势分析已生成")
print("=" * 70)
print(f"\n报告路径: {output_path}")
print(f"报告大小: {os.path.getsize(output_path):,} bytes")
print(f"包含标的: {len(all_data)} 只")
