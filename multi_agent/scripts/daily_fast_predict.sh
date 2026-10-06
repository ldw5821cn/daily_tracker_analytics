#!/usr/bin/env bash
set -euo pipefail

cd /home/liudawei/github/daily_tracker_analytics
. etf_tracker/.venv/bin/activate

DATE=${1:-$(date +%Y-%m-%d)}

# 非交易日静默退出（周末/法定节假日）
if ! TRADE_CHECK=$(DATE="$DATE" python3 - <<'PY'
import akshare as ak, os, sys
cal = ak.tool_trade_date_hist_sina()
dates = set(cal['trade_date'].astype(str))
today = os.environ['DATE']
if today not in dates:
    print(f"[daily_fast_predict] {today} 非交易日，静默退出")
    sys.exit(1)
PY
); then
    echo "$TRADE_CHECK"
    exit 0
fi

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
