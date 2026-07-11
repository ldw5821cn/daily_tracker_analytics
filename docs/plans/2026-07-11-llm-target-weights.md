# LLM 目标权重生成 Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** 让 LLM 基于当前预测信号、回测得分和组合状态，生成 ETF/个股/期货的目标权重与调仓清单，过 RiskGuard 后输出。

**Architecture:** 在 `multi_agent/strategy/portfolio_allocator.py` 中实现规则基线分配器（可解释、不依赖外部 LLM 调用），同时保留 LLM 扩展接口。先跑通确定性权重生成，未来再替换为 LLM 生成层。

**Tech Stack:** Python, sqlite3, existing `multi_agent/core/backtest_utils.py`, `RiskGuard` (optional), `multi_agent/futures_simulator.py`.

---

### Task 1: 设计目标权重规则

**Objective:** 定义不依赖 LLM 的初始权重分配规则，确保可解释、可复现。

**规则：**
- 只给 bullish 标的分配正权重；bearish 分配负权重（期货可做空）；neutral 不配。
- 权重基础 = 信号信心 × 回测综合分（bt_score）× 方向符号。
- 同 category 内归一化到目标敞口上限：ETF 30%、个股 50%、期货 20%。
- 板块分散：个股内部 sector 至少 3 个，单个 sector 不超过 30%。
- 期货只处理 watchlist 中的可做空品种，按 RiskGuard 保证金上限过滤。
- 总风险敞口（绝对权重和）不超过 100%。

---

### Task 2: 创建 `multi_agent/strategy/portfolio_allocator.py`

**Objective:** 实现规则权重生成器，读取 `agentic_predictions` 最新数据，输出目标权重 JSON。

**Files:**
- Create: `multi_agent/strategy/portfolio_allocator.py`

**Input:** 最新 `pred_date` 的 `agentic_predictions` 行。
**Output:** `multi_agent/data/target_weights.json`，格式：
```json
{
  "date": "2026-07-11",
  "total_exposure": 0.95,
  "long_exposure": 0.78,
  "short_exposure": 0.17,
  "targets": [
    {"ticker": "600206", "name": "...", "category": "个股", "signal": "bullish", "target_weight": 0.05, "reason": "..."}
  ]
}
```

**Verification:** 跑 CLI 后检查 JSON 存在，总权重在 [-1, 1] 之间，个股 sector 数量 ≥3。

---

### Task 3: 创建 CLI 脚本

**Objective:** 提供可手动触发和 cron 调用的入口。

**Files:**
- Create: `multi_agent/scripts/run_allocator.py`

**Usage:**
```bash
cd /home/liudawei/github/daily_tracker_analytics
. etf_tracker/.venv/bin/activate
python multi_agent/scripts/run_allocator.py
```

**Verification:** 输出目标权重文件路径和统计。

---

### Task 4: 接入日报

**Objective:** 在 `daily_agentic_report.py` 末尾加入“目标权重 Top5”区块。

**Files:**
- Modify: `multi_agent/scripts/daily_agentic_report.py`

**内容：** 展示 long/short 各 Top3，总敞口、板块分布。
**约束：** 总字符数仍控制在 2000 以内。

---

### Task 5: 更新 cron

**Objective:** 把 `run_allocator.py` 加入 `ee703c84d920` 每日流程。

**Files:**
- Modify: Hermes cron job `ee703c84d920`

---

### Task 6: 提交并推送

**Objective:** 合并到 main 并部署 Pages。

```bash
git add -A
git commit -m "feat: LLM-native target weight generator (rule-based baseline)"
git push origin main
```
