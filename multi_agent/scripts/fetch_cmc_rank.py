#!/usr/bin/env python3
"""最小接入：获取加密货币恐惧贪婪指数（CMC / Alternative.me）。

作为外部站点清单第 1 个数据源（cmc_rank），用于观察全球风险偏好情绪。
数据每日更新一次，保存到 multi_agent/data/external/cmc_rank.json。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT_DIR = os.path.join(REPO_ROOT, "multi_agent", "data", "external")
OUT_PATH = os.path.join(OUT_DIR, "cmc_rank.json")

API_URL = "https://api.alternative.me/fng/?limit=90"


def fetch_fng() -> dict:
    req = urllib.request.Request(
        API_URL,
        headers={
            "User-Agent": "Mozilla/5.0 (daily_tracker_analytics; research)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data


def save(data: dict) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    out = {
        "source": "alternative.me Fear and Greed Index",
        "fetched_at": now,
        "data": data.get("data", []),
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(out['data'])} records to {OUT_PATH}")


def main():
    try:
        data = fetch_fng()
        save(data)
    except Exception as e:
        print(f"Error fetching cmc_rank: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
