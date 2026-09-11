#!/usr/bin/env python3
"""生成 cmc_rank.html 页面（加密货币恐惧贪婪指数）。"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_PATH = os.path.join(REPO_ROOT, "multi_agent", "data", "external", "cmc_rank.json")
OUT_PATH = os.path.join(REPO_ROOT, "docs", "cmc_rank.html")


def classification_cn(c: str) -> str:
    return {
        "Extreme Fear": "极度恐慌",
        "Fear": "恐慌",
        "Neutral": "中性",
        "Greed": "贪婪",
        "Extreme Greed": "极度贪婪",
    }.get(c, c)


def classification_color(c: str) -> str:
    return {
        "Extreme Fear": "#22c55e",  # 绿
        "Fear": "#4ade80",
        "Neutral": "#facc15",
        "Greed": "#f87171",
        "Extreme Greed": "#ef4444",  # 红
    }.get(c, "#94a3b8")


def ts_to_date(ts: str) -> str:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return ts


def generate() -> None:
    if not os.path.exists(DATA_PATH):
        print(f"Data not found: {DATA_PATH}")
        return

    with open(DATA_PATH, "r", encoding="utf-8") as f:
        payload = json.load(f)

    rows = payload.get("data", [])
    if not rows:
        print("No data rows")
        return

    latest = rows[0]
    latest_date = ts_to_date(latest.get("timestamp", ""))
    latest_value = int(latest.get("value", 0))
    latest_class = classification_cn(latest.get("value_classification", "Unknown"))
    latest_color = classification_color(latest.get("value_classification", ""))

    history_rows = "\n".join([
        f"<tr><td>{ts_to_date(r.get('timestamp',''))}</td><td>{r.get('value','')}</td><td style='color:{classification_color(r.get('value_classification',''))}'>{classification_cn(r.get('value_classification',''))}</td></tr>"
        for r in rows[:30]
    ])

    chart_data = json.dumps([
        {"date": ts_to_date(r.get("timestamp", "")), "value": int(r.get("value", 0))}
        for r in reversed(rows)
    ], ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>加密货币恐惧贪婪指数 | daily_tracker_analytics</title>
<script src="https://unpkg.com/lightweight-charts@4.1.0/dist/lightweight-charts.standalone.production.js"></script>
<style>
  :root {{ --bg: #0f172a; --card: #1e293b; --text: #e2e8f0; --muted: #94a3b8; --up: #ef4444; --down: #22c55e; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 24px; }}
  h1 {{ margin: 0 0 8px; font-size: 22px; }}
  .subtitle {{ color: var(--muted); margin-bottom: 24px; font-size: 14px; }}
  .grid {{ display: grid; grid-template-columns: 280px 1fr; gap: 24px; }}
  .card {{ background: var(--card); border-radius: 12px; padding: 20px; }}
  .big {{ font-size: 56px; font-weight: 700; color: {latest_color}; margin: 12px 0; }}
  .label {{ color: var(--muted); font-size: 13px; text-transform: uppercase; letter-spacing: .5px; }}
  .desc {{ margin-top: 12px; font-size: 14px; line-height: 1.6; color: var(--muted); }}
  #chart {{ height: 360px; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 13px; }}
  th, td {{ padding: 8px 10px; text-align: left; border-bottom: 1px solid #334155; }}
  th {{ color: var(--muted); font-weight: 500; }}
  a {{ color: #60a5fa; text-decoration: none; }}
  .nav {{ margin-bottom: 24px; }}
  @media (max-width: 800px) {{ .grid {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
  <div class="nav"><a href="index.html">← 返回首页</a></div>
  <h1>加密货币恐惧贪婪指数</h1>
  <div class="subtitle">数据来源：alternative.me（Crypto Fear & Greed Index），每日更新</div>

  <div class="grid">
    <div class="card">
      <div class="label">最新数值（{latest_date}）</div>
      <div class="big">{latest_value}</div>
      <div style="font-size:18px;color:{latest_color}">{latest_class}</div>
      <div class="desc">
        0-24 极度恐慌 · 25-44 恐慌 · 45-55 中性 · 56-74 贪婪 · 75-100 极度贪婪。<br>
        该指数反映加密货币市场风险偏好，可作为 A 股/全球风险资产的辅助情绪参考。
      </div>
    </div>
    <div class="card">
      <div id="chart"></div>
    </div>
  </div>

  <div class="card" style="margin-top:24px">
    <div class="label">近 30 日历史</div>
    <table>
      <thead><tr><th>日期</th><th>数值</th><th>情绪</th></tr></thead>
      <tbody>{history_rows}</tbody>
    </table>
  </div>

  <script>
    const chartData = {chart_data};
    const chart = LightweightCharts.createChart(document.getElementById('chart'), {{
      layout: {{ background: {{ color: 'transparent' }}, textColor: '#e2e8f0' }},
      grid: {{ vertLines: {{ color: '#334155' }}, horzLines: {{ color: '#334155' }} }},
      rightPriceScale: {{ borderColor: '#334155' }},
      timeScale: {{ borderColor: '#334155', timeVisible: false }},
      crosshair: {{ mode: LightweightCharts.CrosshairMode.Normal }},
    }});
    const line = chart.addLineSeries({{ color: '#60a5fa', lineWidth: 2 }});
    line.setData(chartData);
    chart.timeScale().fitContent();
  </script>
</body>
</html>
"""

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Generated {OUT_PATH}")


if __name__ == "__main__":
    generate()
