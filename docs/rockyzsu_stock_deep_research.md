# Rockyzsu/stock 深度调研报告

## 日期: 2026-10-15

---

## 一、项目概况

| 维度 | 详情 |
|------|------|
| **定位** | 个人量化交易学习项目（"30天掌握量化交易"） |
| **Stars** | 8,900 |
| **历史** | 2016年创建，2026-10-15 仍在更新（10年活跃） |
| **架构** | 传统量化（tushare/akshare + MySQL/MongoDB） |
| **许可证** | BSD 3-Clause |

---

## 二、核心模块源码分析

### 1. monitor/ 实时监控模块

#### 1.1 big_deal.py - 大单监控

```python
# 核心逻辑
def getBigDeal(self, code, vol):
    df = ts.get_today_ticks(code)  # 获取tick数据
    t = df[df['vol'] > vol]  # 筛选大于指定手数的大单
    if len(t) > 0:
        self.logger.info("Big volume {}".format(code))
```

**可借鉴点**:
- ✅ 大单监控思路（tick 数据筛选）
- ✅ 异常成交量检测

**局限性**:
- ❌ 使用旧版 tushare 接口（已失效）
- ❌ 全市场遍历效率低
- ❌ 无 LLM 分析，仅记录日志

**接入难度**: 中（需要更新数据源）

---

#### 1.2 ceiling_break.py - 涨停开板监控

```python
# 核心逻辑
def break_ceil(self, code):
    while 1:
        df = ts.get_realtime_quotes(code)
        v = float(df['b1_v'].values[0])  # 买一挂单量
        
        # 涨幅 < 9% 说明已开板
        if self.percent(float(df.iloc[0]['price']), float(df.iloc[0]['pre_close'])) < 9:
            self.notify(title)  # 推送通知
            break
        
        # 买一挂单 < 1000手，预警
        if v <= 1000:
            self.notify(title)  # "开板了"预警
```

**可借鉴点**:
- ✅ 涨停封单监控（买一挂单量）
- ✅ 开板预警逻辑（涨幅 < 9%）
- ✅ 多线程监控多只股票

**局限性**:
- ❌ 使用旧版 tushare 实时接口（已失效）
- ❌ 无限循环无退出机制
- ❌ 无断线重连

**接入难度**: 高（需要重写数据源）

---

#### 1.3 realtime_monitor_ts.py - 实时监控（复杂版）

```python
# 功能
- 可转债监控（转股溢价率）
- 正股监控
- 涨跌幅预警（8%/-5%）
```

**可借鉴点**:
- ✅ 可转债监控思路
- ✅ 多标的监控框架

**局限性**:
- ❌ 代码混乱，大量注释掉的旧代码
- ❌ 依赖已失效的 tushare pro 接口
- ❌ 配置文件硬编码

**接入难度**: 高（需要大量重构）

---

### 2. k-line/ K线形态识别

#### recognize_form.py - TALib 形态识别

```python
# 使用 TALib 识别 K线形态
df['tow_crows'] = talib.CDL2CROWS(
    df['open'].values, 
    df['high'].values, 
    df['low'].values, 
    df['close'].values
)

# 筛选出形态
pattern = df[(df['tow_crows'] == 100) | (df['tow_crows'] == -100)]
```

**可借鉴点**:
- ✅ TALib K线形态识别（两只乌鸦、长上影等）
- ✅ 可视化标注

**局限性**:
- ❌ 仅支持少量形态
- ❌ 无回测验证
- ❌ 无机器学习结合

**接入难度**: 低（TALib 现成可用）

---

### 3. machine_learning/ 预测模块

#### 贝叶斯预测涨跌.py

**可借鉴点**:
- ✅ 贝叶斯分类器用于涨跌预测
- ✅ 特征工程思路

**局限性**:
- ❌ 简单贝叶斯，准确率有限
- ❌ 无特征重要性分析
- ❌ 无深度学习

**接入难度**: 低（但效果有限）

---

### 4. datahub/ 数据采集

#### dfcf_hot_block.py - 东财热门板块

```python
# 抓取东财涨停板数据
url = "https://push2ex.eastmoney.com/getTopicZTPool"
params = {
    "ut": ut,  # 动态获取的 token
    "dpt": "wz.ztzt",
    "Pageindex": "0",
    "pagesize": "10000",
    "sort": "fbt:asc",
    "date": date,
}
```

**可借鉴点**:
- ✅ 东财涨停板 API 调用
- ✅ 动态获取 token（ut）

**局限性**:
- ❌ 反爬虫机制可能被更新
- ❌ 无数据缓存

**接入难度**: 中（需要维护）

---

### 5. analysis/ 分析模块

#### filterstock.py - 选股器

```python
# 功能
- 剔除 ST 股
- 筛选创新低股票
- 地域分类
```

**可借鉴点**:
- ✅ 基础选股逻辑
- ✅ 数据库存储

**局限性**:
- ❌ 使用旧版 tushare
- ❌ 无多因子选股

**接入难度**: 中

---

#### fupan.py - 复盘

```python
# 板块复盘
def hot_industry():
    # 按行业分组
    for name, group in basic_df.groupby('industry'):
        # 计算板块平均涨跌幅
        mean = sum(percent) / len(v)
```

**可借鉴点**:
- ✅ 板块热度分析
- ✅ 行业对比

**局限性**:
- ❌ 简单平均，无权重
- ❌ 无 MongoDB 也能用 SQLite

**接入难度**: 低

---

## 三、与现有系统对比

| 维度 | Rockyzsu/stock | daily_tracker_analytics |
|------|----------------|-------------------------|
| **架构** | 传统量化 | LLM-native |
| **数据源** | tushare (旧版) | HiThink/腾讯/akshare |
| **数据库** | MySQL/MongoDB | SQLite |
| **实时性** | 实时监控 | 盘后分析为主 |
| **AI 使用** | 无 | LLM 分析 |
| **代码质量** | 个人学习 | 生产级 |
| **维护性** | 10年沉淀但混乱 | 活跃迭代 |

---

## 四、可借鉴模块清单

| 模块 | 价值 | 接入难度 | 建议 |
|------|------|----------|------|
| **TALib K线形态** | ⭐⭐⭐ | 低 | ✅ 立即接入 |
| **东财涨停板 API** | ⭐⭐⭐ | 中 | ✅ 可参考 |
| **涨停封单监控** | ⭐⭐ | 高 | ⚠️ 需重写 |
| **大单监控** | ⭐⭐ | 中 | ⚠️ 需更新数据源 |
| **可转债监控** | ⭐⭐ | 高 | ⚠️ 您不交易可转债 |
| **贝叶斯预测** | ⭐ | 低 | ❌ 效果有限 |
| **选股器** | ⭐⭐ | 中 | ⚠️ 您已有 smart_stock_picker |

---

## 五、推荐接入方案

### 立即接入（本周）

#### 1. TALib K线形态识别

```python
# 在现有 technical_analyst.py 中添加
import talib

def recognize_patterns(df):
    """识别K线形态"""
    patterns = {
        '两只乌鸦': talib.CDL2CROWS(df['open'], df['high'], df['low'], df['close']),
        '三只乌鸦': talib.CDL3BLACKCROWS(df['open'], df['high'], df['low'], df['close']),
        '锤子线': talib.CDLHAMMER(df['open'], df['high'], df['low'], df['close']),
        '上吊线': talib.CDLHANGINGMAN(df['open'], df['high'], df['low'], df['close']),
    }
    return patterns
```

**价值**: 增强技术分析能力

---

### 中期接入（本月）

#### 2. 东财涨停板 API

```python
# 更新现有 sentiment_fetcher.py
def fetch_zt_pool(date: str) -> pd.DataFrame:
    """抓取涨停板数据"""
    url = "https://push2ex.eastmoney.com/getTopicZTPool"
    # ... 实现
```

**价值**: 替代现有 akshare 方案

---

### 不建议接入

- ❌ 实时监控模块（需要重写，且您以盘后分析为主）
- ❌ 贝叶斯预测（效果有限，您已有 Kronos）
- ❌ MongoDB 依赖（与您的 SQLite 路线冲突）

---

## 六、结论

**Rockyzsu/stock 是一个有价值的参考项目，但不适合直接接入。**

### 核心价值
1. ✅ TALib K线形态识别（立即可用）
2. ✅ 东财 API 调用方式（可参考）
3. ✅ 量化交易学习路径（适合初学者）

### 局限性
1. ❌ 代码质量参差（个人学习项目）
2. ❌ 依赖旧版 tushare（已失效）
3. ❌ 架构与您的系统不兼容
4. ❌ 无 LLM 集成

### 建议
- **学习思路**: 可以学习其监控、分析、回测的模块划分
- **代码复用**: 仅 TALib K线形态识别值得直接复用
- **架构参考**: 不建议照搬，您的 LLM-native 架构更先进

---

报告生成时间: 2026-10-15 23:20
