# ETF 回测数据源大全

## 一、国内数据源（A股/ETF）

### 1. 东方财富（当前使用）
- **API**: `https://push2his.eastmoney.com/api/qt/stock/kline/get`
- **优点**: 免费、稳定、数据完整、支持前复权
- **缺点**: 有频率限制，大量请求可能被封
- **适用**: 日线、周线、月线历史数据
- **代码示例**:
```python
secid = f"1.{etf_code}"  # 1=上海, 0=深圳
url = f"https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57&klt=101&fqt=1&end=20500101&lmt={days}"
```

### 2. 同花顺 iFinD（模型内置）
- **API**: `iFinDPy` Python库
- **优点**: 数据质量高、实时性强、支持分钟级数据
- **缺点**: 需要安装库、部分功能需付费
- **适用**: 专业量化分析、分钟级回测
- **代码**:
```python
from iFinDPy import *
# 使用模型内置数据库能力
```

### 3. AkShare（开源免费）
- **GitHub**: https://github.com/akfamily/akshare
- **优点**: 完全免费、开源、A股数据最全、更新及时
- **缺点**: 依赖较多、部分接口不稳定
- **适用**: 日线、分钟线、财务数据、宏观经济
- **安装**: `pip install akshare`
- **代码**:
```python
import akshare as ak
# ETF历史行情
df = ak.fund_etf_hist_em(symbol="516150", period="daily", start_date="20240101", adjust="qfq")
# 分钟级数据
df = ak.fund_etf_hist_min_em(symbol="516150", period="1", adjust="qfq")
# 实时行情
df = ak.fund_etf_spot_em()
```

### 4. Tushare（部分免费）
- **官网**: https://tushare.pro
- **优点**: 数据规范、接口统一、文档完善
- **缺点**: 高级数据需积分/付费
- **适用**: 日线、财务数据、行业数据
- **代码**:
```python
import tushare as ts
pro = ts.pro_api('your_token')
df = pro.fund_daily(ts_code='516150.SH', start_date='20240101')
```

### 5. Baostock（免费）
- **官网**: http://baostock.com
- **优点**: 完全免费、无需注册、支持复权
- **缺点**: 数据更新有延迟、接口较少
- **适用**: 日线历史数据
- **代码**:
```python
import baostock as bs
lg = bs.login()
rs = bs.query_history_k_data_plus("sh.516150", "date,code,open,high,low,close,volume", start_date='2024-01-01', frequency="d", adjustflag="3")
```

### 6. 新浪财经
- **API**: `https://quotes.sina.cn/cn/api/quotes.php`
- **优点**: 免费、实时性较好
- **缺点**: 接口不稳定、文档不完善
- **适用**: 实时行情、简单历史数据

---

## 二、国际数据源（美股/全球ETF）

### 1. Yahoo Finance（免费）
- **库**: `yfinance`
- **优点**: 全球数据、免费、支持美股/ETF/期货
- **缺点**: 数据偶有延迟、A股数据不全
- **适用**: 美股ETF (如 VNQ, VTI, GLD)
- **代码**:
```python
import yfinance as yf
ticker = yf.Ticker("516150.SS")  # A股需要加 .SS 或 .SZ
df = ticker.history(period="1y")
```

### 2. Alpha Vantage
- **官网**: https://www.alphavantage.co
- **优点**: 数据质量高、支持多种资产类型
- **缺点**: 免费版频率限制（5次/分钟）
- **适用**: 美股、外汇、加密货币
- **代码**:
```python
import requests
url = f"https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&symbol=VOO&apikey=YOUR_KEY"
```

### 3. Quandl / Nasdaq Data Link
- **官网**: https://data.nasdaq.com
- **优点**: 专业级数据、支持多种格式
- **缺点**: 大部分数据付费
- **适用**: 机构级量化研究

### 4. FRED（美联储经济数据）
- **官网**: https://fred.stlouisfed.org
- **优点**: 宏观经济数据权威、免费
- **缺点**: 只有宏观数据，无个股数据
- **适用**: 利率、通胀、就业等宏观指标

---

## 三、专业量化平台（数据+回测一体）

### 1. JoinQuant（聚宽）
- **官网**: https://www.joinquant.com
- **优点**: 数据完整、回测引擎强大、社区活跃
- **缺点**: 免费版有额度限制
- **适用**: 策略研究、实盘交易
- **代码**:
```python
# 在聚宽平台内使用
set_benchmark('516150.XSHG')
```

### 2. RiceQuant（米筐）
- **官网**: https://www.ricequant.com
- **优点**: 数据质量高、API友好
- **缺点**: 高级功能付费
- **适用**: 专业量化策略开发

### 3. BigQuant
- **官网**: https://bigquant.com
- **优点**: AI+量化、数据丰富
- **缺点**: 学习曲线较陡
- **适用**: AI驱动策略

---

## 四、加密货币数据源

### 1. Binance API
- **优点**: 数据最全、实时性高、免费
- **适用**: BTC、ETH等数字货币
- **代码**:
```python
import requests
url = "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=365"
```

### 2. CoinGecko
- **官网**: https://www.coingecko.com
- **优点**: 免费、数据丰富
- **缺点**: 频率限制

---

## 五、推荐组合策略

### 对于 A股 ETF（如 516150）
```
优先级:
1. 东方财富 API（免费、稳定）
2. AkShare（免费、功能丰富）
3. Tushare（免费额度够用）
4. Baostock（完全免费）
```

### 对于美股 ETF
```
优先级:
1. Yahoo Finance (yfinance)
2. Alpha Vantage
3. Quandl
```

### 对于专业量化研究
```
1. 聚宽/米筐（数据+回测一体）
2. iFinD（高质量数据）
3. Wind（机构级，付费）
```

---

## 六、数据质量对比

| 数据源 | 实时性 | 稳定性 | 免费额度 | A股支持 | 分钟级 |
|--------|--------|--------|----------|---------|--------|
| 东方财富 | ⭐⭐⭐ | ⭐⭐⭐ | 无限制 | ✅ | ❌ |
| AkShare | ⭐⭐⭐ | ⭐⭐ | 无限制 | ✅ | ✅ |
| Tushare | ⭐⭐⭐ | ⭐⭐⭐ | 有限 | ✅ | ✅ |
| Baostock | ⭐⭐ | ⭐⭐ | 无限制 | ✅ | ❌ |
| Yahoo Finance | ⭐⭐ | ⭐⭐ | 无限制 | ⚠️ | ✅ |
| iFinD | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 需安装 | ✅ | ✅ |

---

## 七、实际代码集成建议

```python
class MultiSourceDataFetcher:
    """多数据源获取器"""
    
    def __init__(self):
        self.sources = [
            self._fetch_eastmoney,      # 东方财富
            self._fetch_akshare,        # AkShare
            self._fetch_tushare,        # Tushare
            self._fetch_yfinance,       # Yahoo Finance
        ]
    
    def fetch(self, etf_code, days=120):
        """按优先级尝试所有数据源"""
        for source in self.sources:
            try:
                df = source(etf_code, days)
                if df is not None and len(df) > 0:
                    return df
            except Exception as e:
                print(f"{source.__name__} 失败: {e}")
                continue
        raise Exception("所有数据源均失败")
```
