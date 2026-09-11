#!/usr/bin/env python3
"""更新宏观流动性数据（FRED 公开 CSV，无需 API key）。

覆盖指标：
- WALCL：美联储总资产（百万美元）
- SOFR：有担保隔夜融资利率
- DGS10：10 年期美债收益率
- DXY：美元指数（FRED 代码 DTWEXBGS）
- VIXCLS：CBOE 波动率指数
- T10Y2Y：10Y-2Y 美债期限利差

输出格式与历史文件 multi_agent/data/macro_liquidity/YYYYMMDD_macro_liquidity.json 保持一致。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
import requests

PR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT_DIR = os.path.join(PR, "multi_agent", "data", "macro_liquidity")

FRED_SERIES = {
    "fed_total_assets": "WALCL",
    "sofr": "SOFR",
    "us10y": "DGS10",
    "dxy": "DTWEXBGS",
    "vix": "VIXCLS",
    "yield_curve_10y2y": "T10Y2Y",
}


def _fetch_csv(series: str):
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
    r = requests.get(url, timeout=(10, 30), headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return r.text


def _parse_csv(text: str, tail_n: int = 12):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    rows = []
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) < 2:
            continue
        date_str, value_str = parts[0], parts[1].strip()
        if not value_str:
            continue
        try:
            rows.append({"date": date_str, "value": float(value_str)})
        except ValueError:
            continue
    if not rows:
        return None
    rows.sort(key=lambda x: x["date"])
    latest = rows[-1]
    previous = None
    for r in reversed(rows[:-1]):
        if r["value"] != latest["value"] or r["date"] != latest["date"]:
            previous = r
            break
    return {
        "series": lines[0].split(",")[-1],
        "latest": latest,
        "previous": previous or latest,
        "history_tail": rows[-tail_n:],
    }


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    flat_date = datetime.now().strftime("%Y%m%d")
    result = {"date": today, "fred": {}}
    flat = {
        "date": today,
        "walcl": None, "walcl_1w_change": None,
        "sofr": None, "dgs10": None, "dxy": None, "vixcls": None, "t10y2y": None,
    }
    for name, series in FRED_SERIES.items():
        try:
            text = _fetch_csv(series)
            parsed = _parse_csv(text)
            if parsed:
                result["fred"][name] = parsed
                latest_val = parsed["latest"]["value"]
                prev_val = parsed["previous"]["value"]
                if name == "fed_total_assets":
                    flat["walcl"] = round(latest_val / 1000, 1)  # 百万美元 -> 十亿美元
                    if prev_val:
                        flat["walcl_1w_change"] = round((latest_val - prev_val) / 1000, 1)
                elif name == "sofr":
                    flat["sofr"] = latest_val
                elif name == "us10y":
                    flat["dgs10"] = latest_val
                elif name == "dxy":
                    flat["dxy"] = latest_val
                elif name == "vix":
                    flat["vixcls"] = latest_val
                elif name == "yield_curve_10y2y":
                    flat["t10y2y"] = latest_val
                print(f"✅ {name} ({series}): latest={parsed['latest']}")
            else:
                print(f"⚠️ {name} ({series}): no data")
        except Exception as e:
            print(f"❌ {name} ({series}): {e}")

    # 保留详细嵌套数据在 details 字段，便于追溯
    result["details"] = result.pop("fred")
    # 扁平字段直接放在顶层，便于 macro_analyst 读取
    result.update(flat)

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, f"{flat_date}_macro_liquidity.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"Saved {out_path}")


if __name__ == "__main__":
    main()
