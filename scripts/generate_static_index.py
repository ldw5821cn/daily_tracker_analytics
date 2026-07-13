#!/usr/bin/env python3
"""生成分类式 GitHub Pages 首页 + 各类型独立页面

顶部 Tab 导航：ETF / 个股 / 期货 / 综合
每个页面内按日期倒序分组，日期下展示当天该类型的所有报告。
"""
import os
import re
import json
import sys
import sqlite3
from datetime import datetime
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(SCRIPT_DIR)
DOCS_DIR = os.path.join(REPO_DIR, "docs")
REPORTS_DIR = os.path.join(DOCS_DIR, "reports")
WATCHLIST_PATH = os.path.join(REPO_DIR, "multi_agent", "watchlist.json")
DB_PATH = os.path.join(REPO_DIR, "multi_agent", "data", "llm_predictions.db")

# 注入 core 路径，使 watchlist 和 backtest_utils 可用
sys.path.insert(0, os.path.join(REPO_DIR, "multi_agent"))
from core.backtest_utils import parse_backtest_summary

TYPE_TITLES = {
    "ETF": "ETF 基金分析",
    "个股": "股票分析",
    "期货": "期货趋势",
    "美股": "美股市场",
    "综合": "综合报告",
}

TYPE_ICONS = {
    "ETF": "📈",
    "个股": "📊",
    "期货": "⚡",
    "美股": "🇺🇸",
    "综合": "📋",
}

TYPE_FILES = {
    "ETF": "etfs.html",
    "个股": "stocks.html",
    "期货": "futures.html",
    "美股": "us_market.html",
    "综合": "comprehensive.html",
}


def load_watchlist():
    """加载 watchlist，按 ticker 映射 category 和名称
    合并 multi_agent/watchlist.json 和 core.watchlist 的默认列表
    """
    merged = {}

    # 1. 先加载 core.watchlist 的默认列表（包含高股息组合等）
    try:
        from core.watchlist import DEFAULT_STOCKS
        for item in DEFAULT_STOCKS:
            merged[item["ticker"]] = item
    except Exception as e:
        print(f"⚠️ 加载 core.watchlist 默认列表失败: {e}")

    # 2. 再用 watchlist.json 覆盖（用户自定义的最新列表）
    if os.path.exists(WATCHLIST_PATH):
        try:
            with open(WATCHLIST_PATH, "r", encoding="utf-8") as f:
                items = json.load(f)
            for item in items:
                merged[item["ticker"]] = item
        except Exception as e:
            print(f"⚠️ 加载 watchlist.json 失败: {e}")

    return merged


def load_agentic_predictions():
    """从 agentic_predictions 读取最新一天的预测数据"""
    if not os.path.exists(DB_PATH):
        return None, None, []
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    latest = cur.execute("SELECT MAX(pred_date) FROM agentic_predictions").fetchone()[0]
    if not latest:
        conn.close()
        return None, None, []
    rows = cur.execute("""
        SELECT ticker, name, sector, category, signal, confidence, weighted_score,
               horizon_1d, horizon_3d, horizon_5d, horizon_10d,
               current_price, target_price, stop_loss, position_pct, reasoning, backtest_summary
        FROM agentic_predictions
        WHERE pred_date=?
        ORDER BY category, weighted_score DESC
    """, (latest,)).fetchall()
    conn.close()
    return latest, [dict(r) for r in rows], rows


def _signal_class(signal):
    if signal == 'bullish':
        return 'bullish'
    elif signal == 'bearish':
        return 'bearish'
    return 'neutral'


def _signal_cn(signal):
    if signal == 'bullish':
        return '看多'
    elif signal == 'bearish':
        return '看空'
    return '中性'




def build_agentic_table(rows, page_type, top_n=50):
    """根据页面类型构建实时预测表格 HTML；按回测得分排序。"""
    filtered = [r for r in rows if r.get('category') == page_type]
    if not filtered:
        return ""

    # 注入统一回测指标
    for r in filtered:
        bt = parse_backtest_summary(r.get('backtest_summary'))
        r.update(bt)
    filtered.sort(key=lambda x: x['bt_score'], reverse=True)

    bullish = [r for r in filtered if r.get('signal') == 'bullish']
    bearish = [r for r in filtered if r.get('signal') == 'bearish']
    neutral = [r for r in filtered if r.get('signal') == 'neutral']

    lines = [
        f'<div class="section-title">🔥 {page_type} 实时预测（{len(filtered)}只 | 看多{len(bullish)} 看空{len(bearish)} 中性{len(neutral)}）</div>',
        '<table><tr><th>名称</th><th>代码</th><th>板块</th><th>信号</th><th>评分</th><th>60日收益</th><th>60日回撤</th><th>60日夏普</th><th>现价</th><th>目标</th><th>止损</th><th>仓位</th></tr>',
    ]

    for r in filtered[:top_n]:
        sig = r.get('signal', 'neutral')
        sig_cls = _signal_class(sig)
        sig_cn = _signal_cn(sig)
        pos = (r.get('position_pct') or 0) * 100
        lines.append(
            f'<tr><td class="name">{r.get("name", r["ticker"])}</td>'
            f'<td>{r["ticker"]}</td>'
            f'<td>{r.get("sector", "")}</td>'
            f'<td class="{sig_cls}">{sig_cn}</td>'
            f'<td>{r.get("weighted_score", 0)}</td>'
            f'<td>{r.get("bt_return_60d", 0):+.1f}%</td>'
            f'<td>{r.get("bt_max_dd_60d", 0):.1f}%</td>'
            f'<td>{r.get("bt_sharpe_60d", 0):.2f}</td>'
            f'<td>{r.get("current_price", "")}</td>'
            f'<td>{r.get("target_price", "")}</td>'
            f'<td>{r.get("stop_loss", "")}</td>'
            f'<td>{pos:.0f}%</td></tr>'
        )
    lines.append('</table>')
    return "\n".join(lines)


def build_agentic_top_cards(rows, top_n=5):
    """构建今日重点推荐卡片；按回测得分排序。"""
    for r in rows:
        bt = parse_backtest_summary(r.get('backtest_summary'))
        r.update(bt)
    bullish = sorted([r for r in rows if r.get('signal') == 'bullish'],
                     key=lambda x: x['bt_score'], reverse=True)[:top_n]
    if not bullish:
        return ""
    cards = []
    for r in bullish:
        pos = (r.get('position_pct') or 0) * 100
        cards.append(
            f'''<div class="card">
                <div class="card-title">🔥 {r.get("name", r["ticker"])} ({r["ticker"]})</div>
                <div class="card-meta">评分 {r.get("weighted_score", 0)} | 60日收益 {r.get("bt_return_60d", 0):+.1f}% | 回撤 {r.get("bt_max_dd_60d", 0):.1f}%</div>
                <div class="card-price">目标 {r.get("target_price", "")} | 止损 {r.get("stop_loss", "")}</div>
                <div class="card-reason">{r.get("reasoning", "")}</div>
            </div>'''
        )
    return '<div class="section-title">🏆 今日重点推荐（按回测排序）</div>\n<div class="cards">\n' + "\n".join(cards) + '\n</div>'



def extract_ticker_from_filename(filename):
    """从文件名中提取 ticker"""
    # multi_agent_601991_20260706.md -> 601991
    m = re.search(r'multi_agent_(\d+|[A-Z]+\d*)_\d+\.md', filename)
    if m:
        return m.group(1)
    # futures_CU0_20260706.md -> CU0
    m = re.search(r'futures_([A-Z]+\d*)_\d+\.md', filename)
    if m:
        return m.group(1)
    return None


def classify_report(filename, watchlist):
    """根据文件名和 watchlist 的 category 分类"""
    # 美股报告直接识别
    if filename.startswith('us_market_'):
        return "美股"
    # 期货报告直接识别
    if filename.startswith('futures_'):
        return "期货"
    # 对比报告 / 综合报告
    if "comparison" in filename.lower():
        return "综合"

    ticker = extract_ticker_from_filename(filename)
    if ticker and ticker in watchlist:
        cat = watchlist[ticker].get("category", "个股")
        if cat in TYPE_TITLES:
            return cat
    return "综合"


MD_TO_HTML_CSS = """<style>
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Noto Sans SC', sans-serif; background: #0d1117; color: #c9d1d9; max-width: 960px; margin: 40px auto; padding: 0 20px; line-height: 1.7; }
  h1, h2, h3, h4 { color: #58a6ff; }
  a { color: #58a6ff; }
  code { background: #161b22; padding: 2px 6px; border-radius: 4px; }
  pre { background: #161b22; padding: 12px; border-radius: 8px; overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; margin: 16px 0; }
  th, td { border: 1px solid #30363d; padding: 8px 12px; text-align: left; }
  th { background: #161b22; }
  blockquote { border-left: 4px solid #30363d; margin: 0; padding-left: 16px; color: #8b949e; }
  .nav { margin-bottom: 24px; }
  .nav a { color: #58a6ff; text-decoration: none; margin-right: 16px; }
</style>
<div class="nav"><a href="/daily_tracker_analytics/stocks.html">← 返回报告首页</a></div>
"""


def md_to_html(md_text):
    """极简 markdown 转 HTML（支持表格、标题、列表、加粗）"""
    html = md_text
    # 转义
    html = html.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    # 标题
    for i in range(6, 0, -1):
        html = re.sub(rf'^#{"#"*(i-1)}\s+(.*?)$', rf'<h{i}>\1</h{i}>', html, flags=re.MULTILINE)
    # 粗体
    html = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', html)
    # 斜体
    html = re.sub(r'(?<!\*)\*(?!\*)(.*?)\*(?!\*)', r'<em>\1</em>', html)
    # 行内代码
    html = re.sub(r'`([^`]+)`', r'<code>\1</code>', html)
    # 代码块
    html = re.sub(r'```[\s\S]*?```', lambda m: f'<pre>{m.group(0)[3:-3]}</pre>', html)
    # 表格
    html = _render_tables(html)
    # 列表
    html = _render_lists(html)
    # 段落
    paragraphs = html.split('\n\n')
    out = []
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        if p.startswith('<h') or p.startswith('<ul') or p.startswith('<ol') or p.startswith('<table') or p.startswith('<pre') or p.startswith('<div'):
            out.append(p)
        else:
            out.append(f'<p>{p.replace(chr(10), "<br>")}</p>')
    return '\n'.join(out)


def _render_lists(html):
    """渲染无序列表"""
    lines = html.split('\n')
    out = []
    in_list = False
    for line in lines:
        stripped = line.strip()
        if re.match(r'^[-*]\s+', stripped):
            if not in_list:
                out.append('<ul>')
                in_list = True
            item = re.sub(r'^[-*]\s+', '', stripped)
            out.append(f'<li>{item}</li>')
        else:
            if in_list:
                out.append('</ul>')
                in_list = False
            out.append(line)
    if in_list:
        out.append('</ul>')
    return '\n'.join(out)


def _render_tables(html):
    """渲染 markdown 表格"""
    lines = html.split('\n')
    out = []
    i = 0
    while i < len(lines):
        if i + 1 < len(lines) and '|' in lines[i] and re.match(r'^\|?[-:\|\s]+\|?$', lines[i+1].strip()):
            rows = [lines[i]]
            i += 2
            while i < len(lines) and '|' in lines[i]:
                rows.append(lines[i])
                i += 1
            out.append(_build_table(rows))
        else:
            out.append(lines[i])
            i += 1
    return '\n'.join(out)


def _build_table(rows):
    html_rows = []
    for idx, row in enumerate(rows):
        cells = [c.strip() for c in row.split('|')]
        # 去掉 markdown 表格首尾的空白单元格
        if cells and cells[0] == '' and cells[-1] == '':
            cells = cells[1:-1]
        if idx == 0:
            html_rows.append('<thead><tr>' + ''.join(f'<th>{c}</th>' for c in cells) + '</tr></thead>')
        else:
            html_rows.append('<tr>' + ''.join(f'<td>{c}</td>' for c in cells) + '</tr>')
    return '<table>' + '\n'.join(html_rows) + '</table>'


def convert_md_to_html(md_path, html_path):
    """如果 html 不存在或比 md 旧，则转换"""
    if os.path.exists(html_path) and os.path.getmtime(html_path) >= os.path.getmtime(md_path):
        return
    with open(md_path, 'r', encoding='utf-8') as f:
        md_text = f.read()
    html_body = md_to_html(md_text)
    html = f'<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="UTF-8">\n<title>投资分析报告</title>\n</head>\n<body>\n{MD_TO_HTML_CSS}\n{html_body}\n</body>\n</html>'
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)


def collect_reports():
    """扫描 reports/ 下所有 markdown 报告，并转换为 html"""
    reports = []
    if not os.path.isdir(REPORTS_DIR):
        return reports

    watchlist = load_watchlist()

    for entry in sorted(os.listdir(REPORTS_DIR), reverse=True):
        date_dir = os.path.join(REPORTS_DIR, entry)
        if not os.path.isdir(date_dir) or not re.match(r'^\d{4}-\d{2}-\d{2}$', entry):
            continue

        for f in sorted(os.listdir(date_dir), reverse=True):
            if not f.endswith(".md"):
                continue
            filepath = os.path.join(date_dir, f)
            if not os.path.isfile(filepath):
                continue

            report_type = classify_report(f, watchlist)
            size_kb = os.path.getsize(filepath) // 1024

            # 生成 html 版本
            html_name = f[:-3] + '.html'
            html_path = os.path.join(date_dir, html_name)
            convert_md_to_html(filepath, html_path)

            # 链接指向 html
            rel_url = f"reports/{entry}/{html_name}"

            reports.append({
                "date": entry,
                "file": f,
                "type": report_type,
                "size_kb": size_kb,
                "url": rel_url,
                "ticker": extract_ticker_from_filename(f),
            })

    return reports


def group_by_date_and_type(reports):
    """按类型 -> 日期 -> 报告列表 分组"""
    grouped = defaultdict(lambda: defaultdict(list))
    for r in reports:
        grouped[r["type"]][r["date"]].append(r)
    # 每个日期内按文件名排序
    for t in grouped:
        for d in grouped[t]:
            grouped[t][d].sort(key=lambda x: x["file"], reverse=True)
    return grouped


def generate_page_html(page_type, grouped, all_reports, update_time, pred_date=None, pred_rows=None):
    """生成某一个类型页面"""
    type_list = ["ETF", "个股", "期货", "美股", "综合"]
    nav_items = ""
    for t in type_list:
        active = "active" if t == page_type else ""
        nav_items += f'<a class="{active}" href="{TYPE_FILES[t]}">{TYPE_ICONS[t]} {TYPE_TITLES[t]}</a>'

    # 实时预测区（在报告索引之上）
    realtime_section = ""
    if pred_date and pred_rows:
        all_type_rows = [r for r in pred_rows if r.get('category') == page_type]
        if all_type_rows:
            top_cards = build_agentic_top_cards(pred_rows)  # 全部推荐，只展示本页相关的话在卡片里可能混其他类型，这里全部展示
            table_html = build_agentic_table(pred_rows, page_type)
            realtime_section = f'''
    <div class="section-title" style="font-size:1.2em;background:#1f6feb;color:#fff;">🤖 多Agent实时预测（{pred_date} 收盘后）</div>
    <div style="background:#161b22;border:1px solid #30363d;border-top:0;padding:10px 16px;color:#8b949e;font-size:0.85em;">
      这是 <strong>{pred_date}</strong> 的 {page_type} 预测结果。下方 📅 列表是旧版逐日报告。
    </div>
    {top_cards}
    {table_html}
    <div style="text-align:center;padding:12px 0 24px 0;">
      <a href="prediction.html#{page_type}" style="color:#58a6ff;text-decoration:none;font-size:0.9em;">查看 {pred_date} 全部 {page_type} 预测 →</a>
    </div>
    '''

    # 该类型按日期分组的内容
    sections = ""
    items_by_date = grouped.get(page_type, {})
    if not items_by_date and not realtime_section:
        sections = '<div style="padding:40px;text-align:center;color:#8b949e;">暂无报告</div>'
    elif items_by_date:
        # 在最上方加入最新实时预测的日期入口
        if pred_date and pred_rows:
            all_type_rows = [r for r in pred_rows if r.get('category') == page_type]
            if all_type_rows:
                sections += f'''
    <div class="date-section" style="border-left:4px solid #1f6feb;">
      <div class="date-header">📅 {pred_date}（最新实时预测）</div>
            <div class="report-item">
              <a href="prediction.html#{page_type}" target="_blank">📄 {pred_date} {page_type} 多Agent预测总览（{len(all_type_rows)}只）</a>
              <div class="meta">{pred_date} · 实时预测</div>
            </div>
    </div>'''
        for date in sorted(items_by_date.keys(), reverse=True):
            reports = items_by_date[date]
            rows = ""
            for r in reports:
                # 显示名称：尝试用 watchlist 名称
                name = r["ticker"]
                wl = load_watchlist()
                if r["ticker"] and r["ticker"] in wl:
                    name = f"{wl[r['ticker']]['name']}({r['ticker']})"
                elif "comparison" in r["file"]:
                    name = "📊 对比汇总报告"
                else:
                    name = r["file"]
                rows += f'''
            <div class="report-item">
              <a href="{r['url']}" target="_blank">📄 {name}</a>
              <div class="meta">{date} · {r['size_kb']} KB</div>
            </div>'''

            sections += f'''
    <div class="date-section">
      <div class="date-header">📅 {date}</div>
      {rows}
    </div>'''

    # 统计
    type_counts = {t: len(grouped.get(t, {})) for t in type_list}
    total_reports = len(all_reports)

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{TYPE_TITLES[page_type]} | A股 & 期货 多维度投资分析</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Noto Sans SC', sans-serif;
    background: #0d1117; color: #c9d1d9;
    max-width: 1100px; margin: 0 auto; padding: 30px 20px;
  }}
  h1 {{ color: #58a6ff; font-size: 1.5em; margin-bottom: 6px; }}
  .subtitle {{ color: #8b949e; margin-bottom: 20px; font-size: 0.9em; }}
  .repo-link {{ color: #8b949e; font-size: 0.85em; margin-bottom: 20px; }}
  .repo-link a {{ color: #58a6ff; text-decoration: none; }}

  .nav {{
    display: flex; gap: 8px; margin-bottom: 24px; flex-wrap: wrap;
    border-bottom: 1px solid #30363d; padding-bottom: 10px;
  }}
  .nav a {{
    padding: 8px 18px; border-radius: 20px; font-size: 0.9em;
    text-decoration: none; cursor: pointer; transition: all 0.15s;
    border: 1px solid #30363d; color: #8b949e; background: #161b22;
  }}
  .nav a:hover {{ border-color: #58a6ff; color: #58a6ff; background: #1f6feb11; }}
  .nav a.active {{ border-color: #58a6ff; color: #fff; background: #1f6feb; }}

  .stats {{ display: flex; gap: 16px; margin-bottom: 28px; flex-wrap: wrap; }}
  .stat-card {{
    background: #161b22; border: 1px solid #30363d; border-radius: 8px;
    padding: 14px 20px; flex: 1; min-width: 100px; text-align: center;
  }}
  .stat-card .num {{ font-size: 1.4em; font-weight: 700; color: #58a6ff; }}
  .stat-card .label {{ font-size: 0.78em; color: #8b949e; margin-top: 2px; }}

  .section-title {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px 8px 0 0; padding: 12px 16px; font-weight: 600; color: #f0f6fc; margin-top: 20px; }}
  .date-section {{ margin-bottom: 20px; border: 1px solid #30363d; border-radius: 8px; overflow: hidden; }}
  .date-header {{
    background: #161b22; padding: 12px 16px; font-weight: 600; color: #f0f6fc;
    border-bottom: 1px solid #30363d;
  }}
  .report-item {{
    padding: 10px 16px; border-bottom: 1px solid #30363d;
    display: flex; justify-content: space-between; align-items: center;
    transition: background 0.2s;
  }}
  .report-item:last-child {{ border-bottom: none; }}
  .report-item:hover {{ background: #1c2128; }}
  .report-item a {{ color: #58a6ff; text-decoration: none; }}
  .report-item a:hover {{ text-decoration: underline; }}
  .report-item .meta {{ font-size: 0.85em; color: #8b949e; }}

  table {{ width: 100%; border-collapse: collapse; font-size: 0.88em; margin-bottom: 24px; }}
  th {{ background: #161b22; border: 1px solid #30363d; padding: 7px 9px; text-align: left; color: #8b949e; font-weight: 600; font-size: 0.82em; white-space: nowrap; position: sticky; top: 0; }}
  td {{ border: 1px solid #30363d; padding: 6px 9px; transition: background .15s; }}
  tr:hover td {{ background: #1c2128; }}
  .name {{ font-weight: 600; color: #f0f6fc; }}
  .bullish {{ color: #3fb950; font-weight: 600; }}
  .bearish {{ color: #f85149; font-weight: 600; }}
  .neutral {{ color: #d29922; font-weight: 600; }}

  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px; margin-bottom: 24px; }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 14px; border-left: 4px solid #3fb950; }}
  .card-title {{ font-weight: 600; color: #f0f6fc; margin-bottom: 6px; }}
  .card-meta {{ color: #8b949e; font-size: 0.82em; margin-bottom: 4px; }}
  .card-price {{ color: #f0f6fc; font-size: 0.88em; margin-bottom: 4px; }}
  .card-reason {{ color: #8b949e; font-size: 0.78em; line-height: 1.5; }}

  .footer {{ text-align: center; color: #484f58; font-size: 0.78em; margin-top: 36px; padding-top: 16px; border-top: 1px solid #21262d; }}
</style>
</head>
<body>
<h1>📊 A股 & 期货 多维度投资分析</h1>
<p class="subtitle">ETF / 个股 / 期货 / 综合 — 每日数据驱动分析报告</p>

<div class="repo-link">
  📂 <a href="https://github.com/ldw5821cn/daily_tracker_analytics/tree/main/docs/reports" target="_blank">ldw5821cn/daily_tracker_analytics</a>
</div>

<div class="nav">
{nav_items}
</div>

<div class="stats">
  <div class="stat-card"><div class="num">{len(type_list)}</div><div class="label">分析类型</div></div>
  <div class="stat-card"><div class="num">{total_reports}</div><div class="label">总报告数</div></div>
  <div class="stat-card"><div class="num">{sum(1 for _ in grouped.get('ETF', {}).values())}</div><div class="label">ETF日期</div></div>
  <div class="stat-card"><div class="num">{sum(1 for _ in grouped.get('个股', {}).values())}</div><div class="label">个股日期</div></div>
  <div class="stat-card"><div class="num">{sum(1 for _ in grouped.get('期货', {}).values())}</div><div class="label">期货日期</div></div>
  <div class="stat-card"><div class="num">{sum(1 for _ in grouped.get('美股', {}).values())}</div><div class="label">美股日期</div></div>
</div>

{realtime_section}

{sections}

<div class="footer">
  <p>报告自动生成 | 数据源: Tushare / AkShare / 新浪财经 / 腾讯财经</p>
  <p>更新时间: {update_time}</p>
</div>
</body>
</html>'''


def main():
    reports = collect_reports()
    grouped = group_by_date_and_type(reports)
    update_time = datetime.now().strftime('%Y-%m-%d %H:%M')

    pred_date, pred_rows, _ = load_agentic_predictions()
    print(f"🤖 加载 agentic_predictions: {pred_date} {len(pred_rows) if pred_rows else 0} 条")

    for t in ["ETF", "个股", "期货", "美股", "综合"]:
        html = generate_page_html(t, grouped, reports, update_time, pred_date=pred_date, pred_rows=pred_rows)
        path = os.path.join(DOCS_DIR, TYPE_FILES[t])
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"✅ 生成 {TYPE_FILES[t]} ({TYPE_TITLES[t]}): {len(grouped.get(t, {}))} 个日期")

    # 首页默认跳转到 个股 页面
    index_path = os.path.join(DOCS_DIR, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="0; url={TYPE_FILES['个股']}">
<title>A股 & 期货 多维度投资分析</title>
</head>
<body>
<p>Redirecting to <a href="{TYPE_FILES['个股']}">{TYPE_FILES['个股']}</a>...</p>
</body>
</html>''')

    print(f"✅ 已生成首页重定向: {index_path}")
    print(f"   📊 总报告数: {len(reports)}")


if __name__ == "__main__":
    main()
