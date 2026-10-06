# Kronos 金融 K 线基础模型调研报告

## 项目概览

| 属性 | 内容 |
|------|------|
| **名称** | Kronos: A Foundation Model for the Language of Financial Markets |
| **作者** | shiyu-coder（清华大学团队） |
| **论文** | AAAI 2026 录用 |
| **Stars** | 32K+ |
| **核心创新** | 首个开源金融 K 线（OHLCV）基础模型 |

---

## 核心能力

### 1. 预训练规模
- **45 个全球交易所**
- **120 亿条 K 线数据**
- 股票、加密货币、期货、外汇
- 1min / 5min / 15min / 30min / 60min / 日线

### 2. 技术架构
```
K线数据 (OHLCV)
    ↓
Tokenizer（层级向量量化 Hierarchical VQ）
    ↓
离散 Token 序列
    ↓
自回归 Transformer（Decoder-only）
    ↓
预测未来 K 线 + 波动率 + 合成数据
```

### 3. 模型版本

| 模型 | 参数量 | 上下文 | 推荐场景 | 开源 |
|------|--------|--------|----------|------|
| **Kronos-mini** | 4.1M | 2048 | 入门体验、快速验证 | ✅ |
| **Kronos-small** | 24.7M | 512 | 日常研究、A股预测 | ✅ |
| **Kronos-base** | 102.3M | 512 | 高精度预测、微调 | ✅ |
| **Kronos-large** | 499.2M | 512 | 最高精度 | ❌ |

---

## 量化系统接入方案

### 方案 A：本地推理（推荐）

**适用**：有 GPU 或追求低延迟

```python
from model import Kronos, KronosTokenizer, KronosPredictor

# 加载模型
tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
model = Kronos.from_pretrained("NeoQuasar/Kronos-small")
predictor = KronosPredictor(model, tokenizer, max_context=512)

# A股数据预测
import akshare as ak
raw = ak.stock_zh_a_hist(symbol="000001", period="daily", start_date="20240101")
df = pd.DataFrame({
    'timestamps': pd.to_datetime(raw['日期']),
    'open': raw['开盘'],
    'high': raw['最高'],
    'low': raw['最低'],
    'close': raw['收盘'],
    'volume': raw['成交量'],
})

# 预测未来3日
pred_df = predictor.predict(
    df=df,
    x_timestamp=df['timestamps'],
    y_timestamp=future_timestamps,
    pred_len=3,
    T=1.0,
    top_p=0.9,
    sample_count=100  # 多路径采样
)
```

### 方案 B：API 服务化

**适用**：多用户、分布式部署

基于 **Kronos-Decision**（zhizhixia/Kronos-Decision）二次开发：
- 已内置 A 股数据管道（akshare → baostock → 缓存）
- 决策信号系统（BUY/HOLD/SELL）
- Web 界面

### 方案 C：云端调用

**适用**：无本地 GPU

参考 **gu-piao-yu-ce**（reikwei/gu-piao-yu-ce）：
- FastAPI 后端部署到 VPS
- GitHub Actions 每日同步 A 股数据
- Cloudflare Pages 前端

---

## 与现有系统集成

### 你的 daily_tracker_analytics 可接入点

| 模块 | 当前实现 | Kronos 增强 |
|------|---------|------------|
| **P0 证据链** | LLM 分析 | + Kronos K线预测作为客观证据 |
| **P2 情绪分析** | 涨停/跌停统计 | + Kronos 预测未来波动率 |
| **P3 题材雷达** | 行业聚类 | + Kronos 预测题材持续性 |
| **选股策略** | LLM 评分 | + Kronos 预测收益排序 |

### 具体接入代码

```python
# multi_agent/kronos_predictor.py
class KronosStockPredictor:
    """Kronos A股预测器"""
    
    def __init__(self, model_size="small"):
        self.model_size = model_size
        self.predictor = None
        self._load_model()
    
    def _load_model(self):
        """惰性加载模型"""
        if self.predictor is None:
            tokenizer = KronosTokenizer.from_pretrained(
                f"NeoQuasar/Kronos-Tokenizer-base"
            )
            model = Kronos.from_pretrained(
                f"NeoQuasar/Kronos-{self.model_size}"
            )
            self.predictor = KronosPredictor(model, tokenizer)
    
    def predict_stock(self, symbol: str, days: int = 3) -> dict:
        """预测个股未来走势"""
        # 1. 获取 A股数据（akshare）
        df = self._fetch_data(symbol)
        
        # 2. Kronos 多路径预测
        pred_df = self.predictor.predict(
            df=df,
            pred_len=days,
            sample_count=100,  # 100条路径
            T=1.0,
            top_p=0.9
        )
        
        # 3. 统计分析
        analysis = {
            'symbol': symbol,
            'pred_dates': pred_df.index.tolist(),
            'expected_return': pred_df['close'].mean() / df['close'].iloc[-1] - 1,
            'volatility': pred_df['close'].std() / df['close'].iloc[-1],
            'bull_prob': (pred_df['close'] > df['close'].iloc[-1]).mean(),
            'bear_prob': (pred_df['close'] < df['close'].iloc[-1]).mean(),
            'pred_paths': pred_df.values.tolist(),  # 用于可视化
        }
        
        return analysis
```

---

## 关键指标解读

### Kronos 输出信号

| 信号 | 计算方式 | 用途 |
|------|---------|------|
| **预期收益** | 100条路径均值 | 选股排序 |
| **波动率** | 路径标准差 | 风险评估 |
| **上涨概率** | 收盘价>当前价的比例 | 多空判断 |
| **置信区间** | 5%/95% 分位数 | 止损设置 |

### 与传统指标对比

| 维度 | 技术指标 (MACD/RSI) | Kronos |
|------|---------------------|--------|
| 输入 | 手工特征 | 原始 OHLCV |
| 输出 | 单点预测 | 概率分布 |
| 多路径 | ❌ | ✅ 100条采样 |
| 波动率预测 | ❌ | ✅ 内置 |
| 过拟合风险 | 高 | 低（基础模型） |

---

## 性能评测

### 零样本能力

- **RankIC +93%** vs 最强 TSFM（时序基础模型）
- 在 A 股、美股、加密货币均表现优异

### 计算需求

| 模型 | GPU 显存 | 推理速度（日线） |
|------|---------|----------------|
| mini | ~1GB | <1秒 |
| small | ~2GB | 1-2秒 |
| base | ~4GB | 3-5秒 |

---

## 风险与限制

1. **非投资建议**：Kronos 是研究工具，不构成买卖建议
2. **数据质量**：A股数据需清洗（停牌、除权等）
3. **模型偏差**：预训练以历史数据为主，极端行情可能失效
4. **计算成本**：base 模型需 GPU，CPU 较慢

---

## 推荐接入路径

### 短期（1-2周）
1. 安装 Kronos：`pip install -r requirements.txt`
2. 下载模型：`NeoQuasar/Kronos-small`
3. 测试 A股预测：贵州茅台、平安银行等

### 中期（1个月）
1. 集成到 `daily_report.py`：盘前报告增加 Kronos 预测
2. 与 P0 证据链结合：LLM 分析 + Kronos 客观预测
3. 回测验证：对比 Kronos 信号 vs 实际收益

### 长期（3个月）
1. 微调模型：用 A股特定数据 fine-tune
2. 实时推理：盘中 tick 级预测
3. 策略优化：Kronos + 情绪分析 + 题材雷达多因子融合

---

## 参考资源

- **原项目**: https://github.com/shiyu-coder/Kronos
- **中文指南**: https://github.com/Vincentwei1021/kronos-guide-cn
- **A股决策系统**: https://github.com/zhizhixia/Kronos-Decision
- **论文**: arXiv 2508.02739

---

**结论**: Kronos 是目前最强的开源金融 K 线模型，值得接入你的量化系统，作为 LLM 分析的客观补充。
