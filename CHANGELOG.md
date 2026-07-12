# Changelog

> 从 v1.0.0 开始，系统进入稳定迭代阶段。
> 每次变更必须更新此文件，遵循 [Keep a Changelog](https://keepachangelog.com/) 规范。

## [1.0.0] — 2026-07-09

### 系统里程碑
全链路量化投资系统初始版本。从零构建，覆盖 A 股全量扫描 → LLM 评分 → 组合管理 → 每日复盘 → Pages 展示的完整闭环。

### Added
- **全A股扫描器** (`all_a_shares_scanner.py`) — Tushare 获取 3953 只活跃 A 股，qt.gtimg.cn 批量实时行情
- **六维评分引擎** (`strategy_scoring.py`) — 动量趋势 30 + 成交验证 20 + 估值安全 15 + 市值弹性 15 + 换手质量 10 + 价格舒适 10，含风控扣分（涨停/换手>25%/亏损）+ 板块分散逻辑
- **板块扫描器** (`sector_scanner.py`) — 131 标的分 82 板块即时打分+情绪周期分析
- **LLM 预测器** (`predictor.py`) — 数据采集 + Hermes 本地 LLM 预测
- **模拟盘引擎** (`simulator.py`) — 10 万初始资金，等权分配，止损 -8%/止盈 +20%
- **每日复盘引擎** (`daily_reflection.py`) — 六维分析（大盘/板块/组合/策略/外部/行动）+ 策略权重自动进化 + 知识库沉淀
- **雪球组合桥接** (`eastmoney_bridge.py`) — easytrader + cookies，支持 adjust_weight 一键调仓
- **数据层** (`data_layer.py`) — TickFlow 客户端，支持多数据源自动切换
- **大师思维工具箱** (`skills/master_mindset.py`) — 炒股养家情绪周期 + 利弗莫尔关键点 + 巴菲特护城河 + 格雷厄姆安全边际
- **Pages 前端页面** — `index.html` / `etfs.html` / `stocks.html` / `portfolio.html` / `prediction.html` / `futures.html` / `us_market.html`
- **一键全链路脚本** (`run_pipeline.py`)
- **cron 三时段** — 09:00 盘前扫描 / 11:30 午间快评 / 15:30 盘后复盘+组合更新+Pages
- **密钥安全隔离** — `.env` + `.gitignore` + `.env.example`

---

## [1.0.1] — 2026-07-12

### Changed
- **因子精选标准**：按来源差异化通过条件（auto_fe 更宽松），保证来源多样性配额（rule ≥ 8、auto_fe ≥ 5、composite ≥ 1），让自动特征工程因子有机会进入精选库。
- **自动特征工程**：
  - 阈值生成前对特征做 1%/99% 缩尾，避免极值导致信号全 1。
  - 评估时淘汰覆盖率极端（非零仓位 < 5% 或 > 95%）的候选因子。
- **LLM 因子可信度**：解释器输出 `filtered_factors`，下游（评分、日报、页面）优先使用可信因子。
- 替换传统 ML 模型（LightGBM/XGBoost/RF/ARIMA/LSTM）为纯 LLM 预测
- 数据源从 131 只自选扩展到全 A 股 3953 只
- 评分引擎从 v1 升级至 v2（6 维 + 风控 + 板块分散）
- 所有文件去品牌化（移除"郑希"命名，归入 `hermes-invest` skill）

### Removed
- 雪球模拟盘接入（A3C 反爬无法绕过）
- 旧版多 Agent 量化日报系统 cron 已暂停

---

## 版本规范

- **主版本号**：架构/数据源重大变更
- **次版本号**：新增功能模块
- **补丁号**：Bug 修复、参数调优、文档更新

每次提交时更新此文件，格式：
```markdown
## [版本号] — YYYY-MM-DD

### Added / Changed / Fixed / Removed
- 具体变更说明
```
