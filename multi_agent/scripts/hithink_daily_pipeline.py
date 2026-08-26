#!/usr/bin/env python3
"""同花顺 Financial-API 每日数据流水线：
1. 全 A 股估值/行情快照 -> fundamentals_cache
2. 特色数据 -> hithink_cache
3. 生成 docs/hithink_market_dashboard.html 供 GitHub Pages 展示

Usage:
    . etf_tracker/.venv/bin/activate
    python3 multi_agent/scripts/hithink_daily_pipeline.py
"""
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path('/home/liudawei/github/daily_tracker_analytics')
DOCS_DIR = ROOT / 'docs'
CACHE_DIR = ROOT / 'multi_agent' / 'data' / 'hithink_cache'
FUNDAMENTALS_DIR = ROOT / 'multi_agent' / 'data' / 'fundamentals_cache'


def run(cmd: list, env: dict) -> int:
    """调用子脚本，复用当前 venv。"""
    print(f'[pipeline] {" ".join(str(c) for c in cmd)}')
    result = subprocess.run([sys.executable] + [str(c) for c in cmd], cwd=ROOT, env=env)
    return result.returncode


def today_str() -> str:
    return datetime.now().strftime('%Y-%m-%d')


def _thscode_to_code(thscode: str) -> str:
    return thscode.split('.')[0]


def _load_json(path: Path):
    if not path.exists():
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def build_dashboard() -> Path:
    today = today_str()
    # 加载特色数据
    limit_up = _load_json(CACHE_DIR / f'limit_up_pool_{today}.json').get('item', [])
    limit_down = _load_json(CACHE_DIR / f'limit_down_pool_{today}.json').get('item', [])
    limit_break = _load_json(CACHE_DIR / f'limit_break_pool_{today}.json').get('item', [])
    ladder = _load_json(CACHE_DIR / f'limit_up_ladder_{today}.json')
    hot = _load_json(CACHE_DIR / f'hot_stock_list_{today}.json').get('item', [])
    dt = _load_json(CACHE_DIR / f'dragon_tiger_list_{today}.json')

    # 加载 fundamentals 获取最热股价格
    fund_path = FUNDAMENTALS_DIR / f'{today}.json'
    fundamentals = _load_json(fund_path).get('fundamentals', {})

    window = ladder.get('window', {})
    board_caps = window.get('board_caps', {})
    ladder_items = ladder.get('item', [])
    if ladder_items:
        latest = ladder_items[0]
        ladder_date = latest.get('date', today)
        boards = latest.get('boards', {})
    else:
        ladder_date = today
        boards = {}

    rows_up = []
    for item in limit_up[:20]:
        code = _thscode_to_code(item.get('thscode', ''))
        name = item.get('name', '')
        rows_up.append(f"<tr><td>{code}</td><td>{name}</td><td>{item.get('last_price', '')}</td><td style='color:#ef4444'>+{item.get('price_change_ratio_pct', 0):.2f}%</td></tr>")

    rows_break = []
    for item in limit_break[:20]:
        code = _thscode_to_code(item.get('thscode', ''))
        name = item.get('name', '')
        rows_break.append(f"<tr><td>{code}</td><td>{name}</td><td>{item.get('last_price', '')}</td><td style='color:#22c55e'>{item.get('price_change_ratio_pct', 0):.2f}%</td></tr>")

    rows_hot = []
    for item in hot[:15]:
        code = _thscode_to_code(item.get('thscode', ''))
        name = item.get('name', '')
        f = fundamentals.get(code, {})
        rows_hot.append(f"<tr><td>{item.get('rank', '')}</td><td>{code}</td><td>{name}</td><td>{item.get('heat', '')}</td><td>{f.get('close', '-')}</td><td>{f.get('pe_ratio', '-')}</td><td>{f.get('pb_ratio', '-')}</td></tr>")

    ladder_sections = []
    for board_name, label in [('seven_over', '7连板+'), ('six_board', '6连板'), ('five_board', '5连板'), ('four_board', '4连板'), ('three_board', '3连板'), ('two_board', '2连板')]:
        stocks = boards.get(board_name, [])
        if not stocks:
            continue
        rows = []
        for s in stocks:
            code = _thscode_to_code(s.get('thscode', ''))
            rows.append(f"<tr><td>{code}</td><td>{s.get('name', '')}</td><td>{s.get('last_price', '')}</td></tr>")
        ladder_sections.append(f"""
        <div class="card"><div class="card-title">{label} ({len(stocks)}只)</div>
        <div class="table-responsive"><table><thead><tr><th>代码</th><th>名称</th><th>现价</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
        </div>
        """)

    dt_count = dt.get('stock_count', 0)
    dt_hot = dt.get('hot_money_items', [])[:10]
    dt_hot_rows = []
    for hm in dt_hot:
        dt_hot_rows.append(f"<tr><td>{hm.get('name', '')}</td><td>{hm.get('net_buy_amount', '')}</td></tr>")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>同花顺市场数据看板 - {today}</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Noto Sans SC', sans-serif; background: #0f172a; color: #e2e8f0; line-height: 1.6; }}
.container {{ max-width: 1400px; margin: 0 auto; padding: 20px; }}
.header {{ background: linear-gradient(135deg, #1e293b, #334155); padding: 30px; border-radius: 16px; margin-bottom: 24px; }}
.header h1 {{ font-size: 24px; margin-bottom: 8px; }}
.header .sub {{ color: #94a3b8; font-size: 14px; }}
.stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 24px; }}
.stat-card {{ background: #1e293b; border-radius: 12px; padding: 16px 10px; text-align: center; }}
.stat-card .num {{ font-size: 28px; font-weight: bold; white-space: nowrap; }}
.stat-card .label {{ color: #94a3b8; font-size: 12px; margin-top: 4px; }}
.section-title {{ font-size: 18px; font-weight: 600; margin: 24px 0 12px; padding-left: 12px; border-left: 4px solid #6366f1; }}
.nav {{ display: flex; flex-direction: row; flex-wrap: wrap; justify-content: flex-start; align-items: center; gap: 10px; margin-bottom: 20px; }}
.nav a {{ display: inline-block; white-space: nowrap; color: #94a3b8; text-decoration: none; padding: 8px 18px; border-radius: 20px; background: #1e293b; border: 1px solid #334155; font-size: 13px; }}
.nav a:hover {{ background: #334155; color: #e2e8f0; }}
.table-responsive {{ overflow-x: auto; -webkit-overflow-scrolling: touch; margin-bottom: 24px; border-radius: 12px; }}
table {{ width: 100%; border-collapse: collapse; background: #1e293b; font-size: 13px; min-width: 600px; }}
th {{ background: #334155; padding: 10px 12px; text-align: left; font-size: 12px; color: #94a3b8; }}
td {{ padding: 8px 12px; border-bottom: 1px solid #1e293b; font-size: 13px; }}
tr:hover {{ background: #334155; }}
.card {{ background: #1e293b; border-radius: 12px; padding: 16px; margin-bottom: 16px; }}
.card-title {{ font-weight: 600; margin-bottom: 10px; color: #e2e8f0; }}
.footer {{ text-align: center; color: #475569; font-size: 12px; margin-top: 40px; padding: 20px; }}
</style>
</head>
<body>
<div class="container">
<div class="header">
<h1>🔥 同花顺市场数据看板</h1>
<div class="sub">数据日期 {today} · 数据源：同花顺 Financial-API</div>
</div>
<div class="nav">
<a href="index.html">🏠 首页</a>
<a href="prediction.html">🏛️ 预测</a>
<a href="stocks.html">📈 个股</a>
<a href="etfs.html">📊 ETF</a>
<a href="cron_status.html">⏰ 任务</a>
</div>

<div class="stats-grid">
<div class="stat-card"><div class="num" style="color:#ef4444">{len(limit_up)}</div><div class="label">涨停</div></div>
<div class="stat-card"><div class="num" style="color:#22c55e">{len(limit_down)}</div><div class="label">跌停</div></div>
<div class="stat-card"><div class="num" style="color:#f59e0b">{len(limit_break)}</div><div class="label">炸板</div></div>
<div class="stat-card"><div class="num">{sum(board_caps.values())}</div><div class="label">连板股总数</div></div>
<div class="stat-card"><div class="num">{len(hot)}</div><div class="label">热股榜</div></div>
<div class="stat-card"><div class="num">{dt_count}</div><div class="label">龙虎榜个股</div></div>
</div>

<h2 class="section-title">📈 涨停池 Top20</h2>
<div class="table-responsive"><table><thead><tr><th>代码</th><th>名称</th><th>现价</th><th>涨幅</th></tr></thead><tbody>{''.join(rows_up)}</tbody></table></div>

<h2 class="section-title">💥 炸板池 Top20</h2>
<div class="table-responsive"><table><thead><tr><th>代码</th><th>名称</th><th>现价</th><th>涨幅</th></tr></thead><tbody>{''.join(rows_break)}</tbody></table></div>

<h2 class="section-title">🔥 热股榜 Top15（带估值）</h2>
<div class="table-responsive"><table><thead><tr><th>排名</th><th>代码</th><th>名称</th><th>热度</th><th>现价</th><th>PE_TTM</th><th>PB</th></tr></thead><tbody>{''.join(rows_hot)}</tbody></table></div>

<h2 class="section-title">🪜 连板天梯 ({ladder_date})</h2>
{''.join(ladder_sections)}

<h2 class="section-title">🐉 龙虎榜游资动向 Top10</h2>
<div class="table-responsive"><table><thead><tr><th>游资/机构</th><th>净买入额</th></tr></thead><tbody>{''.join(dt_hot_rows)}</tbody></table></div>

<div class="footer">生成时间 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · daily_tracker_analytics</div>
</div>
</body>
</html>"""
    out = DOCS_DIR / 'hithink_market_dashboard.html'
    out.write_text(html, encoding='utf-8')
    print(f'[pipeline] dashboard -> {out}')
    return out


def main():
    # 确保子进程能读到 API Key
    import dotenv
    dotenv.load_dotenv(ROOT / '.env')
    scripts = ROOT / 'multi_agent' / 'scripts'
    steps = [
        [scripts / 'hithink_fundamentals_cache.py'],
        [scripts / 'hithink_special_data.py'],
    ]
    for step in steps:
        env = os.environ.copy()
        if run(step, env) != 0:
            print(f'[pipeline] 失败: {" ".join(str(s) for s in step)}')
            return 1
    build_dashboard()
    return 0


if __name__ == '__main__':
    sys.exit(main())
