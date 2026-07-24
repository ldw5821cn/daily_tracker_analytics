#!/usr/bin/env python3
"""每日市场资金/情绪指标采集：融资融券、期权 PCR/VIX、北向资金（可用时）。

数据统一存入 warehouse.sentiment 表，metric 前缀区分：
- margin_sse_* / margin_szse_*: 沪市/深市融资融券
- northbound_*: 北向资金（停更则返回空）
- option_pcr_* / option_vix_*: 期权 PCR 和 VIX
"""
import json
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional

PR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(PR, "multi_agent"))

import akshare as ak
import pandas as pd
from core.warehouse import get_warehouse_conn

OUT = os.path.join(PR, "multi_agent", "data", "market_flow.json")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def fetch_northbound(start: str, end: str) -> Optional[Dict]:
    """北向资金；akshare 最新数据可能已停更，返回能取到的最新有效记录。"""
    try:
        df = ak.stock_hsgt_hist_em(symbol="北向资金")
        if df is None or df.empty:
            return None
        df = df[["日期", "当日成交净买额", "买入成交额", "卖出成交额", "当日资金流入"]].copy()
        df = df.sort_values("日期")
        df_valid = df[df["当日成交净买额"].notna()]
        if df_valid.empty:
            return None
        latest = df_valid.iloc[-1]
        return {
            "date": str(latest["日期"])[:10],
            "net_buy": float(latest["当日成交净买额"]) if pd.notna(latest["当日成交净买额"]) else None,
            "buy_amount": float(latest["买入成交额"]) if pd.notna(latest["买入成交额"]) else None,
            "sell_amount": float(latest["卖出成交额"]) if pd.notna(latest["卖出成交额"]) else None,
            "inflow": float(latest["当日资金流入"]) if pd.notna(latest["当日资金流入"]) else None,
        }
    except Exception as e:
        print(f"  ⚠️ 北向资金: {e}")
        return None


def fetch_margin_sse(start: str, end: str) -> List[Dict]:
    """沪市融资融券。"""
    try:
        df = ak.stock_margin_sse(start_date=start.replace("-", ""), end_date=end.replace("-", ""))
        if df is None or df.empty:
            return []
        df = df.sort_values("信用交易日期")
        rows = []
        for _, r in df.iterrows():
            rows.append({
                "date": datetime.strptime(str(r["信用交易日期"]), "%Y%m%d").strftime("%Y-%m-%d"),
                "fin_balance": float(r["融资余额"]) if pd.notna(r["融资余额"]) else None,
                "fin_buy": float(r["融资买入额"]) if pd.notna(r["融资买入额"]) else None,
                "sec_balance": float(r["融券余量金额"]) if pd.notna(r["融券余量金额"]) else None,
                "sec_sell": float(r["融券卖出量"]) if pd.notna(r["融券卖出量"]) else None,
                "total_balance": float(r["融资融券余额"]) if pd.notna(r["融资融券余额"]) else None,
            })
        return rows
    except Exception as e:
        print(f"  ⚠️ 沪市融资: {e}")
        return []


def fetch_margin_szse(end: str) -> List[Dict]:
    """深市融资融券，按日期单查；非交易日返回空，跳过。"""
    rows = []
    for i in range(15):
        d = (datetime.strptime(end, "%Y-%m-%d") - timedelta(days=i)).strftime("%Y%m%d")
        try:
            df = ak.stock_margin_szse(date=d)
            if df is None or df.empty or len(df.columns) == 0:
                continue
            required = {"融资买入额", "融资余额", "融券卖出量", "融券余额", "融资融券余额"}
            if not required.issubset(set(df.columns)):
                continue
            r = df.iloc[0]
            rows.append({
                "date": datetime.strptime(d, "%Y%m%d").strftime("%Y-%m-%d"),
                "fin_buy": float(r["融资买入额"]) if pd.notna(r["融资买入额"]) else None,
                "fin_balance": float(r["融资余额"]) if pd.notna(r["融资余额"]) else None,
                "sec_sell": float(r["融券卖出量"]) if pd.notna(r["融券卖出量"]) else None,
                "sec_balance": float(r["融券余量金额"]) if pd.notna(r["融券余量金额"]) else None,
                "total_balance": float(r["融资融券余额"]) if pd.notna(r["融资融券余额"]) else None,
            })
        except Exception as e:
            pass  # 非交易日或数据缺失，静默跳过
    return rows


def fetch_option_pcr(end: str) -> List[Dict]:
    """期权 PCR 和 VIX。"""
    rows = []
    for symbol in ["50etf", "300etf", "500etf"]:
        try:
            df = getattr(ak, f"index_option_{symbol}_qvix")()
            if df is None or df.empty:
                continue
            df = df.sort_values("date").tail(10)
            for _, r in df.iterrows():
                rows.append({
                    "date": str(r["date"])[:10],
                    "symbol": symbol.upper(),
                    "vix_open": float(r["open"]) if pd.notna(r["open"]) else None,
                    "vix_high": float(r["high"]) if pd.notna(r["high"]) else None,
                    "vix_low": float(r["low"]) if pd.notna(r["low"]) else None,
                    "vix_close": float(r["close"]) if pd.notna(r["close"]) else None,
                })
        except Exception as e:
            print(f"  ⚠️ 期权 VIX {symbol}: {e}")

    for exchange in ["sse", "szse"]:
        for i in range(15):
            d = (datetime.strptime(end, "%Y-%m-%d") - timedelta(days=i)).strftime("%Y%m%d")
            try:
                df = getattr(ak, f"option_daily_stats_{exchange}")(date=d)
                if df is None or df.empty or "合约标的代码" not in df.columns:
                    continue
                pcr_col = "认沽/认购" if "认沽/认购" in df.columns else "认沽/认购持仓比"
                if pcr_col not in df.columns:
                    continue
                for _, r in df.iterrows():
                    rows.append({
                        "date": d[:4] + "-" + d[4:6] + "-" + d[6:],
                        "symbol": f"{exchange}_{r['合约标的代码']}",
                        "pcr": float(r[pcr_col]) if pd.notna(r[pcr_col]) else None,
                        "call_volume": float(r.get("认购成交量", 0)) if pd.notna(r.get("认购成交量")) else None,
                        "put_volume": float(r.get("认沽成交量", 0)) if pd.notna(r.get("认沽成交量")) else None,
                        "total_open_interest": float(r.get("未平仓合约总数", 0)) if pd.notna(r.get("未平仓合约总数")) else None,
                    })
            except Exception:
                pass  # 非交易日或数据缺失，静默跳过
    return rows


def save_to_sentiment(records: List[Dict]) -> Dict[str, int]:
    """将 market flow 指标写入 warehouse.sentiment 表。"""
    if not records:
        return {"saved": 0, "errors": 0}
    conn = get_warehouse_conn()
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stats = {"saved": 0, "errors": 0}
    for r in records:
        try:
            cur.execute(
                """
                INSERT INTO sentiment (date, ticker, metric, value, detail, source, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(date, ticker, metric) DO UPDATE SET
                    value=excluded.value, detail=excluded.detail, source=excluded.source, updated_at=excluded.updated_at
                """,
                (
                    r["date"],
                    r.get("ticker", ""),
                    r["metric"],
                    r.get("value"),
                    json.dumps(r.get("detail", {}), ensure_ascii=False, default=str),
                    r.get("source", "market_flow"),
                    now,
                ),
            )
            stats["saved"] += 1
        except Exception as e:
            print(f"  ❌ 保存失败 {r}: {e}")
            stats["errors"] += 1
    conn.commit()
    conn.close()
    return stats


def build_records() -> List[Dict]:
    today = _today()
    start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    records = []

    nb = fetch_northbound(start, today)
    if nb:
        records.append({
            "date": nb["date"], "ticker": "", "metric": "northbound_net_buy",
            "value": nb["net_buy"], "detail": nb, "source": "akshare_northbound",
        })

    for r in fetch_margin_sse(start, today):
        records.append({
            "date": r["date"], "ticker": "", "metric": "margin_sse_total_balance",
            "value": r["total_balance"], "detail": r, "source": "akshare_margin_sse",
        })

    for r in fetch_margin_szse(today):
        records.append({
            "date": r["date"], "ticker": "", "metric": "margin_szse_total_balance",
            "value": r["total_balance"], "detail": r, "source": "akshare_margin_szse",
        })

    for r in fetch_option_pcr(today):
        if "vix_close" in r:
            records.append({
                "date": r["date"], "ticker": r["symbol"], "metric": "option_vix_close",
                "value": r["vix_close"], "detail": r, "source": "akshare_option_vix",
            })
        if "pcr" in r:
            records.append({
                "date": r["date"], "ticker": r["symbol"], "metric": "option_pcr",
                "value": r["pcr"], "detail": r, "source": "akshare_option_stats",
            })

    return records


def main():
    records = build_records()
    stats = save_to_sentiment(records)
    print(f"[market_flow] 保存 {stats['saved']} 条，失败 {stats['errors']} 条")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "today": _today(),
            "records": records,
            "stats": stats,
        }, f, ensure_ascii=False, indent=2, default=str)
    print(f"[market_flow] 已写入 {OUT}")


if __name__ == "__main__":
    main()
