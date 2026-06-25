# Agentic LLM 增强报告

`daily_tracker_analytics/multi_agent/agentic_report_generator.py` 借鉴 Fincept Terminal 的 AI Agent 投研框架，在原有技术面 / 基本面 / 新闻 / 辩论四 Agent 基础上，新增一组 Specialist Agent：

- **技术面分析师** (Technical Analyst)
- **基本面分析师** (Fundamental Analyst)
- **新闻舆情分析师** (News Analyst)
- **风控官** (Risk Manager)
- **投资组合经理** (Portfolio Manager) — 汇总前述观点给出最终裁决

每个 Specialist 独立调用 LLM，输出结构化 JSON，最终由 Portfolio Manager 综合生成包含“评级 / 信号 / 置信度 / 目标区间 / 止损 / 建议仓位 / 操作检查清单”的 Markdown 报告。

## 成本控制

全量对 100 只标的同时启用 Agentic，每只标的约 6 次 LLM 调用，费用较高。因此默认采用**白名单机制**：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `AGENTIC_REPORT` | `1` | 总开关。`0/false/off/no` 时全部关闭 Agentic |
| `AGENTIC_WHITELIST` | `516150,515880` | 逗号分隔的 ticker 白名单，仅对这些标的启用 Agentic |

示例：

```bash
# 关闭 Agentic
export AGENTIC_REPORT=0

# 仅对稀土 ETF 和通信 ETF 启用
export AGENTIC_WHITELIST=516150,515880

# 扩大白名单（再加几只重点个股）
export AGENTIC_WHITELIST=516150,515880,601991,688981
```

## 运行

```bash
cd /home/liudawei/github/daily_tracker_analytics/multi_agent
python daily_report.py --mode wechat
```

## 扩展白名单

编辑 `daily_report.py` 中的 `AGENTIC_WHITELIST` 默认值，或在 cron 运行时通过环境变量注入。

## 依赖

- 复用项目已有的 `llm_report_generator.LLMProvider`
- 无需额外安装包
- API Key 读取优先级：`LLM_REPORT_API_KEY` → `KIMI_API_KEY` → `ANTHROPIC_API_KEY` → `OPENAI_API_KEY` → `AUXILIARY_VISION_API_KEY`
