# daily_tracker_analytics 三模块改造方案

> 调研范围：daily_tracker_analytics（目标仓库）、ZhuLinsen/daily_stock_analysis（参考仓库）  
> 调研重点：参考仓库的 `src/services/daily_market_context.py`、`src/core/config_registry.py`、`src/services/intelligence_service.py`、`src/data/stock_index_loader.py`  
> 输出目标：先方案，后实施；不直接改代码，只给出可落地改造计划。

---

## 仓库现状速览

### 目标仓库（daily_tracker_analytics）
- 所有业务代码集中在 `multi_agent/` 下，没有 `src/` 目录。
- 配置分散：
  - 机械打分参数：`multi_agent/config/predictor_params.json`
  - 市场规则：`multi_agent/config/market_rules.json`
  - 雪球/微信配置：`multi_agent/config/xueqiu_config.json`
  - 环境变量零散使用（`TUSHARE_TOKEN`、`SIM_CAPITAL` 等）
- 市场上下文：没有统一模型，大盘状态通过 `macro_report.json`、`market_flow.json`、`morning_validation.json` 等数据文件传递，由 `predictor.py` 在 prompt 中硬编码注入。
- 资讯：只有 `multi_agent/core/news_engine.py` 通过 `akshare` 抓东方财富个股新闻，没有多源可配置情报池，没有持久化。
- 股票代码补全：没有统一映射，`watchlist.py` 硬编码 100+ 只标的，用户输入中文名或模糊代码无法自动解析。
- 数据加载：已有 `multi_agent/core/data_loader_registry.py`，但那是**数据源加载器**注册表，不是**配置/字段**注册表。

### 参考仓库（daily_stock_analysis）可借鉴点
- `daily_market_context.py`：提供 `DailyMarketContext` 数据类、`get_context` 缓存、`format_daily_market_context_prompt_section` 统一注入 prompt，支持 query_id 作用域缓存、区域（cn/hk/us/jp/kr）、风险标签、仓位提示。
- `config_registry.py`：`_FIELD_DEFINITIONS` + `_CATEGORY_DEFINITIONS` + `build_schema_response()`，统一配置字段元数据、分类、默认值、UI 控制、验证规则。
- `intelligence_service.py`：可配置 RSS/Atom/NewsNow 情报源，持久化到 `IntelligenceItem`/`IntelligenceSource`，支持 fetch/upsert/list、自动刷新、DNS 安全校验。
- `stock_index_loader.py`：基于 `stocks.index.json` 做股票名称/代码双向映射，支持 A 股、港股、美股、日股、韩股，支持多候选路径加载与缓存。

---

## 方案一：每日市场上下文注入

### 当前缺失点
1. 没有统一的市场上下文数据模型，导致大盘摘要、风险标签、仓位提示散落在多个 JSON 文件和脚本里。
2. `predictor.py` 中 `ZHENGXI_METHODOLOGY` 与提示词硬编码，大盘环境是每次重新写死，没有"复用当日市场上下文"机制。
3. 多 agent/多轮分析时，大盘上下文重复生成或重复读取，浪费 token 与调用时间。
4. 缺少风险标签（`high_risk`、`conservative`、`market_cooling`、`low_position_cap`）的自动提取，无法作为策略阈值输入。
5. 没有区域化市场上下文（A 股/港股/美股/期货），不同标的注入错误上下文会导致提示质量下降。

### 借鉴内容
- 参考 `daily_market_context.py` 的 `DailyMarketContext` 数据类，新增目标仓库的 `multi_agent/core/daily_market_context.py`。
- 借鉴 `get_context` 的缓存策略：
  - 当日首次运行时生成；
  - 同 query_id / 同运行会话复用；
  - 支持 force_refresh 与 allow_generate 开关；
  - 历史记录回读（可写入 `multi_agent/data/market_context_history.jsonl`）。
- 借鉴 `format_daily_market_context_prompt_section` 统一渲染"大盘环境摘要"段落，并在其中加入不可信上下文护栏（`BEGIN_UNTRUSTED_MARKET_SUMMARY`）。
- 风险标签与仓位提示使用参考仓库中的正则/关键词匹配逻辑。

### 改动文件
| 文件 | 改动说明 |
|------|----------|
| 新增 `multi_agent/core/daily_market_context.py` | 核心上下文服务：数据模型、缓存、风险标签提取、prompt 渲染 |
| 修改 `multi_agent/predictor.py` | 在 `build_prediction_prompt` 中调用 `format_daily_market_context_prompt_section()` 注入上下文 |
| 修改 `multi_agent/core/market_rules.py` | 提供 `market_phase()`、`market_light()` 等轻量判断，供上下文服务消费 |
| 修改 `multi_agent/daily_tracker.py` | 在每日/每周入口先生成市场上下文，再传给预测器 |
| 新增 `multi_agent/scripts/save_market_context.py` | 独立脚本供 cron 调用，提前生成并缓存当日上下文 |
| 修改 `multi_agent/scripts/daily_evening_pipeline.sh` | 加入 `save_market_context.py` 调用步骤 |
| 新增 `tests/test_daily_market_context.py` | 单测：缓存命中、区域过滤、风险标签提取、prompt 渲染 |

### 风险与验证方式
- **风险 1**：加入大盘上下文后，会改变现有 LLM 输出分布（signal、confidence、max_position）。
  - 验证：在 `tests/` 中跑 20 只历史标的对比实验，统计加入前后 `bullish/neutral/bearish` 分布变化，确认没有系统性偏移。
- **风险 2**：缓存 key 过期或 query_id 作用域导致不同任务复用错误上下文。
  - 验证：单测覆盖 `current_query_id` 匹配、跨 query 隔离、`force_refresh` 强制刷新。
- **风险 3**：多数据源（A 股/期货/美股）上下文混用。
  - 验证：按 `category`（个股/ETF/期货/美股）传入 region，单测确保日股代码不注入 A 股上下文。

### 优先级与预估工作量
- **优先级**：P0（最基础，影响后续配置与情报池的 prompt 注入）。
- **预估工作量**：2～3 天（含单测与一次 end-to-end 跑通）。

---

## 方案二：配置注册表统一

### 当前缺失点
1. 配置项分散在多个 JSON 文件和环境变量，没有统一默认值、验证规则、文档说明。
2. `predictor_params.json` 的权重/阈值在代码中多处直接读取，没有 schema 约束；新增字段时无法自动校验。
3. 没有面向 UI/CLI 的 schema 接口，后续若做 Web 配置页面需要重复解析各种 JSON。
4. 没有字段分类（如 AI 模型、数据源、通知、系统、回测），配置管理混乱。
5. 没有配置变更审计与回滚能力（例如权重误改后难以追溯）。

### 借鉴内容
- 参考 `config_registry.py` 的 `_FIELD_DEFINITIONS` + `_CATEGORY_DEFINITIONS` 模式，为目标仓库建立 `multi_agent/core/config_registry.py`。
- 每个字段定义：
  - 字段名、标题、描述、分类、数据类型、UI 控件、是否敏感、是否必填、默认值、可选项、验证规则、示例。
- 提供 `build_schema_response()` 输出完整 schema，供 CLI/API/UI 使用。
- 兼容现有 JSON 配置：先读取旧 JSON 文件，再被环境变量覆盖，再用注册表默认值兜底。

### 改动文件
| 文件 | 改动说明 |
|------|----------|
| 新增 `multi_agent/core/config_registry.py` | 配置注册表核心：字段定义、分类、schema 生成、读取与默认值 |
| 修改 `multi_agent/predictor.py` | 将 `predictor_params.json` 的读取改为 `config_registry.get_config()` |
| 修改 `multi_agent/strategy/factor_scoring.py` | 读取权重/阈值时走配置注册表 |
| 修改 `multi_agent/strategy/portfolio_allocator.py` | 读取组合参数时走配置注册表 |
| 修改 `multi_agent/core/market_rules.py` | 读取市场规则时走配置注册表，保留本地 JSON 作为 fallback |
| 修改 `multi_agent/daily_tracker.py` | 入口统一从注册表获取配置 |
| 新增 `multi_agent/scripts/dump_config_schema.py` | CLI 导出 schema，用于人工审查与 UI 联调 |
| 新增 `tests/test_config_registry.py` | 单测：字段存在、默认值、验证规则、schema 完整性 |

### 风险与验证方式
- **风险 1**：注册表默认值与现有 JSON 不一致，导致打分/阈值行为变化。
  - 验证：先导出当前 JSON 中的值，原样写入 `_FIELD_DEFINITIONS` 的 `default_value`，确保新旧默认值一致；再跑 `run_pipeline.py --scan-only` 对比 Top10 输出。
- **风险 2**：所有读取路径没有全部切换，出现部分配置仍然读旧文件。
  - 验证：在 `tests/` 中 mock 配置注册表，检测 `predictor.py`、`factor_scoring.py`、`portfolio_allocator.py` 是否都访问 `config_registry`。
- **风险 3**：环境变量覆盖逻辑与 JSON 优先级错误。
  - 验证：单测覆盖 `env > json > default` 三级优先级，并覆盖布尔/数字/数组类型转换。

### 优先级与预估工作量
- **优先级**：P1（需要先完成方案一，因为市场上下文中的参数也属于配置）。
- **预估工作量**：3～4 天（涉及面广，需要逐个模块替换读取路径）。

---

## 方案三：资讯情报池 + 股票代码补全

### 当前缺失点
1. 资讯能力薄弱：只有 `news_engine.py` 通过 `akshare` 抓东方财富个股新闻，不支持 RSS/Atom/NewsNow 多源，不支持持久化、检索、按市场/标的筛选。
2. 没有情报池概念：无法把财联社、华尔街见闻、金十、SEC、HKEX 等源作为可配置输入注入 LLM prompt。
3. 没有股票代码补全：用户输入"平安银行"、"腾讯"、"AAPL"、"005930" 无法自动解析成统一代码；`watchlist.py` 硬编码，扩展性差。
4. 跨市场标的（港股、美股、日股、韩股）没有名称/代码映射，导致 `predictor.py` 对非 A 股标的数据收集容易出错。
5. 没有情报项的 retention/清理机制，长期运行会堆积数据。

### 借鉴内容
- **资讯情报池**：参考 `intelligence_service.py` + `intelligence_repo.py`。
  - 定义情报源 `IntelligenceSource`（名称、类型、URL、scope、market、启用状态）。
  - 定义情报项 `IntelligenceItem`（标题、摘要、URL、来源、发布时间、抓取时间、市场/标的）。
  - 内置模板：财联社、雪球、华尔街见闻、金十、格隆汇、SEC、HKEX、MarketWatch（按需启用）。
  - 支持 RSS/Atom 解析、NewsNow JSON 解析、URL DNS 安全校验（拒绝内网/私有地址）。
  - 支持 `fetch_enabled_sources()` 批量刷新、`refresh_auto_sources()` 自动刷新。
- **股票代码补全**：参考 `stock_index_loader.py` + `market_symbol_utils.py`。
  - 维护 `stocks.index.json`：每行 `[canonical_code, display_code, name_zh, pinyin, pinyin_initials, aliases, market, type, active, sort]`。
  - 提供 `get_index_stock_name(code)` 和 `resolve_index_stock_code(query)`。
  - 支持 A 股（000001.SZ/000001）、港股（00700.HK/HK00700/700）、美股（AAPL）、日股（7203.T/7203）、韩股（005930.KS/005930）。
  - 候选路径：远程缓存优先、本地 `apps/dsa-web/public` 次之、本地 `static/` 兜底。

### 改动文件
| 文件 | 改动说明 |
|------|----------|
| 新增 `multi_agent/core/intelligence_service.py` | 简化版情报服务：源管理、RSS/Atom/NewsNow 抓取、持久化、安全校验 |
| 新增 `multi_agent/core/intelligence_repo.py` | 情报源/情报项的 CRUD 与 retention（基于现有 `core/db.py` 或 SQLite） |
| 修改 `multi_agent/core/news_engine.py` | 保留东财个股新闻，同时调用 intelligence_service 查询/注入市场级情报 |
| 新增 `multi_agent/core/stock_index_loader.py` | 股票名称/代码映射加载、缓存、多候选路径 |
| 修改 `multi_agent/core/watchlist.py` | `add_stock` 支持中文名/模糊代码，自动解析 canonical code；提供 `get_index_stock_name` 给前端 |
| 修改 `multi_agent/run_pipeline.py` | 扫描输入支持名称/代码混排；在预测 prompt 中注入相关情报摘要 |
| 修改 `multi_agent/predictor.py` | 在 `build_prediction_prompt` 中追加与标的相关的最近情报 |
| 新增 `multi_agent/scripts/refresh_intelligence.py` | 手动/定时刷新情报源 |
| 新增 `multi_agent/data/stocks.index.json` | 初始股票索引（可先用 A 股+港股+美股，日/韩逐步补全） |
| 新增 `tests/test_intelligence_service.py` | 单测：RSS 解析、NewsNow 解析、私有地址拒绝、dedup、retention |
| 新增 `tests/test_stock_index_loader.py` | 单测：名称解析、代码解析、多候选路径、缓存、区域规则 |

### 风险与验证方式
- **风险 1**：新增情报源网络不稳定，可能导致 pipeline 失败。
  - 验证：情报服务设计为 fail-open，单个源失败不影响主流程；`run_pipeline.py` 中情报注入前检查是否为空。
- **风险 2**：股票索引歧义（如"700"可能是港股腾讯，也可能是 A 股*ST 股）。
  - 验证：单测覆盖歧义输入；`watchlist.py` 中默认不自动解析纯数字短码，要求用户输入完整代码或带市场前缀。
- **风险 3**：情报池数据库 schema 与现有 `warehouse.db`/`prediction_data.db` 混用导致冲突。
  - 验证：情报表单独使用 `multi_agent/data/intelligence.db`；schema 使用独立前缀 `intel_`。
- **风险 4**：LLM prompt 中加入新闻摘要后 token 变长，可能触发截断或成本上升。
  - 验证：限制每个标的最多 3 条、每条摘要 300 字；记录 prompt token 长度，超过阈值时降级为标题列表。

### 优先级与预估工作量
- **优先级**：P1（与方案二可并行，但依赖方案一的 prompt 注入位置）。
- **预估工作量**：4～5 天（情报源 + 索引 + 单测 + 与 predictor 联调）。

---

## 总体优先级建议

1. **P0 - 方案一：每日市场上下文注入**（2～3 天）
   - 最基础，直接提升 LLM prompt 一致性，也为方案二、三的字段/资讯提供统一注入点。
2. **P1 - 方案三：资讯情报池 + 股票代码补全**（4～5 天）
   - 能显著增强预测信息量，且与方案一一起提升 prompt 质量。
3. **P1 - 方案二：配置注册表统一**（3～4 天）
   - 可与方案三并行，但建议在方案一完成后启动，避免同时改动 prompt 和配置导致难以归因。

合计工作量：约 **9～12 天**（单人串行），若两人并行方案二与方案三可压缩至 **6～8 天**。

---

## 后续实施建议
1. 先确认目标仓库的代码规范与 AGENTS.md（参考仓库已提供），避免 PR 被驳回。
2. 每个方案单独一个 PR，分别跑 `run_pipeline.py --scan-only` 做前后对比。
3. 新增模块必须带单测；涉及 prompt 改动的 PR 需附前后输出对比。
4. 不直接复制参考仓库文件，而是根据目标仓库的 `multi_agent/` 结构重新组织，保持目录边界一致。
5. 配置注册表先以"读取现有 JSON + 环境变量"为主，不强行迁移所有历史配置，降低风险。
