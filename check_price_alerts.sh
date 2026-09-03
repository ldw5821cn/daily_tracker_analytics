#!/usr/bin/env bash
set -euo pipefail
cd /home/liudawei/github/daily_tracker_analytics
. etf_tracker/.venv/bin/activate
python3 etf_tracker/scripts/check_price_alerts.py
