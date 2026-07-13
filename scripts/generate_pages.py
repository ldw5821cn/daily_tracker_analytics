#!/usr/bin/env python3
"""统一生成 GitHub Pages 静态页面（prediction 风格）。"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'multi_agent'))
from core.backtest_utils import inject_backtest_metrics
from core.db import get_predictions_conn, get_latest_predictions, get_price_date_map, get_predictions_stats
from core.db import get_futures_positions as _db_get_futures_positions

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join(REPO_ROOT, "docs")
DB_PATH = os.path.join(REPO_ROOT, "multi_agent", "data", "llm_predictions.db")

SCENARIO_NAME_CN = {
    'daily_long_short_no_cost': '每日多空（无成本）',
    'daily_long_short_with_cost': '每日多空（含成本）',
    'weekly_long_only_no_cost': '每周只做多（无成本）',
    'weekly_long_only_with_cost': '每周只做多（含成本）',
    'weekly_long_only_risk_no_cost': '每周只做多+止损风控（无成本）',
    'weekly_long_only_risk_with_cost': '每周只做多+止损风控（含成本）',
}

SCENARIO_DESC = {
    'daily_long_short_no_cost': '每日按因子得分 Top10 做多、Bottom10 做空，不考虑交易成本',
    'daily_long_short_with_cost': '每日按因子得分 Top10 做多、Bottom10 做空，扣 0.1% 单边交易成本',
    'weekly_long_only_no_cost': '每周五选因子得分 Top10 等权做多，不考虑交易成本',
    'weekly_long_only_with_cost': '每周五选因子得分 Top10 等权做多，扣 0.1% 交易成本',
    'weekly_long_only_risk_no_cost': '每周五 Top10 等权 + 个股回撤 8% 止损 + 组合回撤 15% 清仓，无成本',
    'weekly_long_only_risk_with_cost': '每周五 Top10 等权 + 个股回撤 8% 止损 + 组合回撤 15% 清仓，含成本',
}

SIGNAL_EMOJI = {'bullish': '🔥', 'neutral': '➖', 'bearish': '❄️'}
SIGNAL_COLOR = {'bullish': '#ef4444', 'neutral': '#94a3b8', 'bearish': '#22c55e'}
SIGNAL_CN = {'bullish': '看多', 'neutral': '中性', 'bearish': '看空'}

TABS = [
    ('index.html', '🏠', '首页'),
    ('stocks.html', '📈', '个股'),
    ('us_market.html', '🇺🇸', '美股'),
    ('etfs.html', '📊', 'ETF'),
    ('futures.html', '📉', '期货'),
    ('prediction.html', '🏛️', '预测'),
    ('portfolio.html', '💼', '持仓组合'),
]

CSS = """<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Noto Sans SC', sans-serif; background: #0f172a; color: #e2e8f0; line-height: 1.6; }
.container { max-width: 1400px; margin: 0 auto; padding: 20px; }
.header { background: linear-gradient(135deg, #1e293b, #334155); padding: 30px; border-radius: 16px; margin-bottom: 24px; }
.header h1 { font-size: 24px; margin-bottom: 8px; }
.header .sub { color: #94a3b8; font-size: 14px; }
.stats-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; margin-bottom: 24px; }
@media (max-width: 768px) { .stats-grid { grid-template-columns: repeat(3, 1fr); } }
.stat-card { background: #1e293b; border-radius: 12px; padding: 16px 10px; text-align: center; }
.stat-card .num { font-size: 28px; font-weight: bold; white-space: nowrap; }
.stat-card .label { color: #94a3b8; font-size: 12px; margin-top: 4px; }
.section-title { font-size: 18px; font-weight: 600; margin: 24px 0 12px; padding-left: 12px; border-left: 4px solid #6366f1; }
.nav { display: flex; flex-direction: row; flex-wrap: wrap; justify-content: flex-start; align-items: center; gap: 10px; margin-bottom: 20px; }
.nav a { display: inline-block; white-space: nowrap; color: #94a3b8; text-decoration: none; padding: 8px 18px; border-radius: 20px; background: #1e293b; border: 1px solid #334155; font-size: 13px; }
.nav a:hover { background: #334155; color: #e2e8f0; }
.nav a.active { background: linear-gradient(135deg, #6366f1, #8b5cf6); color: #fff; border-color: transparent; }
.footer { text-align: center; color: #475569; font-size: 12px; margin-top: 40px; padding: 20px; }
.model-tag { display: inline-block; background: #6366f1; color: #fff; padding: 2px 8px; border-radius: 12px; font-size: 12px; margin-left: 8px; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; margin-bottom: 24px; }
.card { background: #1e293b; border-radius: 12px; padding: 16px; border-left: 4px solid #6366f1; }
.card.bull { border-left-color: #ef4444; }
.card.bear { border-left-color: #22c55e; }
.card-title { font-weight: 600; margin-bottom: 6px; }
.card-meta { color: #94a3b8; font-size: 12px; margin-bottom: 6px; }
.card-price { color: #e2e8f0; font-size: 13px; margin-bottom: 6px; }
.card-reason { color: #94a3b8; font-size: 12px; line-height: 1.5; }
.table-responsive { overflow-x: auto; -webkit-overflow-scrolling: touch; margin-bottom: 24px; border-radius: 12px; }
table { width: 100%; border-collapse: collapse; background: #1e293b; font-size: 13px; min-width: 800px; }
th { background: #334155; padding: 10px 12px; text-align: left; font-size: 12px; color: #94a3b8; text-transform: uppercase; }
td { padding: 8px 12px; border-bottom: 1px solid #1e293b; font-size: 13px; }
tr:hover { background: #334155; }
.note { background: #1e293b; padding: 16px; border-radius: 12px; color: #cbd5e1; line-height: 1.8; margin-bottom: 24px; }
.empty { text-align: center; color: #64748b; padding: 40px; }
</style>"""


def _load_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _load_db_stats():
    if not os.path.exists(DB_PATH):
        return None, None
    stats = get_predictions_stats()
    display_date = stats.get('latest_pred_date') or datetime.now().strftime('%Y-%m-%d')
    rows = get_latest_predictions(display_date)

    for p in rows:
        inject_backtest_metrics(p)
    rows = sorted(rows, key=lambda x: x.get('bt_score', 0), reverse=True)

    return {
        'display_date': display_date,
        'today_preds': stats.get('today_count', 0),
        'agentic_total': stats.get('total', 0),
        'today_details': rows,
    }, rows


def _build_nav(active):
    items = []
    for href, icon, label in TABS:
        cls = 'active' if href == active else ''
        items.append(f'<a href="{href}" class="{cls}">{icon} {label}</a>')
    return '<div class="nav">\n' + '\n'.join(items) + '\n</div>'


def _header(title, subtitle, tag=''):
    tag_html = f'<span class="model-tag">{tag}</span>' if tag else ''
    return f"""
    <div class="header">
        <h1>{title} {tag_html}</h1>
        <div class="sub">{subtitle}</div>
    </div>"""


def _stats(cards_html):
    return f'<div class="stats-grid">\n{cards_html}\n</div>'


def _stat_card(num, label, color=None):
    style = f' style="color:{color}"' if color else ''
    return f"""
        <div class="stat-card">
            <div class="num"{style}>{num}</div>
            <div class="label">{label}</div>
        </div>"""


def _row_html(p):
    sig = p.get('signal', 'neutral')
    conf = (p.get('confidence') or 0.5) * 100
    color = SIGNAL_COLOR.get(sig, '#94a3b8')
    emoji = SIGNAL_EMOJI.get(sig, '⚪')
    comp = json.loads(p.get('component_scores') or '{}') if isinstance(p.get('component_scores'), str) else p.get('component_scores', {})
    tech = comp.get('technical', '-') if isinstance(comp, dict) else '-'
    price_date = p.get('price_date') or ''
    price_date_fmt = price_date.replace('-', '/') if price_date else ''
    price_label = f"{price_date_fmt} 现价" if price_date_fmt else '现价'
    price_str = f"{price_label} {p.get('current_price', '')}".strip()
    return f"""
        <tr>
            <td><b>{p.get('ticker', '')}</b></td>
            <td>{p.get('name', '')}</td>
            <td>{p.get('sector', '')}</td>
            <td style="color:{color};font-weight:bold">{emoji} {SIGNAL_CN.get(sig, sig)}</td>
            <td>{p.get('weighted_score', 0)}</td>
            <td>{conf:.0f}%</td>
            <td>{p.get('horizon_1d', '')}</td>
            <td>{p.get('horizon_3d', '')}</td>
            <td>{p.get('horizon_5d', '')}</td>
            <td>{p.get('horizon_10d', '')}</td>
            <td>{p.get('bt_return_60d', 0):+.1f}%</td>
            <td>{p.get('bt_max_dd_60d', 0):.1f}%</td>
            <td>{p.get('bt_sharpe_60d', 0):.2f}</td>
            <td>{price_str}</td>
            <td>{p.get('target_price', '')}</td>
            <td>{p.get('stop_loss', '')}</td>
            <td>{(p.get('position_pct') or 0)*100:.0f}%</td>
            <td title="{p.get('reasoning', '')}">{tech}</td>
        </tr>"""


def _table_html(items, title):
    if not items:
        return f'<div class="section-title">{title}</div>\n<div class="empty">暂无数据</div>'
    order = {'bullish': 0, 'bearish': 1, 'neutral': 2}
    items = sorted(items, key=lambda x: (order.get(x.get('signal'), 99), -x.get('weighted_score', 0)))
    rows = "".join(_row_html(p) for p in items)
    return f"""
    <div class="section-title">{title}</div>
    <div class="table-responsive">
    <table>
        <thead><tr><th>代码</th><th>名称</th><th>板块</th><th>信号</th><th>评分</th><th>信心</th><th>1日</th><th>3日</th><th>5日</th><th>10日</th><th>60日收益</th><th>60日回撤</th><th>60日夏普</th><th>价格</th><th>目标</th><th>止损</th><th>仓位</th><th>技术分</th></tr></thead>
        <tbody>{rows}</tbody>
    </table>
    </div>"""


def _top_cards(rows, title, sig, color_class):
    filtered = [r for r in rows if r.get('signal') == sig]
    if not filtered:
        return ""
    for r in filtered:
        inject_backtest_metrics(r)
    top = sorted(filtered, key=lambda x: x.get('bt_score', 0), reverse=True)[:5]
    cards = ""
    for p in top:
        price_date = p.get('price_date') or ''
        price_date_fmt = price_date.replace('-', '/') if price_date else ''
        price_label = f"{price_date_fmt} 现价" if price_date_fmt else '现价'
        cards += f"""
            <div class="card {color_class}">
                <div class="card-title">{SIGNAL_EMOJI[sig]} {p.get('name', p.get('ticker'))} ({p.get('ticker')})</div>
                <div class="card-meta">评分 {p.get('weighted_score')} | 60日收益 {p.get('bt_return_60d', 0):+.1f}% | 回撤 {p.get('bt_max_dd_60d', 0):.1f}%</div>
                <div class="card-price">{price_label} {p.get('current_price')} | 目标 {p.get('target_price')} | 止损 {p.get('stop_loss')}</div>
                <div class="card-reason">{p.get('reasoning', '')}</div>
            </div>"""
    return f'<div class="section-title">{title}</div>\n<div class="cards">\n{cards}\n</div>'


def _portfolio_backtest_html():
    bt = _load_json(os.path.join(REPO_ROOT, 'multi_agent', 'data', 'vectorbt_portfolio_backtest.json'))
    if not bt or 'scenarios' not in bt:
        return ""
    rows = []
    for name, v in bt['scenarios'].items():
        if 'error' in v:
            continue
        highlight = name == 'weekly_long_only_risk_with_cost'
        style = 'style="background:#1e293b;font-weight:bold"' if highlight else ''
        cn_name = SCENARIO_NAME_CN.get(name, name)
        desc = SCENARIO_DESC.get(name, '')
        rows.append(
            f"<tr {style}><td>{cn_name}{' ⭐' if highlight else ''}</td>"
            f"<td style=\"color:{'#ef4444' if v['annualized_return'] >= 0 else '#22c55e'}\">{v['annualized_return']:+.2f}%</td>"
            f"<td>{v['max_drawdown']:.2f}%</td>"
            f"<td>{v['sharpe_ratio']:.2f}</td>"
            f"<td>{v['calmar_ratio']:.2f}</td>"
            f"<td>{v['num_trades']}</td>"
            f"<td>{desc}</td></tr>"
        )
    return f"""
    <div class="section-title">📊 VectorBT 组合滚动回测</div>
    <div class="table-responsive">
    <table>
        <thead><tr><th>场景</th><th>年化</th><th>回撤</th><th>夏普</th><th>Calmar</th><th>交易次数</th><th>含义</th></tr></thead>
        <tbody>{"".join(rows)}</tbody>
    </table>
    </div>"""


def _validation_html():
    val = _load_json(os.path.join(REPO_ROOT, 'multi_agent', 'data', 'morning_validation.json'))
    if not val or not val.get('overall'):
        return ""
    overall = val['overall']
    by_cat = val.get('by_category', {})
    cat_rows = ""
    for cat, stat in by_cat.items():
        cat_rows += f"""
            <div class="stat-card">
                <div class="num" style="color:{'#ef4444' if stat['accuracy'] >= 50 else '#22c55e'}">{stat['accuracy']:.1f}%</div>
                <div class="label">{cat} ({stat['correct']}/{stat['total']})</div>
            </div>"""
    return f"""
    <div class="section-title">🎯 昨日 1 日方向验证（{val.get('pred_date', '')} 预测）</div>
    <div class="stats-grid">
        <div class="stat-card">
            <div class="num" style="color:{'#ef4444' if overall['accuracy'] >= 50 else '#22c55e'}">{overall['accuracy']:.1f}%</div>
            <div class="label">总体准确率 ({overall['correct']}/{overall['total']})</div>
        </div>
        {cat_rows}
    </div>
"""


def _build_page_skeleton(title, body, active_tab, subtitle, tag=''):
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
{CSS}
</head>
<body>
<div class="container">
{_build_nav(active_tab)}
{_header(title.split(' - ')[0], subtitle, tag)}
{body}
    <div class="footer">
        数据由 Hermes Agent + 多Agent LLM 预测系统自动生成 · 研究辅助非投资建议 ·
        <a href="https://github.com/ldw5821cn/daily_tracker_analytics" style="color:#6366f1">GitHub</a>
    </div>
</div>
</body>
</html>"""


def generate_prediction_page(stats, rows, out_name='prediction.html'):
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    bullish = [r for r in rows if r.get('signal') == 'bullish']
    bearish = [r for r in rows if r.get('signal') == 'bearish']

    cards = ""
    cards += _top_cards(rows, '🏆 重点看多（按回测排序）', 'bullish', 'bull')
    cards += _top_cards(rows, '❄️ 重点看空（按回测排序）', 'bearish', 'bear')

    stats_cards = "".join([
        _stat_card('统一回测', 'multi_period_backtest'),
        _stat_card(stats['today_preds'], '今日预测'),
        _stat_card(stats['agentic_total'], '累计预测'),
        _stat_card(len(bullish), '看多', '#ef4444'),
        _stat_card(len(bearish), '看空', '#22c55e'),
    ])

    cat_tables = ""
    for cat in ['ETF', '个股', '期货']:
        items = [r for r in rows if r.get('category') == cat]
        if items:
            cat_tables += _table_html(items, f"📂 {cat} ({len(items)}只)")

    body = f"""
{stats_cards}
{cards}
{_validation_html()}
{_portfolio_backtest_html()}
{cat_tables}
"""
    price_dates = sorted(set(r.get('price_date') for r in rows if r.get('price_date')))
    price_date_str = f"价格日期: {', '.join(price_dates)}" if price_dates else "价格日期: 未知"
    title = f"🏛️ LLM 预测 - {now}"
    subtitle = f"{now} · 基于多 Agent 融合预测 ({price_date_str})"
    html = _build_page_skeleton(title, body, out_name, subtitle, tag='🧠 多Agent融合')
    _write(out_name, html)


def generate_category_page(rows, category, out_name, title_cn):
    items = [r for r in rows if r.get('category') == category]
    bullish = [r for r in items if r.get('signal') == 'bullish']
    bearish = [r for r in items if r.get('signal') == 'bearish']
    neutral = [r for r in items if r.get('signal') == 'neutral']

    stats_cards = "".join([
        _stat_card(len(items), '总数'),
        _stat_card(len(bullish), '看多', '#ef4444'),
        _stat_card(len(bearish), '看空', '#22c55e'),
        _stat_card(len(neutral), '中性'),
    ])

    cards = ""
    cards += _top_cards(items, '🏆 重点看多', 'bullish', 'bull')
    cards += _top_cards(items, '❄️ 重点看空', 'bearish', 'bear')

    body = f"""
{stats_cards}
{cards}
{_table_html(items, f'📂 {title_cn} ({len(items)}只)')}
"""
    price_dates = sorted(set(r.get('price_date') for r in items if r.get('price_date')))
    price_date_str = f"价格日期: {', '.join(price_dates)}" if price_dates else "价格日期: 未知"
    date = datetime.now().strftime('%Y-%m-%d %H:%M')
    title = f"{title_cn} - {date}"
    subtitle = f"{date} · 最新预测 {len(items)} 只 ({price_date_str})"
    html = _build_page_skeleton(title, body, out_name, subtitle)
    _write(out_name, html)


def generate_index_page(stats, rows):
    bullish = [r for r in rows if r.get('signal') == 'bullish']
    bearish = [r for r in rows if r.get('signal') == 'bearish']
    cats = {}
    for r in rows:
        cats.setdefault(r.get('category'), []).append(r)

    stats_cards = "".join([
        _stat_card(stats['today_preds'], '今日预测'),
        _stat_card(len(bullish), '看多', '#ef4444'),
        _stat_card(len(bearish), '看空', '#22c55e'),
        _stat_card(len(cats.get('个股', [])), '个股'),
        _stat_card(len(cats.get('ETF', [])), 'ETF'),
        _stat_card(len(cats.get('期货', [])), '期货'),
    ])

    cards = _top_cards(rows, '🔥 今日重点看多', 'bullish', 'bull')

    body = f"""
<div class="stats-grid">
{stats_cards}
</div>
{cards}
{_validation_html()}
{_portfolio_backtest_html()}
{_table_html(sorted(bullish, key=lambda x: x.get('bt_score', 0), reverse=True)[:10], '🏆 Top 10 看多个股/ETF/期货')}
"""
    date = datetime.now().strftime('%Y-%m-%d %H:%M')
    price_dates = sorted(set(r.get('price_date') for r in rows if r.get('price_date')))
    price_date_str = f"价格日期: {', '.join(price_dates)}" if price_dates else "价格日期: 未知"
    title = f"🏠 首页 - A股 & 期货 多维度投资分析 - {date}"
    subtitle = f"{date} · 首页概览 · {price_date_str}"
    html = _build_page_skeleton(title, body, 'index.html', subtitle)
    _write('index.html', html)


def generate_us_market_page():
    # 美股暂无实时预测，展示旧美股报告入口或说明
    body = """
    <div class="note">
        <p>🇺🇸 美股实时预测暂未接入。</p>
        <p>当前主要覆盖 A 股（个股/ETF）和 期货。</p>
        <p>如需美股分析，请先用现有美股报告或后续接入 Yahoo Finance / Alpha Vantage 数据源。</p>
    </div>
    """
    date = datetime.now().strftime('%Y-%m-%d %H:%M')
    title = f"🇺🇸 美股市场 - {date}"
    subtitle = f"{date} · 美股市场 · 实时预测待接入"
    html = _build_page_skeleton(title, body, 'us_market.html', subtitle)
    _write('us_market.html', html)


def generate_portfolio_page():
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    tw = _load_json(os.path.join(REPO_ROOT, 'multi_agent', 'data', 'target_weights.json'))
    rb = _load_json(os.path.join(REPO_ROOT, 'multi_agent', 'data', 'stock_etf_rebalance_list.json'))

    total_value = rb.get('total_portfolio_value', 50000)
    long_amount = rb.get('long_amount', 0)
    short_amount = rb.get('short_amount', 0)
    skipped = rb.get('skipped_count', 0)
    date = tw.get('date', '') or rb.get('date', '')

    # 从数据库读取价格日期
    price_date_map = get_price_date_map()

    stats_cards = "".join([
        _stat_card(f"{tw.get('total_exposure', 0)*100:.1f}%", '总敞口'),
        _stat_card(f"{tw.get('long_exposure', 0)*100:.1f}%", '做多', '#ef4444'),
        _stat_card(f"{tw.get('short_exposure', 0)*100:.1f}%", '做空', '#22c55e'),
        _stat_card(f"{tw.get('net_exposure', 0)*100:+.1f}%", '净敞口'),
    ])

    def _target_rows(items):
        # 排序：看多 -> 看空 -> 中性
        order = {'bullish': 0, 'bearish': 1, 'neutral': 2}
        items = sorted(items, key=lambda x: (order.get(x.get('signal', 'neutral'), 99), -abs(x.get('target_weight', 0))))
        rows = ""
        for t in items:
            sig = t.get('signal', 'neutral')
            color = SIGNAL_COLOR.get(sig, '#94a3b8')
            cn = SIGNAL_CN.get(sig, sig)
            price_date = price_date_map.get(t.get('ticker'), '')
            price_date_fmt = price_date.replace('-', '/') if price_date else ''
            price_label = f"{price_date_fmt} 现价" if price_date_fmt else '现价'
            rows += f"""
        <tr>
            <td><b>{t.get('ticker')}</b></td>
            <td>{t.get('name')}</td>
            <td style="color:{color}">{cn}</td>
            <td>{t.get('target_weight', 0)*100:.2f}%</td>
            <td>{price_label} {t.get('current_price', 0)}</td>
            <td>{t.get('target_price', 0)}</td>
            <td>{t.get('stop_loss', 0)}</td>
            <td title="{t.get('reason', '')}">{t.get('reason', '')[:40]}</td>
        </tr>"""
        return rows

    def _action_rows(items):
        rows = ""
        for item in items:
            action_color = {'买入': '#ef4444', '减持/卖出': '#22c55e', '融券卖出': '#22c55e', '不操作': '#94a3b8'}.get(item.get('action'), '#e2e8f0')
            price_date = price_date_map.get(item.get('ticker'), '')
            price_date_fmt = price_date.replace('-', '/') if price_date else ''
            price_label = f"{price_date_fmt} 现价" if price_date_fmt else '现价'
            rows += f"""
        <tr>
            <td style="color:{action_color}"><b>{item.get('action')}</b></td>
            <td>{item.get('ticker')}</td>
            <td>{item.get('name')}</td>
            <td>¥{item.get('target_amount', 0):,.0f}</td>
            <td>{item.get('target_weight', 0)*100:.2f}%</td>
            <td>{price_label} {item.get('current_price', 0)}</td>
            <td>{item.get('constraint_note', '')}</td>
        </tr>"""
        return rows

    # 按类别分组
    stock_targets = [t for t in tw.get('targets', []) if t.get('category') == '个股']
    etf_targets = [t for t in tw.get('targets', []) if t.get('category') == 'ETF']
    futures_targets = [t for t in tw.get('targets', []) if t.get('category') == '期货']

    stock_actions = [i for i in rb.get('items', []) if i.get('category') == '个股']
    etf_actions = [i for i in rb.get('items', []) if i.get('category') == 'ETF']

    # 期货模拟盘持仓
    futures_positions = _load_futures_positions()

    body = f"""
{stats_cards}
    <div class="note">
        <p>📌 本地目标权重模拟盘，非雪球真实持仓。</p>
        <p>买入金额合计: <b>¥{long_amount:,.0f}</b> | 融券金额: <b>¥{short_amount:,.0f}</b> | 跳过 <b>{skipped}</b> 只（A股一手约束）。</p>
        <p>A 股个股不能做空，负权重仅输出为"减持/卖出"建议；ETF 做空需开通融券账户。</p>
    </div>
    <div class="section-title">📈 股票持仓</div>
    <table>
        <thead><tr><th>代码</th><th>名称</th><th>信号</th><th>目标权重</th><th>现价</th><th>目标价</th><th>止损</th><th>理由</th></tr></thead>
        <tbody>{_target_rows(stock_targets) if stock_targets else '<tr><td colspan="8" class="empty">暂无股票目标持仓</td></tr>'}</tbody>
    </table>
    <div class="section-title">📝 股票调仓建议</div>
    <table>
        <thead><tr><th>操作</th><th>代码</th><th>名称</th><th>目标金额</th><th>目标权重</th><th>现价</th><th>约束</th></tr></thead>
        <tbody>{_action_rows(stock_actions) if stock_actions else '<tr><td colspan="7" class="empty">暂无股票调仓建议</td></tr>'}</tbody>
    </table>
    <div class="section-title">📊 ETF 持仓</div>
    <table>
        <thead><tr><th>代码</th><th>名称</th><th>信号</th><th>目标权重</th><th>现价</th><th>目标价</th><th>止损</th><th>理由</th></tr></thead>
        <tbody>{_target_rows(etf_targets) if etf_targets else '<tr><td colspan="8" class="empty">暂无 ETF 目标持仓</td></tr>'}</tbody>
    </table>
    <div class="section-title">📝 ETF 调仓建议</div>
    <table>
        <thead><tr><th>操作</th><th>代码</th><th>名称</th><th>目标金额</th><th>目标权重</th><th>现价</th><th>约束</th></tr></thead>
        <tbody>{_action_rows(etf_actions) if etf_actions else '<tr><td colspan="7" class="empty">暂无 ETF 调仓建议</td></tr>'}</tbody>
    </table>
    <div class="section-title">📉 期货持仓</div>
    <table>
        <thead><tr><th>代码</th><th>名称</th><th>信号</th><th>目标权重</th><th>现价</th><th>目标价</th><th>止损</th><th>理由</th></tr></thead>
        <tbody>{_target_rows(futures_targets) if futures_targets else '<tr><td colspan="8" class="empty">暂无期货目标持仓</td></tr>'}</tbody>
    </table>
    <div class="section-title">📝 期货模拟盘持仓</div>
    <table>
        <thead><tr><th>品种</th><th>方向</th><th>手数</th><th>开仓均价</th><th>当前价</th><th>浮盈/亏</th></tr></thead>
        <tbody>{futures_positions if futures_positions else '<tr><td colspan="6" class="empty">暂无期货持仓</td></tr>'}</tbody>
    </table>
"""
    title = f"💼 持仓组合 - {date}"
    subtitle = f"{date} · 目标权重组合 · 组合市值 ¥{total_value:,.0f}"
    html = _build_page_skeleton(title, body, 'portfolio.html', subtitle)
    _write('portfolio.html', html)


def _load_futures_positions():
    """通过 DAO 读取期货模拟盘持仓并生成 HTML 行。"""
    rows = _db_get_futures_positions(active_only=True)
    if not rows:
        return ""
    html = ""
    for r in rows:
        direction_cn = '多' if r.get('direction') == 'long' else '空'
        pnl = r.get('pnl_total', 0) or 0
        color = '#ef4444' if pnl >= 0 else '#22c55e'
        html += f"""
        <tr>
            <td><b>{r.get('contract')}</b></td>
            <td>{direction_cn}</td>
            <td>{r.get('lots')}</td>
            <td>{r.get('entry_price')}</td>
            <td>{r.get('current_price')}</td>
            <td style="color:{color}">{pnl:+.2f}</td>
        </tr>"""
    return html



def _write(name, html):
    path = os.path.join(DOCS_DIR, name)
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"✅ 生成: {path}")


if __name__ == '__main__':
    stats, rows = _load_db_stats()
    if stats is None:
        print('❌ 数据库不存在')
        sys.exit(1)

    generate_index_page(stats, rows)
    generate_category_page(rows, '个股', 'stocks.html', '📈 个股')
    generate_us_market_page()
    generate_category_page(rows, 'ETF', 'etfs.html', '📊 ETF')
    generate_category_page(rows, '期货', 'futures.html', '📉 期货')
    generate_prediction_page(stats, rows)
    generate_portfolio_page()

    print('✅ 全部页面生成完成')
