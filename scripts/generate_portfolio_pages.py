#!/usr/bin/env python3
"""
生成雪球组合ZH3650487的持仓+收益展示页
每日盘后由 run_pipeline.py 调用的 Pages 生成脚本

用法：
  cd /home/liudawei/github/daily_tracker_analytics
  python3 scripts/generate_portfolio_pages.py
"""

import sys
import os
import json
import urllib.request
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'multi_agent'))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join(REPO_ROOT, "docs")
os.makedirs(DOCS_DIR, exist_ok=True)

# 雪球配置
COOKIES_FILE = os.path.join(REPO_ROOT, 'multi_agent', 'data', 'xueqiu_config.json')
PORTFOLIO_CODE = 'ZH3650487'
INITIAL_CAPITAL = 1000000  #雪球组合初始100万


def fetch_portfolio():
    """通过easytrader获取组合数据"""
    from xueqiu_rebalancer import get_user
    user = get_user()
    s = user.s
    
    result = {}
    
    # 1. 组合净值 / 收益
    q = s.get(f'https://xueqiu.com/cubes/quote.json?code={PORTFOLIO_CODE}').json()
    nv = q.get(PORTFOLIO_CODE, {})
    result['name'] = nv.get('name', PORTFOLIO_CODE)
    result['net_value'] = float(nv.get('net_value', 1.0))
    result['daily_gain'] = float(nv.get('daily_gain', 0))
    result['total_gain'] = float(nv.get('total_gain', 0))
    result['annualized_gain'] = float(nv.get('annualized_gain', 0))
    result['monthly_gain'] = float(nv.get('monthly_gain', 0))
    
    # 2. 当前持仓
    pos = user.position
    result['holdings'] = []
    
    batch_q = []
    for p in pos:
        code = p['stock_code']
        prefix = 'sh' if code.startswith('SH') else 'sz'
        batch_q.append(f'{prefix}{code[2:]}')
    
    # 批量查行情
    if batch_q:
        url = 'http://qt.gtimg.cn/q=' + ','.join(batch_q)
        req = urllib.request.Request(url)
        req.add_header('Referer', 'https://xueqiu.com')
        resp = urllib.request.urlopen(req, timeout=10)
        text = resp.read().decode('gbk')
        
        quotes = {}
        for line in text.strip().split(';'):
            if not line.strip(): continue
            try:
                data = line.split('~')
                if len(data) > 45:
                    sc = data[2]
                    quotes[sc] = {
                        'price': float(data[3]) if data[3] else 0,
                        'chg_pct': float(data[32]) if data[32] else 0,
                        'high': float(data[33]) if data[33] else 0,
                        'low': float(data[34]) if data[34] else 0,
                        'open': float(data[5]) if data[5] else 0,
                        'turnover': float(data[37]) if data[37] else 0,
                        'pe': float(data[39]) if data[39] else 0,
                        'mkt_cap': float(data[45]) if data[45] else 0,
                    }
            except:
                pass
        
        for p in pos:
            code = p['stock_code']
            short = code[2:]
            q_data = quotes.get(short, {})
            price = q_data.get('price', 0)
            
            # 计算盈亏（easytrader cost_price 是按仓位%换算的，我们用实际买入价）
            entry_price = p.get('cost_price', price)
            shares = p.get('current_amount', 100)
            
            result['holdings'].append({
                'symbol': code,
                'name': p.get('stock_name', code),
                'price': price,
                'chg_pct': q_data.get('chg_pct', 0),
                'entry_price': entry_price,
                'shares': shares,
                'market_value': p.get('market_value', 0),
                'pe': q_data.get('pe', 0),
                'mkt_cap': q_data.get('mkt_cap', 0),
            })
    
    # 3. 现金比例
    cur = s.get(f'https://xueqiu.com/cubes/rebalancing/current.json?cube_symbol={PORTFOLIO_CODE}').json()
    result['cash_pct'] = float(cur.get('last_rb', {}).get('cash', 0))
    result['total_asset'] = result['net_value'] * INITIAL_CAPITAL
    result['stock_value'] = sum(h.get('market_value', 0) for h in result['holdings'])
    
    # 4. 调仓历史（最近几条）
    hist = s.get(f'https://xueqiu.com/cubes/rebalancing/history.json?cube_symbol={PORTFOLIO_CODE}&count=3').json()
    result['rebalance_history'] = []
    for h in hist.get('list', []):
        from datetime import datetime as dt
        ts = h.get('created_at', 0) // 1000
        result['rebalance_history'].append({
            'time': dt.fromtimestamp(ts).strftime('%m-%d %H:%M') if ts else '-',
            'status': h.get('status', ''),
            'cash': h.get('cash', 0),
        })
    
    result['updated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M')
    user.s.close()
    return result


def generate_html(data):
    """生成组合展示HTML"""
    h = data['holdings']
    nav = data['net_value']
    daily = data['daily_gain']
    total = data['total_gain']
    cash = data['cash_pct']
    total_asset = data['total_asset']
    stock_value = data['stock_value']
    
    daily_color = '#22c55e' if daily >= 0 else '#ef4444'
    
    # 持仓表格行
    rows = ''
    for i, stk in enumerate(h):
        chg_color = '#22c55e' if stk['chg_pct'] >= 0 else '#ef4444'
        pnl = (stk['price'] / stk['entry_price'] - 1) * 100 if stk['entry_price'] > 0 else 0
        pnl_color = '#22c55e' if pnl >= 0 else '#ef4444'
        mv = stk['market_value'] / 10000
        rows += f'''
        <tr>
            <td>{i+1}</td>
            <td><strong>{stk['name']}</strong></td>
            <td><code>{stk['symbol']}</code></td>
            <td style="color:{chg_color}">{stk['price']:.2f}</td>
            <td style="color:{chg_color}">{chg_pct_repr(stk['chg_pct'])}</td>
            <td style="color:{pnl_color}">{pnl_repr(pnl)}</td>
            <td style="text-align:right">{mv:.1f}万</td>
            <td style="text-align:right">{pe_repr(stk['pe'])}</td>
        </tr>'''
    
    # 调仓历史
    rebal_rows = ''
    for r in data.get('rebalance_history', []):
        status_icon = '✅' if r['status'] == 'success' else '❌' if r['status'] == 'failed' else '⏳'
        rebal_rows += f'<tr><td>{r["time"]}</td><td>{status_icon} {r["status"]}</td><td>现金 {r["cash"]}%</td></tr>'
    
    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>雪球组合 {PORTFOLIO_CODE}</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #e2e8f0; line-height: 1.6; }}
.container {{ max-width: 1200px; margin: 0 auto; padding: 20px; }}
.header {{ background: linear-gradient(135deg, #1e293b, #334155); padding: 30px; border-radius: 16px; margin-bottom: 24px; }}
.header h1 {{ font-size: 24px; margin-bottom: 4px; }}
.header .sub {{ color: #94a3b8; font-size: 13px; }}
.nav {{ display: flex; gap: 12px; margin-bottom: 20px; flex-wrap: wrap; }}
.nav a {{ color: #94a3b8; text-decoration: none; padding: 6px 16px; border-radius: 8px; background: #1e293b; font-size: 13px; }}
.nav a:hover {{ background: #334155; color: #e2e8f0; }}
.stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 16px; margin-bottom: 24px; }}
.stat-card {{ background: #1e293b; border-radius: 12px; padding: 20px; text-align: center; }}
.stat-card .num {{ font-size: 28px; font-weight: bold; }}
.stat-card .label {{ color: #94a3b8; font-size: 12px; margin-top: 4px; }}
.stat-card .sub-num {{ font-size: 14px; margin-top: 4px; }}
table {{ width: 100%; border-collapse: collapse; background: #1e293b; border-radius: 12px; overflow: hidden; margin-bottom: 24px; }}
th {{ background: #334155; padding: 12px 16px; text-align: left; font-size: 12px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.5px; }}
td {{ padding: 10px 16px; border-bottom: 1px solid #0f172a; font-size: 14px; }}
tr:hover {{ background: #1e293b; }}
.section-title {{ font-size: 18px; font-weight: 600; margin: 24px 0 12px; padding-left: 12px; border-left: 4px solid #6366f1; }}
.footer {{ text-align: center; color: #475569; font-size: 12px; margin-top: 40px; padding: 20px; }}
.progress-bar {{ height: 8px; background: #0f172a; border-radius: 4px; overflow: hidden; margin-top: 6px; }}
.progress-fill {{ height: 100%; border-radius: 4px; background: linear-gradient(90deg, #22c55e, #16a34a); }}
.badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
.badge-green {{ background: #166534; color: #86efac; }}
.badge-red {{ background: #7f1d1d; color: #fca5a5; }}
</style>
</head>
<body>
<div class="container">
    <div class="nav">
        <a href="index.html">🏠 首页</a>
        <a href="stocks.html">📈 个股</a>
        <a href="etfs.html">📊 ETF</a>
        <a href="futures.html">📉 期货</a>
        <a href="prediction.html">🏛️ 预测</a>
        <a href="portfolio.html" style="background:#6366f1;color:#fff">💼 组合</a>
    </div>

    <div class="header">
        <h1>💼 雪球组合</h1>
        <div class="sub">{PORTFOLIO_CODE} · {data['name']} · 更新于 {data['updated_at']}</div>
    </div>

    <div class="stats-grid">
        <div class="stat-card">
            <div class="num" style="color:{daily_color}">{nav:.4f}</div>
            <div class="label">组合净值</div>
            <div class="sub-num" style="color:{daily_color}">日涨跌 {daily_repr(daily)}</div>
        </div>
        <div class="stat-card">
            <div class="num" style="color:#22c55e">+{total:.2f}%</div>
            <div class="label">累计收益</div>
            <div class="sub-num" style="color:#94a3b8">成立以来</div>
        </div>
        <div class="stat-card">
            <div class="num">{total_asset/10000:.1f}万</div>
            <div class="label">总资产</div>
            <div class="sub-num" style="color:#94a3b8">持仓 {stock_value/10000:.1f}万 · 现金 {total_asset - stock_value:.0f}</div>
            <div class="progress-bar"><div class="progress-fill" style="width:{stock_value/total_asset*100:.0f}%"></div></div>
        </div>
        <div class="stat-card">
            <div class="num">{len(h)}</div>
            <div class="label">持仓数量</div>
            <div class="sub-num">{sum(1 for s in h if s['chg_pct'] >= 0)}🟢 / {sum(1 for s in h if s['chg_pct'] < 0)}🔴</div>
        </div>
    </div>

    <div class="section-title">📋 持仓明细（按实时行情排序）</div>
    <table>
        <thead>
            <tr><th>#</th><th>名称</th><th>代码</th><th>现价</th><th>日涨跌</th><th>持仓盈亏</th><th>市值</th><th>PE</th></tr>
        </thead>
        <tbody>
            {rows}
        </tbody>
    </table>

    <div class="section-title">📜 最近调仓记录</div>
    <table>
        <thead><tr><th>时间</th><th>状态</th><th>调仓后现金</th></tr></thead>
        <tbody>
            {rebal_rows if rebal_rows else '<tr><td colspan="3" style="text-align:center;color:#64748b">暂无调仓记录</td></tr>'}
        </tbody>
    </table>

    <div class="footer">
        数据来源：雪球组合 · 腾讯实时行情 · 预测系统<br>
        更新时间：{data['updated_at']} · 研究辅助，非投资建议
    </div>
</div>
</body>
</html>'''
    return html


def chg_pct_repr(v):
    if v > 0: return f'<span class="badge badge-green">+{v:.2f}%</span>'
    if v < 0: return f'<span class="badge badge-red">{v:.2f}%</span>'
    return f'<span class="badge" style="background:#334155;color:#94a3b8">0.00%</span>'


def pnl_repr(v):
    if abs(v) < 0.01: return f'<span style="color:#94a3b8">-</span>'
    if v > 0: return f'+{v:.2f}%'
    return f'{v:.2f}%'


def pe_repr(v):
    if v <= 0: return '<span style="color:#94a3b8">亏损</span>'
    if v > 200: return '<span style="color:#eab308">>200</span>'
    return f'{v:.1f}'


def daily_repr(v):
    return f'+{v:.2f}%' if v >= 0 else f'{v:.2f}%'


if __name__ == '__main__':
    print(f'📊 获取组合 {PORTFOLIO_CODE} 数据...')
    try:
        data = fetch_portfolio()
        html = generate_html(data)
        out_path = os.path.join(DOCS_DIR, 'portfolio.html')
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(html)
        print(f'✅ 组合页面已生成: {out_path}')
        print(f'   净值: {data["net_value"]:.4f} | 日涨跌: {data["daily_gain"]:+.2f}%')
        print(f'   持仓: {len(data["holdings"])} 只 | 总资产: {data["total_asset"]/10000:.1f}万')
    except Exception as e:
        print(f'❌ 生成失败: {e}')
        import traceback; traceback.print_exc()
        sys.exit(1)
