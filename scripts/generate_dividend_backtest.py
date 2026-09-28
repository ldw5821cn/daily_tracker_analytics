#!/usr/bin/env python3
"""
生成高股息回测可视化图表
输出: docs/dividend_backtest.html
"""

import urllib.request
import json
import math
from pathlib import Path

BASE = Path(__file__).resolve().parent
REPO = BASE.parent
DOCS = REPO / 'docs'

# 候选标的
CANDIDATES = {
    'sz000333': {'name': '美的集团', 'code': '000333', 'div_yield': 4.0, 'color': '#FF6B6B'},
    'sz000429': {'name': '粤高速A', 'code': '000429', 'div_yield': 4.4, 'color': '#4ECDC4'},
    'sz000598': {'name': '兴蓉环境', 'code': '000598', 'div_yield': 5.0, 'color': '#45B7D1'},
    'sz000001': {'name': '平安银行', 'code': '000001', 'div_yield': 6.3, 'color': '#96CEB4'},
    'sz000651': {'name': '格力电器', 'code': '000651', 'div_yield': 6.2, 'color': '#FECA57'},
    'sz000895': {'name': '双汇发展', 'code': '000895', 'div_yield': 7.5, 'color': '#9B59B6'},
    'sz003816': {'name': '中国广核', 'code': '003816', 'div_yield': 4.7, 'color': '#E67E22'},
    'sz159905': {'name': '深红利ETF', 'code': '159905', 'div_yield': 4.5, 'color': '#95A5A6'},
}

def fetch_kline(code, days=750):
    """拉取日K线"""
    url = f'https://web.ifzq.gtimg.cn/appstock/app/kline/kline?param={code},day,,,{days}'
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        
        kline = data['data'][code].get('day', [])
        if not kline:
            return None
        
        return [{'date': k[0], 'close': float(k[2])} for k in kline]
    except:
        return None

def calculate_metrics(closes):
    """计算回测指标"""
    if len(closes) < 2:
        return None
    
    # 归一化净值（从1开始）
    nav = [c / closes[0] for c in closes]
    
    # 年化收益
    total_return = nav[-1] - 1
    years = len(nav) / 250
    annual_return = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0
    
    # 最大回撤
    max_dd = 0
    peak = nav[0]
    dd_curve = []
    for n in nav:
        if n > peak:
            peak = n
        dd = (peak - n) / peak
        dd_curve.append(dd)
        if dd > max_dd:
            max_dd = dd
    
    # 波动率
    returns = [(nav[i] - nav[i-1]) / nav[i-1] for i in range(1, len(nav))]
    if len(returns) > 1:
        mean_r = sum(returns) / len(returns)
        variance = sum((r - mean_r) ** 2 for r in returns) / len(returns)
        annual_vol = math.sqrt(variance) * math.sqrt(252)
    else:
        annual_vol = 0
    
    # 夏普
    rf = 0.02
    sharpe = (annual_return - rf) / annual_vol if annual_vol > 0 else 0
    
    # 卡玛
    calmar = annual_return / max_dd if max_dd > 0 else 0
    
    return {
        'nav': nav,
        'dd_curve': dd_curve,
        'annual_return': annual_return * 100,
        'max_dd': max_dd * 100,
        'sharpe': sharpe,
        'calmar': calmar,
    }

def generate_html(all_data):
    """生成HTML报告"""
    
    # 准备图表数据
    chart_data = {}
    for code, data in all_data.items():
        info = CANDIDATES[code]
        chart_data[code] = {
            'name': info['name'],
            'code': info['code'],
            'color': info['color'],
            'div_yield': info['div_yield'],
            'nav': data['nav'][::5],  # 每5个点采样，减少数据量
            'dd': data['dd_curve'][::5],
            'dates': [d['date'] for d in data['raw']][::5],
            'metrics': {
                'annual': data['annual_return'],
                'max_dd': data['max_dd'],
                'sharpe': data['sharpe'],
                'calmar': data['calmar'],
            }
        }
    
    # 按卡玛排序
    sorted_codes = sorted(chart_data.keys(), key=lambda x: chart_data[x]['metrics']['calmar'], reverse=True)
    
    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>深市高股息回测报告</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif; background: #0d1117; color: #c9d1d9; padding: 20px; }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        h1 {{ text-align: center; color: #58a6ff; margin-bottom: 10px; font-size: 28px; }}
        .subtitle {{ text-align: center; color: #8b949e; margin-bottom: 30px; font-size: 14px; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); gap: 20px; margin-bottom: 30px; }}
        .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 20px; }}
        .card h2 {{ color: #58a6ff; font-size: 18px; margin-bottom: 15px; }}
        .chart-container {{ position: relative; height: 300px; margin-bottom: 20px; }}
        .metrics-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
        .metrics-table th, .metrics-table td {{ padding: 10px; text-align: center; border-bottom: 1px solid #30363d; }}
        .metrics-table th {{ background: #1c2128; color: #58a6ff; font-weight: 600; }}
        .metrics-table tr:hover {{ background: #1c2128; }}
        .highlight {{ color: #3fb950; font-weight: bold; }}
        .warning {{ color: #d29922; }}
        .danger {{ color: #f85149; }}
        .rank {{ display: inline-block; width: 24px; height: 24px; line-height: 24px; border-radius: 50%; background: #58a6ff; color: white; font-weight: bold; }}
        .rank-1 {{ background: #FFD700; }}
        .rank-2 {{ background: #C0C0C0; }}
        .rank-3 {{ background: #CD7F32; }}
        .recommend-box {{ background: #1c2128; border-left: 4px solid #3fb950; padding: 15px; margin: 20px 0; }}
        .recommend-box h3 {{ color: #3fb950; margin-bottom: 10px; }}
        .avoid-box {{ background: #2d1515; border-left: 4px solid #f85149; padding: 15px; margin: 20px 0; }}
        .avoid-box h3 {{ color: #f85149; margin-bottom: 10px; }}
        .footer {{ text-align: center; color: #8b949e; margin-top: 30px; font-size: 12px; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 深市高股息低风险回测报告</h1>
        <div class="subtitle">基于3年历史数据（750交易日）| 生成时间: 2026-09-28</div>
        
        <div class="grid">
            <div class="card" style="grid-column: 1 / -1;">
                <h2>🏆 综合排名（卡玛比率排序）</h2>
                <table class="metrics-table">
                    <thead>
                        <tr>
                            <th>排名</th>
                            <th>标的</th>
                            <th>年化收益</th>
                            <th>最大回撤</th>
                            <th>卡玛比率</th>
                            <th>夏普比率</th>
                            <th>股息率</th>
                            <th>推荐</th>
                        </tr>
                    </thead>
                    <tbody>
'''
    
    for i, code in enumerate(sorted_codes, 1):
        d = chart_data[code]
        m = d['metrics']
        rank_class = f'rank rank-{i}' if i <= 3 else 'rank'
        
        # 推荐等级
        if m['calmar'] > 0.5 and d['div_yield'] >= 4:
            rec = '<span class="highlight">🥇 核心</span>'
        elif m['calmar'] > 0.3 and d['div_yield'] >= 4:
            rec = '<span class="highlight">🥈 可选</span>'
        elif m['calmar'] > 0:
            rec = '<span class="warning">🥉 谨慎</span>'
        else:
            rec = '<span class="danger">❌ 回避</span>'
        
        html += f'''                        <tr>
                            <td><span class="{rank_class}">{i}</span></td>
                            <td><strong>{d['name']}</strong><br><small>{d['code']}</small></td>
                            <td class="{'highlight' if m['annual'] > 0 else 'danger'}">{m['annual']:+.1f}%</td>
                            <td>{m['max_dd']:.1f}%</td>
                            <td class="highlight">{m['calmar']:.2f}</td>
                            <td>{m['sharpe']:.2f}</td>
                            <td>{d['div_yield']:.1f}%</td>
                            <td>{rec}</td>
                        </tr>
'''
    
    html += '''                    </tbody>
                </table>
            </div>
        </div>
        
        <div class="grid">
            <div class="card" style="grid-column: 1 / -1;">
                <h2>📈 净值曲线对比（3年）</h2>
                <div class="chart-container" style="height: 400px;">
                    <canvas id="navChart"></canvas>
                </div>
            </div>
        </div>
        
        <div class="grid">
            <div class="card" style="grid-column: 1 / -1;">
                <h2>📉 回撤曲线对比</h2>
                <div class="chart-container" style="height: 300px;">
                    <canvas id="ddChart"></canvas>
                </div>
            </div>
        </div>
        
        <div class="grid">
            <div class="card">
                <h2>✅ 核心推荐</h2>
                <div class="recommend-box">
                    <h3>🥇 美的集团 (000333)</h3>
                    <p>卡玛比率 <strong>0.83</strong> 最高，收益/回撤比最优</p>
                    <p>3年年化 <strong>+15.1%</strong>，最大回撤仅 18.3%</p>
                    <p>股息率 4.0%，家电龙头，分红逐年提升</p>
                    <p><strong>适合:</strong> 长期核心仓位（40-50%）</p>
                </div>
                <div class="recommend-box">
                    <h3>🥈 粤高速A (000429)</h3>
                    <p>3年年化 <strong>+16.8%</strong> 最高，股息率 4.4%</p>
                    <p>高速公路现金流稳定，但回撤稍大（27.4%）</p>
                    <p><strong>适合:</strong> 增强收益（20-30%）</p>
                </div>
                <div class="recommend-box">
                    <h3>🥉 兴蓉环境 (000598)</h3>
                    <p>水务公用，业绩极稳，股息率 <strong>5.0%</strong></p>
                    <p>年化 +7.7%，防御性强，波动小</p>
                    <p><strong>适合:</strong> 防御底仓（20-30%）</p>
                </div>
            </div>
            
            <div class="card">
                <h2>❌ 高股息陷阱（回避）</h2>
                <div class="avoid-box">
                    <h3>双汇发展 (000895)</h3>
                    <p>股息率 7.5% 最高，但3年年化 <strong>-2.0%</strong></p>
                    <p>股价下跌抵消股息，实际亏损</p>
                    <p>高股息 ≠ 好投资 ❌</p>
                </div>
                <div class="avoid-box">
                    <h3>平安银行 (000001)</h3>
                    <p>股息率 6.3%，但3年年化仅 <strong>+0.3%</strong></p>
                    <p>银行估值长期低迷，收益跑不赢通胀</p>
                    <p>除非特别看好银行反转 ❌</p>
                </div>
                <div class="avoid-box">
                    <h3>深红利ETF (159905)</h3>
                    <p>股息率 4.5%，但3年年化 <strong>-6.7%</strong></p>
                    <p>ETF管理费+追踪误差拖累，不如直接买个股</p>
                    <p>你当前持仓，建议调仓 ❌</p>
                </div>
            </div>
        </div>
        
        <div class="grid">
            <div class="card" style="grid-column: 1 / -1;">
                <h2>📋 5万资金配置建议</h2>
                <table class="metrics-table">
                    <thead>
                        <tr>
                            <th>方案</th>
                            <th>配置</th>
                            <th>预期年化</th>
                            <th>股息率</th>
                            <th>风险等级</th>
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td><strong>A 均衡</strong></td>
                            <td>美的2万 + 兴蓉1.5万 + 粤高速1万 + 现金0.5万</td>
                            <td class="highlight">8-12%</td>
                            <td>4.5%</td>
                            <td>🟡 中等</td>
                        </tr>
                        <tr>
                            <td><strong>B 保守</strong></td>
                            <td>兴蓉2万 + 美的1.5万 + 广核1万 + 现金0.5万</td>
                            <td>6-9%</td>
                            <td class="highlight">4.8%</td>
                            <td>🟢 低</td>
                        </tr>
                        <tr>
                            <td><strong>C 积极</strong></td>
                            <td>美的2.5万 + 粤高速1.5万 + 兴蓉0.5万 + 现金0.5万</td>
                            <td class="highlight">10-14%</td>
                            <td>4.2%</td>
                            <td>🔴 中高</td>
                        </tr>
                    </tbody>
                </table>
                <p style="margin-top: 15px; color: #8b949e;">
                    💡 推荐方案A：美的做核心，兴蓉防御，粤高速增强，保留现金灵活补仓
                </p>
            </div>
        </div>
        
        <div class="footer">
            <p>⚠️ 免责声明：回测基于历史数据，未来表现不保证。投资有风险，入市需谨慎。</p>
            <p>数据来源：腾讯财经 | 生成：daily_tracker_analytics</p>
        </div>
    </div>
    
    <script>
        // 净值曲线
        const navCtx = document.getElementById('navChart').getContext('2d');
        new Chart(navCtx, {
            type: 'line',
            data: {
                labels: ''' + json.dumps(chart_data[sorted_codes[0]]['dates']) + ''',
                datasets: [
'''
    
    for code in sorted_codes[:5]:  # 只画前5名
        d = chart_data[code]
        html += f'''                    {{
                        label: '{d["name"]} (卡玛{d["metrics"]["calmar"]:.2f})',
                        data: {json.dumps(d['nav'])},
                        borderColor: '{d["color"]}',
                        backgroundColor: '{d["color"]}20',
                        borderWidth: 2,
                        pointRadius: 0,
                        tension: 0.1
                    }},
'''
    
    html += '''                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: {
                    mode: 'index',
                    intersect: false
                },
                plugins: {
                    legend: {
                        position: 'top',
                        labels: { color: '#c9d1d9' }
                    },
                    tooltip: {
                        callbacks: {
                            label: function(ctx) {
                                return ctx.dataset.label + ': ' + ctx.parsed.y.toFixed(3);
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        ticks: { color: '#8b949e', maxTicksLimit: 10 },
                        grid: { color: '#30363d' }
                    },
                    y: {
                        ticks: { color: '#8b949e' },
                        grid: { color: '#30363d' },
                        title: { display: true, text: '净值', color: '#8b949e' }
                    }
                }
            }
        });
        
        // 回撤曲线
        const ddCtx = document.getElementById('ddChart').getContext('2d');
        new Chart(ddCtx, {
            type: 'line',
            data: {
                labels: ''' + json.dumps(chart_data[sorted_codes[0]]['dates']) + ''',
                datasets: [
'''
    
    for code in sorted_codes[:5]:
        d = chart_data[code]
        html += f'''                    {{
                        label: '{d["name"]}',
                        data: {json.dumps([x*100 for x in d['dd']])},
                        borderColor: '{d["color"]}',
                        backgroundColor: 'transparent',
                        borderWidth: 2,
                        pointRadius: 0,
                        tension: 0.1,
                        fill: true,
                        backgroundColor: '{d["color"]}10'
                    }},
'''
    
    html += '''                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: {
                    mode: 'index',
                    intersect: false
                },
                plugins: {
                    legend: {
                        position: 'top',
                        labels: { color: '#c9d1d9' }
                    }
                },
                scales: {
                    x: {
                        ticks: { color: '#8b949e', maxTicksLimit: 10 },
                        grid: { color: '#30363d' }
                    },
                    y: {
                        ticks: { color: '#8b949e', callback: function(v) { return v + '%'; } },
                        grid: { color: '#30363d' },
                        title: { display: true, text: '回撤 %', color: '#8b949e' },
                        reverse: true
                    }
                }
            }
        });
    </script>
</body>
</html>'''
    
    return html

def main():
    print("拉取历史数据...")
    
    all_data = {}
    for code in CANDIDATES:
        print(f"  {CANDIDATES[code]['name']}...", end=' ')
        raw = fetch_kline(code, 750)
        if not raw:
            print("❌")
            continue
        
        closes = [d['close'] for d in raw]
        metrics = calculate_metrics(closes)
        if metrics:
            all_data[code] = {
                'raw': raw,
                **metrics
            }
            print(f"✅ {metrics['annual_return']:+.1f}%")
    
    print(f"\n生成HTML报告...")
    html = generate_html(all_data)
    
    output_path = DOCS / 'dividend_backtest.html'
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    
    print(f"✅ 已保存: {output_path}")
    print(f"   大小: {len(html)/1024:.1f} KB")
    
    return output_path

if __name__ == '__main__':
    main()
