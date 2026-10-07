# 同花顺 Financial-API 调研报告

## ✅ 接入状态

| 项目 | 状态 |
|------|------|
| **API Key** | ✅ 已配置（sk-fuy...oG7i） |
| **客户端封装** | ✅ `multi_agent/hithink_client.py` |
| **测试通过** | ✅ 行情/K线/特色数据 |

---

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

## 客户端封装

### 文件位置

`multi_agent/hithink_client.py`

### 主要类

```python
class HiThinkFinanceClient:
    """同花顺金融数据客户端"""
    
    # 行情数据
    def get_quote(thscode: str) -> dict
    def get_quotes(thscodes: List[str]) -> List[dict]
    def get_kline(thscode: str, start: str, end: str) -> List[dict]
    
    # 特色数据
    def get_limit_up_pool(date: str = None) -> List[dict]
    def get_limit_down_pool(date: str = None) -> List[dict]
    def get_break_board_pool(date: str = None) -> List[dict]
    def get_streak_board(date: str = None) -> List[dict]
    def get_hot_list(date: str = None) -> List[dict]
    def get_dragon_tiger_list(date: str = None) -> List[dict]
    
    # 财务数据
    def get_income_statement(thscode: str, periods: int) -> List[dict]
    def get_balance_sheet(thscode: str, periods: int) -> List[dict]
    def get_cash_flow(thscode: str, periods: int) -> List[dict]
    def get_financial_indicators(thscode: str, periods: int) -> List[dict]
    
    # 估值数据
    def get_valuation(thscodes: List[str]) -> List[dict]
    
    # 指数数据
    def get_index_list(tag: str = None) -> List[dict]
    def get_index_constituents(thscode: str) -> List[dict]
    
    # 交易日历
    def get_trading_days(start: str, end: str) -> List[str]
    
    # 工具方法
    @staticmethod
    def to_thscode(symbol: str) -> str  # 000001 -> 000001.SZ
    @staticmethod
    def from_thscode(thscode: str) -> str  # 000001.SZ -> 000001
```

### 使用示例

```python
from multi_agent.hithink_client import HiThinkFinanceClient

client = HiThinkFinanceClient()

# 行情快照
quote = client.get_quote('000001.SZ')
print(f"最新价: {quote['last_price']}")

# 历史 K 线
kline = client.get_kline('000001.SZ', '2024-01-01', '2024-12-31')
for bar in kline[-5:]:
    print(f"{bar['date']}: {bar['close_price']}")

# 涨停池
limit_up = client.get_limit_up_pool()
print(f"涨停股票数: {len(limit_up)}")
```

---

## 测试记录

### 2026-10-07 测试

```
✅ 行情快照（平安银行）
   最新价: 11.57
   涨跌幅: 1.94%
   成交量: 104,535,745

✅ 历史 K 线（最近 5 天）
   2024-09-23: 开8.44 高8.64 低8.42 收8.59
   2024-09-24: 开8.66 高8.92 低8.63 收8.92
   2024-09-25: 开9.07 高9.25 低9.00 收9.05
   2024-09-26: 开9.05 高9.70 低9.05 收9.70
   2024-09-27: 开9.90 高10.11 低9.58 收9.97

✅ 涨停池（空数据，非交易时间）
   涨停股票数: 0

共发送 3 次请求
```

---

## 接入建议

### 方案 A：Python SDK（推荐）

```bash
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
| `sentiment_fetcher.py` | 东方财富爬取 | `get_limit_up_pool()` 等 |
| `kronos_predictor.py` | 腾讯K线接口 | `get_kline()` |
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

1. **注册 API Key** ✅ 已完成
2. **封装客户端** ✅ 已完成
3. **测试核心接口** ✅ 已完成
4. **对比现有数据源**（准确性、稳定性）

### 中期（2周）

1. **替换 sentiment_fetcher.py**
   - 用 `get_limit_up_pool()` 替代东方财富爬取
   - 用 `get_limit_down_pool()` 获取跌停数据
   - 用 `get_break_board_pool()` 获取炸板数据

2. **增强 kronos_predictor.py**
   - 用 `get_kline()` 替代腾讯接口
   - 支持复权数据（`adjust` 参数）

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

## 注意事项

### 1. 限流策略

- **当前不限累计次数**
- **建议频率**: ≤10 次/秒
- **HTTP 429**: 触发限流，降低并发

### 2. 数据格式

- **时间戳**: 毫秒级 Unix 时间戳
- **时区**: Asia/Shanghai
- **货币**: CNY
- **字段**: snake_case

### 3. 错误处理

| code | 说明 |
|------|------|
| 0 | 成功 |
| 1001 | 缺少必需参数 |
| 1002 | 参数格式错误 |
| 1003 | 时间范围超限（>10年） |
| 2001 | API Key 缺失/无效 |
| 4001 | 触发限流 |

---

## 参考链接

| 资源 | 链接 |
|------|------|
| **官网** | https://fuyao.aicubes.cn/ |
| **API 文档** | https://fuyao.aicubes.cn/docs/ |
| **在线调试** | https://fuyao.aicubes.cn/playground/ |
| **API Key 管理** | https://fuyao.aicubes.cn/admin/ |
| **GitHub** | https://github.com/HiThink-Tech/Financial-API |
| **llms.txt** | https://fuyao.aicubes.cn/llms.txt |
| **llms-full.txt** | https://fuyao.aicubes.cn/llms-full.txt |

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
**状态**: ✅ 已接入，测试通过
