#!/usr/bin/env bash
set -euo pipefail

# [已废弃] 该脚本调用旧 daily_report.py 单 Agent 日报，已被多 Agent 系统替代。
# 当前日报入口：multi_agent/scripts/daily_agentic_report.py
# 当前页面生成入口：scripts/generate_pages.py + scripts/generate_static_index.py
# 当前部署入口：scripts/deploy_reports.sh

REPO="/home/liudawei/github/daily_tracker_analytics"
DATE="${1:-$(date +%Y-%m-%d)}"
LOG="/tmp/daily_report_${DATE}.log"

echo "[DEPRECATED] daily_report.sh 已废弃。" | tee -a "$LOG"
echo "如需日报，请运行：. etf_tracker/.venv/bin/activate && python multi_agent/scripts/daily_agentic_report.py" | tee -a "$LOG"
exit 0
