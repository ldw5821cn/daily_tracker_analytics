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
from core.backtest_utils import sort_by_backtest, inject_backtest_metrics

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join(REPO_ROOT, "docs")
DB_PATH = os.path.join(REPO_ROOT, "multi_agent", "data", "llm_predictions.db")
BACKTEST_JSON = os.path.join(REPO_ROOT, "multi_agent", "data", "llm_backtest_results.json")


def _load_backtest_summary():
    """加载 LLM 信号回测评估结果。"""
    if not os.path.exists(BACKTEST_JSON):
        return {}
    try:
        with open(BACKTEST_JSON, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _backtest_html(stats) -> str:
    """生成信号回测评估 HTML 区块。"""
    bt = _load_backtest_summary()
    if not bt or 'by_category' not in bt:
        return ""
    rows = ""
    for cat in ['ETF', '个股', '期货']:
        groups = bt.get('by_category', {}).get(cat, {})
        if not groups:
            continue
        for sig in ['bullish', 'bearish', 'neutral']:
            vals = groups.get(sig, {})
            if not vals:
                continue
            rows += f"""
            <tr>
                <td>{cat}</td>
                <td>{ {'bullish':'看多','bearish':'看空','neutral':'中性'}.get(sig, sig) }</td>
                <td>{vals.get('count', 0)}</td>
                <td>{vals.get('avg_return_60d', 0):+.1f}%</td>
                <td>{vals.get('avg_return_30d', 0):+.1f}%</td>
                <td>{vals.get('avg_max_drawdown_60d', 0):.1f}%</td>
                <td>{vals.get('avg_sharpe_60d', 0):.2f}</td>
                <td>{vals.get('win_rate_60d', 0):.0f}%</td>
            </tr>"""
    if not rows:
        return ""
    return f"""
    <div class="section-title">📈 LLM 信号回测评估（按 backtest_summary 聚合）</div>
    <table>
        <thead><tr><th>分类</th><th>信号</th><th>数量</th><th>60日收益</th><th>30日收益</th><th>60日回撤</th><th>60日夏普</th><th>60日胜率</th></tr></thead>
        <tbody>
            {rows}
        </tbody>
    </table>
    """


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
        SELECT ticker, name, sector, category, signal, confidence,
               horizon_1d, horizon_3d, horizon_5d, horizon_10d,
               current_price, target_price, stop_loss, position_pct,
               weighted_score, reasoning, bull_points, bear_points,
               component_scores, backtest_summary
        FROM agentic_predictions WHERE pred_date=?
        ORDER BY category, weighted_score DESC
    """, (today,)).fetchall()

    if not rows:
        # 取最近一次的 agentic 预测
        cur.execute("SELECT pred_date FROM agentic_predictions ORDER BY pred_date DESC LIMIT 1")
        last = cur.fetchone()
        if last:
            display_date = last['pred_date']
            rows = cur.execute("""
                SELECT ticker, name, sector, category, signal, confidence,
                       horizon_1d, horizon_3d, horizon_5d, horizon_10d,
                       current_price, target_price, stop_loss, position_pct,
                       weighted_score, reasoning, bull_points, bear_points,
                       component_scores, backtest_summary
                FROM agentic_predictions WHERE pred_date=?
                ORDER BY category, weighted_score DESC
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

    # 注入统一回测指标并按回测得分排序
    for p in stats['today_details']:
        inject_backtest_metrics(p)
    stats['today_details'] = sorted(
        stats['today_details'],
        key=lambda x: x.get('bt_score', 0),
        reverse=True
    )

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

    is_agentic = stats.get('source_table') == 'agentic'
    total_preds = stats.get('agentic_total', 0) if is_agentic else stats.get('legacy_total', 0)

    signal_emoji = {'bullish': '🔥', 'neutral': '➖', 'bearish': '❄️'}
    signal_color = {'bullish': '#22c55e', 'neutral': '#94a3b8', 'bearish': '#ef4444'}
    signal_cn = {'bullish': '看多', 'neutral': '中性', 'bearish': '看空'}

    details = stats.get('today_details', [])

    # 按 category 分组
    groups = {}
    for p in details:
        cat = p.get('category') or '其他'
        groups.setdefault(cat, []).append(p)

    # 总体统计
    bullish = [p for p in details if p.get('signal') == 'bullish']
    bearish = [p for p in details if p.get('signal') == 'bearish']
    neutral = [p for p in details if p.get('signal') == 'neutral']

    # 总体统计卡片：移除准确率卡片，换成"回测统一口径"
    acc_color = "#94a3b8"
    stats_card_html = f"""
        <div class="stat-card">
            <div class="num" style="color:{acc_color}">统一回测</div>
            <div class="label">multi_period_backtest</div>
        </div>
        <div class="stat-card">
            <div class="num">{stats.get('today_preds', 0)}</div>
            <div class="label">今日预测</div>
        </div>
        <div class="stat-card">
            <div class="num">{total_preds}</div>
            <div class="label">累计预测</div>
        </div>
        <div class="stat-card">
            <div class="num">{len(bullish)}</div>
            <div class="label">看多</div>
        </div>
        <div class="stat-card">
            <div class="num">{len(bearish)}</div>
            <div class="label">看空</div>
        </div>
    """

    # Top 推荐：按回测得分排序，而不只是加权评分
    for p in details:
        inject_backtest_metrics(p)
    top_bull = sorted(bullish, key=lambda x: x.get('bt_score', 0), reverse=True)[:5]
    top_bear = sorted(bearish, key=lambda x: x.get('bt_score', 0), reverse=True)[:5]

    def _rows_html(rows):
        s = ""
        for p in rows:
            sig = p.get('signal', 'neutral')
            conf = p.get('confidence', 0.5) * 100
            emoji = signal_emoji.get(sig, '⚪')
            color = signal_color.get(sig, '#666')
            comp = json.loads(p.get('component_scores') or '{}') if isinstance(p.get('component_scores'), str) else p.get('component_scores', {})
            tech = comp.get('technical', '-') if isinstance(comp, dict) else '-'
            s += f"""
        <tr>
            <td><b>{p.get('ticker', '')}</b></td>
            <td>{p.get('name', '')}</td>
            <td>{p.get('sector', '')}</td>
            <td style="color:{color};font-weight:bold">{emoji} {signal_cn.get(sig, sig)}</td>
            <td>{p.get('weighted_score', 0)}</td>
            <td>{conf:.0f}%</td>
            <td>{p.get('horizon_1d', '')}</td>
            <td>{p.get('horizon_3d', '')}</td>
            <td>{p.get('horizon_5d', '')}</td>
            <td>{p.get('horizon_10d', '')}</td>
            <td>{p.get('bt_return_60d', 0):+.1f}%</td>
            <td>{p.get('bt_max_dd_60d', 0):.1f}%</td>
            <td>{p.get('bt_sharpe_60d', 0):.2f}</td>
            <td>{p.get('current_price', '')}</td>
            <td>{p.get('target_price', '')}</td>
            <td>{p.get('stop_loss', '')}</td>
            <td>{(p.get('position_pct') or 0)*100:.0f}%</td>
            <td title="{p.get('reasoning', '')}">{tech}</td>
        </tr>"""
        return s

    category_html = ""
    for cat in ['ETF', '个股', '期货']:
        if cat not in groups:
            continue
        items = groups[cat]
        cat_bull = len([p for p in items if p.get('signal') == 'bullish'])
        cat_bear = len([p for p in items if p.get('signal') == 'bearish'])
        # 按回测得分排序（已注入）
        items = sorted(items, key=lambda x: x.get('bt_score', 0), reverse=True)
        category_html += f"""
    <div class="section-title">📂 {cat} ({len(items)}只 | 看多{cat_bull} 看空{cat_bear})</div>
    <table>
        <thead><tr><th>代码</th><th>名称</th><th>板块</th><th>信号</th><th>评分</th><th>信心</th><th>1日</th><th>3日</th><th>5日</th><th>10日</th><th>60日收益</th><th>60日回撤</th><th>60日夏普</th><th>现价</th><th>目标</th><th>止损</th><th>仓位</th><th>技术分</th></tr></thead>
        <tbody>
            {_rows_html(items)}
        </tbody>
    </table>"""

    # 准确率统计区域改为“回测统计说明”：不再展示低准确率的方向验证
    # 后续统一使用 multi_period_backtest 作为唯一回测口径
    stats_note = f"""
    <div class="section-title">🎯 回测口径</div>
    <div style="background:#1e293b;padding:16px;border-radius:12px;margin-bottom:24px;color:#cbd5e1;line-height:1.8">
        <p>统一回测方案：<b>multi_period_backtest</b>（30/60/90/120天持有收益、最大回撤、夏普）。</p>
        <p>旧方向验证（validation_results / unified_validation_results）已弃用：样本小且准确率接近随机，无参考价值。</p>
        <p>表格已按「60日收益综合分」排序，重点推荐同时考虑技术面评分和历史回测表现。</p>
    </div>
    """

    # Top 推荐区
    top_html = ""
    if top_bull:
        top_html += '<div class="section-title">🏆 重点看多（按回测排序）</div><div class="cards">'
        for p in top_bull:
            top_html += f"""
            <div class="card bull">
                <div class="card-title">🔥 {p.get('name', p.get('ticker'))} ({p.get('ticker')})</div>
                <div class="card-meta">评分 {p.get('weighted_score')} | 60日收益 {p.get('bt_return_60d', 0):+.1f}% | 回撤 {p.get('bt_max_dd_60d', 0):.1f}%</div>
                <div class="card-price">目标 {p.get('target_price')} | 止损 {p.get('stop_loss')}</div>
                <div class="card-reason">{p.get('reasoning', '')}</div>
            </div>"""
        top_html += '</div>'
    if top_bear:
        top_html += '<div class="section-title">❄️ 重点看空（按回测排序）</div><div class="cards">'
        for p in top_bear:
            top_html += f"""
            <div class="card bear">
                <div class="card-title">❄️ {p.get('name', p.get('ticker'))} ({p.get('ticker')})</div>
                <div class="card-meta">评分 {p.get('weighted_score')} | 60日收益 {p.get('bt_return_60d', 0):+.1f}% | 回撤 {p.get('bt_max_dd_60d', 0):.1f}%</div>
                <div class="card-price">目标 {p.get('target_price')} | 止损 {p.get('stop_loss')}</div>
                <div class="card-reason">{p.get('reasoning', '')}</div>
            </div>"""
        top_html += '</div>'

    # 周期统计行：已弃用方向验证，这里显示为空（保留结构方便后续）
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

    model_tag = "🧠 多Agent融合" if is_agentic else "📈 趋势算法"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title> LLM 预测 - {now}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #e2e8f0; line-height: 1.6; }}
.container {{ max-width: 1400px; margin: 0 auto; padding: 20px; }}
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
tr:hover {{ background: #334155; }}
.section-title {{ font-size: 18px; font-weight: 600; margin: 24px 0 12px; padding-left: 12px; border-left: 4px solid #6366f1; }}
.nav {{ display: flex; gap: 12px; margin-bottom: 20px; flex-wrap: wrap; }}
.nav a {{ color: #94a3b8; text-decoration: none; padding: 6px 16px; border-radius: 8px; background: #1e293b; font-size: 13px; }}
.nav a:hover {{ background: #334155; color: #e2e8f0; }}
.footer {{ text-align: center; color: #475569; font-size: 12px; margin-top: 40px; padding: 20px; }}
.model-tag {{ display: inline-block; background: #6366f1; color: #fff; padding: 2px 8px; border-radius: 12px; font-size: 12px; margin-left: 8px; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; margin-bottom: 24px; }}
.card {{ background: #1e293b; border-radius: 12px; padding: 16px; border-left: 4px solid #6366f1; }}
.card.bull {{ border-left-color: #22c55e; }}
.card.bear {{ border-left-color: #ef4444; }}
.card-title {{ font-weight: 600; margin-bottom: 6px; }}
.card-meta {{ color: #94a3b8; font-size: 12px; margin-bottom: 6px; }}
.card-price {{ color: #e2e8f0; font-size: 13px; margin-bottom: 6px; }}
.card-reason {{ color: #94a3b8; font-size: 12px; line-height: 1.5; }}
</style>
</head>
<body>
<div class="container">
    <div class="nav">
        <a href="index.html">🏠 首页</a>
        <a href="stocks.html">📈 个股</a>
        <a href="etfs.html">📊 ETF</a>
        <a href="futures.html">📉 期货</a>
        <a href="prediction.html" style="background:#6366f1;color:#fff">🏛️ 预测</a>
        <a href="portfolio.html">💼 组合</a>
    </div>

    <div class="header">
        <h1>🏛️ LLM 预测 <span class="model-tag">{model_tag}</span></h1>
        <div class="sub">{now} · 基于（易方达基金经理）投资方法论 {display_date_label}</div>
    </div>

    <div class="stats-grid">
{stats_card_html}
    </div>

    {top_html}

    {_backtest_html(stats)}

    {_build_factors_html()}

    {_build_portfolio_backtest_html()}

    {stats_note}

    {category_html}

    <div class="section-title">🎯 回测按周期统计</div>
    <table>
        <thead><tr><th>周期</th><th>总数</th><th>正确</th><th>准确率</th></tr></thead>
        <tbody>
            {horizon_rows if horizon_rows else '<tr><td colspan="4" style="text-align:center;color:#64748b">方向验证已弃用，回测口径请参考顶部说明</td></tr>'}
        </tbody>
    </table>

    <div class="footer">
        数据由 Hermes Agent + 多Agent LLM 预测系统自动生成 · 研究辅助非投资建议 ·
        <a href="https://github.com/ldw5821cn/daily_tracker_analytics" style="color:#6366f1">GitHub</a>
    </div>
</div>

    <!-- LLM 舆情摘要区块 -->
    <div class="section">
        <h2>🗞️ 重点标的 LLM 舆情</h2>
        {_build_news_sentiment_html()}
    </div>

</body>
</html>"""


def _build_factors_html():
    """生成 LLM 因子库 HTML 区块：展示全库 + 精选。"""
    try:
        with open('multi_agent/data/llm_factors.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        return f"<!-- factors load failed: {e} -->"
    factors = data.get('factors', [])
    if not factors:
        return '<p>暂无 LLM 因子数据</p>'
    try:
        with open('multi_agent/data/llm_factors_selected.json', 'r', encoding='utf-8') as f:
            selected_data = json.load(f)
        selected = selected_data.get('filtered_factors') or selected_data.get('factors', [])
    except Exception:
        selected = []

    cards = []
    for f in factors[:10]:
        source = f.get('source', 'rule')
        src_tag = {'rule': '规则', 'llm': 'LLM', 'composite': '组合', 'auto_fe': '自动FE'}.get(source, source)
        cards.append(
            f'<div class="card">'
            f'<div class="card-title">🧬 {f["name"]} <span style="font-size:12px;color:#94a3b8">({src_tag})</span></div>'
            f'<div class="card-reason">{f.get("description", "")}</div>'
            f'<div class="card-meta">夏普 {f.get("avg_sharpe")} | 收益 {f.get("avg_return")}% | 回撤 {f.get("avg_drawdown")}% | 胜率 {f.get("avg_win_rate")}% | Calmar {f.get("avg_calmar")}</div>'
            f'</div>'
        )

    selected_cards = []
    for f in selected[:10]:
        source = f.get('source', 'rule')
        src_tag = {'rule': '规则', 'llm': 'LLM', 'composite': '组合', 'auto_fe': '自动FE'}.get(source, source)
        selected_cards.append(
            f'<div class="card">'
            f'<div class="card-title">🎯 {f["name"]} <span style="font-size:12px;color:#94a3b8">({src_tag})</span></div>'
            f'<div class="card-reason">{f.get("description", "")}</div>'
            f'<div class="card-meta">测试收益 {f.get("avg_test_return")}% | 测试回撤 {f.get("avg_test_drawdown")}% | Rank IC {f.get("avg_rank_ic")}</div>'
            f'<div class="card-reason" style="color:#94a3b8">🧠 {f.get("llm_interpretation", "")} (可信度{f.get("llm_credibility_score", "")})</div>'
            f'</div>'
        )
    return (
        f'<div class="section"><h2>🧬 LLM 因子库（共 {len(factors)} 个，精选 {len(selected)} 个）</h2>\n'
        + "\n".join(cards)
        + (f'<h3 style="margin-top:24px">🎯 精选因子（样本外验证 + 正交化）</h3>\n' + "\n".join(selected_cards) if selected_cards else '')
        + '</div>'
    )


def _build_portfolio_backtest_html():
    try:
        with open('multi_agent/data/vectorbt_portfolio_backtest.json', 'r', encoding='utf-8') as f:
            bt = json.load(f)
    except Exception as e:
        return f'<!-- portfolio backtest load failed: {e} -->'
    if 'error' in bt:
        return f'<p>组合回测错误: {bt["error"]}</p>'
    scenarios = bt.get('scenarios', {})
    rows = []
    for name, v in scenarios.items():
        if 'error' in v:
            continue
        # 高亮带成本只做多+风控策略
        highlight = name == 'weekly_long_only_risk_with_cost'
        row_style = 'style="background:#1e293b;font-weight:bold"' if highlight else ''
        rows.append(
            f"<tr {row_style}><td>{name}{' ⭐' if highlight else ''}</td>"
            f"<td style=\"color:{'#22c55e' if v['annualized_return'] >= 0 else '#ef4444'}\">{v['annualized_return']:+.2f}%</td>"
            f"<td>{v['max_drawdown']:.2f}%</td>"
            f"<td>{v['sharpe_ratio']:.2f}</td>"
            f"<td>{v['calmar_ratio']:.2f}</td>"
            f"<td>{v['num_trades']}</td>"
            f"</tr>"
        )
    table = "\n".join(rows)
    best = bt.get('best_scenario', '')
    recommended = 'weekly_long_only_risk_with_cost'
    return f"""
    <div class="section">
        <h2>📊 VectorBT 组合滚动回测（多场景对比）</h2>
        <div class="cards">
            <div class="card"><div class="card-title">夏普最高(无成本)</div><div class="card-meta" style="font-size:24px;color:#22c55e">{best}</div></div>
            <div class="card"><div class="card-title">推荐策略(带成本)</div><div class="card-meta" style="font-size:24px;color:#6366f1">{recommended}</div></div>
        </div>
        <table>
            <thead><tr><th>场景</th><th>年化</th><th>回撤</th><th>夏普</th><th>Calmar</th><th>交易次数</th></tr></thead>
            <tbody>
                {table}
            </tbody>
        </table>
    </div>
    """


def _build_news_sentiment_html():
    try:
        with open('multi_agent/data/news_sentiment.json', 'r', encoding='utf-8') as f:
            ns = json.load(f)
    except Exception as e:
        return f"<!-- news sentiment load failed: {e} -->"
    items = ns.get('items', [])[:3]
    if not items:
        return "<p>暂无舆情数据</p>"
    cards = []
    for it in items:
        emoji = {'积极': '🟢', '消极': '🔴'}.get(it['sentiment'], '⚪')
        summary = (it.get('llm_summary') or '暂无摘要').replace('<', '&lt;').replace('>', '&gt;')
        titles = '<br>'.join(it.get('latest_titles', [])[:2]).replace('<', '&lt;').replace('>', '&gt;')
        cards.append(
            f'<div class="card"><h3>{emoji} {it["name"]}({it["ticker"]}) {it["sentiment"]}</h3>'
            f'<p><b>LLM 摘要：</b>{summary}</p>'
            f'<p><b>最新标题：</b>{titles}</p></div>'
        )
    return "\n".join(cards)


def generate_portfolio_html():
    """基于本地 target_weights/stock_etf_rebalance_list 生成组合页面。"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tw_path = os.path.join(repo, 'multi_agent', 'data', 'target_weights.json')
    rb_path = os.path.join(repo, 'multi_agent', 'data', 'stock_etf_rebalance_list.json')

    tw = {}
    if os.path.exists(tw_path):
        with open(tw_path, 'r', encoding='utf-8') as f:
            tw = json.load(f)

    rb = {}
    if os.path.exists(rb_path):
        with open(rb_path, 'r', encoding='utf-8') as f:
            rb = json.load(f)

    total_value = rb.get('total_portfolio_value', 50000)
    long_amount = rb.get('long_amount', 0)
    short_amount = rb.get('short_amount', 0)
    skipped = rb.get('skipped_count', 0)
    date = tw.get('date', '') or rb.get('date', '')

    # 目标权重概览
    summary_rows = f"""
        <div class="stat-card">
            <div class="num">{tw.get('total_exposure', 0)*100:.1f}%</div>
            <div class="label">总敞口</div>
        </div>
        <div class="stat-card">
            <div class="num" style="color:#22c55e">{tw.get('long_exposure', 0)*100:.1f}%</div>
            <div class="label">做多</div>
        </div>
        <div class="stat-card">
            <div class="num" style="color:#ef4444">{tw.get('short_exposure', 0)*100:.1f}%</div>
            <div class="label">做空</div>
        </div>
        <div class="stat-card">
            <div class="num">{tw.get('net_exposure', 0)*100:+.1f}%</div>
            <div class="label">净敞口</div>
        </div>
    """

    # 持仓列表（目标权重）
    targets = tw.get('targets', [])
    targets = [t for t in targets if t.get('category') in ('个股', 'ETF')]
    targets = sorted(targets, key=lambda x: abs(x.get('target_weight', 0)), reverse=True)
    target_rows = ""
    for t in targets:
        sig = t.get('signal', 'neutral')
        color = {'bullish': '#22c55e', 'bearish': '#ef4444', 'neutral': '#94a3b8'}.get(sig, '#94a3b8')
        cn = {'bullish': '看多', 'bearish': '看空', 'neutral': '中性'}.get(sig, sig)
        target_rows += f"""
        <tr>
            <td><b>{t.get('ticker')}</b></td>
            <td>{t.get('name')}</td>
            <td>{t.get('category')}</td>
            <td>{t.get('sector')}</td>
            <td style="color:{color}">{cn}</td>
            <td>{t.get('target_weight', 0)*100:.2f}%</td>
            <td>{t.get('current_price', 0)}</td>
            <td>{t.get('target_price', 0)}</td>
            <td>{t.get('stop_loss', 0)}</td>
            <td title="{t.get('reason', '')}">{t.get('reason', '')[:30]}</td>
        </tr>"""

    # 调仓建议
    items = rb.get('items', [])
    action_rows = ""
    for item in items:
        if item.get('target_amount', 0) == 0 and item.get('action') in ('持有',):
            continue
        action_color = {'买入': '#22c55e', '减持/卖出': '#ef4444', '融券卖出': '#f97316', '不操作': '#94a3b8'}.get(item.get('action'), '#e2e8f0')
        action_rows += f"""
        <tr>
            <td style="color:{action_color}"><b>{item.get('action')}</b></td>
            <td>{item.get('ticker')}</td>
            <td>{item.get('name')}</td>
            <td>{item.get('category')}</td>
            <td>¥{item.get('target_amount', 0):,.0f}</td>
            <td>{item.get('target_weight', 0)*100:.2f}%</td>
            <td>{item.get('current_price', 0)}</td>
            <td>{item.get('constraint_note', '')}</td>
            <td title="{item.get('reason', '')}">{item.get('reason', '')[:30]}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>组合目标权重 - {date}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #e2e8f0; line-height: 1.6; }}
.container {{ max-width: 1400px; margin: 0 auto; padding: 20px; }}
.header {{ background: linear-gradient(135deg, #1e293b, #334155); padding: 30px; border-radius: 16px; margin-bottom: 24px; }}
.header h1 {{ font-size: 24px; margin-bottom: 8px; }}
.header .sub {{ color: #94a3b8; font-size: 14px; }}
.stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }}
.stat-card {{ background: #1e293b; border-radius: 12px; padding: 20px; text-align: center; }}
.stat-card .num {{ font-size: 32px; font-weight: bold; }}
.stat-card .label {{ color: #94a3b8; font-size: 12px; margin-top: 4px; }}
.section-title {{ font-size: 18px; font-weight: 600; margin: 24px 0 12px; padding-left: 12px; border-left: 4px solid #6366f1; }}
.nav {{ display: flex; gap: 12px; margin-bottom: 20px; flex-wrap: wrap; }}
.nav a {{ color: #94a3b8; text-decoration: none; padding: 6px 16px; border-radius: 8px; background: #1e293b; font-size: 13px; }}
.nav a:hover {{ background: #334155; color: #e2e8f0; }}
.footer {{ text-align: center; color: #475569; font-size: 12px; margin-top: 40px; padding: 20px; }}
table {{ width: 100%; border-collapse: collapse; background: #1e293b; border-radius: 12px; overflow: hidden; margin-bottom: 24px; font-size: 13px; }}
th {{ background: #334155; padding: 10px 12px; text-align: left; font-size: 12px; color: #94a3b8; text-transform: uppercase; }}
td {{ padding: 8px 12px; border-bottom: 1px solid #1e293b; font-size: 13px; }}
tr:hover {{ background: #334155; }}
.note {{ background: #1e293b; padding: 16px; border-radius: 12px; color: #cbd5e1; line-height: 1.8; margin-bottom: 24px; }}
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
        <h1>💼 目标权重组合</h1>
        <div class="sub">{date} · 基于 factor_score + 风险约束生成 · 组合市值 ¥{total_value:,.0f}</div>
    </div>

    <div class="stats-grid">
{summary_rows}
    </div>

    <div class="note">
        <p>📌 这不是雪球真实持仓，而是本地目标权重模拟盘。</p>
        <p>买入金额合计: <b>¥{long_amount:,.0f}</b> | 融券金额: <b>¥{short_amount:,.0f}</b> | 跳过 <b>{skipped}</b> 只（A股一手约束）。</p>
        <p>A 股个股不能做空，负权重仅输出为"减持/卖出"建议；ETF 做空需开通融券账户。</p>
    </div>

    <div class="section-title">🎯 目标持仓列表（股票+ETF）</div>
    <table>
        <thead><tr><th>代码</th><th>名称</th><th>类别</th><th>板块</th><th>信号</th><th>目标权重</th><th>现价</th><th>目标价</th><th>止损</th><th>理由</th></tr></thead>
        <tbody>
            {target_rows if target_rows else '<tr><td colspan="10" style="text-align:center;color:#64748b">暂无目标权重数据</td></tr>'}
        </tbody>
    </table>

    <div class="section-title">📝 调仓建议清单</div>
    <table>
        <thead><tr><th>操作</th><th>代码</th><th>名称</th><th>类别</th><th>目标金额</th><th>目标权重</th><th>现价</th><th>约束</th><th>理由</th></tr></thead>
        <tbody>
            {action_rows if action_rows else '<tr><td colspan="9" style="text-align:center;color:#64748b">暂无调仓建议</td></tr>'}
        </tbody>
    </table>

    <div class="footer">
        本地目标权重模拟盘 · 研究辅助，非投资建议 · 更新于 {now}
    </div>
</div>
</body>
</html>"""

    out_path = os.path.join(repo, 'docs', 'portfolio.html')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"✅ 组合页面生成: {out_path}")


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
    print(f"   回测口径: multi_period_backtest（方向验证已弃用）")

    generate_portfolio_html()
