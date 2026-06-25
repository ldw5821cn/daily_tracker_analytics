#!/usr/bin/env bash
set -euo pipefail

# 夜间全量 watchlist 跑批：生成所有标的单标报告 + 对比报告 + 期货报告，推送到 GitHub Pages

REPO="/home/liudawei/github/daily_tracker_analytics"
VENV="/home/liudawei/github/daily_tracker_analytics/etf_tracker/.venv"
DATE="${1:-$(date +%Y-%m-%d)}"
LOG="/tmp/full_watchlist_${DATE}.log"

echo "================================" | tee -a "$LOG"
echo "🌙 全量 watchlist 夜间跑批: $DATE" | tee -a "$LOG"
echo "⏰ $(date '+%Y-%m-%d %H:%M:%S')" | tee -a "$LOG"
echo "================================" | tee -a "$LOG"

cd "$REPO/multi_agent"

# 用 daily_report.py 的 markdown 模式跑全量 watchlist 并保存所有报告
AGENTIC_REPORT=0 "$VENV/bin/python" daily_report.py --mode markdown --all --date "$DATE" >> "$LOG" 2>&1

# 生成静态索引（会自动把所有 md 转成 html）
cd "$REPO"
python3 scripts/generate_static_index.py >> "$LOG" 2>&1

# 部署到 GitHub Pages
bash scripts/deploy_reports.sh >> "$LOG" 2>&1

echo "✅ 全量 watchlist 跑批完成: $DATE" | tee -a "$LOG"
