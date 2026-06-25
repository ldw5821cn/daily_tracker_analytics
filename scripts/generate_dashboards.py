#!/usr/bin/env python3
"""
生成分类式独立分析页面 — 实时数据填充版
stocks.html / etfs.html / futures.html
每个页面展示对应分类的最新技术指标
"""
import sys, os, json, time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.request
import re

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'multi_agent'))

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
DOCS_DIR = os.path.join(REPO_ROOT, "docs")
WATCHLIST_PATH = os.path.join(REPO_ROOT, "multi_agent", "watchlist.json")
HTML_DIR = os.path.join(DOCS_DIR, "reports")

# ====== 实时数据获取 ======

def fetch_realtime(ticker):
    """通过腾讯证券获取实时行情（支持A股和ETF）"""
    try:
        prefix = "sh" if ticker.startswith(('6', '5')) else "sz"
        url = f"http://qt.gtimg.cn/q={prefix}{ticker}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode('gbk')
        parts = text.split('~')
        if len(parts) > 39:
            return {
                'price': float(parts[3]) if parts[3] else 0,
                'prev_close': float(parts[4]) if parts[4] else 0,
                'change_pct': float(parts[32]) if parts[32] else 0,
                'high': float(parts[33]) if parts[33] else 0,
                'low': float(parts[34]) if parts[34] else 0,
                'volume': float(parts[6]) if parts[6] else 0,
                'turnover': float(parts[37]) if parts[37] else 0,
                'pe': float(parts[39]) if len(parts) > 39 and parts[39] else 0,
            }
    except:
        pass
    return None


def fetch_futures_all():
    """获取所有期货行情"""
    from core.futures import analyze_futures_trend, CATEGORIES
    results = {}
    for cat, members in CATEGORIES.items():
        for code in members:
            r = analyze_futures_trend(code, '')
            if 'error' not in r:
                r['cat'] = cat
                results[code] = r
    return results


# ====== HTML 模板 ======

TEMPLATE_HEAD = '''<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{title}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Noto Sans SC',sans-serif;background:#0d1117;color:#c9d1d9;max-width:1000px;margin:0 auto;padding:40px 20px;}}
h1{{color:#58a6ff;font-size:1.6em;margin-bottom:4px;}}
.subtitle{{color:#8b949e;margin-bottom:20px;font-size:0.9em;}}
.back{{color:#58a6ff;text-decoration:none;font-size:0.9em;display:inline-block;margin-bottom:12px;}}
.nav{{display:flex;gap:6px;margin-bottom:20px;flex-wrap:wrap;}}
.nav a{{padding:6px 14px;border-radius:20px;font-size:0.85em;text-decoration:none;border:1px solid #30363d;color:#8b949e;transition:all .15s;}}
.nav a:hover{{border-color:#58a6ff;color:#58a6ff;background:#1f6feb11;}}
.nav a.active{{border-color:#58a6ff;color:#58a6ff;background:#1f6feb22;font-weight:600;}}
table{{width:100%;border-collapse:collapse;font-size:0.88em;}}
th{{background:#161b22;border:1px solid #30363d;padding:7px 9px;text-align:left;color:#8b949e;font-weight:600;font-size:0.82em;white-space:nowrap;position:sticky;top:0;}}
td{{border:1px solid #30363d;padding:6px 9px;transition:background .15s;}}
tr:hover td{{background:#1c2128;}}
.name{{font-weight:600;color:#f0f6fc;}}
.price{{color:#f0f6fc;font-weight:600;}}
.bullish{{color:#3fb950;font-weight:600;}}
.bearish{{color:#f85149;font-weight:600;}}
.neutral{{color:#d29922;font-weight:600;}}
.section-title{{background:#161b22;border:1px solid #30363d;border-radius:8px 8px 0 0;padding:10px 14px;font-weight:600;font-size:1em;margin-top:20px;}}
.footer{{text-align:center;color:#484f58;font-size:0.78em;margin-top:36px;padding-top:16px;border-top:1px solid #21262d;}}
.nodata{{color:#8b949e;text-align:center;padding:20px;border:1px solid #30363d;}}
</style></head><body>
<h1>{icon} {title}</h1>
<p class="subtitle">{desc}</p>
<a class="back" href="../">← 返回首页</a>
<div class="nav">
<a href="../">首页</a>
<a href="etfs.html" class="active-{etf_active}">{icon_etf} ETF</a>
<a href="stocks.html" class="active-{stock_active}">{icon_stock} 个股</a>
<a href="futures.html" class="active-{fut_active}">{icon_fut} 期货</a>
</div>
'''

TEMPLATE_FOOT = '''<div class="footer">
<p>数据来源: 新浪财经 / 腾讯证券 / 新浪期货 | 技术分析仅供参考 | ⚠️ 不构成投资建议</p>
<p>更新时间: {update_time}</p>
</div></body></html>'''


def _nav_class(active_page, this_page):
    return "active" if active_page == this_page else ""


def generate_html(active, icon, title, desc, table_html):
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    head = TEMPLATE_HEAD.format(
        title=title, icon=icon,
        icon_etf="📈", icon_stock="📊", icon_fut="⚡",
        desc=desc, update_time=now,
        etf_active=_nav_class(active, "etfs"),
        stock_active=_nav_class(active, "stocks"),
        fut_active=_nav_class(active, "futures"),
    )
    foot = TEMPLATE_FOOT.format(update_time=now)
    return head + table_html + foot


def generate_etf_page(watchlist):
    etfs = [w for w in watchlist if w.get('category') == 'ETF']
    
    # Batch fetch real-time prices
    rows_html = ""
    with ThreadPoolExecutor(max_workers=8) as ex:
        fut_map = {ex.submit(fetch_realtime, w['ticker']): w for w in etfs}
        for fut in as_completed(fut_map):
            w = fut_map[fut]
            rt = fut.result()
            if rt:
                chg = rt.get('change_pct', 0)
                sig = '看多' if chg > 1 else '看空' if chg < -1 else '中性'
                sig_cls = 'bullish' if sig == '看多' else 'bearish' if sig == '看空' else 'neutral'
                chg_str = f'<span class="{sig_cls}">{chg:+.2f}%</span>'
                price_str = f'{rt["price"]:.3f}'
            else:
                chg_str = '<span class="status">—</span>'
                price_str = '<span class="status">—</span>'
                sig = '—'
            
            rows_html += f'<tr><td class="name">{w["name"]}</td><td>{w["ticker"]}</td><td class="price">{price_str}</td><td class="{sig_cls}">{sig}</td><td>{chg_str}</td></tr>\n'

    table = f'''<div class="section-title">📈 {len(etfs)} 只 ETF</div>
<table><tr><th>名称</th><th>代码</th><th>最新价</th><th>信号</th><th>涨跌幅</th></tr>
{rows_html}</table>'''
    return generate_html("etfs", "📈", "ETF 趋势分析",
                         f"{len(etfs)} 只 ETF — 实时行情 + 技术信号", table)


def generate_stock_page(watchlist):
    stocks = [w for w in watchlist if w.get('category') == '个股']
    
    rows_html = ""
    with ThreadPoolExecutor(max_workers=10) as ex:
        fut_map = {ex.submit(fetch_realtime, w['ticker']): w for w in stocks}
        for fut in as_completed(fut_map):
            w = fut_map[fut]
            rt = fut.result()
            sector = w.get('sector', w.get('theme', ''))
            if rt:
                chg = rt.get('change_pct', 0)
                sig = '看多' if chg > 1 else '看空' if chg < -1 else '中性'
                sig_cls = 'bullish' if sig == '看多' else 'bearish' if sig == '看空' else 'neutral'
                price_str = f'{rt["price"]:.3f}'
            else:
                sig = '—'
                sig_cls = 'neutral'
                price_str = '<span class="status">—</span>'
            
            rows_html += f'<tr><td class="name">{w["name"]}</td><td>{w["ticker"]}</td><td>{sector}</td><td class="price">{price_str}</td><td class="{sig_cls}">{sig}</td></tr>\n'

    table = f'''<div class="section-title">📊 {len(stocks)} 只个股</div>
<table><tr><th>名称</th><th>代码</th><th>板块</th><th>最新价</th><th>信号</th></tr>
{rows_html}</table>'''
    return generate_html("stocks", "📊", "个股分析",
                         f"{len(stocks)} 只个股 — 实时行情 + 板块分类", table)


def generate_futures_page(watchlist):
    """期货分析页（复用已有逻辑 + 统一模板）"""
    from core.futures import analyze_futures_trend, CATEGORIES, FUTURES_MAP

    name_map = {code: name for code, name in FUTURES_MAP}

    sections = ""
    for cat, members in CATEGORIES.items():
        rows = ""
        for code in members:
            r = analyze_futures_trend(code, name_map.get(code, code))
            if 'error' in r:
                continue
            sig = r.get('signal', '?')
            sig_cls = 'bullish' if sig == '看多' else 'bearish' if sig == '看空' else 'neutral'
            trend = r.get('trend_20d', 0)
            rows += f'<tr><td class="name">{r["name"]}</td><td class="price">{r["price"]}</td>'
            rows += f'<td class="{sig_cls}">{sig}</td><td>RSI {r["rsi"]:.1f}</td><td>{r["macd"]}</td><td>{r["ma_trend"]}</td>'
            rows += f'<td><span class="{sig_cls}">{trend:+.2f}%</span></td></tr>\n'
        
        if rows:
            icons = {'有色': '🟡', '黑色': '⛏️', '能化': '🛢️', '农产品': '🌾'}
            sections += f'<div class="section-title">{icons.get(cat, "📊")} {cat}</div>\n'
            sections += f'<table><tr><th>品种</th><th>价格</th><th>信号</th><th>RSI</th><th>MACD</th><th>均线</th><th>20日趋势</th></tr>{rows}</table>\n'

    return generate_html("futures", "⚡", "期货主力合约趋势分析",
                         "21 个期货品种 — 技术指标 + 多周期回测", sections)


def main():
    os.makedirs(HTML_DIR, exist_ok=True)

    with open(WATCHLIST_PATH) as f:
        watchlist = json.load(f)

    print("📈 生成 ETF 分析页...")
    etf_html = generate_etf_page(watchlist)
    etf_count = len([w for w in watchlist if w.get('category') == 'ETF'])
    with open(os.path.join(HTML_DIR, "etfs.html"), 'w', encoding='utf-8') as f:
        f.write(etf_html)
    print(f"   ✅ etfs.html ({etf_count} 只)")

    print("📊 生成个股分析页...")
    stock_html = generate_stock_page(watchlist)
    stock_count = len([w for w in watchlist if w.get('category') == '个股'])
    with open(os.path.join(HTML_DIR, "stocks.html"), 'w', encoding='utf-8') as f:
        f.write(stock_html)
    print(f"   ✅ stocks.html ({stock_count} 只)")

    print("⚡ 生成期货分析页...")
    fut_html = generate_futures_page(watchlist)
    fut_count = len([w for w in watchlist if w.get('category') == '期货'])
    with open(os.path.join(HTML_DIR, "futures.html"), 'w', encoding='utf-8') as f:
        f.write(fut_html)
    print(f"   ✅ futures.html ({fut_count} 个)")

    print(f"\n🎉 全部完成! 可在 Pages 查看:")
    print(f"   ETF:  etfs.html")
    print(f"   个股: stocks.html")
    print(f"   期货: futures.html")


if __name__ == '__main__':
    main()
