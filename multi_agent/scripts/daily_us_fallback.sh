#!/usr/bin/env bash
set -euo pipefail

cd /home/liudawei/github/daily_tracker_analytics
. etf_tracker/.venv/bin/activate

DATE=$(date +%Y-%m-%d)
SAVED=$(python3 -c "
from multi_agent.core.db import get_predictions_conn
conn = get_predictions_conn()
c = conn.execute('SELECT COUNT(*) FROM agentic_predictions WHERE pred_date=?', ('$DATE',)).fetchone()[0]
print(c)
conn.close()
")

if [ "$SAVED" -ge 180 ]; then
    echo "A-share prediction already exists ($SAVED records), skip US fallback"
    exit 0
fi

echo "Running US fallback prediction for $DATE"
timeout 1800 python3 multi_agent/scripts/daily_agentic_predictor.py --date "$DATE" --workers 4 --item_timeout 60 --fast --categories US
python3 scripts/generate_pages.py --date "$DATE"
git add docs/ && git commit -m "daily: $DATE US fallback prediction + pages" && git push
