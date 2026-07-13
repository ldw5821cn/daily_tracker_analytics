#!/usr/bin/env bash
set -euo pipefail

# 盘后全链路自动化：验证 → 错误分析 → 生成反思 → 生成 Pages → 推送
REPO_DIR="/home/liudawei/github/daily_tracker_analytics"
LOG_DIR="${REPO_DIR}/logs"
LOG_FILE="${LOG_DIR}/daily_evening_pipeline_$(date +%F).log"
mkdir -p "${LOG_DIR}"

exec > >(tee -a "${LOG_FILE}") 2>&1

echo "[$(date +'%Y-%m-%d %H:%M:%S')] 启动盘后全链路流水线"

cd "${REPO_DIR}"

# 激活 venv
# shellcheck source=/dev/null
source "etf_tracker/.venv/bin/activate"

PYTHON="python3"

# 1. 验证前一日 agentic_predictions 的 1 日方向准确率
# 16:00 收盘后，当日收盘价已可用，验证前一日预测（或最新预测日期）
echo "[$(date +'%Y-%m-%d %H:%M:%S')] 1/5 运行 morning_validation.py"
"${PYTHON}" multi_agent/scripts/morning_validation.py

# 2. 预测失败分析：对比成功/失败标的特征，找出系统短板
echo "[$(date +'%Y-%m-%d %H:%M:%S')] 2/5 运行 analyze_prediction_errors.py"
"${PYTHON}" multi_agent/scripts/analyze_prediction_errors.py

# 3. 基于验证和错误分析生成 LLM 反思
# 该步依赖 LLM，若失败不应阻塞后续页面生成与推送
echo "[$(date +'%Y-%m-%d %H:%M:%S')] 3/5 运行 generate_prediction_reflection.py"
"${PYTHON}" multi_agent/scripts/generate_prediction_reflection.py || echo "⚠️ generate_prediction_reflection.py 失败，继续执行"

# 4. 生成 GitHub Pages 静态页面（包含预测、持仓、分类等）
echo "[$(date +'%Y-%m-%d %H:%M:%S')] 4/5 运行 scripts/generate_pages.py"
"${PYTHON}" scripts/generate_pages.py

# 5. 提交并推送 docs/ 与数据 JSON（不推送 llm_predictions.db）
# .gitignore 已排除 *.db，因此无需特别处理
echo "[$(date +'%Y-%m-%d %H:%M:%S')] 5/5 提交并推送 docs/ 与数据 JSON"
if git diff --cached --quiet && git diff --quiet -- docs/ multi_agent/data/*.json; then
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] 没有可提交的变更，跳过 git commit/push"
else
    git add docs/ multi_agent/data/*.json
    git commit -m "auto: evening reflection $(date +%F)" || true
    git push
fi

echo "[$(date +'%Y-%m-%d %H:%M:%S')] 盘后全链路流水线完成"
