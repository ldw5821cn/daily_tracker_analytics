#!/usr/bin/env python3
"""
生成 LLM 预测的 GitHub Pages HTML 页面

从 agentic_predictions 表读取多 Agent 预测结果，
同时保留旧 predictions 表的验证统计。

用法：
  cd /home/liudawei/github/daily_tracker_analytics
  python3 scripts/generate_pages.py
"""

import sys
import os
import json
import sqlite3
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'multi_agent'))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join(REPO_ROOT, "docs")
DB_PATH = os.path.join(REPO_ROOT, "multi_agent", "data", "llm_predictions.db")


def get_db_stats():
    """从回测数据库获取统计"""
    if not os.path.exists(DB_PATH):
        return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    stats = {}

    # 旧表：总预测数 + 验证准确率
    cur.execute("SELECT COUNT(*) as c FROM predictions")
    stats['legacy_total'] = cur.fetchone()['c']

    cur.execute("SELECT COUNT(*) as c FROM agentic_predictions")
    stats['agentic_total'] = cur.fetchone()['c']

    # 今日预测：优先新表
    today = datetime.now().strftime('%Y-%m-%d')
    cur.execute("SELECT COUNT(*) as c FROM agentic_predictions WHERE pred_date=?", (today,))
    agentic_today = cur.fetchone()['c']
    cur.execute("SELECT COUNT(*) as c FROM predictions WHERE pred_date=?", (today,))
    legacy_today = cur.fetchone()['c']
    stats['today_preds'] = agentic_today or legacy_today

    # 验证统计（旧表数据）
    cur.execute("""
        SELECT COUNT(*) as total, SUM(direction_correct) as correct
        FROM validation_results
    """)
    row = cur.fetchone()
    stats['validated'] = row['total'] or 0
    stats['correct'] = int(row['correct'] or 0)
    stats['accuracy'] = round(stats['correct'] / stats['validated'] * 100, 1) if stats['validated'] > 0 else 0

    # 按周期
    stats['by_horizon'] = {}
    for h in [1, 3, 5, 10]:
        cur.execute("""
            SELECT COUNT(*) as t, SUM(direction_correct) as c
            FROM validation_results WHERE horizon=?
        """, (h,))
        r = cur.fetchone()
        if r and r['t']:
            stats['by_horizon'][f'{h}d'] = {
                'total': r['t'], 'correct': int(r['c'] or 0),
                'accuracy': round(r['c'] / r['t'] * 100, 1)
            }

    # 今日预测明细：优先新表 agentic_predictions
    display_date = today
    source_table = 'agentic'
    rows = cur.execute("""
        SELECT ticker, name, sector, signal, confidence,
               horizon_1d, horizon_3d, horizon_5d, horizon_10d,
               current_price, target_price, stop_loss, position_pct,
               weighted_score, reasoning, bull_points, bear_points,
               component_scores
        FROM agentic_predictions WHERE pred_date=?
        ORDER BY sector, ticker
    """, (today,)).fetchall()

    if not rows:
        # 取最近一次的 agentic 预测
        cur.execute("SELECT pred_date FROM agentic_predictions ORDER BY pred_date DESC LIMIT 1")
        last = cur.fetchone()
        if last:
            display_date = last['pred_date']
            rows = cur.execute("""
                SELECT ticker, name, sector, signal, confidence,
                       horizon_1d, horizon_3d, horizon_5d, horizon_10d,
                       current_price, target_price, stop_loss, position_pct,
                       weighted_score, reasoning, bull_points, bear_points,
                       component_scores
                FROM agentic_predictions WHERE pred_date=?
                ORDER BY sector, ticker
            """, (display_date,)).fetchall()

    if not rows:
        # 回退旧表
        source_table = 'legacy'
        cur.execute("""
            SELECT ticker, name, sector, signal, confidence,
                   horizon_1d, horizon_3d, horizon_5d, horizon_10d,
                   current_price
            FROM predictions WHERE pred_date=?
            ORDER BY sector, ticker
        """, (today,))
        rows = cur.fetchall()
        if not rows:
            cur.execute("SELECT pred_date FROM predictions ORDER BY pred_date DESC LIMIT 1")
            last = cur.fetchone()
            if last:
                display_date = last['pred_date']
                rows = cur.execute("""
                    SELECT ticker, name, sector, signal, confidence,
                           horizon_1d, horizon_3d, horizon_5d, horizon_10d,
                           current_price
                    FROM predictions WHERE pred_date=?
                    ORDER BY sector, ticker
                """, (display_date,)).fetchall()

    stats['today_details'] = [dict(r) for r in rows]
    stats['display_date'] = display_date
    stats['source_table'] = source_table

    # 近期趋势（近7天）
    week = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    cur.execute("""
        SELECT COUNT(*) as t, SUM(v.direction_correct) as c
        FROM validation_results v JOIN predictions p ON v.prediction_id=p.id
        WHERE p.pred_date >= ?
    """, (week,))
    r = cur.fetchone()
    if r and r['t']:
        stats['week_accuracy'] = round(r['c'] / r['t'] * 100, 1)

    conn.close()
    return stats


def generate_html(stats):
    """生成 HTML 页面"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M')

    acc_color = "#22c55e" if stats['accuracy'] >= 60 else "#eab308" if stats['accuracy'] >= 40 else "#ef4444"

    # 预测表格行
    pred_rows = ""
    signal_emoji = {'bullish': '🟢', 'neutral': '🟡', 'bearish': '🔴'}
    signal_color = {'bullish': '#22c55e', 'neutral': '#eab308', 'bearish': '#ef4444'}
    is_agentic = stats.get('source_table') == 'agentic'

    for p in stats.get('today_details', []):
        sig = p.get('signal', 'neutral')
        conf = p.get('confidence', 0.5) * 100
        emoji = signal_emoji.get(sig, '⚪')
        color = signal_color.get(sig, '#666')
        if is_agentic:
            # 新表：展示更多列
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
            <td>{p.get('target_price', '')}</td>
            <td>{p.get('stop_loss', '')}</td>
            <td>{p.get('position_pct', 0)*100:.0f}%</td>
        </tr>"""
        else:
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

    # 周期统计行
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

    # 显示日期标签
    display_date_label = stats.get('display_date', '')
    today = datetime.now().strftime('%Y-%m-%d')
    if display_date_label and display_date_label != today:
        display_date_label = f"(最新: {display_date_label})"
    else:
        display_date_label = ""

    # 模型标签
    model_tag = "🧠 多Agent融合" if is_agentic else "📈 趋势算法"
    total_preds = stats.get('agentic_total', 0) if is_agentic else stats.get('legacy_total', 0)

    # 表头
    if is_agentic:
        thead = "<tr><th>代码</th><th>名称</th><th>板块</th><th>信号</th><th>信心</th><th>1日</th><th>3日</th><th>5日</th><th>10日</th><th>现价</th><th>目标</th><th>止损</th><th>建议仓位</th></tr>"
    else:
        thead = "<tr><th>代码</th><th>名称</th><th>板块</th><th>信号</th><th>信心</th><th>1日</th><th>3日</th><th>5日</th><th>10日</th><th>现价</th></tr>"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title> LLM 预测 - {now}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #e2e8f0; line-height: 1.6; }}
.container {{ max-width: 1200px; margin: 0 auto; padding: 20px; }}
.header {{ background: linear-gradient(135deg, #1e293b, #334155); padding: 30px; border-radius: 16px; margin-bottom: 24px; }}
.header h1 {{ font-size: 24px; margin-bottom: 8px; }}
.header .sub {{ color: #94a3b8; font-size: 14px; }}
.stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }}
.stat-card {{ background: #1e293b; border-radius: 12px; padding: 20px; text-align: center; }}
.stat-card .num {{ font-size: 32px; font-weight: bold; }}
.stat-card .label {{ color: #94a3b8; font-size: 12px; margin-top: 4px; }}
table {{ width: 100%; border-collapse: collapse; background: #1e293b; border-radius: 12px; overflow: hidden; margin-bottom: 24px; font-size: 13px; }}
th {{ background: #334155; padding: 10px 12px; text-align: left; font-size: 12px; color: #94a3b8; text-transform: uppercase; }}
td {{ padding: 8px 12px; border-bottom: 1px solid #1e293b; font-size: 13px; }}
tr:hover {{ background: #1e293b; }}
.section-title {{ font-size: 18px; font-weight: 600; margin: 24px 0 12px; padding-left: 12px; border-left: 4px solid #6366f1; }}
.nav {{ display: flex; gap: 12px; margin-bottom: 20px; flex-wrap: wrap; }}
.nav a {{ color: #94a3b8; text-decoration: none; padding: 6px 16px; border-radius: 8px; background: #1e293b; font-size: 13px; }}
.nav a:hover {{ background: #334155; color: #e2e8f0; }}
.footer {{ text-align: center; color: #475569; font-size: 12px; margin-top: 40px; padding: 20px; }}
.model-tag {{ display: inline-block; background: #6366f1; color: #fff; padding: 2px 8px; border-radius: 12px; font-size: 12px; margin-left: 8px; }}
</style>
</head>
<body>
<div class="container">
    <div class="nav">
        <a href="index.html">🏠 首页</a>
        <a href="stocks.html">📈 个股</a>
        <a href="etfs.html">📊 ETF</a>
        <a href="futures.html">📉 期货</a>
        <a href="prediction.html" style="background:#6366f1;color:#fff">🏛️ </a>
        <a href="portfolio.html">💼 组合</a>
    </div>

    <div class="header">
        <h1>🏛️LLM 预测 <span class="model-tag">{model_tag}</span></h1>
        <div class="sub">{now} · 基于（易方达基金经理）投资方法论</div>
    </div>

    <div class="stats-grid">
        <div class="stat-card">
            <div class="num">{stats.get('today_preds', 0)}</div>
            <div class="label">今日预测</div>
        </div>
        <div class="stat-card">
            <div class="num" style="color:{acc_color}">{stats.get('accuracy', 0)}%</div>
            <div class="label">总体准确率 (共{stats.get('validated', 0)}次验证)</div>
        </div>
        <div class="stat-card">
            <div class="num">{total_preds}</div>
            <div class="label">累计预测</div>
        </div>
        <div class="stat-card">
            <div class="num">{stats.get('validated', 0)}</div>
            <div class="label">已验证</div>
        </div>
    </div>

    <div class="section-title">📊 今日预测明细 {display_date_label}</div>
    <table>
        <thead>
            {thead}
        </thead>
        <tbody>
            {pred_rows if pred_rows else '<tr><td colspan="13" style="text-align:center;color:#64748b">今日暂无预测记录</td></tr>'}
        </tbody>
    </table>

    <div class="section-title">🎯 准确率统计（按周期）</div>
    <table>
        <thead><tr><th>周期</th><th>总数</th><th>正确</th><th>准确率</th></tr></thead>
        <tbody>
            {horizon_rows if horizon_rows else '<tr><td colspan="4" style="text-align:center;color:#64748b">暂无数据</td></tr>'}
        </tbody>
    </table>
    
    <div class="footer">
        数据由 Hermes Agent + 多Agent LLM 预测系统自动生成 · 研究辅助非投资建议 · 
        <a href="https://github.com/ldw5821cn/daily_tracker_analytics" style="color:#6366f1">GitHub</a>
    </div>
</div>
</body>
</html>"""


if __name__ == "__main__":
    stats = get_db_stats()
    if stats is None:
        print("❌ 回测数据库不存在，尚无预测数据")
        sys.exit(1)

    html = generate_html(stats)

    os.makedirs(DOCS_DIR, exist_ok=True)
    out_path = os.path.join(DOCS_DIR, "prediction.html")
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"✅ 预测页面生成: {out_path}")
    print(f"   模型来源: {'多Agent融合' if stats.get('source_table')=='agentic' else '趋势算法'}")
    print(f"   今日预测: {stats.get('today_preds')} 条")
    print(f"   累计预测: {stats.get('agentic_total', 0)} 条 (新) + {stats.get('legacy_total', 0)} 条 (旧)")
    print(f"   总体准确率: {stats.get('accuracy', 0)}% ({stats.get('validated', 0)}次验证)")
