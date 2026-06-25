#!/usr/bin/env bash
# ===================================
# 多板块 ETF 智能投资分析系统 - 定时任务脚本
# 每天 8:00 执行分析，结果通过 Hermes 微信推送
# ===================================

set -euo pipefail

PROJECT_DIR="/home/liudawei/github/daily_tracker_analytics/etf_tracker"
VENV_DIR="$PROJECT_DIR/.venv"
LOG_DIR="$PROJECT_DIR/logs"
REPORT_DIR="$PROJECT_DIR/reports"
LOG_FILE="$LOG_DIR/daily_$(date +%Y%m%d).log"
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

# 确保目录存在
mkdir -p "$LOG_DIR" "$REPORT_DIR"

# 日志函数
log() {
    echo "[$TIMESTAMP] $1" | tee -a "$LOG_FILE"
}

log "========================================="
log "📊 ETF 日报生成任务启动"
log "⏰ 时间: $TIMESTAMP"
log "========================================="

# 检查虚拟环境
if [ ! -f "$VENV_DIR/bin/activate" ]; then
    log "❌ 虚拟环境不存在: $VENV_DIR"
    exit 1
fi

# 激活虚拟环境
source "$VENV_DIR/bin/activate"
cd "$PROJECT_DIR"

PYTHON_CMD="$VENV_DIR/bin/python"
if [ ! -x "$PYTHON_CMD" ]; then
    log "❌ Python 不存在: $PYTHON_CMD"
    exit 1
fi

log "工作目录: $PROJECT_DIR"

# 加载 .env 环境变量
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    source "$PROJECT_DIR/.env"
    set +a
    log "已加载 .env 配置"
fi

# 运行 ETF 跟踪分析
log "开始运行 ETF 分析..."
$PYTHON_CMD etf_tracker.py 2>&1 | tee -a "$LOG_FILE"
EXIT_CODE=$?

# 发送微信通知的辅助函数
send_wechat_notify() {
    local msg="$1"
    hermes send --to weixin "$msg" 2>&1 | tee -a "$LOG_FILE" || true
}

if [ $EXIT_CODE -eq 0 ]; then
    log "✅ ETF 分析完成"

    # 获取最新报告
    LATEST_REPORT=$(ls -t "$REPORT_DIR"/*.md 2>/dev/null | head -1)

    if [ -n "$LATEST_REPORT" ] && [ -f "$LATEST_REPORT" ]; then
        log "📄 报告: $LATEST_REPORT"

        # 提取报告摘要（标题和关键段落）
        REPORT_SUMMARY=$(head -c 2500 "$LATEST_REPORT")

        # 通过 Hermes 发送微信通知
        WX_MSG="📊 ETF日报 $(date '+%m-%d')

$(head -c 2000 "$LATEST_REPORT")

📁 完整报告: $LATEST_REPORT
⏰ $(date '+%H:%M')"
        send_wechat_notify "$WX_MSG"

        log "✅ 报告已输出并推送微信"
    else
        log "⚠️ 未找到报告文件"
        send_wechat_notify "⚠️ ETF 分析已完成($(date '+%m-%d %H:%M'))，但未生成报告文件"
    fi
else
    log "❌ ETF 分析失败 (exit: $EXIT_CODE)"
    send_wechat_notify "❌ ETF 分析失败 ($(date '+%m-%d %H:%M'))，请查看日志: $LOG_FILE"
fi

log "========================================="
log "✅ 任务完成"
log "========================================="
echo "" >> "$LOG_FILE"
