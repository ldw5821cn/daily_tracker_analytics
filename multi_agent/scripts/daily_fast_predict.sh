#!/usr/bin/env bash
set -euo pipefail

cd /home/liudawei/github/daily_tracker_analytics
. etf_tracker/.venv/bin/activate

DATE=${1:-$(date +%Y-%m-%d)}

echo "Running daily fast prediction for $DATE"
timeout 1800 python3 multi_agent/scripts/daily_agentic_predictor.py \
    --date "$DATE" \
    --workers 6 \
    --item_timeout 30 \
    --fast \
    --skip_us \
    --categories ETF,个股,期货

python3 scripts/generate_pages.py --date "$DATE"

git add docs/ multi_agent/data/macro_report.json 2>/dev/null || true
git commit -m "daily: $DATE fast prediction + pages" || true
git push
