#!/usr/bin/env bash
set -euo pipefail

# 盘后全链路自动化：更新数据 → 验证 → 回测 → 模型表现 → 生成 Pages → 推送
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

# 0. 更新 warehouse 日线行情（增量：最近 5 个交易日，覆盖假期和延迟）
echo "[$(date +'%Y-%m-%d %H:%M:%S')] 0/8 更新 warehouse 日线行情（最近 5 个交易日）"
"${PYTHON}" multi_agent/scripts/fetch_warehouse_data.py --start $(date -d '5 days ago' +%Y-%m-%d) --workers 1

# 0.1 更新美股缺失标的（如新增标的）
echo "[$(date +'%Y-%m-%d %H:%M:%S')] 0.1/8 更新美股缺失标的"
"${PYTHON}" multi_agent/scripts/backfill_us_patch.py || echo "⚠️ backfill_us_patch.py 失败，继续执行"

# 0.2 更新指数和宏观（外汇为快照，当日有效）
echo "[$(date +'%Y-%m-%d %H:%M:%S')] 0.2/8 更新指数与宏观数据"
"${PYTHON}" multi_agent/scripts/backfill_macro_index.py || echo "⚠️ backfill_macro_index.py 失败，继续执行"

# 1. 验证前一日 agentic_predictions 的 1 日方向准确率
# 16:00 收盘后，当日收盘价已可用，验证前一日预测（或最新预测日期）
echo "[$(date +'%Y-%m-%d %H:%M:%S')] 1/8 运行 morning_validation.py"
"${PYTHON}" multi_agent/scripts/morning_validation.py

# 2. 预测失败分析：对比成功/失败标的特征，找出系统短板
echo "[$(date +'%Y-%m-%d %H:%M:%S')] 2/8 运行 analyze_prediction_errors.py"
"${PYTHON}" multi_agent/scripts/analyze_prediction_errors.py

# 3. 基于验证和错误分析生成 LLM 反思
# 该步依赖 LLM，若失败不应阻塞后续页面生成与推送
echo "[$(date +'%Y-%m-%d %H:%M:%S')] 3/8 运行 generate_prediction_reflection.py"
"${PYTHON}" multi_agent/scripts/generate_prediction_reflection.py || echo "⚠️ generate_prediction_reflection.py 失败，继续执行"

# 4. 根据反思自动调整超参数（A/B 对比记录）
echo "[$(date +'%Y-%m-%d %H:%M:%S')] 4/8 运行 auto_tune_from_reflection.py + A/B 测试"
"${PYTHON}" multi_agent/scripts/auto_tune_from_reflection.py
"${PYTHON}" multi_agent/scripts/ab_test_predictions.py

# 5. 生成 warehouse 真实收益回测报告
# 替换旧版实时拉价回测，使用统一 warehouse 日线数据源
echo "[$(date +'%Y-%m-%d %H:%M:%S')] 5/9 运行 backtest_predictions.py（warehouse 数据源）"
"${PYTHON}" multi_agent/scripts/backtest_predictions.py

# 6. 计算模型表现（warehouse 真实 5d 收益）
echo "[$(date +'%Y-%m-%d %H:%M:%S')] 6/9 计算模型表现（model_performance + parameter_stability）"
"${PYTHON}" multi_agent/scripts/generate_model_performance.py
"${PYTHON}" multi_agent/scripts/evaluate_parameter_stability.py

# 7. 生成 GitHub Pages 静态页面（包含预测、持仓、分类等）
echo "[$(date +'%Y-%m-%d %H:%M:%S')] 7/9 运行 scripts/generate_pages.py"
"${PYTHON}" scripts/generate_pages.py

# 8. 提交并推送 docs/ 与数据 JSON（不推送 llm_predictions.db）
# .gitignore 已排除 *.db，因此无需特别处理
echo "[$(date +'%Y-%m-%d %H:%M:%S')] 8/9 提交并推送 docs/ 与数据 JSON"
if git diff --cached --quiet && git diff --quiet -- docs/ multi_agent/data/*.json; then
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] 没有可提交的变更，跳过 git commit/push"
else
    git add docs/ multi_agent/data/*.json
    git commit -m "auto: evening reflection $(date +%F)" || true
    git push
fi

echo "[$(date +'%Y-%m-%d %H:%M:%S')] 9/9 盘后全链路流水线完成"
