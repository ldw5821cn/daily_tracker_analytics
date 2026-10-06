#!/usr/bin/env python3
"""
生成 Kronos 预测可视化图表
输出: docs/kronos_visualization.html
"""

import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
REPO = BASE.parent
sys.path.insert(0, str(REPO))

# 读取预测数据
with open(REPO / 'docs' / 'kronos_predictions.json') as f:
    data = json.load(f)

predictions = data['predictions']

# 生成 HTML
html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Kronos AI 预测可视化</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0a0e27; color: #fff; min-height: 100vh; padding: 20px; }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        .header {{ text-align: center; margin-bottom: 30px; padding: 20px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border-radius: 15px; }}
        .header h1 {{ font-size: 2em; margin-bottom: 10px; }}
        .header p {{ opacity: 0.9; font-size: 1.1em; }}
        .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 30px; }}
        .stat-card {{ background: rgba(255,255,255,0.1); padding: 20px; border-radius: 12px; text-align: center; backdrop-filter: blur(10px); border: 1px solid rgba(255,255,255,0.2); }}
        .stat-value {{ font-size: 2.5em; font-weight: bold; margin-bottom: 5px; }}
        .stat-label {{ opacity: 0.8; font-size: 0.9em; }}
        .chart-container {{ background: rgba(255,255,255,0.05); padding: 20px; border-radius: 15px; margin-bottom: 20px; border: 1px solid rgba(255,255,255,0.1); }}
        .chart-title {{ font-size: 1.3em; margin-bottom: 15px; color: #fff; }}
        .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
        @media (max-width: 968px) {{ .grid {{ grid-template-columns: 1fr; }} }}
        .stock-card {{ background: rgba(255,255,255,0.08); padding: 15px; border-radius: 12px; margin-bottom: 10px; border-left: 4px solid #667eea; }}
        .stock-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }}
        .stock-name {{ font-weight: bold; font-size: 1.1em; }}
        .stock-signal {{ padding: 5px 12px; border-radius: 20px; font-size: 0.85em; font-weight: bold; }}
        .signal-hold {{ background: #ffc107; color: #000; }}
        .signal-buy {{ background: #28a745; color: #fff; }}
        .signal-sell {{ background: #dc3545; color: #fff; }}
        .stock-metrics {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; font-size: 0.9em; }}
        .metric {{ text-align: center; }}
        .metric-value {{ font-weight: bold; font-size: 1.2em; }}
        .metric-label {{ opacity: 0.7; font-size: 0.8em; }}
        .positive {{ color: #28a745; }}
        .negative {{ color: #dc3545; }}
        .footer {{ text-align: center; margin-top: 30px; opacity: 0.6; font-size: 0.9em; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🤖 Kronos AI 预测可视化</h1>
            <p>生成时间: {data['date']} | 基于 Kronos-mini 金融 K 线基础模型</p>
        </div>

        <div class="stats">
            <div class="stat-card">
                <div class="stat-value">{data['summary']['total']}</div>
                <div class="stat-label">预测股票数</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" style="color: #ffc107;">{data['summary']['hold']}</div>
                <div class="stat-label">持有信号</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" style="color: #28a745;">{data['summary']['buy']}</div>
                <div class="stat-label">买入信号</div>
            </div>
            <div class="stat-card">
                <div class="stat-value" style="color: #dc3545;">{data['summary']['sell']}</div>
                <div class="stat-label">卖出信号</div>
            </div>
        </div>

        <div class="grid">
            <div class="chart-container">
                <div class="chart-title">📊 预期收益对比</div>
                <canvas id="returnChart"></canvas>
            </div>
            <div class="chart-container">
                <div class="chart-title">📈 上涨概率分布</div>
                <canvas id="probChart"></canvas>
            </div>
        </div>

        <div class="chart-container">
            <div class="chart-title">📋 个股预测详情</div>
"""

for pred in predictions:
    signal_class = f"signal-{pred['signal'].lower()}"
    ret_class = "positive" if pred['expected_return'] > 0 else "negative"
    prob_class = "positive" if pred['bull_prob'] > 0.5 else "negative"
    
    html += f"""
            <div class="stock-card">
                <div class="stock-header">
                    <span class="stock-name">{pred['name']} ({pred['symbol']})</span>
                    <span class="stock-signal {signal_class}">{pred['signal']}</span>
                </div>
                <div class="stock-metrics">
                    <div class="metric">
                        <div class="metric-value">${pred['current_price']:.2f}</div>
                        <div class="metric-label">现价</div>
                    </div>
                    <div class="metric">
                        <div class="metric-value {ret_class}">{pred['expected_return']*100:+.2f}%</div>
                        <div class="metric-label">5日预期收益</div>
                    </div>
                    <div class="metric">
                        <div class="metric-value {prob_class}">{pred['bull_prob']*100:.0f}%</div>
                        <div class="metric-label">上涨概率</div>
                    </div>
                </div>
            </div>
"""

html += f"""
        </div>

        <div class="footer">
            <p>Powered by Kronos Foundation Model | 仅供研究参考，不构成投资建议</p>
            <p>LLM-native 量化系统 v1.0</p>
        </div>
    </div>

    <script>
        // 预期收益对比图
        const returnCtx = document.getElementById('returnChart').getContext('2d');
        new Chart(returnCtx, {{
            type: 'bar',
            data: {{
                labels: {[f"'{p['name']}'" for p in predictions]},
                datasets: [{{
                    label: '5日预期收益 (%)',
                    data: {[p['expected_return']*100 for p in predictions]},
                    backgroundColor: {[('"rgba(40, 167, 69, 0.8)"' if p['expected_return'] > 0 else '"rgba(220, 53, 69, 0.8)"') for p in predictions]},
                    borderColor: {[('"rgba(40, 167, 69, 1)"' if p['expected_return'] > 0 else '"rgba(220, 53, 69, 1)"') for p in predictions]},
                    borderWidth: 2
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: true,
                plugins: {{
                    legend: {{ display: false }}
                }},
                scales: {{
                    y: {{
                        beginAtZero: true,
                        ticks: {{ color: '#fff' }},
                        grid: {{ color: 'rgba(255,255,255,0.1)' }}
                    }},
                    x: {{
                        ticks: {{ color: '#fff' }},
                        grid: {{ color: 'rgba(255,255,255,0.1)' }}
                    }}
                }}
            }}
        }});

        // 上涨概率分布图
        const probCtx = document.getElementById('probChart').getContext('2d');
        new Chart(probCtx, {{
            type: 'doughnut',
            data: {{
                labels: ['看涨 (>60%)', '震荡 (40-60%)', '看跌 (<40%)'],
                datasets: [{{
                    data: [
                        {len([p for p in predictions if p['bull_prob'] > 0.6])},
                        {len([p for p in predictions if 0.4 <= p['bull_prob'] <= 0.6])},
                        {len([p for p in predictions if p['bull_prob'] < 0.4])}
                    ],
                    backgroundColor: [
                        'rgba(40, 167, 69, 0.8)',
                        'rgba(255, 193, 7, 0.8)',
                        'rgba(220, 53, 69, 0.8)'
                    ],
                    borderColor: [
                        'rgba(40, 167, 69, 1)',
                        'rgba(255, 193, 7, 1)',
                        'rgba(220, 53, 69, 1)'
                    ],
                    borderWidth: 2
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: true,
                plugins: {{
                    legend: {{
                        position: 'bottom',
                        labels: {{ color: '#fff', padding: 15 }}
                    }}
                }}
            }}
        }});
    </script>
</body>
</html>
"""

# 保存
output_path = REPO / 'docs' / 'kronos_visualization.html'
with open(output_path, 'w') as f:
    f.write(html)

print(f"✅ 可视化报告已生成: {output_path}")
print(f"   文件大小: {len(html)} bytes")
