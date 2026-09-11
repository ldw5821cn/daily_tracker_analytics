#!/usr/bin/env bash
set -euo pipefail

cd /home/liudawei/github/daily_tracker_analytics
. etf_tracker/.venv/bin/activate

python3 multi_agent/scripts/fetch_cmc_rank.py
python3 scripts/generate_cmc_rank_page.py

git add docs/cmc_rank.html multi_agent/data/external/cmc_rank.json 2>/dev/null || true
git commit -m "daily: update cmc_rank $(date +%Y-%m-%d)" || true
git push
