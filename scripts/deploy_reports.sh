#!/bin/bash
# deploy_reports.sh — 将新生成的报告提交并推送到 GitHub，触发 Pages 部署
# 在报告生成后调用：python3 quant_agent.py && bash scripts/deploy_reports.sh
# 或者由 cron job 直接调用

set -e

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

LOG_FILE="$REPO_DIR/logs/deploy_$(date +%Y%m%d).log"
mkdir -p "$(dirname "$LOG_FILE")"

echo "========================================" | tee -a "$LOG_FILE"
echo "📤 部署报告到 GitHub Pages" | tee -a "$LOG_FILE"
echo "⏰ $(date '+%Y-%m-%d %H:%M:%S')" | tee -a "$LOG_FILE"
echo "📂 $(pwd)" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"

# 1. 检查是否有新报告
NEW_REPORTS=$(git status --porcelain docs/reports/ | wc -l)
echo "📊 新增/修改的报告文件数: $NEW_REPORTS" | tee -a "$LOG_FILE"

if [ "$NEW_REPORTS" -eq 0 ]; then
  # 检查 docs/ 其他变化
  OTHER_CHANGES=$(git status --porcelain docs/ | wc -l)
  if [ "$OTHER_CHANGES" -eq 0 ]; then
    echo "✅ 没有新报告，跳过部署" | tee -a "$LOG_FILE"
    exit 0
  fi
fi

# 2. 生成静态索引
echo "🏗️  生成静态 index.html..." | tee -a "$LOG_FILE"
python3 scripts/generate_static_index.py 2>&1 | tee -a "$LOG_FILE"

# 3. Commit & Push
echo "📦 Git add..." | tee -a "$LOG_FILE"
git add -A

echo "📝 Git commit..." | tee -a "$LOG_FILE"
REPORT_COUNT=$(find docs/reports -name "multi_etf_report_*.md" -newer "$LOG_FILE" 2>/dev/null | wc -l || echo "?")
git commit -m "chore: auto-deploy reports $(date '+%Y-%m-%d %H:%M')" \
  --author="ETF Bot <etf-bot@zhihu.com>" 2>&1 | tee -a "$LOG_FILE" || {
    echo "ℹ️  无新提交（可能没有变化）" | tee -a "$LOG_FILE"
    exit 0
}

echo "☁️  Git push..." | tee -a "$LOG_FILE"
git push origin main 2>&1 | tee -a "$LOG_FILE"

echo "✅ Pages 部署已触发！GitHub Pages 缓存刷新通常需要 5-10 分钟" | tee -a "$LOG_FILE"
echo "🌐 https://ldw5821cn.github.io/daily_tracker_analytics/" | tee -a "$LOG_FILE"

# 可选：用 cache purge 尝试加速（GitHub Pages 边缘缓存由 Fastly 提供，cache-control 不可用，只能等待）
echo "⏳ 建议 5-10 分钟后访问，或直接刷新页面缓存" | tee -a "$LOG_FILE"
