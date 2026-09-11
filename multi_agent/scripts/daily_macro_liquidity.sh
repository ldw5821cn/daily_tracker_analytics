#!/usr/bin/env bash
set -euo pipefail

cd /home/liudawei/github/daily_tracker_analytics
. etf_tracker/.venv/bin/activate

python3 multi_agent/scripts/fetch_macro_liquidity.py

git add multi_agent/data/macro_liquidity/*.json 2>/dev/null || true
git commit -m "daily: update macro liquidity $(date +%Y-%m-%d)" || true
git push
