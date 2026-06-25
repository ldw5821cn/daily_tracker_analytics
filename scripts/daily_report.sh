#!/usr/bin/env bash
set -euo pipefail

# 每日定时日报：生成 focus 模式 markdown 报告 + 推送 GitHub Pages + 发送微信
REPO="/home/liudawei/github/daily_tracker_analytics"
VENV="/home/liudawei/github/daily_tracker_analytics/etf_tracker/.venv"
DATE="${1:-$(date +%Y-%m-%d)}"
LOG="/tmp/daily_report_${DATE}.log"

echo "================================" | tee -a "$LOG"
echo "📅 每日日报开始: $DATE" | tee -a "$LOG"
echo "⏰ $(date '+%Y-%m-%d %H:%M:%S')" | tee -a "$LOG"
echo "================================" | tee -a "$LOG"

# 1) 生成 A股/ETF/期货 Markdown 报告
generate_markdown() {
    AGENTIC_REPORT=0 "$VENV/bin/python" -c "
import sys, os
sys.path.insert(0, '/home/liudawei/github/daily_tracker_analytics/multi_agent')
from daily_report import generate_markdown_report, load_daily_focus_list

stocks = []
for t, n, c, tags in load_daily_focus_list('/home/liudawei/github/daily_tracker_analytics/multi_agent/daily_focus_list.json'):
    if c != '期货':
        stocks.append((t, n))

print(f'Focus list: {len(stocks)} stocks')
report = generate_markdown_report(stocks, futures_list=None, current_date='$DATE')
print(f'Report length: {len(report)} chars')
    "
}

# 2) 生成美股日报
generate_us_market() {
    "$VENV/bin/python" -c "
import sys
sys.path.insert(0, '/home/liudawei/github/daily_tracker_analytics/multi_agent')
from us_market import generate_us_market_report
generate_us_market_report(current_date='$DATE')
    "
}

generate_markdown

# 2) 生成美股日报
generate_us_market

# 3) 更新 REPORT_INDEX.md
REPORT_INDEX="$REPO/docs/reports/REPORT_INDEX.md"
if ! grep -q "$DATE" "$REPORT_INDEX"; then
    sed -i "s@|------|------|------|@|------|------|------|\n| $DATE | [查看](./$DATE/) | 多Agent日报 |@" "$REPORT_INDEX"
    echo "✅ 更新 REPORT_INDEX.md" | tee -a "$LOG"
fi

# 4) 部署到 GitHub Pages
cd "$REPO"
bash scripts/deploy_reports.sh | tee -a "$LOG"

# 5) 发送微信推送报告（generate_markdown_report 已生成到 /tmp/wechat_report_${DATE}.txt）
WECHAT_REPORT="/tmp/wechat_report_${DATE}.txt"
if [ -f "$WECHAT_REPORT" ] && [ -s "$WECHAT_REPORT" ]; then
    # 使用 Hermes 的 send_message 工具发送微信
    hermes send --to weixin:o9cq8057Bds8X5Bve17Tt1Vcbh10@im.wechat --file "$WECHAT_REPORT" >> "$LOG" 2>&1 || true
fi

echo "✅ 日报流程完成: $DATE" | tee -a "$LOG"
