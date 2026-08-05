#!/usr/bin/env python3
"""为 SMH/SOXX/QQQ 底层龙头生成财报/估值 review 报告。

数据：富途 OpenD `get_market_snapshot` 全部可用字段。
不替代基本面财报，只基于实时行情做持仓质量概览。
"""
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / 'multi_agent' / 'data' / 'tech_earnings'
OUT_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class Stock:
    symbol: str
    name: str
    last_price: float
    total_market_val_b: float
    pe_ratio: float
    pe_ttm_ratio: float
    pb_ratio: float
    ey_ratio: float
    dividend_ttm: float
    dividend_ratio_ttm: float
    highest52weeks_price: float
    lowest52weeks_price: float
    amplitude: float
    turnover_rate: float
    volume: float
    avg_price: float
    after_price: Optional[float] = None
    after_change_rate: Optional[float] = None
    overnight_price: Optional[float] = None
    overnight_change_rate: Optional[float] = None

    raw: Dict = field(default_factory=dict, repr=False)


def _safe_float(v):
    try:
        import pandas as pd
        if pd.isna(v):
            return None
    except Exception:
        pass
    try:
        return float(v)
    except Exception:
        return None


def _fmt(value, fmt="{:.2f}", suffix="") -> str:
    if value is None:
        return "—"
    return fmt.format(value) + suffix


def _52w_position(s: Stock) -> Optional[float]:
    h = s.highest52weeks_price
    l = s.lowest52weeks_price
    if not h or not l or h <= l or not s.last_price:
        return None
    return (s.last_price - l) / (h - l) * 100


def _valuation_label(pe: Optional[float]) -> str:
    if pe is None or pe <= 0:
        return "N/A"
    if pe <= 25:
        return "合理"
    if pe <= 40:
        return "偏贵"
    return "偏高"


def _build_html(stocks: List[Stock], date_str: str) -> str:
    rows = []
    for s in stocks:
        pos = _52w_position(s)
        pos_str = f"{pos:.0f}%" if pos is not None else "—"
        val = _valuation_label(s.pe_ttm_ratio)
        rows.append(
            f"<tr><td><b>{s.symbol}</b></td><td>{s.name}</td>"
            f"<td>{_fmt(s.last_price)}</td>"
            f"<td>{_fmt(s.after_price)} / {_fmt(s.after_change_rate, '{:+.2f}', '%')}</td>"
            f"<td>{_fmt(s.total_market_val_b, '{:.1f}', 'B')}</td>"
            f"<td>{_fmt(s.pe_ratio)}</td>"
            f"<td>{_fmt(s.pe_ttm_ratio)}</td>"
            f"<td>{_fmt(s.pb_ratio)}</td>"
            f"<td>{_fmt(s.ey_ratio, '{:.2f}', '%')}</td>"
            f"<td>{_fmt(s.dividend_ttm, '{:.3f}')} / {_fmt(s.dividend_ratio_ttm, '{:.2f}', '%')}</td>"
            f"<td>{_fmt(s.highest52weeks_price)} / {_fmt(s.lowest52weeks_price)}</td>"
            f"<td>{pos_str}</td>"
            f"<td>{_fmt(s.amplitude, '{:.2f}', '%')}</td>"
            f"<td>{_fmt(s.turnover_rate, '{:.2f}', '%')}</td></tr>"
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>美股科技龙头财报/估值 review - {date_str}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; background:#0f172a; color:#e2e8f0; margin:0; padding:20px; }}
h1 {{ color:#f8fafc; font-size:22px; }}
.section-title {{ font-size:16px; font-weight:bold; margin:20px 0 10px; color:#94a3b8; }}
.table-responsive {{ overflow-x:auto; }}
table {{ width:100%; border-collapse: collapse; font-size:12px; min-width:900px; }}
th, td {{ padding:8px; border-bottom:1px solid #334155; text-align:left; white-space:nowrap; }}
th {{ color:#94a3b8; background:#1e293b; position:sticky; top:0; }}
tr:hover {{ background:#1e293b; }}
.note {{ background:#1e293b; padding:12px; border-radius:8px; color:#94a3b8; margin-top:20px; font-size:13px; }}
</style>
</head>
<body>
<h1>🧬 美股科技龙头财报/估值 review</h1>
<div class="section-title">📅 {date_str} · SMH / SOXX / QQQ 底层核心持仓</div>
<div class="table-responsive">
<table>
<thead>
<tr><th>代码</th><th>名称</th><th>现价</th><th>盘后 / 涨跌</th><th>市值</th><th>PE</th><th>PE_TTM</th><th>PB</th><th>EY</th><th>股息TTM / 率</th><th>52周高/低</th><th>52周位置</th><th>振幅</th><th>换手</th></tr>
</thead>
<tbody>{"".join(rows)}</tbody>
</table>
</div>
<div class="note">
<p><b>说明：</b>数据全部来自富途 OpenD <code>get_market_snapshot</code>，包含所有可用字段。部分字段（如 PB、股息率）可能因财报口径或单位差异与常规金融终端不一致，仅作横向对比参考。本页不构成投资建议。</p>
</div>
</body>
</html>"""


def _build_markdown(stocks: List[Stock], date_str: str) -> str:
    lines = [
        f"# 🧬 美股科技龙头财报/估值 review",
        f"",
        f"**日期**：{date_str}  ",
        f"**覆盖范围**：SMH / SOXX / QQQ 底层核心持仓  ",
        f"**数据源**：富途 OpenD `get_market_snapshot`（全部字段）",
        f"",
        f"| 代码 | 名称 | 现价 | 盘后/涨跌 | 市值 | PE | PE_TTM | PB | EY | 股息TTM/率 | 52周高/低 | 52周位置 | 振幅 | 换手 |",
        f"|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in stocks:
        pos = _52w_position(s)
        lines.append(
            f"| {s.symbol} | {s.name} | "
            f"{_fmt(s.last_price)} | "
            f"{_fmt(s.after_price)} / {_fmt(s.after_change_rate, '{:+.2f}', '%')} | "
            f"{_fmt(s.total_market_val_b, '{:.1f}', 'B')} | "
            f"{_fmt(s.pe_ratio)} | {_fmt(s.pe_ttm_ratio)} | {_fmt(s.pb_ratio)} | "
            f"{_fmt(s.ey_ratio, '{:.2f}', '%')} | "
            f"{_fmt(s.dividend_ttm, '{:.3f}')} / {_fmt(s.dividend_ratio_ttm, '{:.2f}', '%')} | "
            f"{_fmt(s.highest52weeks_price)} / {_fmt(s.lowest52weeks_price)} | "
            f"{_fmt(pos, '{:.0f}', '%') if pos is not None else '—'} | "
            f"{_fmt(s.amplitude, '{:.2f}', '%')} | {_fmt(s.turnover_rate, '{:.2f}', '%')} |"
        )
    lines.extend([
        "",
        "## 要点速评",
        "",
    ])
    for s in stocks:
        lines.append(f"- **{s.symbol} {s.name}**：PE_TTM={_fmt(s.pe_ttm_ratio)}, PB={_fmt(s.pb_ratio)}, 52周位置={_fmt(_52w_position(s), '{:.0f}', '%') if _52w_position(s) is not None else '—'}, 估值={_valuation_label(s.pe_ttm_ratio)}。")
    lines.extend([
        "",
        "## 对 SMH / SOXX / QQQ 持仓的影响",
        "",
        "1. **SMH**：集中度最高的 AI 芯片/半导体 ETF，NVDA/TSM/AVGO 权重最大。",
        "2. **SOXX**：费城半导体指数，覆盖设备/设计/代工全链。",
        "3. **QQQ**：科技巨头主导，MSFT/AAPL/AMZN/GOOG/META 合计权重高，对利率与 AI capex 敏感。",
        "",
        "---",
        "数据由 Hermes Agent 自动生成，研究辅助非投资建议。",
    ])
    return "\n".join(lines)


def fetch_from_futu(codes: List[str]) -> List[Stock]:
    from futu import OpenQuoteContext
    qc = OpenQuoteContext(host='127.0.0.1', port=11111)
    stocks = []
    try:
        ret, df = qc.get_market_snapshot(codes)
        if ret != 0 or df is None or df.empty:
            print(f"get_market_snapshot failed ret={ret}")
            return stocks
        for _, row in df.iterrows():
            raw = row.to_dict()
            s = Stock(
                symbol=str(row['code']).replace('US.', ''),
                name=str(row['name']),
                last_price=_safe_float(row.get('last_price')) or 0.0,
                total_market_val_b=(_safe_float(row.get('total_market_val')) or 0.0) / 1e9,
                pe_ratio=_safe_float(row.get('pe_ratio')) or 0.0,
                pe_ttm_ratio=_safe_float(row.get('pe_ttm_ratio')) or 0.0,
                pb_ratio=_safe_float(row.get('pb_ratio')) or 0.0,
                ey_ratio=_safe_float(row.get('ey_ratio')) or 0.0,
                dividend_ttm=_safe_float(row.get('dividend_ttm')) or 0.0,
                dividend_ratio_ttm=_safe_float(row.get('dividend_ratio_ttm')) or 0.0,
                highest52weeks_price=_safe_float(row.get('highest52weeks_price')) or 0.0,
                lowest52weeks_price=_safe_float(row.get('lowest52weeks_price')) or 0.0,
                amplitude=_safe_float(row.get('amplitude')) or 0.0,
                turnover_rate=_safe_float(row.get('turnover_rate')) or 0.0,
                volume=_safe_float(row.get('volume')) or 0.0,
                avg_price=_safe_float(row.get('avg_price')) or 0.0,
                after_price=_safe_float(row.get('after_price')),
                after_change_rate=_safe_float(row.get('after_change_rate')),
                overnight_price=_safe_float(row.get('overnight_price')),
                overnight_change_rate=_safe_float(row.get('overnight_change_rate')),
                raw=raw,
            )
            stocks.append(s)
    finally:
        qc.close()
    return stocks


def main():
    codes = ['US.NVDA', 'US.TSM', 'US.AVGO', 'US.ASML', 'US.AMD',
             'US.AAPL', 'US.MSFT', 'US.AMZN', 'US.GOOG', 'US.META']
    stocks = fetch_from_futu(codes)
    stocks.sort(key=lambda x: x.total_market_val_b, reverse=True)
    date_str = datetime.now().strftime('%Y-%m-%d %H:%M')

    html = _build_html(stocks, date_str)
    md = _build_markdown(stocks, date_str)
    json_data = [s.raw for s in stocks]

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    (OUT_DIR / f"{ts}_tech_earnings_review.md").write_text(md, encoding='utf-8')
    (OUT_DIR / f"{ts}_tech_earnings_review.html").write_text(html, encoding='utf-8')
    (OUT_DIR / f"{ts}_tech_earnings_review.json").write_text(
        json.dumps(json_data, indent=2, ensure_ascii=False, default=str), encoding='utf-8')
    print(f"✅ 已生成 {len(stocks)} 只龙头 review 到 {OUT_DIR}")


if __name__ == '__main__':
    main()
