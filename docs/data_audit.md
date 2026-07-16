# 数据源审计报告 Data Sources Audit

> 目标：摸清已用/可用/未用的数据源，识别阻塞，按优先级规划集成

---

## 一、当前已接入的数据源

### 1.1 实时/日线价格（通过 data_loader_registry）

| 市场 | Loader | 状态 | 最新日期 |
|---|---|---|---|
| A 股个股 | mootdx → tencent → akshare → eastmoney → local | ✅ 可用 | 2026-07-13 |
| ETF | mootdx → tencent → akshare → eastmoney | ✅ 可用 | 2026-07-13 |
| 期货 | akshare_futures → sina | ✅ 可用 | 2026-07-13 |
| 美股 | yfinance | ❌ 限流/SSL错误 | 2026-07-10 |

### 1.2 基本面（通过 analysts）

| 来源 | 数据 | 状态 | 备注 |
|---|---|---|---|
| `fundamental_analyst` | 财报指标(ROE/PE/PB/营收增速) | ✅ | A股个股、ETF |
| `futures_fundamental_analyst` | 库存变化/基差/仓单/外盘 | ✅ | 49 品种 |
| `technical_analyst` | 技术指标(RSI/MACD/布林带/均线) | ✅ | 全品种 |
| `sentiment_analyst` | 新闻情绪 | ✅ | 基础版 |
| `debate_agent` | 多空辩论 | ✅ | LLM驱动 |

### 1.3 宏观（通过 macro_analyst）

| 数据 | API/函数 | 频率 | 状态 |
|---|---|---|---|
| LPR利率 | `ak.macro_china_lpr` | 月 | ✅ |
| Shibor | `ak.macro_china_shibor_all` | 日 | ✅ |
| 存款准备金率 | `ak.macro_china_reserve_requirement_ratio` | 不定 | ✅ |
| M2 | `ak.macro_china_money_supply` | 月 | ✅ |
| PMI | `ak.macro_china_pmi_yearly` | 月 | ✅ |
| CPI/PPI | `ak.macro_china_cpi_yearly` / `ppi_yearly` | 月 | ✅ |
| GDP | `ak.macro_china_gdp_yearly` | 季 | ✅ |
| 美国利率 | `ak.macro_bank_usa_interest_rate` | 月 | ✅ |
| 美国CPI | `ak.macro_usa_cpi_yoy` | 月 | ✅ |
| 美国失业率 | `ak.macro_usa_unemployment_rate` | 月 | ✅ |
| 美初请失业金 | `ak.macro_usa_initial_jobless` | 周 | ✅ |
| 中美利差曲线 | `ak.bond_zh_us_rate` | 日 | ✅ |
| 国债收益率 | `ak.bond_china_yield` | 日 | ✅ |
| 涨停股池/市场广度 | `ak.stock_zt_pool_em` | 日 | ✅ |

---

## 二、可接但未接的数据源

### 2.1 优先（P0 - 直接影响预测质量）

| 数据 | API | 说明 | 优先级理由 |
|---|---|---|---|
| **个股资金流** | `ak.stock_individual_fund_flow` | 主力/超大单/大单净流入 | 机构动向是股价领先指标 |
| **板块资金流** | `ak.stock_sector_fund_flow_rank` | 行业资金流入排名 | 板块轮动识别 |
| **龙虎榜** | `ak.stock_lhb_detail_em` | 机构买卖/游资跟踪 | 短线爆发力预测 |
| **涨停板块归属** | `ak.stock_zt_pool_em` | 涨停股+所属行业 | 情绪/广度指标已用，但行业维度未解构 |

### 2.2 重要（P1 - 提升宏观/rsk管理）

| 数据 | API | 说明 |
|---|---|---|
| **期权PCR** | `ak.option_sz50_hist` (需检查版本) | 50ETF 期权认沽认购比，市场情绪领先指标 |
| **北向资金** | `ak.stock_hsgt_north_net_flow_in_em` (有问题) | 外资净流入/出，A股风向标 |
| **融资融券余额** | `ak.stock_margin_detail` (需检查版本) | 杠杆资金动向 |
| **期货各月合约价差** | `futures_contract` + k线 | 期限结构（升贴水变化） |

### 2.3 增强（P2 - 丰富维度）

| 数据 | API | 说明 |
|---|---|---|
| **可转债数据** | `ak.bond_cov_bond_adj` | 正股/转债关联 |
| **IPO/解禁日历** | `ak.stock_ipo_info` | 供给压力 |
| **股东增减持** | 待查 akshare 版本 | 内部人交易信号 |
| **大股东质押** | `ak.stock_gpzy_pledge_ratio` | 质押平仓风险 |

---

## 三、阻塞的数据源及其原因

### ❌ 东财系 API（push2.eastmoney.com）

| 函数 | 阻塞原因 | 影响 |
|---|---|---|
| `stock_individual_fund_flow` | ProxyError（7890代理无法连接东财 push API） | 个股资金流不可用 |
| `stock_sector_fund_flow_rank` | 同上 | 板块资金流不可用 |
| `stock_hsgt_*` | 版本不支持 | 北向资金不可用 |

**根因**：WSL 环境中 `push2.eastmoney.com` 被香港/海外线路限制。curl 通过代理可访问，但 akshare 使用的 HTTP 库可能走不同线路。

**解决方案**：
- 方案A：在 WSL 中直连（已确认 `--noproxy "*"` 可以正常获取数据）→ 修改 akshare/requests 的 proxy 设置
- 方案B：通过 `export no_proxy="*push2.eastmoney.com*"` 排除东财域名

### ❌ yfinance 美股

| 原因 | 影响 |
|---|---|
| WSL 网络 → yahoo finance 限流 429 | 美股无法获取实时/日线 |

**解决方案**：
- 方案A：等待 WSL 重启后镜像网络配置生效（`/etc/wsl.conf` 已配置）
- 方案B：使用 `ak.stock_us_spot_em`（东财美股实时行情，但需先解决东财代理问题）

---

## 四、集成优先级路线图

```
Phase 1 (P0) — 本周
├── 个股资金流 → 接入 agentic_predictor 的 component_scores
├── 板块资金流 → 接入宏观分析器，增强板块轮动分析
├── 龙虎榜 → 独立 analyst 或并入 sentiment
└── 修复东财代理问题（noproxy 配置）

Phase 2 (P1) — 下周
├── 期权PCR → 接入宏观分析器
├── 北向资金 → 修复后接入
├── 融资融券 → 接入
└── 期货期限结构 → 接入 futures_fundamental

Phase 3 (P2) — 后续
├── 可转债、IPO解禁、股东增减持、质押
└── 新闻NLP增强（当前sentiment是简单规则，可升级为LLM标注）
```

---

## 五、当前系统数据流总览

```
                    ┌──────────────────────┐
                    │    data_loader_registry │ ◄── mootdx/tencent/akshare/yfinance
                    │    日线 OHLC           │
                    └──────┬───────────────┘
                           ▼
┌──────────┬──────────┬──────────┬──────────┐
│ technical │fundamental │ sentiment │   debate  │
│ analyst   │ analyst   │ analyst  │   agent   │
│ RSI/MACD  │ 财报/库存  │ 新闻情绪  │ LLM辩论   │
│ 布林带/均线│ 基差/仓单  │          │           │
└─────┬─────┴─────┬────┴────┬────┴─────┬────┘
      │           │         │          │
      ▼           ▼         ▼          ▼
┌──────────────────────────────────────────┐
│         agentic_predictor.py              │
│  weighted = Σ(score × weight)            │
│  + macro_override                         │
│  → signal by threshold                   │
└────────────────┬─────────────────────────┘
                 │
                 ▼
┌──────────────────────────────────────────┐
│         morning_validation                │
│         evening pipeline                  │
│         parameter_optimizer.py  ◄── 新增 │
│         (自动调参)                       │
└──────────────────────────────────────────┘
```

**缺失环节**（用 ◌ 标记）：
- ◌ 资金流分析器（P0）
- ◌ 板块轮动分析（P0）
- ◌ 龙虎榜分析（P0）
- ◌ 期权PCR指标（P1）
- ◌ 北向资金分析（P1）

---

## 六、下一步执行计划

### 立即（当前会话中）
1. 配置 `no_proxy` 排除东财域名，修复资金流数据
2. 验证资金流数据质量，确定特征格式
3. 创建 `fund_flow_analyst.py`（新analyst）

### 明天
4. 将资金流分数纳入 `component_scores`
5. 运行优化器检查新特征是否提升准确率
6. 龙虎榜 analyst
