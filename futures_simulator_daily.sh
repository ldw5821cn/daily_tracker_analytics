#!/usr/bin/env bash
set -euo pipefail
cd /home/liudawei/github/daily_tracker_analytics
. etf_tracker/.venv/bin/activate
python3 multi_agent/futures_simulator.py --daily
