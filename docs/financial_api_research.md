# 同花顺 Financial-API 调研报告

## 项目概览

| 属性 | 内容 |
|------|------|
| **名称** | Financial-API (hithink-finance) |
| **官方** | 同花顺 (HiThink) |
| **Stars** | 4,075 |
| **Forks** | 363 |
| **语言** | TypeScript + Python |
| **许可** | MIT |
| **创建** | 2026-06-09 |
| **更新** | 2026-10-07（活跃） |

---

## 核心能力

### 数据覆盖

| 数据类型 | 说明 |
|---------|------|
| **A股实时行情** | 单只/多只/全市场最新价格与交易数据 |
| **历史K线** | 日线、周线、月线（用于回测和趋势分析） |
| **集合竞价** | 实时或终态快照、短期强弱基准 |
| **财务报表** | 利润表、资产负债表、现金流量表、财务指标 |
| **估值数据** | 市盈率 PE、市净率 PB、市销率 PS、市现率 PC |
| **指数与板块** | 目录、成分股、快照、历史行情 |
| **特色数据** | 涨跌停、炸板、连板、异动、热榜、龙虎榜 |
| **公募基金** | 资料、净值、持仓、业绩、资讯 |
| **期货期权** | 品种、合约、持仓、仓单、基差、分时、日K |

### 接入方式

| 方式 | 适用场景 |
|------|---------|
| **REST API** | 网站、App、后台服务 |
| **Python SDK** | 数据处理、Notebook、研究脚本 |
| **CLI** | 终端、自动化任务、批量导出 |
| **MCP** | Chat Bot、IDE、AI Agent |
| **DuckDB** | 本地 SQL 研究、历史数据维护 |
| **Agent Skill** | AI Agent 自动调用 |

---

## 与现有系统对比

### 当前数据源

| 数据源 | 用途 | 稳定性 |
|--------|------|--------|
| 腾讯 K 线接口 | 行情数据 | ⚠️ 非官方，可能失效 |
| 东方财富 | 情绪数据（涨停池等） | ⚠️ 非官方，爬取 |
| akshare | A股数据 | ⚠️ 依赖网页结构 |
| Kronos | AI 预测 | ✅ 本地模型 |

### Financial-API 优势

| 优势 | 说明 |
|------|------|
| **官方数据** | 同花顺官方提供，稳定可靠 |
| **一站式** | 行情 + 财务 + 估值 + 特色数据 |
| **AI Agent 友好** | 原生支持 MCP、Skill |
| **批量能力** | 全市场数据文件下载 |
| **本地 DuckDB** | 长期维护历史数据 |

---

## 接入建议

### 方案 A：Python SDK（推荐）

```python
# 安装
pip install hithink-finance

# 使用
from hithink_finance import HiThinkFinance

client = HiThinkFinance(api_key="your-api-key")

# 获取实时行情
quote = client.stock_quote(thscode="000001.SZ")

# 获取历史K线
kline = client.stock_kline(
    thscode="000001.SZ",
    start_date="2024-01-01",
    end_date="2024-12-31",
    frequency="daily"
)

# 获取涨停数据
limit_up = client.limit_up_pool(date="2024-09-29")
```

### 方案 B：CLI 工具

```bash
# 安装
npm install -g hithink-finance-cli

# 配置
hithink-finance config set api-key your-api-key

# 查询行情
hithink-finance quote --thscode 000001.SZ

# 批量导出
hithink-finance dump --type daily-kline --start 2024-01-01 --end 2024-12-31
```

### 方案 C：MCP 接入

```json
// .hermes/mcp.json
{
  "mcpServers": {
    "hithink-finance": {
      "command": "npx",
      "args": ["-y", "hithink-finance-mcp"],
      "env": {
        "HITHINK_API_KEY": "your-api-key"
      }
    }
  }
}
```

---

## 与现有系统集成

### 替换现有数据源

| 现有模块 | 当前数据源 | 替换为 Financial-API |
|---------|-----------|---------------------|
| `sentiment_fetcher.py` | 东方财富爬取 | `limit_up_pool()` API |
| `kronos_predictor.py` | 腾讯K线接口 | `stock_kline()` API |
| `daily_report.py` | 多源混合 | 统一 Financial-API |

### 新增能力

| 能力 | 现有 | Financial-API |
|------|------|--------------|
| **财务报表** | ❌ 无 | ✅ 利润表/资产负债表/现金流量表 |
| **估值数据** | ❌ 手动计算 | ✅ PE/PB/PS/PC 批量查询 |
| **集合竞价** | ❌ 无 | ✅ 竞价快照、强弱基准 |
| **龙虎榜** | ❌ 无 | ✅ 营业部买卖数据 |
| **期货期权** | ❌ 无 | ✅ 合约、持仓、基差 |

---

## 成本评估

### API Key 获取

- 官网：`https://fuyao.aicubes.cn/`
- 管理：`https://fuyao.aicubes.cn/admin/`

### 免费额度（推测）

| 项目 | 额度 |
|------|------|
| 实时行情 | 有限次/日 |
| 历史K线 | 较大额度 |
| 财务报表 | 较大额度 |
| 特色数据 | 中等额度 |

> 具体额度需注册后查看

---

## 实施计划

### 短期（1周）

1. **注册 API Key**
2. **安装 Python SDK**
3. **测试核心接口**（行情、K线、涨停数据）
4. **对比现有数据源**（准确性、稳定性）

### 中期（2周）

1. **替换 sentiment_fetcher.py**
   - 用 `limit_up_pool()` 替代东方财富爬取
   - 用 `limit_down_pool()` 获取跌停数据
   - 用 `break_board_pool()` 获取炸板数据

2. **增强 kronos_predictor.py**
   - 用 `stock_kline()` 替代腾讯接口
   - 支持复权数据（`adjustflag` 参数）

3. **新增财务估值模块**
   - PE/PB/PS/PC 批量查询
   - 与股息率结合，优化选股策略

### 长期（1个月）

1. **本地 DuckDB 数据库**
   - 全市场历史K线落地
   - SQL 查询加速回测

2. **AI Agent 深度集成**
   - MCP 配置
   - Skill 封装

3. **多因子选股**
   - 财务指标 + 估值 + 情绪 + Kronos 预测

---

## 风险提示

1. **API 稳定性**：官方数据源，相对稳定
2. **成本控制**：需注意免费额度，避免超额
3. **数据延迟**：实时行情可能有秒级延迟
4. **依赖风险**：需注册账号，依赖第三方服务

---

## 结论

**Financial-API 是同花顺官方出品的 A 股数据服务，质量高、覆盖全、AI Agent 友好。**

**建议**：
- ✅ **立即接入**：作为现有数据源的**补充和升级**
- ✅ **优先替换**：东方财富爬取（不稳定）→ 官方 API
- ✅ **新增能力**：财务报表、估值数据、集合竞价
- ⚠️ **成本控制**：注意免费额度，批量任务选低峰期

---

**调研时间**: 2026-10-07  
**项目链接**: https://github.com/HiThink-Tech/Financial-API
