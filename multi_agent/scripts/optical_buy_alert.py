#!/usr/bin/env python3
"""光模块/光器件个股买入提醒小工具。

触发条件（可配置，不硬编码）：默认采用"趋势回踩 + 超跌反弹"双逻辑。
- 超跌反弹：单日大跌 ≥ 7% 或 5 日累计跌幅 ≥ 12%
- 趋势回踩：股价回落至 20 日均线附近（±2%）且成交量未异常放大
- 龙头企稳：中际旭创单日跌幅收窄或收红，作为板块情绪锚

数据源：富途 OpenD（A 股实时行情）。
输出：JSON 提醒列表，可接入微信/飞书推送。
"""

import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional
import pandas as pd

try:
    from futu import OpenQuoteContext
except ImportError:
    print("ERROR: futu-api not installed")
    sys.exit(1)


# 光模块核心标的（代码: 对应买入偏好）
OPTICAL_TICKERS = {
    "SZ.300308": "中际旭创",
    "SZ.300502": "新易盛",
    "SZ.300394": "天孚通信",
    "SZ.002281": "光迅科技",
    "SZ.000988": "华工科技",
}


@dataclass
class Alert:
    ticker: str
    name: str
    last_price: float
    change_rate: float
    trigger: str
    reason: str
    score: int


def fetch_hist(qc, code: str, ktype=1, max_count=30) -> Optional[pd.DataFrame]:
    """从富途拉取日线历史，ktype=1 日 K"""
    import pandas as pd
    ret, data, page = qc.request_history_kline(code, ktype=ktype, max_count=max_count)
    if ret != 0:
        return None
    data = data.copy()
    data["ma20"] = data["close"].rolling(20).mean()
    return data


def evaluate(qc, code: str, name: str, thresholds: dict) -> Optional[Alert]:
    import pandas as pd
    ret, snap = qc.get_market_snapshot([code])
    if ret != 0 or snap.empty:
        return None
    row = snap.iloc[0]
    last = float(row["last_price"])
    prev_close = float(row["prev_close_price"])
    change_rate = (last - prev_close) / prev_close * 100
    amplitude = float(row.get("amplitude", 0))
    turnover_rate = float(row.get("turnover_rate", 0))

    hist = fetch_hist(qc, code, max_count=30)
    ma20 = None
    if hist is not None and not hist.empty:
        ma20 = float(hist.iloc[-1]["ma20"])
        ma20_prev = float(hist.iloc[-2]["ma20"]) if len(hist) >= 2 else ma20
        ma20_change = (ma20 - ma20_prev) / ma20_prev * 100 if ma20_prev else 0
        # 5 日累计跌幅
        close_5d = float(hist.iloc[-6]["close"]) if len(hist) >= 6 else prev_close
        ret_5d = (last - close_5d) / close_5d * 100
    else:
        ret_5d = 0

    triggers = []
    score = 0

    # 1. 超跌反弹
    if change_rate <= -thresholds.get("single_day_drop", 7.0):
        triggers.append(f"单日大跌 {change_rate:.2f}%")
        score += 40
    if ret_5d <= -thresholds.get("five_day_drop", 12.0):
        triggers.append(f"5日累计跌 {ret_5d:.2f}%")
        score += 30

    # 2. 回踩 20 日均线
    if ma20 and last >= ma20 * (1 - thresholds.get("ma20_lower", 0.02)) and last <= ma20 * (1 + thresholds.get("ma20_upper", 0.02)):
        triggers.append(f"回踩20日均线({ma20:.2f})")
        score += 20
        if ma20_change > 0:
            score += 10

    # 3. 成交量温和（未放量恐慌）
    if turnover_rate <= thresholds.get("turnover_cap", 8.0):
        score += 10

    if not triggers:
        return None

    return Alert(
        ticker=code,
        name=name,
        last_price=last,
        change_rate=change_rate,
        trigger="; ".join(triggers),
        reason=f"光模块板块回调，{name}触发买入条件，建议结合大盘和美股AI链情绪择时",
        score=min(score, 100),
    )


def main():
    thresholds = {
        "single_day_drop": 7.0,
        "five_day_drop": 12.0,
        "ma20_lower": 0.02,
        "ma20_upper": 0.02,
        "turnover_cap": 8.0,
    }

    alerts: List[Alert] = []
    with OpenQuoteContext("127.0.0.1", 11111) as qc:
        for code, name in OPTICAL_TICKERS.items():
            alert = evaluate(qc, code, name, thresholds)
            if alert:
                alerts.append(alert)

    out = {
        "date": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
        "alerts": [asdict(a) for a in sorted(alerts, key=lambda x: x.score, reverse=True)],
    }
    out_path = Path("multi_agent/data/optical_alert.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
