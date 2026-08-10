#!/usr/bin/env python3
"""统一生成 GitHub Pages 静态页面（prediction 风格）。"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'multi_agent'))
from core.backtest_utils import inject_backtest_metrics
from core.db import get_predictions_conn, get_latest_predictions, get_price_date_map, get_predictions_stats
from core.db import get_futures_positions as _db_get_futures_positions

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_DIR = os.path.join(REPO_ROOT, "docs")
ARCHIVE_DIR = os.path.join(DOCS_DIR, 'archive')
DB_PATH = os.path.join(REPO_ROOT, "multi_agent", "data", "llm_predictions.db")

SCENARIO_NAME_CN = {
    'daily_long_short_no_cost': '每日多空（无成本）',
    'daily_long_short_with_cost': '每日多空（含成本）',
    'weekly_long_only_no_cost': '每周只做多（无成本）',
    'weekly_long_only_with_cost': '每周只做多（含成本）',
    'weekly_long_only_risk_no_cost': '每周只做多+止损（无成本）',
    'weekly_long_only_risk_with_cost': '每周只做多+止损（含成本）',
}

SCENARIO_DESC = {
    'daily_long_short_no_cost': '每日按技术得分 Top 做多、Bottom 做空，无交易成本',
    'daily_long_short_with_cost': '每日按技术得分 Top 做多、Bottom 做空，扣 0.1% 单边成本',
    'weekly_long_only_no_cost': '每周五按技术得分判定下周方向，无成本',
    'weekly_long_only_with_cost': '每周五按技术得分判定下周方向，扣 0.1% 成本',
    'weekly_long_only_risk_no_cost': '每周五只做多 + 8% 止损，无成本',
    'weekly_long_only_risk_with_cost': '每周五只做多 + 8% 止损，含成本',
}

# 数据库中信号存的是中文，先映射到英文 canonical 再用于统计/排序/颜色
SIGNAL_CN_TO_EN = {'看多': 'bullish', '看空': 'bearish', '中性': 'neutral', 'weak_neutral': 'weak_neutral'}
SIGNAL_EN_TO_CN = {'bullish': '看多', 'bearish': '看空', 'neutral': '中性', 'weak_neutral': '弱中性'}

def _canonical_signal(p):
    sig = p.get('signal', 'neutral')
    return SIGNAL_CN_TO_EN.get(sig, sig)

SIGNAL_EMOJI = {'bullish': '🔥', 'neutral': '➖', 'bearish': '❄️', 'weak_neutral': '➖'}
# A股习惯：涨=红色，跌=绿色
SIGNAL_COLOR = {'bullish': '#ef4444', 'neutral': '#94a3b8', 'bearish': '#22c55e', 'weak_neutral': '#94a3b8'}
SIGNAL_CN = {'bullish': '看多', 'neutral': '中性', 'bearish': '看空', 'weak_neutral': '弱中性'}


def _color_for_return(value, default='#e2e8f0'):
    """A股习惯：涨=红(#ef4444)，跌=绿(#22c55e)；0或缺失用default。"""
    if value is None:
        return default
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v > 0:
        return '#ef4444'
    if v < 0:
        return '#22c55e'
    return default

# 分类页统计：把弱中性并入中性，统计时只看多/看空/中性三档
SIGNAL_GROUP = {'bullish': 'bullish', 'bearish': 'bearish', 'neutral': 'neutral', 'weak_neutral': 'neutral'}

TABS = [
    ('index.html', '🏠', '首页'),
    ('stocks.html', '📈', '个股'),
    ('us_market.html', '🇺🇸', '美股'),
    ('etfs.html', '📊', 'ETF'),
    ('futures.html', '📉', '期货'),
    ('prediction.html', '🏛️', '预测'),
    ('portfolio.html', '💼', '持仓组合'),
    ('xueqiu_returns.html', '💰', '雪球收益'),
    ('backtest.html', '📈', '回测'),
    ('reflection.html', '🧠', '复盘'),
    ('data_health.html', '❤️', '数据健康'),
]

CSS = """<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Noto Sans SC', sans-serif; background: #0f172a; color: #e2e8f0; line-height: 1.6; }
.container { max-width: 1400px; margin: 0 auto; padding: 20px; }
.header { background: linear-gradient(135deg, #1e293b, #334155); padding: 30px; border-radius: 16px; margin-bottom: 24px; }
.header h1 { font-size: 24px; margin-bottom: 8px; }
.header .sub { color: #94a3b8; font-size: 14px; }
.stats-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 12px; margin-bottom: 24px; }
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
.card.bull { border-left-color: #22c55e; }
.card.bear { border-left-color: #ef4444; }
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


def _latest_us_etf_quality_path():
    """返回 multi_agent/data/us_etf_quality/ 下最新的 JSON 报告路径。"""
    d = os.path.join(REPO_ROOT, 'multi_agent', 'data', 'us_etf_quality')
    if not os.path.isdir(d):
        return ''
    files = sorted(
        [f for f in os.listdir(d) if f.endswith('.json')],
        key=lambda f: os.path.getmtime(os.path.join(d, f)),
        reverse=True,
    )
    return os.path.join(d, files[0]) if files else ''


def _us_etf_quality_html(report: dict) -> str:
    """生成美股 ETF 质量评分 HTML 区块。"""
    if not report:
        return ''
    # 兼容两种格式：顶层是对象 {etfs:[...]} 或顶层数组
    etfs = report.get('etfs', []) if isinstance(report, dict) else (report if isinstance(report, list) else [])
    if not etfs:
        return ''
    generated = report.get('generated_at', '')[:19] if isinstance(report, dict) else ''
    rows = []
    for e in etfs:
        total = e.get('total', e.get('total_score', 0))
        grade = e.get('rating', e.get('grade', 'N/A'))
        sc = e.get('scores', {})
        color = '#22c55e' if grade == 'A' else '#f59e0b' if grade == 'B' else '#ef4444'
        rows.append(
            f"<tr><td><b>{e.get('ticker', '')}</b></td><td>{e.get('name', '')}</td>"
            f"<td style='color:{color};font-weight:bold'>{grade} ({total}/{18})</td>"
            f"<td>{e.get('expense_ratio_pct', 0):.3f}%</td><td>{e.get('aum_b', 0):.2f}B</td>"
            f"<td>{sc.get('tracking_error', 0)}</td><td>{e.get('top10_pct', 0):.1f}%</td>"
            f"<td>{sc.get('liquidity', 0)}</td><td>{sc.get('index_quality', 0)}</td>"
            f"<td title='{e.get('verdict', '')}'>{e.get('verdict', '')[:60]}</td></tr>"
        )
    return f"""
    <div class="section-title">🇺🇸 美股 ETF 质量评分（{generated or '最新'}）</div>
    <div class="table-responsive">
    <table>
        <thead><tr><th>代码</th><th>名称</th><th>评级</th><th>费率</th><th>AUM</th><th>跟踪误差</th><th>前10集中度</th><th>流动性</th><th>指数质量</th><th>结论</th></tr></thead>
        <tbody>{"".join(rows)}</tbody>
    </table>
    </div>
    <div class="note">
        <p>基于富途实时数据。评分维度：费用率（3）、跟踪误差（3）、规模（3）、板块集中度（3）、流动性（3）、指数质量（3）。</p>
    </div>
"""


def _macro_liquidity_html() -> str:
    """生成宏观流动性 HTML 卡片。"""
    path = _latest_macro_liquidity_path()
    if not path:
        return ''
    m = _load_json(path)
    if not m or 'assessment' not in m:
        return ''
    a = m.get('assessment', {})
    rating = a.get('rating', '')
    red_count = a.get('red_count', 0)
    red_alerts = a.get('red_alerts', [])
    fred = m.get('fred', {})
    proxies = m.get('proxies', {})
    
    fed = fred.get('fed_total_assets', {}).get('latest', {})
    tga = fred.get('tga_balance', {}).get('latest', {})
    sofr = fred.get('sofr', {}).get('latest', {})
    dgs10 = fred.get('dgs10', {}).get('latest', {})
    net = a.get('fed_net_liquidity', {})
    
    cards = ""
    if net:
        cards += _stat_card(f"{net.get('value_b', 0):.2f}B", "Fed 净流动性", '#ef4444' if red_count > 0 else '#22c55e')
        cards += _stat_card(f"{net.get('week_change_pct', 0):+.2f}%", "周环比", '#22c55e' if net.get('week_change_pct', 0) >= 0 else '#ef4444')
    if sofr:
        cards += _stat_card(f"{sofr.get('value', 0):.2f}%", f"SOFR ({sofr.get('date', '')})")
    if dgs10:
        cards += _stat_card(f"{dgs10.get('value', 0):.2f}%", f"10Y ({dgs10.get('date', '')})")
    if proxies.get('US.VIXY'):
        cards += _stat_card(f"{proxies['US.VIXY']['price']:.2f}", "VIXY 代理")
    if proxies.get('US.UUP'):
        cards += _stat_card(f"{proxies['US.UUP']['price']:.3f}", "UUP 美元")
    
    alerts_html = '<br>'.join(f'🔴 {x}' for x in red_alerts) if red_alerts else '无红色预警'
    return f"""
    <div class="section-title">🌍 宏观流动性 ({rating})</div>
    <div class="stats-grid">
{cards}
    </div>
    <div class="note">
        <p><b>综合评级：</b>{rating}</p>
        <p><b>红色预警：</b>{alerts_html}</p>
        <p>数据源：FRED 公开 CSV + 富途 ETF 代理（UUP/VIXY/FXY）。SOFR 最新日期 {sofr.get('date', 'N/A')}，10Y 最新日期 {dgs10.get('date', 'N/A')}。</p>
    </div>
"""


def _latest_macro_indicators_path() -> str:
    """返回最新 A 股资金面指标路径。"""
    d = os.path.join(REPO_ROOT, 'multi_agent', 'data', 'macro_indicators')
    if not os.path.isdir(d):
        return ''
    files = sorted(
        [f for f in os.listdir(d) if f.endswith('.json')],
        key=lambda f: os.path.getmtime(os.path.join(d, f)),
        reverse=True,
    )
    return os.path.join(d, files[0]) if files else ''


def _latest_regime_date() -> Optional[str]:
    """返回 warehouse 中 market_regime_features 的最新日期。"""
    try:
        conn = sqlite3.connect(os.path.join(REPO_ROOT, 'multi_agent', 'data', 'warehouse.db'))
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT MAX(date) as d FROM market_regime_features")
        row = cur.fetchone()
        conn.close()
        return row['d'] if row and row['d'] else None
    except Exception:
        return None


def _load_regime_features(date: Optional[str] = None) -> Optional[Dict]:
    """从 warehouse 读取 market_regime_features 标量数据。"""
    d = date or _latest_regime_date()
    if not d:
        return None
    try:
        conn = sqlite3.connect(os.path.join(REPO_ROOT, 'multi_agent', 'data', 'warehouse.db'))
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT feature_json FROM market_regime_features WHERE date=?", (d,))
        row = cur.fetchone()
        conn.close()
        if not row or not row['feature_json']:
            return None
        data = json.loads(row['feature_json'])
        scalar = data.get('scalar', {})
        scalar['date'] = d
        return scalar
    except Exception as e:
        print(f"[_load_regime_features] error: {e}")
        return None


def _market_regime_html() -> str:
    """生成 A 股市场状态（涨停/炸板/连板、行业/概念资金流向、LHB、资金面）HTML 区块。"""
    r = _load_regime_features()
    if not r:
        return ''
    date = r.get('date', '')
    up = r.get('up_count', 0)
    down = r.get('down_count', 0)
    breadth_ratio = r.get('breadth_ratio', 0)
    limit_up = r.get('limit_up', 0)
    limit_up_zhaban = r.get('limit_up_zhaban', 0)
    limit_up_lianban = r.get('limit_up_lianban', 0)
    limit_down = r.get('limit_down', 0)
    limit_down_lianban = r.get('limit_down_lianban', 0)
    zhaban_count = r.get('zhaban_count', 0)
    lianban_count = r.get('lianban_count', 0)

    ind_total = r.get('industry_total_net', 0)
    con_total = r.get('concept_total_net', 0)
    top5_industry = r.get('top5_industry_net', [])
    top5_concept = r.get('top5_concept_net', [])

    lhb_count = r.get('lhb_count', 0)
    lhb_inst_net = r.get('lhb_inst_net_buy', 0)
    lhb_inst_buy = r.get('lhb_inst_buy', 0)
    lhb_inst_sell = r.get('lhb_inst_sell', 0)
    lhb_limit_up = r.get('lhb_limit_up_with_inst', 0)
    lhb_limit_down = r.get('lhb_limit_down_with_inst', 0)

    margin = r.get('margin_total_balance', 0)
    pcr = r.get('option_pcr_avg', 0)

    # 市场温度：涨跌比 + 涨停/跌停差
    zt_dt_spread = limit_up - limit_down
    zt_color = '#ef4444' if zt_dt_spread > 0 else '#22c55e' if zt_dt_spread < 0 else '#94a3b8'

    cards = "".join([
        _stat_card(f"{up}/{down}", f"涨跌家数 ({breadth_ratio:.2f})", '#ef4444' if breadth_ratio > 1 else '#22c55e'),
        _stat_card(f"{limit_up}", "涨停", '#ef4444'),
        _stat_card(f"{limit_up_zhaban}", "炸板", '#f59e0b'),
        _stat_card(f"{limit_up_lianban}", "连板", '#ef4444'),
        _stat_card(f"{limit_down}", "跌停", '#22c55e'),
        _stat_card(f"{zt_dt_spread:+d}", "涨停-跌停", zt_color),
    ])

    flow_cards = "".join([
        _stat_card(f"{ind_total:+.2f}亿", "行业净流入", '#ef4444' if ind_total > 0 else '#22c55e'),
        _stat_card(f"{con_total:+.2f}亿", "概念净流入", '#ef4444' if con_total > 0 else '#22c55e'),
    ])

    lhb_cards = "".join([
        _stat_card(f"{lhb_count}", "龙虎榜家数"),
        _stat_card(f"{lhb_inst_net:+.3f}亿", "机构净买入", '#ef4444' if lhb_inst_net > 0 else '#22c55e'),
        _stat_card(f"{lhb_inst_buy:.3f}亿", "机构买入"),
        _stat_card(f"{lhb_inst_sell:.3f}亿", "机构卖出"),
    ])

    capital_cards = "".join([
        _stat_card(f"{margin:,.0f}亿", "两融余额") if margin else "",
        _stat_card(f"{pcr:.2f}", "期权 PCR") if pcr else "",
    ])

    def _top5_rows(items, label):
        if not items:
            return f'<p>暂无 {label} Top5 数据</p>'
        rows = []
        for it in items[:5]:
            name = it.get('name', it.get('行业', 'N/A'))
            val = it.get('net', it.get('net_amount', it.get('净额_亿元', 0)))
            color = '#ef4444' if float(val) > 0 else '#22c55e'
            rows.append(f"<tr><td>{name}</td><td style='color:{color}'>{val:+.2f}亿</td></tr>")
        return f"""
        <table>
            <thead><tr><th>{label}</th><th>净流入</th></tr></thead>
            <tbody>{''.join(rows)}</tbody>
        </table>
        """

    return f"""
    <div class="section-title">🔥 A 股市场状态 ({date})</div>
    <div class="stats-grid">
{cards}
    </div>
    <div class="section-title">💰 资金流向</div>
    <div class="stats-grid">
{flow_cards}
    </div>
    <div class="table-responsive" style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
        {_top5_rows(top5_industry, '行业 Top5')}
        {_top5_rows(top5_concept, '概念 Top5')}
    </div>
    <div class="section-title">🐉 龙虎榜机构动向</div>
    <div class="stats-grid">
{lhb_cards}
    </div>
    <div class="section-title">📊 资金面指标</div>
    <div class="stats-grid">
{capital_cards}
    </div>
    <div class="note">
        <p>涨停/炸板/连板、行业/概念净流入 Top5、龙虎榜机构席位、融资融券与期权 PCR 均来自 akshare / 交易所公开数据，每日盘后自动缓存。</p>
    </div>
"""


def _latest_macro_liquidity_path() -> str:
    """返回最新宏观流动性报告路径。"""
    d = os.path.join(REPO_ROOT, 'multi_agent', 'data', 'macro_liquidity')
    if not os.path.isdir(d):
        return ''
    files = sorted(
        [f for f in os.listdir(d) if f.endswith('.json')],
        key=lambda f: os.path.getmtime(os.path.join(d, f)),
        reverse=True,
    )
    return os.path.join(d, files[0]) if files else ''


def _latest_tech_earnings_path() -> str:
    """返回最新美股科技龙头财报 review 路径。"""
    d = os.path.join(REPO_ROOT, 'multi_agent', 'data', 'tech_earnings')
    if not os.path.isdir(d):
        return ''
    files = sorted(
        [f for f in os.listdir(d) if f.endswith('.html')],
        key=lambda f: os.path.getmtime(os.path.join(d, f)),
        reverse=True,
    )
    return os.path.join(d, files[0]) if files else ''


def _tech_earnings_html() -> str:
    """读取本地最新的美股科技龙头 review HTML 并内嵌。"""
    path = _latest_tech_earnings_path()
    if not path or not os.path.exists(path):
        return ''
    try:
        html = open(path, 'r', encoding='utf-8').read()
        # 只取 body 内容
        body_start = html.find('<body>')
        body_end = html.find('</body>')
        if body_start != -1 and body_end != -1:
            return html[body_start + len('<body>'):body_end]
        return html
    except Exception:
        return ''


def get_all_pred_dates():
    """返回数据库中所有预测日期（降序）。"""
    conn = get_predictions_conn()
    try:
        cur = conn.execute("SELECT DISTINCT pred_date FROM agentic_predictions ORDER BY pred_date DESC")
        return [r[0] for r in cur.fetchall() if r[0]]
    finally:
        conn.close()


def _get_validation_for_date(pred_date):
    """读取按 pred_date 组织的验证准确率文件。"""
    val = _load_json(os.path.join(REPO_ROOT, 'multi_agent', 'data', 'morning_validation.json'))
    if val and val.get('pred_date') == pred_date:
        return val
    return {}


def _get_reflection_for_date(pred_date):
    """读取按 pred_date 组织的反思摘要文件。"""
    refl = _load_json(os.path.join(REPO_ROOT, 'multi_agent', 'data', 'prediction_reflection.json'))
    if refl and refl.get('pred_date') == pred_date:
        return refl
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

    # 推荐策略分布统计
    scenario_counts = Counter()
    for p in rows:
        sc = p.get('recommended_scenario')
        scenario_counts[SCENARIO_NAME_CN.get(sc, sc or 'N/A')] += 1

    return {
        'display_date': display_date,
        'today_preds': stats.get('today_count', 0),
        'agentic_total': stats.get('total', 0),
        'today_details': rows,
        'scenario_counts': dict(scenario_counts.most_common()),
    }, rows


def _build_nav(active, dates=None):
    items = []
    for href, icon, label in TABS:
        cls = 'active' if href == active else ''
        items.append(f'<a href="{href}" class="{cls}">{icon} {label}</a>')
    # 最近 7 日归档下拉选择器
    if dates:
        options = ''.join(
            f'<option value="archive/{d}.html" {"selected" if f"archive/{d}.html" == active else ""}>{d}</option>'
            for d in dates[:7]
        )
        items.append(
            f'<select onchange="location.href=this.value" style="background:#1e293b;color:#94a3b8;border:1px solid #334155;border-radius:20px;padding:8px 14px;font-size:13px;outline:none;cursor:pointer">'
            f'<option value="" disabled>📅 历史归档</option>{options}</select>'
        )
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


def _stat_card(value, label, color=None):
    style = f' style="color:{color}"' if color else ''
    return f'        <div class="stat-card">\n            <div class="num"{style}>{value}</div>\n            <div class="label">{label}</div>\n        </div>\n'


def _stat_grid(cards_html):
    return f'<div class="stats-grid">\n{cards_html}\n</div>'


def _row_html(p, is_us=False):
    sig = _canonical_signal(p)
    conf = (p.get('confidence') or 0.5) * 100
    color = SIGNAL_COLOR.get(sig, '#94a3b8')
    emoji = SIGNAL_EMOJI.get(sig, '⚪')
    comp = json.loads(p.get('component_scores') or '{}') if isinstance(p.get('component_scores'), str) else p.get('component_scores', {})
    tech = comp.get('technical', '-') if isinstance(comp, dict) else '-'
    price_date = p.get('price_date') or ''
    price_date_fmt = price_date.replace('-', '/') if price_date else ''
    price_label = f"{price_date_fmt} 现价" if price_date_fmt else '现价'
    price_str = f"{price_label} {p.get('current_price', '')}".strip()
    sector = p.get('sector', '')
    if is_us:
        industry = p.get('industry', '')
        market_cap = p.get('market_cap', '')
        parts = [x for x in [sector, industry, market_cap] if x]
        sector = ' / '.join(parts[:2]) if parts else ''
    return f"""
        <tr>
            <td><b>{p.get('ticker', '')}</b></td>
            <td>{p.get('name', '')} {_compute_stability(_get_stock_fundamentals(p.get('ticker','')))[1] if p.get('category') == '个股' else ''}</td>
            <td>{sector}</td>
            <td style="color:{color};font-weight:bold">{emoji} {SIGNAL_EN_TO_CN.get(sig, sig)}</td>
            <td>{p.get('weighted_score', 0)}</td>
            <td>{conf:.0f}%</td>
            <td>{p.get('horizon_1d', '')}</td>
            <td>{p.get('horizon_3d', '')}</td>
            <td>{p.get('horizon_5d', '')}</td>
            <td>{p.get('horizon_10d', '')}</td>
            <td style="color:{_color_for_return(p.get('bt_return_60d', 0))}">{p.get('bt_return_60d', 0):+.1f}%</td>
            <td>{p.get('bt_max_dd_60d', 0):.1f}%</td>
            <td>{p.get('bt_sharpe_60d', 0):.2f}</td>
            <td title="{SCENARIO_DESC.get(p.get('recommended_scenario'), '')}">{SCENARIO_NAME_CN.get(p.get('recommended_scenario'), p.get('recommended_scenario', 'N/A'))}</td>
            <td style="color:{_color_for_return(p.get('recommended_return', 0))}">{p.get('recommended_return', 0):+.1f}%</td>
            <td>{p.get('recommended_dd', 0):.1f}%</td>
            <td>{price_str}</td>
            <td>{p.get('target_price', '')}</td>
            <td>{p.get('stop_loss', '')}</td>
            <td>{(p.get('position_pct') or 0)*100:.0f}%</td>
            <td title="{p.get('reasoning', '')}">{tech}</td>
        </tr>"""


def _table_html(items, title, is_us=False):
    if not items:
        return f'<div class="section-title">{title}</div>\n<div class="empty">暂无数据</div>'
    order = {'bullish': 0, 'bearish': 1, 'neutral': 2, 'weak_neutral': 2}
    items = sorted(items, key=lambda x: (order.get(_canonical_signal(x), 99), -x.get('weighted_score', 0)))
    rows = "".join(_row_html(p, is_us=is_us) for p in items)
    return f"""
    <div class="section-title">{title}</div>
    <div class="table-responsive">
    <table>
        <thead><tr><th>代码</th><th>名称</th><th>板块</th><th>信号</th><th>评分</th><th>信心</th><th>1日</th><th>3日</th><th>5日</th><th>10日</th><th>60日收益</th><th>60日回撤</th><th>60日夏普</th><th>推荐策略</th><th>策略收益</th><th>策略回撤</th><th>价格</th><th>目标</th><th>止损</th><th>仓位</th><th>技术分</th></tr></thead>
        <tbody>{rows}</tbody>
    </table>
    </div>"""


def _top_cards(rows, title, sig, color_class, with_chart=False, chart_height=180):
    filtered = [r for r in rows if _canonical_signal(r) == sig]
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
        chart_html = ""
        if with_chart:
            ticker = p.get('ticker', '')
            chart_id = f"chart_{ticker.replace('.', '_').replace('-', '_')}"
            ohlcv = _load_ohlcv_from_warehouse(ticker, days=60)
            if len(ohlcv) >= 20:
                data_json = json.dumps(ohlcv, ensure_ascii=False)
                chart_html = f"""
                <div id="{chart_id}" style="width:100%;height:{chart_height}px;margin-top:10px;border-radius:8px;background:#0f172a;"></div>
                <script>
                (function(){{
                    const chart = LightweightCharts.createChart(document.getElementById('{chart_id}'), {{
                        layout: {{ background: {{ color: '#0f172a' }}, textColor: '#94a3b8' }},
                        grid: {{ vertLines: {{ color: '#1e293b' }}, horzLines: {{ color: '#1e293b' }} }},
                        crosshair: {{ mode: LightweightCharts.CrosshairMode.Normal }},
                        rightPriceScale: {{ borderColor: '#1e293b' }},
                        timeScale: {{ borderColor: '#1e293b', timeVisible: false }},
                    }});
                    const series = chart.addCandlestickSeries({{
                        upColor: '#ef4444', downColor: '#22c55e',
                        borderUpColor: '#ef4444', borderDownColor: '#22c55e',
                        wickUpColor: '#ef4444', wickDownColor: '#22c55e'
                    }});
                    series.setData({data_json});
                    chart.timeScale().fitContent();
                }})();
                </script>
                """
        cards += f"""
            <div class="card {color_class}">
                <div class="card-title">{SIGNAL_EMOJI[sig]} {p.get('name', p.get('ticker'))} ({p.get('ticker')})</div>
                <div class="card-meta">评分 {p.get('weighted_score')} | 60日收益 <span style="color:{_color_for_return(p.get('bt_return_60d', 0))}">{p.get('bt_return_60d', 0):+.1f}%</span> | 回撤 {p.get('bt_max_dd_60d', 0):.1f}%</div>
                <div class="card-meta">推荐策略：{SCENARIO_NAME_CN.get(p.get('recommended_scenario'), 'N/A')} | 收益 <span style="color:{_color_for_return(p.get('recommended_return', 0))}">{p.get('recommended_return', 0):+.1f}%</span> | 回撤 {p.get('recommended_dd', 0):.1f}%</div>
                <div class="card-price">{price_label} {p.get('current_price')} | 目标 {p.get('target_price')} | 止损 {p.get('stop_loss')}</div>
                <div class="card-reason">{p.get('reasoning', '')}</div>
                {chart_html}
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
            f"<td style=\"color:{_color_for_return(v['annualized_return'])}\">{v['annualized_return']:+.2f}%</td>"
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


def _model_performance_html():
    """生成 warehouse 真实 5d 收益模型表现 HTML。"""
    mp = _load_json(os.path.join(REPO_ROOT, 'multi_agent', 'data', 'model_performance.json'))
    if not mp or 'categories' not in mp:
        return ''
    generated = mp.get('generated_at', '')[:19]
    rows = []
    for cat, st in mp['categories'].items():
        if st.get('status') != 'ok':
            rows.append(f'<tr><td>{cat}</td><td colspan="6" style="color:#94a3b8">数据不足（{st.get("n", 0)} 条）</td></tr>')
            continue
        rows.append(
            f'<tr><td><b>{cat}</b></td>'
            f'<td>{st.get("n", 0)}</td>'
            f'<td>{st.get("n_bull", 0)}</td>'
            f'<td>{st.get("n_bear", 0)}</td>'
            f'<td style="color:{"#ef4444" if (st.get("bullish_mean") or 0) >= 0 else "#22c55e"}">{st.get("bullish_mean") if st.get("bullish_mean") is not None else "-"}</td>'
            f'<td style="color:{"#ef4444" if (st.get("bearish_mean") or 0) >= 0 else "#22c55e"}">{st.get("bearish_mean") if st.get("bearish_mean") is not None else "-"}</td>'
            f'<td>{st.get("direction_accuracy", 0):.1f}%</td>'
            f'<td>{st.get("coverage", 0):.1f}%</td></tr>'
        )
    return f"""
    <div class="section-title">🏭 Warehouse 真实 5d 收益模型表现</div>
    <div class="note">
        <p><b>基于仓库实际收盘价的 5 日 forward return 回测。</b>当前样本来自 {mp.get('pred_date', '')} 前所有历史预测，共 {mp.get('ret_map_count', 0)} 条价格-收益记录。生成于 {generated}。</p>
        <p><b>说明：</b>看多信号偏多表示模型偏乐观；看多信号均值为负表示当前参数在 bull 阈值附近表现不佳。数据仍偏少，结果仅供参考，不用于直接调整参数。</p>
    </div>
    <div class="table-responsive">
    <table>
        <thead><tr><th>类别</th><th>样本</th><th>看多</th><th>看空</th><th>看多平均</th><th>看空平均</th><th>方向准确率</th><th>覆盖率</th></tr></thead>
        <tbody>{"".join(rows)}</tbody>
    </table>
    </div>"""


def _forward_return_html():
    """生成 forward return 回测统计 HTML。"""
    bt = _load_json(os.path.join(REPO_ROOT, 'multi_agent', 'data', 'prediction_backtest.json'))
    if not bt or 'summary' not in bt:
        return ''

    summary = bt.get('summary', {})
    portfolio = bt.get('portfolio_summary', {})
    rows = []
    for h in ['1d', '3d', '5d', '10d']:
        s = summary.get(h, {})
        p = portfolio.get(h, {})
        if not s:
            continue
        rows.append(
            f'<tr><td><b>{h}</b></td>'
            f'<td>{s.get("overall_mean_return", 0):+.2f}%</td>'
            f'<td>{s.get("overall_median_return", 0):+.2f}%</td>'
            f'<td>{s.get("overall_win_rate", 0):.1f}%</td>'
            f'<td>{s.get("overall_direction_accuracy", 0):.1f}%</td>'
            f'<td>{s.get("total", 0)}</td>'
            f'<td>{p.get("mean_return", 0):+.2f}%</td>'
            f'<td>{p.get("cumulative_return", 0):+.2f}%</td>'
            f'<td>{p.get("win_rate", 0):.1f}%</td></tr>'
        )
    if not rows:
        return ''

    sig_rows = []
    for h in ['1d', '3d', '5d', '10d']:
        s = summary.get(h, {})
        for sig, st in s.get('by_signal', {}).items():
            sig_rows.append(
                f'<tr><td>{h}</td><td>{sig}</td><td>{st.get("count", 0)}</td>'
                f'<td>{st.get("mean_return", 0):+.2f}%</td>'
                f'<td>{st.get("median_return", 0):+.2f}%</td>'
                f'<td>{st.get("win_rate", 0):.1f}%</td>'
                f'<td>{st.get("direction_accuracy", 0):.1f}%</td></tr>'
            )

    cat_rows = []
    for h in ['1d', '3d', '5d', '10d']:
        s = summary.get(h, {})
        for cat, st in s.get('by_category', {}).items():
            cat_rows.append(
                f'<tr><td>{h}</td><td>{cat}</td><td>{st.get("count", 0)}</td>'
                f'<td>{st.get("mean_return", 0):+.2f}%</td>'
                f'<td>{st.get("win_rate", 0):.1f}%</td>'
                f'<td>{st.get("direction_accuracy", 0):.1f}%</td></tr>'
            )

    date_range = bt.get('date_range', {})
    n_preds = bt.get('n_predictions', 0)
    n_recs = bt.get('n_records', 0)
    generated = bt.get('generated_at', '')

    return f"""
    <div class="section-title">📈 全历史预测 Forward Return 回测</div>
    <div class="note">
        <p><b>样本范围：</b>{date_range.get('start', '')} ~ {date_range.get('end', '')} · 共 {n_preds} 条预测 · {n_recs} 条记录 · 生成于 {generated}</p>
        <p>Forward Return = 以预测日收盘价为基准，未来 N 个交易日的实际收益率。推荐组合统计只取看多信号做等权持有。</p>
    </div>
    <div class="table-responsive">
    <table>
        <thead><tr><th>horizon</th><th>整体均值</th><th>整体中位</th><th>整体胜率</th><th>方向准确率</th><th>样本数</th><th>组合日均</th><th>组合累计</th><th>组合胜率</th></tr></thead>
        <tbody>{"".join(rows)}</tbody>
    </table>
    </div>
    <div class="section-title">📊 按信号方向</div>
    <div class="table-responsive">
    <table>
        <thead><tr><th>horizon</th><th>信号</th><th>样本数</th><th>均值</th><th>中位</th><th>胜率</th><th>方向准确率</th></tr></thead>
        <tbody>{"".join(sig_rows)}</tbody>
    </table>
    </div>
    <div class="section-title">📂 按资产类别</div>
    <div class="table-responsive">
    <table>
        <thead><tr><th>horizon</th><th>类别</th><th>样本数</th><th>均值</th><th>胜率</th><th>方向准确率</th></tr></thead>
        <tbody>{"".join(cat_rows)}</tbody>
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


def _archive_validation_html(pred_date):
    """为归档页生成验证准确率卡片。"""
    val = _get_validation_for_date(pred_date)
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
    <div class="section-title">🎯 验证准确率（{pred_date}）</div>
    <div class="stats-grid">
        <div class="stat-card">
            <div class="num" style="color:{'#ef4444' if overall['accuracy'] >= 50 else '#22c55e'}">{overall['accuracy']:.1f}%</div>
            <div class="label">总体准确率 ({overall['correct']}/{overall['total']})</div>
        </div>
        {cat_rows}
    </div>
"""


def _archive_reflection_html(pred_date):
    """为归档页生成反思摘要。"""
    refl = _get_reflection_for_date(pred_date)
    if not refl:
        return ""
    accuracy = refl.get('accuracy', 0)
    total = refl.get('total', 0)
    correct = refl.get('correct', 0)
    wrong = refl.get('wrong', 0)
    suggestions = refl.get('key_suggestions', [])
    llm_reflection = refl.get('llm_reflection', '')
    suggestions_html = ''.join(f'<li>{s}</li>' for s in suggestions) if suggestions else ''
    llm_html = llm_reflection.replace('\n', '<br>') if isinstance(llm_reflection, str) else ''
    return f"""
    <div class="section-title">🧠 反思摘要</div>
    <div class="note">
        <p><b>准确率 {accuracy}%</b> · 正确 {correct} / 错误 {wrong} / 总计 {total}</p>
        {f'<p><b>关键建议：</b></p><ul>{suggestions_html}</ul>' if suggestions else ''}
        {f'<p><b>LLM 深度反思：</b></p><p>{llm_html}</p>' if llm_html else ''}
    </div>
"""


def _build_page_skeleton(title, body, active_tab, subtitle, tag='', dates=None):
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<script src="https://unpkg.com/lightweight-charts@4.2.2/dist/lightweight-charts.standalone.production.js"></script>
{CSS}
</head>
<body>
<div class="container">
{_build_nav(active_tab, dates=dates)}
{_header(title.split(' - ')[0], subtitle, tag)}
{body}
    <div class="footer">
        数据由 Hermes Agent + 多Agent LLM 预测系统自动生成 · 研究辅助非投资建议 ·
        <a href="https://github.com/ldw5821cn/daily_tracker_analytics" style="color:#6366f1">GitHub</a>
    </div>
</div>
</body>
</html>"""


def generate_prediction_page(stats, rows, out_name='prediction.html', dates=None):
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    bullish = [r for r in rows if _canonical_signal(r) == 'bullish']
    bearish = [r for r in rows if _canonical_signal(r) == 'bearish']

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
{_stat_grid(stats_cards)}
{cards}
{_validation_html()}
{_portfolio_backtest_html()}\n{_forward_return_html()}\n{cat_tables}
"""
    price_dates = sorted(set(r.get('price_date') for r in rows if r.get('price_date')))
    price_date_str = f"价格日期: {', '.join(price_dates)}" if price_dates else "价格日期: 未知"
    title = f"🏛️ LLM 预测 - {now}"
    subtitle = f"{now} · 基于多 Agent 融合预测 ({price_date_str})"
    html = _build_page_skeleton(title, body, out_name, subtitle, tag='🧠 多Agent融合', dates=dates)
    _write(out_name, html)


def generate_archive_page(pred_date, dates=None):
    """生成 docs/archive/YYYY-MM-DD.html 归档页面。"""
    rows = get_latest_predictions(pred_date)
    for p in rows:
        inject_backtest_metrics(p)
    rows = sorted(rows, key=lambda x: x.get('bt_score', 0), reverse=True)

    bullish = [r for r in rows if _canonical_signal(r) == 'bullish']
    bearish = [r for r in rows if _canonical_signal(r) == 'bearish']

    stats_cards = "".join([
        _stat_card(len(rows), '当日预测'),
        _stat_card(len(bullish), '看多', '#ef4444'),
        _stat_card(len(bearish), '看空', '#22c55e'),
    ])

    cards = ""
    cards += _top_cards(rows, '🏆 重点看多（按回测排序）', 'bullish', 'bull')
    cards += _top_cards(rows, '❄️ 重点看空（按回测排序）', 'bearish', 'bear')

    cat_tables = ""
    for cat in ['ETF', '个股', '期货']:
        items = [r for r in rows if r.get('category') == cat]
        if items:
            cat_tables += _table_html(items, f"📂 {cat} ({len(items)}只)")

    body = f"""
{_stat_grid(stats_cards)}
{cards}
{_validation_html()}
{_model_performance_html()}
{_portfolio_backtest_html()}
{_forward_return_html()}
{cat_tables}
"""
    price_dates = sorted(set(r.get('price_date') for r in rows if r.get('price_date')))
    price_date_str = f"价格日期: {', '.join(price_dates)}" if price_dates else "价格日期: 未知"
    title = f"🏛️ 历史预测归档 - {pred_date}"
    subtitle = f"{pred_date} · 多 Agent 融合预测 ({price_date_str})"
    out_name = f"archive/{pred_date}.html"
    html = _build_page_skeleton(title, body, out_name, subtitle, tag='历史归档', dates=dates)
    _write(out_name, html)


def generate_category_page(rows, category, out_name, title_cn, dates=None):
    items = [r for r in rows if r.get('category') == category]
    bullish = [r for r in items if _canonical_signal(r) == 'bullish']
    bearish = [r for r in items if _canonical_signal(r) == 'bearish']
    neutral = [r for r in items if _canonical_signal(r) in ('neutral', 'weak_neutral')]

    stats_cards = "".join([
        _stat_card(len(items), '总数'),
        _stat_card(len(bullish), '看多', '#ef4444'),
        _stat_card(len(bearish), '看空', '#22c55e'),
        _stat_card(len(neutral), '中性'),
    ])

    cards = ""
    cards += _top_cards(items, '🏆 重点看多', 'bullish', 'bull', with_chart=True)
    cards += _top_cards(items, '❄️ 重点看空', 'bearish', 'bear', with_chart=True)

    body = f"""
{_stat_grid(stats_cards)}
{cards}
{_table_html(items, f'📂 {title_cn} ({len(items)}只)')}
"""
    price_dates = sorted(set(r.get('price_date') for r in items if r.get('price_date')))
    price_date_str = f"价格日期: {', '.join(price_dates)}" if price_dates else "价格日期: 未知"
    date = datetime.now().strftime('%Y-%m-%d %H:%M')
    title = f"{title_cn} - {date}"
    subtitle = f"{date} · 最新预测 {len(items)} 只 ({price_date_str})"
    html = _build_page_skeleton(title, body, out_name, subtitle, dates=dates)
    _write(out_name, html)


def generate_index_page(stats, rows, dates=None):
    bullish = [r for r in rows if _canonical_signal(r) == 'bullish']
    bearish = [r for r in rows if _canonical_signal(r) == 'bearish']
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
        _stat_card(len(cats.get('US', [])), '美股'),
    ])

    scenario_counts = stats.get('scenario_counts', {})
    scenario_cards = "".join([
        _stat_card(count, f"{name}", '#94a3b8')
        for name, count in scenario_counts.items()
    ])

    cards = _top_cards(rows, '🔥 今日重点看多', 'bullish', 'bull')

    body = f"""
<div class="stats-grid">
{stats_cards}
</div>
{cards}
{_market_regime_html()}
{_validation_html()}
<div class="section-title">📊 推荐策略分布</div>
<div class="stats-grid">
{scenario_cards}
</div>
{_model_performance_html()}
{_portfolio_backtest_html()}
{_forward_return_html()}
{_table_html(sorted(bullish, key=lambda x: x.get('bt_score', 0), reverse=True)[:10], '🏆 Top 10 看多个股/ETF/期货')}
"""
    date = datetime.now().strftime('%Y-%m-%d %H:%M')
    price_dates = sorted(set(r.get('price_date') for r in rows if r.get('price_date')))
    price_date_str = f"价格日期: {', '.join(price_dates)}" if price_dates else "价格日期: 未知"
    title = f"🏠 首页 - A股 & 期货 多维度投资分析 - {date}"
    subtitle = f"{date} · 首页概览 · {price_date_str}"
    html = _build_page_skeleton(title, body, 'index.html', subtitle, dates=dates)
    _write('index.html', html)


def generate_us_market_page(dates=None):
    # 美股：从数据库读取 category='US' 的最新预测
    all_rows = get_latest_predictions()
    rows = [r for r in all_rows if r.get('category') == 'US']
    for r in rows:
        inject_backtest_metrics(r)
    bullish = [r for r in rows if _canonical_signal(r) == 'bullish']
    bearish = [r for r in rows if _canonical_signal(r) == 'bearish']
    neutral = [r for r in rows if _canonical_signal(r) in ('neutral', 'weak_neutral')]

    stats_cards = "".join([
        _stat_card(len(rows), '美股标的'),
        _stat_card(len(bullish), '看多', '#ef4444'),
        _stat_card(len(bearish), '看空', '#22c55e'),
        _stat_card(len(neutral), '中性'),
    ])

    body = f"""
<div class="stats-grid">
{stats_cards}
</div>
{_macro_liquidity_html()}
{_table_html(rows, f'🇺🇸 美股预测 ({len(rows)}只)', is_us=True)}
<div class="note">
    <p>美股数据源：富途 OpenD / akshare 新浪美股前复权 / yfinance 备用。价格为美元。</p>
    <p>宏观流动性数据来自 FRED 公开 CSV + 富途 ETF 代理（UUP/VIXY/FXY）。</p>
</div>
"""
    date = datetime.now().strftime('%Y-%m-%d %H:%M')
    price_dates = sorted(set(r.get('price_date') for r in rows if r.get('price_date')))
    price_date_str = f"价格日期: {', '.join(price_dates)}" if price_dates else "价格日期: 未知"
    title = f"🇺🇸 美股市场 - {date}"
    subtitle = f"{date} · 美股市场 · {len(rows)} 只 · {price_date_str}"
    html = _build_page_skeleton(title, body, 'us_market.html', subtitle, dates=dates)
    _write('us_market.html', html)


def _load_xueqiu_portfolios():
    """加载雪球组合配置与最近一次调仓状态。"""
    cfg = _load_json(os.path.join(REPO_ROOT, 'multi_agent', 'config', 'xueqiu_config.json'))
    state = _load_json(os.path.join(REPO_ROOT, 'multi_agent', 'data', 'portfolio_state.json'))
    portfolios = []
    xq_weights = (state.get('xueqiu_r') or {}).get('weights', {})
    for code, p in cfg.get('portfolios', {}).items():
        if p.get('source') == 'futures_simulator':
            continue
        holdings = []
        source_label = p.get('source', '')
        if source_label == 'fixed':
            for code6, w in (p.get('holdings') or {}).items():
                holdings.append({'ticker': code6, 'name': '', 'weight': w, 'actual': None})
        elif source_label == 'allocator':
            # 使用最近一次执行后的真实权重
            for k, w in xq_weights.items():
                # 去掉 SH/SZ 前缀
                code6 = k[2:] if k.startswith(('SH', 'SZ', 'BJ')) else k
                if code6.isdigit():
                    holdings.append({'ticker': code6, 'name': '', 'weight': w/100.0, 'actual': w/100.0})
        portfolios.append({
            'code': code,
            'name': p.get('name', code),
            'source': source_label,
            'holdings': holdings,
        })
    return portfolios


def _load_xueqiu_portfolios():
    """加载雪球组合配置与最近一次调仓状态。"""
    cfg = _load_json(os.path.join(REPO_ROOT, 'multi_agent', 'config', 'xueqiu_config.json'))
    state = _load_json(os.path.join(REPO_ROOT, 'multi_agent', 'data', 'portfolio_state.json'))
    portfolios = []
    xq_weights = (state.get('xueqiu_r') or {}).get('weights', {})
    for code, p in cfg.get('portfolios', {}).items():
        if p.get('source') == 'futures_simulator':
            continue
        holdings = []
        source_label = p.get('source', '')
        if source_label == 'fixed':
            for code6, w in (p.get('holdings') or {}).items():
                holdings.append({'ticker': code6, 'name': '', 'weight': w, 'actual': None})
        elif source_label == 'allocator':
            for k, w in xq_weights.items():
                code6 = k[2:] if k.startswith(('SH', 'SZ', 'BJ')) else k
                if code6.isdigit():
                    holdings.append({'ticker': code6, 'name': '', 'weight': w/100.0, 'actual': w/100.0})
        portfolios.append({
            'code': code,
            'name': p.get('name', code),
            'source': source_label,
            'holdings': holdings,
        })
    return portfolios


def generate_portfolio_page(dates=None):
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    tw = _load_json(os.path.join(REPO_ROOT, 'multi_agent', 'data', 'target_weights.json'))
    rb = _load_json(os.path.join(REPO_ROOT, 'multi_agent', 'data', 'stock_etf_rebalance_list.json'))

    total_value = tw.get('total_value', rb.get('total_portfolio_value', 50000))
    long_amount = rb.get('long_amount', 0)
    short_amount = rb.get('short_amount', 0)
    skipped = rb.get('skipped_count', 0)
    date = tw.get('date', '') or rb.get('date', '') or datetime.now().strftime('%Y-%m-%d')

    # 从 target_weights 直接显示目标持仓（当 rebalance list 为空时兜底）
    all_targets = tw.get('targets', [])
    if not all_targets:
        # 兼容旧字段
        all_targets = rb.get('items', [])

    # 从数据库读取价格日期
    price_date_map = get_price_date_map()

    # 加载 ETF 质量评分（美股）
    us_etf_quality = _load_json(_latest_us_etf_quality_path())

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
            <td>{t.get('name')} {_compute_stability(_get_stock_fundamentals(t.get('ticker','')))[1]}</td>
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
            <td>{item.get('name')} {_compute_stability(_get_stock_fundamentals(item.get('ticker','')))[1]}</td>
            <td>¥{item.get('target_amount', 0):,.0f}</td>
            <td>{item.get('target_weight', 0)*100:.2f}%</td>
            <td>{price_label} {item.get('current_price', 0)}</td>
            <td>{item.get('constraint_note', '')}</td>
        </tr>"""
        return rows

    # 按类别分组
    stock_targets = [t for t in all_targets if t.get('category') == '个股']
    etf_targets = [t for t in all_targets if t.get('category') == 'ETF']
    futures_targets = [t for t in all_targets if t.get('category') == '期货']
    us_targets = [t for t in all_targets if t.get('category') == 'US']

    stock_actions = [i for i in rb.get('items', []) if i.get('category') == '个股']
    etf_actions = [i for i in rb.get('items', []) if i.get('category') == 'ETF']

    # 期货模拟盘持仓
    futures_positions = _load_futures_positions()

    xq_portfolios = _load_xueqiu_portfolios()

    # 构建 ticker -> name/price 映射（从最新预测）
    conn = get_predictions_conn()
    try:
        cur = conn.execute("SELECT ticker, name, current_price, price_date FROM agentic_predictions WHERE pred_date=(SELECT MAX(pred_date) FROM agentic_predictions)")
        rows = cur.fetchall()
        _name_map = {r['ticker']: r['name'] for r in rows}
        _price_map = {r['ticker']: r['current_price'] for r in rows}
        _pdate_map = {r['ticker']: r['price_date'] for r in rows}
    finally:
        conn.close()

    def _xueqiu_rows(holdings):
        rows = ""
        for h in holdings:
            ticker = h.get('ticker', '')
            name = h.get('name') or _name_map.get(ticker, '')
            price_date = _pdate_map.get(ticker, '')
            current_price = _price_map.get(ticker, 0) or 0
            # 当最新预测表中无价格/名称时，实时补一次行情
            if not name or not current_price:
                try:
                    from core.data_layer import get_realtime_price
                    rt = get_realtime_price(ticker)
                    if rt:
                        if not name:
                            name = rt.get('name', '')
                        if not current_price:
                            current_price = rt.get('price', 0)
                            price_date = '实时'
                except Exception:
                    pass
            price_date_fmt = price_date.replace('-', '/') if price_date else ''
            price_label = f"{price_date_fmt} 现价" if price_date_fmt else '现价'
            price_str = f"{price_label} {current_price:.2f}" if current_price else f"{price_label} —"
            w = h.get('weight', 0)
            rows += f"""
        <tr>
            <td><b>{ticker}</b></td>
            <td>{name}</td>
            <td>{w*100:.2f}%</td>
            <td>{price_str}</td>
        </tr>"""
        return rows

    xq_sections = ""
    for xp in xq_portfolios:
        h = xp.get('holdings', [])
        xq_sections += f"""
    <div class="section-title">📌 雪球 {xp['code']} ({xp['name']})</div>
    <table>
        <thead><tr><th>代码</th><th>名称</th><th>目标权重</th><th>现价</th></tr></thead>
        <tbody>{_xueqiu_rows(h) if h else '<tr><td colspan="4" class="empty">暂无持仓</td></tr>'}</tbody>
    </table>"""

    body = f"""
{_stat_grid(stats_cards)}
    <div class="note">
        <p>📌 本地目标权重模拟盘，非雪球真实持仓。股票/ETF 以雪球组合实际持仓为准。</p>
        <p>买入金额合计: <b>¥{long_amount:,.0f}</b> | 融券金额: <b>¥{short_amount:,.0f}</b> | 跳过 <b>{skipped}</b> 只（A股一手约束）。</p>
        <p>A 股个股不能做空，负权重仅输出为"减持/卖出"建议；ETF 做空需开通融券账户。</p>
    </div>
{xq_sections}
    {_us_etf_quality_html(us_etf_quality)}
    {_tech_earnings_html()}
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
    <div class="section-title">🇺🇸 美股目标持仓</div>
    <table>
        <thead><tr><th>代码</th><th>名称</th><th>信号</th><th>目标权重</th><th>现价</th><th>目标价</th><th>止损</th><th>理由</th></tr></thead>
        <tbody>{_target_rows(us_targets) if us_targets else '<tr><td colspan="8" class="empty">暂无美股目标持仓</td></tr>'}</tbody>
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
    html = _build_page_skeleton(title, body, 'portfolio.html', subtitle, dates=dates)
    _write('portfolio.html', html)



def _contract_cn(contract: str) -> str:
    """将期货合约代码转换为中文名称。"""
    code = contract.rstrip('0123456789')
    cn = {
        'RM':'菜粕','OI':'菜油','CF':'棉花','ZN':'沪锌','HC':'热卷',
        'CU':'沪铜','AL':'沪铝','AU':'黄金','AG':'白银','RB':'螺纹钢',
        'I':'铁矿石','JM':'焦煤','J':'焦炭','MA':'甲醇','TA':'PTA',
        'EG':'乙二醇','FG':'玻璃','SA':'纯碱','RU':'橡胶','NR':'20号胶',
        'SC':'原油','FU':'燃油','LU':'低硫燃油','BU':'沥青','L':'聚乙烯',
        'PP':'聚丙烯','PG':'液化气','EB':'苯乙烯','SM':'硅锰','SF':'硅铁',
        'SP':'纸浆','AP':'苹果','CJ':'红枣','CY':'棉纱','PF':'短纤',
        'UR':'尿素','V':'PVC','PB':'沪铅','SN':'沪锡','NI':'沪镍',
        'SS':'不锈钢','BC':'国际铜','SI':'工业硅','LH':'生猪','JD':'鸡蛋',
        'M':'豆粕','Y':'豆油','P':'棕榈油','SR':'白糖','AO':'氧化铝',
    }.get(code, contract)
    return f"{cn}({contract})"

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
            <td><b>{_contract_cn(r.get('contract', ''))}</b></td>
            <td>{direction_cn}</td>
            <td>{r.get('lots')}</td>
            <td>{r.get('entry_price')}</td>
            <td>{r.get('current_price')}</td>
            <td style="color:{color}">{pnl:+.2f}</td>
        </tr>"""
    return html



def _load_ohlcv_from_warehouse(ticker: str, days: int = 60):
    """从 warehouse.daily_bar 读取最近 N 日 K 线，用于 lightweight-charts。"""
    try:
        conn = sqlite3.connect(os.path.join(REPO_ROOT, 'multi_agent', 'data', 'warehouse.db'))
        conn.row_factory = sqlite3.Row
        since = (datetime.now() - timedelta(days=days * 1.5)).strftime('%Y-%m-%d')
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume FROM daily_bar WHERE ticker=? AND date>=? ORDER BY date",
            (ticker, since)
        ).fetchall()
        conn.close()
        return [
            {'time': r['date'], 'open': round(r['open'], 3), 'high': round(r['high'], 3),
             'low': round(r['low'], 3), 'close': round(r['close'], 3), 'volume': round(r['volume'], 0)}
            for r in rows
        ]
    except Exception:
        return []

def _get_stock_fundamentals(ticker: str) -> dict:
    """获取个股基本面数据：优先 fundamentals_cache，其次 DB component_scores。"""
    # 优先读取每日财务缓存
    try:
        d = os.path.join(REPO_ROOT, 'multi_agent', 'data', 'fundamentals_cache')
        if os.path.isdir(d):
            files = sorted([f for f in os.listdir(d) if f.endswith('.json')], reverse=True)
            if files:
                with open(os.path.join(d, files[0]), encoding='utf-8') as f:
                    cache = json.load(f)
                fd = (cache.get('fundamentals') or {}).get(str(ticker).zfill(6))
                if fd and (fd.get('roe') or fd.get('pe_ratio') or fd.get('market_cap')):
                    return fd
    except Exception:
        pass
    # 回退：DB component_scores
    try:
        conn = sqlite3.connect(os.path.join(REPO_ROOT, 'multi_agent', 'data', 'llm_predictions.db'))
        cur = conn.execute('SELECT component_scores FROM agentic_predictions WHERE ticker=? ORDER BY pred_date DESC LIMIT 1', (ticker,))
        row = cur.fetchone()
        conn.close()
        if row and row[0]:
            sc = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            return sc.get('fundamental', {})
    except Exception:
        pass
    return {}


def _compute_stability(fd: dict) -> tuple:
    """根据基本面数据计算稳健型评分(0-100)和标签。"""
    score = 50
    mcap = fd.get('market_cap', 0)
    pe = fd.get('pe_ratio', 50)
    div = fd.get('dividend_yield', 0)
    pb = fd.get('pb_ratio', 5)
    if mcap > 5000: score += 25
    elif mcap > 1000: score += 20
    elif mcap > 500: score += 10
    if 8 <= pe <= 25: score += 15
    elif 25 < pe <= 40: score += 5
    elif pe < 0 or pe > 100: score -= 10
    if div > 5: score += 20
    elif div > 3: score += 15
    elif div > 2: score += 5
    if pb < 2: score += 10
    elif pb < 4: score += 5
    elif pb > 10: score -= 5
    score = max(0, min(100, score))
    tag = '🛡️稳健型' if score >= 75 else '📈成长型' if score <= 45 else '⚖️均衡型'
    return score, tag


def _load_ohlcv_from_warehouse(ticker: str, days: int = 60):
    """从 warehouse.daily_bar 读取最近 N 日 K 线，用于 lightweight-charts。"""
    try:
        conn = sqlite3.connect(os.path.join(REPO_ROOT, 'multi_agent', 'data', 'warehouse.db'))
        conn.row_factory = sqlite3.Row
        since = (datetime.now() - timedelta(days=days * 1.5)).strftime('%Y-%m-%d')
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume FROM daily_bar WHERE ticker=? AND date>=? ORDER BY date",
            (ticker, since)
        ).fetchall()
        conn.close()
        return [
            {'time': r['date'], 'open': round(r['open'], 3), 'high': round(r['high'], 3),
             'low': round(r['low'], 3), 'close': round(r['close'], 3), 'volume': round(r['volume'], 0)}
            for r in rows
        ]
    except Exception:
        return []


def _write(name, html):
    path = os.path.join(DOCS_DIR, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"✅ 生成: {path}")


def generate_backtest_page(dates=None):
    """生成 Forward Return 回测独立页面。"""
    body = f"""
{_model_performance_html()}
{_forward_return_html()}
{_portfolio_backtest_html()}
"""
    title = "📈 回测验证"
    subtitle = "全历史预测 forward return 回测 · 按信号方向/资产类别拆解"
    html = _build_page_skeleton(title, body, 'backtest.html', subtitle, tag='📊 数据驱动回测', dates=dates)
    _write('backtest.html', html)


def generate_data_health_page(dates=None):
    """生成数据健康检查页面。"""
    dh = _load_json(os.path.join(REPO_ROOT, 'multi_agent', 'data', 'data_health_check.json'))
    if not dh:
        return
    today = dh.get('today', '')
    generated = dh.get('generated_at', '')[:19]
    status = dh.get('status', 'unknown')
    issues = dh.get('issues', [])
    warehouse = dh.get('warehouse', {})
    predictions = dh.get('predictions', {})
    trainable = dh.get('trainable_status', {})

    status_color = '#22c55e' if status == 'ok' else '#ef4444'
    status_cn = '正常' if status == 'ok' else '异常'

    wh_rows = ""
    for t, st in warehouse.items():
        if isinstance(st, dict) and 'count' in st:
            lag = st.get('lag_days', 'N/A')
            up = st.get('up_to_date', '')
            up_cn = '✅' if up else '⚠️' if up is False else ''
            wh_rows += f"<tr><td><b>{t}</b></td><td>{st.get('count', 0):,}</td><td>{st.get('min_date', '-')}</td><td>{st.get('max_date', '-')}</td><td>{lag}</td><td>{up_cn}</td></tr>"
        elif isinstance(st, dict):
            for cat, cst in st.items():
                lag = cst.get('lag_days', 'N/A')
                wh_rows += f"<tr><td>{t}.{cat}</td><td>{cst.get('count', 0):,}</td><td>-</td><td>{cst.get('max_date', '-')}</td><td>{lag}</td><td></td></tr>"

    pred_cat = predictions.get('latest_by_category', {})
    pred_rows = ""
    for cat, n in pred_cat.items():
        pred_rows += f"<tr><td>{cat}</td><td>{n}</td></tr>"

    train_rows = ""
    for cat, st in trainable.items():
        trainable_flag = st.get('trainable', False)
        color = '#22c55e' if trainable_flag else '#ef4444'
        train_rows += f"<tr><td>{cat}</td><td>{st.get('evaluable_days', 0)}</td><td style='color:{color}'>{'可训练' if trainable_flag else '不可训练'}</td><td>{st.get('note', '')}</td></tr>"

    issues_html = ''.join(f'<li>{i}</li>' for i in issues) or '<li>无异常</li>'

    body = f"""
    <div class="stats-grid">
        <div class="stat-card">
            <div class="num" style="color:{status_color}">{status_cn}</div>
            <div class="label">数据健康状态</div>
        </div>
        <div class="stat-card">
            <div class="num">{today}</div>
            <div class="label">检查日期</div>
        </div>
        <div class="stat-card">
            <div class="num">{generated}</div>
            <div class="label">生成时间</div>
        </div>
    </div>

    <div class="section-title">⚠️ 检查结果</div>
    <div class="note">
        <ul>{issues_html}</ul>
    </div>

    <div class="section-title">🏭 Warehouse 表状态</div>
    <div class="table-responsive">
    <table>
        <thead><tr><th>表</th><th>记录数</th><th>最早日期</th><th>最新日期</th><th>滞后天数</th><th>最新</th></tr></thead>
        <tbody>{wh_rows}</tbody>
    </table>
    </div>

    <div class="section-title">📝 预测最新分布</div>
    <div class="note">
        <p>最新预测日期：<b>{predictions.get('latest_pred_date', '-')}</b> · 总数 <b>{predictions.get('total_predictions', 0):,}</b> · 滞后 <b>{predictions.get('pred_lag_days', 'N/A')}</b> 天</p>
    </div>
    <div class="table-responsive">
    <table>
        <thead><tr><th>类别</th><th>数量</th></tr></thead>
        <tbody>{pred_rows}</tbody>
    </table>
    </div>

    <div class="section-title">🧠 参数优化训练状态</div>
    <div class="table-responsive">
    <table>
        <thead><tr><th>类别</th><th>可评估天数</th><th>状态</th><th>说明</th></tr></thead>
        <tbody>{train_rows}</tbody>
    </table>
    </div>
"""
    title = f"❤️ 数据健康 - {today}"
    subtitle = f"{today} · Warehouse / 预测 / 训练状态检查"
    html = _build_page_skeleton(title, body, 'data_health.html', subtitle, tag='📡 监控', dates=dates)
    _write('data_health.html', html)


def generate_reflection_page(dates=None):
    refl_path = os.path.join(REPO_ROOT, 'multi_agent', 'data', 'prediction_reflection.json')
    refl_history_path = os.path.join(REPO_ROOT, 'multi_agent', 'data', 'prediction_reflection_history.jsonl')
    refl = _load_json(refl_path) or {}
    # 历史文件作为 fallback：只有当前 reflection 为空/过旧时，才用历史文件补全
    history_lines = []
    try:
        with open(refl_history_path, 'r', encoding='utf-8') as f:
            history_lines = [json.loads(line) for line in f if line.strip()]
    except Exception:
        pass
    if history_lines:
        latest_hist = max(history_lines, key=lambda x: x.get('pred_date', '') or '')
        # 如果当前 reflection 没有 llm_reflection 内容，且历史中有，则补全
        if not refl.get('llm_reflection') and latest_hist.get('llm_reflection'):
            refl = latest_hist
            try:
                with open(refl_path, 'w', encoding='utf-8') as f:
                    json.dump(refl, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
        # 如果历史更新，则采用历史（正常情况不应发生，主文件应由脚本最新写入）
        elif latest_hist.get('pred_date', '') > refl.get('pred_date', ''):
            refl = latest_hist
            try:
                with open(refl_path, 'w', encoding='utf-8') as f:
                    json.dump(refl, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
    if not refl:
        return
    pred_date = refl.get('pred_date', '未知')
    accuracy = refl.get('accuracy', 0)
    total = refl.get('total', 0)
    correct = refl.get('correct', 0)
    wrong = refl.get('wrong', 0)
    by_signal = refl.get('by_signal', {})
    by_category = refl.get('by_category', {})
    feature_compare = refl.get('feature_compare', {})
    component_compare = refl.get('component_compare', {})
    llm_reflection = refl.get('llm_reflection', '')

    def _signal_error_rows(data):
        rows = ""
        for sig, stat in data.items():
            rows += f"\n        <tr><td>{sig}</td><td>{stat.get('total', 0)}</td><td>{stat.get('wrong', 0)}</td><td>{stat.get('error_rate', 0)}%</td></tr>"
        return rows

    def _divergence_rows(data):
        rows = ""
        for key, stat in data.items():
            if not isinstance(stat, dict):
                continue
            rows += f"\n        <tr><td>{key}</td><td>{stat.get('correct_avg', 0)}</td><td>{stat.get('wrong_avg', 0)}</td></tr>"
            by_sig = stat.get('by_wrong_signal', {})
            for sig, d in by_sig.items():
                if isinstance(d, dict):
                    rows += f"\n        <tr><td style='padding-left:24px;color:#94a3b8'>└ {sig}错误样本</td><td>{d.get('avg', 0)}</td><td>n={d.get('n', 0)}</td></tr>"
        return rows

    def _compare_rows(data):
        rows = ""
        for key, stat in data.items():
            if not isinstance(stat, dict):
                continue
            rows += f"\n        <tr><td>{key}</td><td>{stat.get('correct_avg', 0)}</td><td>{stat.get('wrong_avg', 0)}</td></tr>"
        return rows

    llm_html = llm_reflection.replace('\n', '<br>')
    # 简单 markdown 标题加粗转换，提升可读性
    import re
    llm_html = re.sub(r'^(#{1,6})\s*(.+)$', r'<strong>\2</strong>', llm_html, flags=re.MULTILINE)
    llm_html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', llm_html)

    divergence_analysis = refl.get('divergence_analysis', {})

    diagnostic_path = os.path.join(REPO_ROOT, 'multi_agent', 'data', '2026-08-07_bearish_diagnostic.json')
    diagnostic = _load_json(diagnostic_path) or {}

    diagnostic_html = ""
    if diagnostic:
        diagnostic_html = f"""
    <div class="section-title">🎯 错误看空信号诊断</div>
    <div class="note">
        <p><strong>问题：</strong>{diagnostic.get('summary', '')}</p>
        <p>看空信号 {diagnostic.get('statistics', {}).get('total_bearish')} 条，错误 {diagnostic.get('statistics', {}).get('wrong_bearish')} 条，错误率 {diagnostic.get('statistics', {}).get('wrong_rate')}% | 错误看空平均加权分 {diagnostic.get('statistics', {}).get('avg_weighted_score_wrong_bearish')}（ bear 阈值 {diagnostic.get('statistics', {}).get('threshold_bear')}）</p>
        <p><strong>根因：</strong>{diagnostic.get('root_cause', '')}</p>
        <p><strong>已采取措施：</strong>{diagnostic.get('action_taken', {}).get('parameter_adjustment', '')} {diagnostic.get('action_taken', {}).get('why_not_hard_rule', '')}</p>
    </div>
"""

    body = f"""
    <div class="header">
        <h1>🧠 每日预测反思 </h1>
        <div class="sub">{pred_date} 预测 · 验证准确率 {accuracy}% ({correct}/{total}) · 错误 {wrong} 个</div>
    </div>

    <div class="stats-grid">
        <div class="stat-card">
            <div class="num" style="color:{'#ef4444' if accuracy >= 50 else '#22c55e'}">{accuracy}%</div>
            <div class="label">总体准确率</div>
        </div>
        <div class="stat-card">
            <div class="num">{correct}</div>
            <div class="label">正确</div>
        </div>
        <div class="stat-card">
            <div class="num">{wrong}</div>
            <div class="label">错误</div>
        </div>
    </div>

    <div class="section-title">📈 按信号方向错误率</div>
    <div class="table-responsive">
    <table>
        <thead><tr><th>信号</th><th>总数</th><th>错误</th><th>错误率</th></tr></thead>
        <tbody>{_signal_error_rows(by_signal)}</tbody>
    </table>
    </div>

    <div class="section-title">📂 按资产类别错误率</div>
    <div class="table-responsive">
    <table>
        <thead><tr><th>类别</th><th>总数</th><th>错误</th><th>错误率</th></tr></thead>
        <tbody>{_signal_error_rows(by_category)}</tbody>
    </table>
    </div>

    <div class="section-title">🔍 正确 vs 错误特征对比</div>
    <div class="table-responsive">
    <table>
        <thead><tr><th>特征</th><th>正确样本平均</th><th>错误样本平均</th></tr></thead>
        <tbody>{_compare_rows(feature_compare)}</tbody>
    </table>
    </div>

    <div class="section-title">🧩 分项得分对比</div>
    <div class="table-responsive">
    <table>
        <thead><tr><th>分项</th><th>正确样本平均</th><th>错误样本平均</th></tr></thead>
        <tbody>{_compare_rows(component_compare)}</tbody>
    </table>
    </div>

    <div class="section-title">⚡ 因子背离度分析</div>
    <div class="table-responsive">
    <table>
        <thead><tr><th>背离指标</th><th>正确样本平均</th><th>错误样本平均</th></tr></thead>
        <tbody>{_divergence_rows(divergence_analysis)}</tbody>
    </table>
    </div>
{diagnostic_html}
    <div class="section-title">🤖 LLM 深度反思</div>
    <div class="note">{llm_html}</div>
"""
    title = f"🧠 复盘 - {pred_date}"
    subtitle = f"{pred_date} 预测验证后反思 · 生成于 {refl.get('generated_at', '')[:19]}"
    html = _build_page_skeleton(title, body, 'reflection.html', subtitle, dates=dates)
    _write('reflection.html', html)


def generate_xueqiu_returns_page(dates=None):
    """生成雪球组合收益跟踪页面。"""
    path = os.path.join(REPO_ROOT, 'multi_agent', 'data', 'xueqiu_portfolio_returns.json')
    data = _load_json(path)
    if not data or not data.get('portfolios'):
        return

    portfolios = data['portfolios']
    today = datetime.now().strftime('%Y-%m-%d')

    # 汇总卡片
    cards = ""
    for code, p in portfolios.items():
        total = p.get('total_return_pct', 0)
        color = _color_for_return(total)
        cards += f"""
        <div class="card" style="border-left-color:{color}">
            <div class="card-title">{p.get('name', code)} <span style="font-size:12px;color:#94a3b8">{code}</span></div>
            <div class="card-meta">最新净值 {p.get('latest_nav', 0):.4f} · {p.get('latest_date', '-')}</div>
            <div class="card-price" style="color:{color};font-size:24px;font-weight:bold">{total:+.2f}%</div>
            <div class="card-meta">最高 {p.get('max_return_pct', 0):.2f}% ({p.get('max_return_date', '-')}) · 最低 {p.get('min_return_pct', 0):.2f}% ({p.get('min_return_date', '-')})</div>
        </div>
        """

    # 历史曲线表格
    rows = ""
    # 取所有历史日期并合并
    all_dates = set()
    for p in portfolios.values():
        all_dates.update(h.get('date', '') for h in p.get('history', []))
    all_dates = sorted(all_dates, reverse=True)

    for d in all_dates[:30]:
        row_cells = f"<td>{d}</td>"
        for code in portfolios.keys():
            hist = portfolios[code].get('history', [])
            match = next((h for h in hist if h.get('date') == d), None)
            if match:
                pct = match.get('percent', 0)
                color = _color_for_return(pct)
                row_cells += f"<td style='color:{color}'>{pct:+.2f}%</td>"
            else:
                row_cells += "<td>-</td>"
        rows += f"<tr>{row_cells}</tr>"

    header_cells = "<th>日期</th>" + "".join(f"<th>{portfolios[code].get('name', code)}</th>" for code in portfolios.keys())

    body = f"""
    <div class="header">
        <h1>💰 雪球组合收益跟踪</h1>
        <div class="sub">每日自动更新 · 最后更新 {data.get('last_updated', '-')}</div>
    </div>

    <div class="cards">{cards}</div>

    <div class="section-title">📅 最近30日每日收益</div>
    <div class="table-responsive">
    <table>
        <thead><tr>{header_cells}</tr></thead>
        <tbody>{rows}</tbody>
    </table>
    </div>

    <div class="note">
        <p>数据来源：雪球组合净值 API（https://xueqiu.com/cubes/nav_daily/all.json）。累计收益 = 最新净值 - 1。</p>
    </div>
    """
    title = f"💰 雪球收益 - {today}"
    subtitle = f"{today} · 雪球组合累计收益跟踪"
    html = _build_page_skeleton(title, body, 'xueqiu_returns.html', subtitle, dates=dates)
    _write('xueqiu_returns.html', html)


if __name__ == '__main__':
    stats, rows = _load_db_stats()
    if stats is None:
        print('❌ 数据库不存在')
        sys.exit(1)

    dates = get_all_pred_dates()
    generate_index_page(stats, rows, dates=dates)
    generate_category_page(rows, '个股', 'stocks.html', '📈 个股', dates=dates)
    generate_us_market_page(dates=dates)
    generate_category_page(rows, 'ETF', 'etfs.html', '📊 ETF', dates=dates)
    generate_category_page(rows, '期货', 'futures.html', '📉 期货', dates=dates)
    generate_prediction_page(stats, rows, dates=dates)
    generate_portfolio_page(dates=dates)
    generate_backtest_page(dates=dates)
    generate_data_health_page(dates=dates)
    generate_reflection_page(dates=dates)
    generate_xueqiu_returns_page(dates=dates)
    for d in dates:
        generate_archive_page(d, dates=dates)

    print('✅ 全部页面生成完成')
