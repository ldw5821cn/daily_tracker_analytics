# Cron 任务中断问题反思与优化报告

## 问题描述

**时间**: 2026-11-12 08:35-08:44
**现象**: 当日定时任务未推送报告，所有任务状态显示：
```
error: Gateway shutdown (final-cleanup) killed the job's tool subprocess before the run finished.
```

## 根因分析

1. **直接原因**: Gateway 在 08:44:49 异常退出，systemd 自动重启
2. **退出原因**: 飞书 websocket 收到 `ConnectionClosedOK (1000)`，Gateway 将其视为异常并退出
3. **关键问题**: Hermes 内置 cron 任务作为 Gateway 的子进程运行，Gateway 退出时强制 kill 所有子进程
4. **结果**: 正在运行的报告生成任务被中断，报告未完成，自然无法推送

## 优化措施

### 1. 失败重试机制（方案D）

新增 `scripts/cron_retry_wrapper.py`:
- 最大重试 3 次
- 支持重试间隔、超时控制
- 任务锁防止重复运行
- 结果持久化到 `~/.hermes/cron/results/`

新增 wrapper 脚本:
- `~/.hermes/scripts/daily_evening_pipeline_with_retry.sh`
- `~/.hermes/scripts/morning_brief_with_retry.sh`
- `~/.hermes/scripts/etf_report_with_retry.sh`

### 2. 独立运行 cron 任务（方案A）

新增 systemd timer 服务，使 cron 任务独立于 Gateway 运行：
- `hermes-evening-pipeline.timer` - 工作日 15:30 盘后报告
- `hermes-morning-brief.timer` - 工作日 08:15 盘前简报
- `hermes-etf-daily.timer` - 工作日 08:00 ETF日报

### 3. 修复盘前简报推送

移除 `.gitignore` 中对 `docs/morning_briefs/*.html` 的忽略，使盘前简报能正常推送到 GitHub Pages。

## 验证结果

- ✅ systemd timer 可独立触发 morning-brief 服务
- ✅ 重试 wrapper 成功执行并保存结果
- ✅ 盘前简报 `2026-11-21.html` 成功推送到 Pages
- ✅ 验证 URL: https://ldw5821cn.github.io/daily_tracker_analytics/morning_briefs/2026-11-21.html

## 后续监控

1. 观察 11-23（周一）定时任务是否正常执行
2. 使用 `systemctl --user list-timers` 检查 timer 状态
3. 使用 `journalctl --user -u hermes-morning-brief.service` 查看执行日志
4. 原有 Hermes cron 任务可逐步迁移到 systemd timer，或保留作为备份

## 相关提交

- `d7a8abdd` feat: 新增 cron 任务失败重试 wrapper
- `5694aa5a` feat: 新增 systemd timer 独立运行关键 cron 任务
- `96fa1866` fix: 盘前简报改为可推送
