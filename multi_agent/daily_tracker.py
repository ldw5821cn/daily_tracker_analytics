#!/usr/bin/env python3
"""
 · 每日追踪器

每日盘后运行，更新模拟盘持仓，检查止损，记录净值，生成报告。

用法：
  python3 daily_tracker.py --weekly    # 每周扫描+初始化模拟盘（周一一早/周末）
  python3 daily_tracker.py --daily     # 每日更新（盘后15:30-17:00）
  python3 daily_tracker.py --pages     # 仅更新Pages页面
  python3 daily_tracker.py --eastmoney # 导出东方财富操作指令

流程：
  每周模式：全A股扫描→Top10推荐→模拟盘初始化→Pages→微信推送
  每日模式：更新行情→模拟盘更新→止损检查→回测验证→Pages→微信推送
"""
import sys
import os
import json
import subprocess
import time
from datetime import datetime

BASE = '/home/liudawei/github/daily_tracker_analytics'
SCRIPTS = os.path.join(BASE, 'scripts')
MULTI_AGENT = os.path.join(BASE, 'multi_agent')
DATA_DIR = os.path.join(MULTI_AGENT, 'data')
os.makedirs(DATA_DIR, exist_ok=True)

sys.path.insert(0, MULTI_AGENT)
sys.path.insert(0, SCRIPTS)


def run_cmd(cmd: str, cwd: str = None) -> str:
    """运行命令并捕获输出"""
    result = subprocess.run(
        ['bash', '-c', cmd],
        cwd=cwd or BASE,
        capture_output=True, text=True, timeout=300
    )
    if result.returncode != 0:
        print(f"  ⚠️  命令错误 (exit={result.returncode}): {result.stderr[:300]}")
    return result.stdout or result.stderr


def weekly_scan():
    """每周全量扫描 + 模拟盘初始化"""
    print(f"\n{'='*60}")
    print(f"  🏆  · 每周全量扫描")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")
    start = time.time()
    
    # Step 1: 全A股扫描→推荐
    print(f"\n📡 Step 1: 全A股扫描 3953+ 只...")
    sys.path.insert(0, MULTI_AGENT)
    from run_pipeline import scan_all_a_shares, scan_and_score
    
    stocks = scan_all_a_shares()
    all_scores = scan_and_score(stocks)
    top10 = all_scores[:10]
    
    top10_path = os.path.join(DATA_DIR, 'weekly_top10.json')
    with open(top10_path, 'w', encoding='utf-8') as f:
        json.dump(top10, f, ensure_ascii=False, indent=2)
    
    print(f"\n  📋 Top10:")
    for i, s in enumerate(top10, 1):
        print(f"  {i:2d}. {s['name']:8s}({s['code']}) 评分{s['composite_score']:3d} {s['changepercent']:+.2f}%")
    
    # Step 2: 模拟盘初始化
    print(f"\n💰 Step 2: 模拟盘初始化...")
    from simulator import init_weekly_portfolio, get_portfolio_summary, format_wechat_summary
    
    init_result = init_weekly_portfolio(top10, capital=min(100000.0, 
                                         float(os.environ.get('SIM_CAPITAL', '100000'))))
    summary = get_portfolio_summary()
    wechat = format_wechat_summary(summary)
    
    # Step 3: Pages
    print(f"\n📊 Step 3: Pages更新...")
    update_pages(top10, summary)
    
    # Step 4: 部署
    print(f"\n🚀 Step 4: Pages部署...")
    run_cmd('bash scripts/deploy_reports.sh')
    
    elapsed = time.time() - start
    print(f"\n{'='*60}")
    print(f"  ✅ 每周扫描完成! 耗时: {elapsed:.0f}s")
    print(f"{'='*60}")
    
    return {
        'type': 'weekly',
        'wechat': wechat,
        'top10': [{'name': s['name'], 'code': s['code'], 'score': s['composite_score']} for s in top10],
        'positions': summary['positions'],
        'total_value': summary['total_value'],
        'elapsed': elapsed,
    }


def daily_update():
    """每日更新"""
    print(f"\n{'='*60}")
    print(f"  💰  · 每日模拟盘追踪")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")
    start = time.time()
    
    sys.path.insert(0, MULTI_AGENT)
    from simulator import update_daily, get_portfolio_summary, format_wechat_summary, check_stop_loss, check_take_profit
    from predictor import collect_watchlist
    from run_pipeline import validate
    
    # Step 1: 采集行情
    data_file = os.path.join(DATA_DIR, 'daily_cron_data.json')
    print(f"\n📡 Step 1: 采集行情数据...")
    collect_watchlist(output_path=data_file)
    
    # Step 2: 模拟盘更新
    print(f"\n💰 Step 2: 模拟盘更新...")
    with open(data_file, 'r') as f:
        data = json.load(f)
    
    upd_result = update_daily(data)
    if 'error' in upd_result:
        print(f"  ⚠️ {upd_result['error']}")
    else:
        print(f"  日收益: {upd_result['daily_return']:+.2f}% | 累计: {upd_result['cumulative_return']:+.2f}%")
    
    # Step 3: 止损检查
    print(f"\n🛑 Step 3: 止损/止盈检查...")
    sl = check_stop_loss()
    tp = check_take_profit()
    if sl:
        print(f"  ⚠️ 止损信号: {len(sl)}个")
        for a in sl:
            print(f"  🔴 {a['name']}({a['ticker']}) 亏损{a['loss_pct']:.1f}%")
    if tp:
        print(f"  🎯 止盈信号: {len(tp)}个")
        for a in tp:
            print(f"  🟢 {a['name']}({a['ticker']}) 盈利{a['profit_pct']:.1f}%")
    if not sl and not tp:
        print(f"  ✅ 无信号")
    
    # Step 4: 回测验证
    print(f"\n🔍 Step 4: 回测验证...")
    validate()
    
    # Step 5: Pages
    print(f"\n📊 Step 5: Pages更新...")
    summary = get_portfolio_summary()
    update_pages(weekly_data=None, sim_summary=summary)
    
    # Step 6: 部署
    print(f"\n🚀 Step 6: Pages部署...")
    run_cmd('bash scripts/deploy_reports.sh')
    
    wechat = format_wechat_summary(summary)
    
    elapsed = time.time() - start
    print(f"\n{'='*60}")
    print(f"  ✅ 每日追踪完成! 耗时: {elapsed:.0f}s")
    print(f"{'='*60}")
    
    return {
        'type': 'daily',
        'wechat': wechat,
        'sl_alerts': len(sl),
        'tp_alerts': len(tp),
        'daily_return': upd_result.get('daily_return'),
        'cumulative_return': upd_result.get('cumulative_return'),
        'total_value': upd_result.get('total_value'),
        'elapsed': elapsed,
    }


def update_pages(weekly_data=None, sim_summary=None):
    """更新Pages页面，加入模拟盘数据"""
    from generate_pages import get_db_stats, generate_html as base_generate_html
    
    stats = get_db_stats()
    if stats is None:
        stats = {
            'total_preds': 0, 'today_preds': 0,
            'validated': 0, 'correct': 0, 'accuracy': 0,
            'by_horizon': {}, 'today_details': [],
        }
    
    # 加入模拟盘数据
    if sim_summary and 'error' not in sim_summary:
        stats['simulator'] = {
            'total_value': sim_summary['total_value'],
            'cash': sim_summary['cash'],
            'stocks_value': sim_summary['stocks_value'],
            'positions': sim_summary['positions'],
            'daily_return': sim_summary.get('latest_snapshot', {}).get('daily_return', 0),
            'cumulative_return': sim_summary.get('latest_snapshot', {}).get('cumulative_return', 0),
            'positions_detail': sim_summary.get('positions_detail', []),
        }
    
    # 加入每周Top10
    if weekly_data:
        stats['market_top10'] = [
            {
                'rank': i+1,
                'name': s['name'],
                'code': s.get('code', s.get('ticker', '')),
                'score': s['composite_score'],
                'changepercent': f"{s['changepercent']:+.2f}%",
                'price': s['price'],
            }
            for i, s in enumerate(weekly_data[:10])
        ]
    
    # 生成含模拟盘的HTML
    html = generate_html_with_simulator(stats)
    
    docs_dir = os.path.join(BASE, 'docs')
    os.makedirs(docs_dir, exist_ok=True)
    out_path = os.path.join(docs_dir, 'prediction.html')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)
    
    print(f"  ✅ Pages: {out_path}")


def generate_html_with_simulator(stats):
    """生成含模拟盘的HTML"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    
    # 颜色
    acc_color = "#22c55e" if stats['accuracy'] >= 60 else "#eab308" if stats['accuracy'] >= 40 else "#ef4444"
    
    # 预测表格
    pred_rows = ""
    signal_emoji = {'bullish': '🟢', 'neutral': '🟡', 'bearish': '🔴'}
    signal_color = {'bullish': '#22c55e', 'neutral': '#eab308', 'bearish': '#ef4444'}
    
    for p in stats.get('today_details', []):
        sig = p.get('signal', 'neutral')
        conf = p.get('confidence', 0.5) * 100
        emoji = signal_emoji.get(sig, '⚪')
        color = signal_color.get(sig, '#666')
        pred_rows += f"""
        <tr>
            <td>{p.get('ticker', '')}</td>
            <td>{p.get('name', '')}</td>
            <td>{p.get('sector', '')}</td>
            <td style="color:{color};font-weight:bold">{emoji} {sig}</td>
            <td>{conf:.0f}%</td>
            <td>{p.get('horizon_1d', '')}</td>
            <td>{p.get('horizon_3d', '')}</td>
            <td>{p.get('horizon_5d', '')}</td>
            <td>{p.get('horizon_10d', '')}</td>
            <td>{p.get('current_price', '')}</td>
        </tr>"""
    
    # 周期统计
    horizon_rows = ""
    for h_name, h_data in sorted(stats.get('by_horizon', {}).items()):
        h_color = "#22c55e" if h_data['accuracy'] >= 55 else "#eab308" if h_data['accuracy'] >= 40 else "#ef4444"
        horizon_rows += f"""
        <tr>
            <td>{h_name}</td>
            <td>{h_data['total']}</td>
            <td>{h_data['correct']}</td>
            <td style="color:{h_color};font-weight:bold">{h_data['accuracy']}%</td>
        </tr>"""
    
    # 模拟盘表格行
    sim = stats.get('simulator', {})
    sim_pos_rows = ""
    for p in sim.get('positions_detail', []):
        pnl_c = "#22c55e" if p['pnl_pct'] >= 0 else "#ef4444"
        sim_pos_rows += f"""
        <tr>
            <td>{p['name']}</td>
            <td>{p['ticker']}</td>
            <td>{p['shares']:.0f}</td>
            <td>{p['entry_price']:.2f}</td>
            <td>{p['current_price']:.2f}</td>
            <td>{p['cost']:.0f}</td>
            <td>{p['market_value']:.0f}</td>
            <td style="color:{pnl_c};font-weight:bold">{p['pnl_pct']:+.2f}%</td>
            <td>{p['weight']:.1f}%</td>
        </tr>"""
    
    # 每周推荐表格
    weekly_rows = ""
    for s in stats.get('market_top10', []):
        weekly_rows += f"""
        <tr>
            <td>{s['rank']}</td>
            <td>{s['name']}</td>
            <td>{s['code']}</td>
            <td>{s['score']}</td>
            <td>{s['changepercent']}</td>
            <td>{s['price']}</td>
        </tr>"""
    
    # 模拟盘收益颜色
    cum_ret = sim.get('cumulative_return', 0)
    ret_color = "#22c55e" if cum_ret >= 0 else "#ef4444"
    
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title> · 模拟盘 - {now}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #e2e8f0; line-height: 1.6; }}
.container {{ max-width: 1400px; margin: 0 auto; padding: 20px; }}
.header {{ background: linear-gradient(135deg, #1e293b, #334155); padding: 30px; border-radius: 16px; margin-bottom: 24px; }}
.header h1 {{ font-size: 24px; margin-bottom: 8px; }}
.header .sub {{ color: #94a3b8; font-size: 14px; }}
.stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 16px; margin-bottom: 24px; }}
.stat-card {{ background: #1e293b; border-radius: 12px; padding: 20px; text-align: center; }}
.stat-card .num {{ font-size: 28px; font-weight: bold; }}
.stat-card .label {{ color: #94a3b8; font-size: 12px; margin-top: 4px; }}
table {{ width: 100%; border-collapse: collapse; background: #1e293b; border-radius: 12px; overflow: hidden; margin-bottom: 24px; }}
th {{ background: #334155; padding: 10px 14px; text-align: left; font-size: 12px; color: #94a3b8; text-transform: uppercase; }}
td {{ padding: 8px 14px; border-bottom: 1px solid #1e293b; font-size: 13px; }}
tr:hover {{ background: #1e293b; }}
.section-title {{ font-size: 16px; font-weight: 600; margin: 24px 0 12px; padding-left: 12px; border-left: 4px solid #6366f1; }}
.section-title.green {{ border-left-color: #22c55e; }}
.section-title.red {{ border-left-color: #ef4444; }}
.nav {{ display: flex; gap: 12px; margin-bottom: 20px; flex-wrap: wrap; }}
.nav a {{ color: #94a3b8; text-decoration: none; padding: 6px 16px; border-radius: 8px; background: #1e293b; font-size: 13px; }}
.nav a:hover {{ background: #334155; color: #e2e8f0; }}
.nav a.active {{ background: #6366f1; color: #fff; }}
.footer {{ text-align: center; color: #475569; font-size: 12px; margin-top: 40px; padding: 20px; }}
.progress-bar {{ height: 6px; border-radius: 3px; margin-top: 4px; }}
</style>
</head>
<body>
<div class="container">
    <div class="nav">
        <a href="index.html">🏠 首页</a>
        <a href="stocks.html">📈 个股</a>
        <a href="etfs.html">📊 ETF</a>
        <a href="futures.html">📉 期货</a>
        <a href="prediction.html" class="active">🏛️ </a>
    </div>

    <div class="header">
        <h1>🏛️  · 模拟盘 & 预测</h1>
        <div class="sub">{now} · 基于（易方达基金经理）投资方法论</div>
    </div>

    <div class="section-title {'green' if cum_ret >= 0 else 'red'}">💰 模拟盘持仓</div>
    <div class="stats-grid">
        <div class="stat-card">
            <div class="num" style="color:{ret_color}">{sim.get('cumulative_return', 0):+.2f}%</div>
            <div class="label">累计收益</div>
        </div>
        <div class="stat-card">
            <div class="num">{sim.get('total_value', 0):.0f}</div>
            <div class="label">总资产 (初始10万)</div>
        </div>
        <div class="stat-card">
            <div class="num">{sim.get('daily_return', 0):+.2f}%</div>
            <div class="label">今日收益</div>
        </div>
        <div class="stat-card">
            <div class="num">{sim.get('positions', 0)}</div>
            <div class="label">持仓数</div>
        </div>
        <div class="stat-card">
            <div class="num">{sim.get('cash', 0):.0f}</div>
            <div class="label">现金</div>
        </div>
        <div class="stat-card">
            <div class="num">{sim.get('stocks_value', 0):.0f}</div>
            <div class="label">股票市值</div>
        </div>
    </div>

    <table>
        <thead>
            <tr><th>名称</th><th>代码</th><th>股数</th><th>成本价</th><th>现价</th><th>成本</th><th>市值</th><th>盈亏%</th><th>权重</th></tr>
        </thead>
        <tbody>
            {sim_pos_rows if sim_pos_rows else '<tr><td colspan="9" style="text-align:center;color:#64748b">尚无持仓 — 等待每周扫描</td></tr>'}
        </tbody>
    </table>

    <div class="section-title">🏆 本周推荐 Top10</div>
    <table>
        <thead>
            <tr><th>#</th><th>名称</th><th>代码</th><th>评分</th><th>涨幅</th><th>现价</th></tr>
        </thead>
        <tbody>
            {weekly_rows if weekly_rows else '<tr><td colspan="6" style="text-align:center;color:#64748b">等待每周扫描</td></tr>'}
        </tbody>
    </table>

    <div class="section-title">📊 今日预测明细</div>
    <table>
        <thead>
            <tr><th>代码</th><th>名称</th><th>板块</th><th>信号</th><th>信心</th><th>1日</th><th>3日</th><th>5日</th><th>10日</th><th>现价</th></tr>
        </thead>
        <tbody>
            {pred_rows if pred_rows else '<tr><td colspan="10" style="text-align:center;color:#64748b">今日暂无预测记录</td></tr>'}
        </tbody>
    </table>

    <div class="section-title">🎯 准确率统计</div>
    <div class="stats-grid">
        <div class="stat-card">
            <div class="num" style="color:{acc_color}">{stats.get('accuracy', 0)}%</div>
            <div class="label">总体准确率 (共{stats.get('validated', 0)}次验证)</div>
        </div>
        <div class="stat-card">
            <div class="num">{stats.get('total_preds', 0)}</div>
            <div class="label">累计预测</div>
        </div>
        <div class="stat-card">
            <div class="num">{stats.get('today_preds', 0)}</div>
            <div class="label">今日预测</div>
        </div>
    </div>

    <table>
        <thead><tr><th>周期</th><th>总数</th><th>正确</th><th>准确率</th></tr></thead>
        <tbody>
            {horizon_rows if horizon_rows else '<tr><td colspan="4" style="text-align:center;color:#64748b">暂无数据</td></tr>'}
        </tbody>
    </table>
    
    <div class="footer">
        数据由 Hermes Agent + hermes-invest skill 自动生成 · 初始模拟资金10万 · 研究辅助非投资建议 · 
        <a href="https://github.com/ldw5821cn/daily_tracker_analytics" style="color:#6366f1">GitHub</a>
    </div>
</div>
</body>
</html>"""


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description=' · 每日追踪器')
    parser.add_argument('--weekly', action='store_true', help='每周扫描+模拟盘初始化')
    parser.add_argument('--daily', action='store_true', help='每日更新')
    parser.add_argument('--pages', action='store_true', help='仅更新Pages')
    parser.add_argument('--eastmoney', action='store_true', help='导出东方财富操作指令')
    
    args = parser.parse_args()
    
    if args.weekly:
        result = weekly_scan()
        print(f"\n{'='*60}")
        print(result['wechat'])
    
    elif args.daily:
        result = daily_update()
        print(f"\n{'='*60}")
        print(result['wechat'])
    
    elif args.pages:
        from simulator import get_portfolio_summary
        try:
            summary = get_portfolio_summary()
        except:
            summary = {'error': '无数据'}
        update_pages(sim_summary=summary if 'error' not in summary else None)
        run_cmd('bash scripts/deploy_reports.sh')
        print("✅ Pages updated")
    
    elif args.eastmoney:
        from simulator import export_for_eastmoney
        result = export_for_eastmoney()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    
    else:
        parser.print_help()
