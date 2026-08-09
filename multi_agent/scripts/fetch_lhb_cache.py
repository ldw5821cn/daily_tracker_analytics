"""
Fetch A-share 龙虎榜 (LHB) data from akshare and cache as JSON.
Uses stock_lhb_detail_daily_sina for the daily list (stable)
and stock_lhb_jgmx_sina for institutional buy/sell details.
Files: multi_agent/data/lhb_cache/{date}.json
Fields:
  - date
  - total_count: 上榜个股数（去重代码）
  - inst_net_buy_total: 机构净买入总额（亿元）
  - inst_buy_total: 机构买入总额（亿元）
  - inst_sell_total: 机构卖出总额（亿元）
  - limit_up_with_inst: 涨停且机构净买入>0 的家数
  - limit_down_with_inst_sell: 跌停且机构净买入<0 的家数
  - top_buyers: 机构净买入前10条（代码、名称、净买额、原因）
"""
import os
import sys
import json
import argparse
from datetime import datetime, timedelta
import akshare as ak
import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "lhb_cache")


def parse_date_arg(d):
    if d == "yesterday":
        return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    if d == "today":
        return datetime.now().strftime("%Y-%m-%d")
    return d


def _retry(fn, retries=3, delay=2):
    import time
    last = None
    for i in range(retries):
        try:
            return fn()
        except Exception as e:
            last = e
            if i < retries - 1:
                time.sleep(delay)
    raise last


def fetch_lhb(date: str):
    """Return dict with summary stats."""
    # Daily list via sina
    try:
        df_detail = _retry(lambda: ak.stock_lhb_detail_daily_sina(date=date.replace("-", "")))
    except Exception as e:
        return {"error": f"detail_daily_sina: {e}", "date": date}
    if df_detail is None or df_detail.empty:
        return {"date": date, "total_count": 0, "note": "no data"}

    detail = df_detail.copy()
    col_map_detail = {
        "股票代码": "code", "股票名称": "name", "收盘价": "close", "对应值": "indicator_value",
        "成交量": "volume", "成交额": "amount", "指标": "reason",
    }
    detail.rename(columns={k: v for k, v in col_map_detail.items() if k in detail.columns}, inplace=True)

    for c in ["close", "volume", "amount"]:
        if c in detail.columns:
            detail[c] = pd.to_numeric(detail[c], errors="coerce")

    # Institutional details via sina (all recent dates, filter by date)
    try:
        df_inst = _retry(lambda: ak.stock_lhb_jgmx_sina())
    except Exception as e:
        df_inst = pd.DataFrame()

    inst = pd.DataFrame()
    if not df_inst.empty:
        inst = df_inst.copy()
        col_map_inst = {
            "股票代码": "code", "股票名称": "name", "交易日期": "trade_date",
            "机构席位买入额": "inst_buy", "机构席位卖出额": "inst_sell", "类型": "reason",
        }
        inst.rename(columns={k: v for k, v in col_map_inst.items() if k in inst.columns}, inplace=True)
        inst["trade_date"] = pd.to_datetime(inst["trade_date"]).dt.strftime("%Y-%m-%d")
        inst = inst[inst["trade_date"] == date]
        for c in ["inst_buy", "inst_sell"]:
            if c in inst.columns:
                inst[c] = pd.to_numeric(inst[c], errors="coerce")
        # Aggregate per code
        if not inst.empty:
            inst_agg = inst.groupby(["code", "name"], as_index=False).agg({
                "inst_buy": "sum",
                "inst_sell": "sum",
                "reason": lambda x: "; ".join(str(v) for v in x.unique() if pd.notna(v))[:200],
            })
            inst_agg["inst_net_buy"] = inst_agg["inst_buy"] - inst_agg["inst_sell"]
        else:
            inst_agg = pd.DataFrame(columns=["code", "name", "inst_buy", "inst_sell", "inst_net_buy", "reason"])
    else:
        inst_agg = pd.DataFrame(columns=["code", "name", "inst_buy", "inst_sell", "inst_net_buy", "reason"])

    # merge detail with inst by code; keep name from detail (left side)
    merged = detail.merge(inst_agg.drop(columns=["name"]), on="code", how="left")

    total_count = merged["code"].nunique()
    inst_net_total = merged["inst_net_buy"].sum()
    inst_buy_total = merged["inst_buy"].sum()
    inst_sell_total = merged["inst_sell"].sum()

    # 涨停 & 机构买入，跌停 & 机构卖出
    limit_up = 0
    limit_down = 0
    # sina 龙虎榜列表没有涨跌幅列，但我们可以用 close 与指标文本推断涨停/跌停原因
    # 这里用 reason 文本关键词 + 机构净买入符号
    if "reason" in merged.columns and "inst_net_buy" in merged.columns:
        up_mask = merged["reason"].str.contains("涨幅|涨停", na=False)
        down_mask = merged["reason"].str.contains("跌幅|跌停", na=False)
        limit_up = int(((up_mask) & (merged["inst_net_buy"] > 0)).sum())
        limit_down = int(((down_mask) & (merged["inst_net_buy"] < 0)).sum())

    top_buyers = []
    if "inst_net_buy" in merged.columns and not merged["inst_net_buy"].isna().all():
        top = merged.sort_values("inst_net_buy", ascending=False).head(10)
        for _, r in top.iterrows():
            top_buyers.append({
                "code": str(r.get("code", "")),
                "name": str(r.get("name", "")),
                "inst_net_buy": round(float(r.get("inst_net_buy", 0)) / 1e8, 3),
                "reason": str(r.get("reason_x", "") or r.get("reason_y", "") or ""),
            })

    return {
        "date": date,
        "total_count": int(total_count),
        "inst_net_buy_total": round(float(inst_net_total) / 1e8, 3),
        "inst_buy_total": round(float(inst_buy_total) / 1e8, 3),
        "inst_sell_total": round(float(inst_sell_total) / 1e8, 3),
        "limit_up_with_inst": limit_up,
        "limit_down_with_inst_sell": limit_down,
        "top_inst_buyers": top_buyers,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="yesterday", help="YYYY-MM-DD or yesterday/today")
    parser.add_argument("--start", help="backfill start YYYY-MM-DD")
    parser.add_argument("--end", help="backfill end YYYY-MM-DD")
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)

    if args.start and args.end:
        dates = pd.date_range(args.start, args.end).strftime("%Y-%m-%d").tolist()
    else:
        dates = [parse_date_arg(args.date)]

    for d in dates:
        out = os.path.join(DATA_DIR, f"{d}.json")
        if os.path.exists(out):
            print(f"skip {d}, exists")
            continue
        try:
            data = fetch_lhb(d)
        except Exception as e:
            data = {"date": d, "error": str(e)}
        with open(out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"saved {out}: {data.get('total_count', 0)} records")


if __name__ == "__main__":
    main()
