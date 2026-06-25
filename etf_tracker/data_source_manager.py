#!/usr/bin/env python3
"""
统一数据源管理器 (DataSourceManager)
支持多数据源优先级配置和故障自动切换
集成: TickFlow, AkShare, 东方财富, Tushare, Baostock, Yahoo Finance
"""

import os
import json
import time
import urllib.request
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Union
from dataclasses import dataclass, field
from enum import Enum
import warnings
warnings.filterwarnings('ignore')


class DataSourceType(Enum):
    """数据源类型枚举"""
    TICKFLOW = "tickflow"
    AKSHARE = "akshare"
    EASTMONEY = "eastmoney"
    TUSHARE = "tushare"
    BAOSTOCK = "baostock"
    YFINANCE = "yfinance"


@dataclass
class DataSourceConfig:
    """数据源配置"""
    name: str
    source_type: DataSourceType
    enabled: bool = True
    priority: int = 1  # 数字越小优先级越高
    timeout: int = 15
    retry_count: int = 3
    retry_delay: float = 2.0
    # 特定数据源配置
    token: str = ""  # Tushare token
    api_key: str = ""  # 其他API key
    
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "source_type": self.source_type.value,
            "enabled": self.enabled,
            "priority": self.priority,
            "timeout": self.timeout,
            "retry_count": self.retry_count,
            "retry_delay": self.retry_delay,
            "token": self.token,
            "api_key": self.api_key
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "DataSourceConfig":
        return cls(
            name=data.get("name", "unknown"),
            source_type=DataSourceType(data.get("source_type", "akshare")),
            enabled=data.get("enabled", True),
            priority=data.get("priority", 1),
            timeout=data.get("timeout", 15),
            retry_count=data.get("retry_count", 3),
            retry_delay=data.get("retry_delay", 2.0),
            token=data.get("token", ""),
            api_key=data.get("api_key", "")
        )


class DataSourceStatus:
    """数据源状态跟踪"""
    def __init__(self):
        self.last_success_time: Optional[datetime] = None
        self.last_error_time: Optional[datetime] = None
        self.last_error_message: str = ""
        self.success_count: int = 0
        self.error_count: int = 0
        self.consecutive_errors: int = 0
        self.is_available: bool = True
        self.avg_response_time: float = 0.0
        self.total_calls: int = 0
    
    def record_success(self, response_time: float):
        self.last_success_time = datetime.now()
        self.success_count += 1
        self.consecutive_errors = 0
        self.is_available = True
        self.total_calls += 1
        # 更新平均响应时间
        self.avg_response_time = (self.avg_response_time * (self.total_calls - 1) + response_time) / self.total_calls
    
    def record_error(self, error_message: str):
        self.last_error_time = datetime.now()
        self.last_error_message = error_message
        self.error_count += 1
        self.consecutive_errors += 1
        self.total_calls += 1
        # 连续错误超过5次，标记为不可用
        if self.consecutive_errors >= 5:
            self.is_available = False
    
    def get_health_score(self) -> float:
        """获取健康评分 (0-100)"""
        if self.total_calls == 0:
            return 50.0
        
        success_rate = self.success_count / self.total_calls
        error_penalty = min(self.consecutive_errors * 10, 50)
        
        score = success_rate * 100 - error_penalty
        return max(0, min(100, score))
    
    def __repr__(self):
        return f"DataSourceStatus(available={self.is_available}, health={self.get_health_score():.1f}, calls={self.total_calls})"


# ============ 各数据源适配器 ============

class BaseDataAdapter:
    """数据源适配器基类"""
    
    def __init__(self, config: DataSourceConfig):
        self.config = config
        self.status = DataSourceStatus()
    
    def get_etf_kline(self, etf_code: str, days: int = 120) -> pd.DataFrame:
        """获取ETF K线数据"""
        raise NotImplementedError
    
    def get_stock_kline(self, stock_code: str, days: int = 120) -> pd.DataFrame:
        """获取个股K线数据"""
        raise NotImplementedError
    
    def get_fund_flow(self, code: str) -> Dict:
        """获取资金流向数据"""
        raise NotImplementedError
    
    def get_index_data(self, index_code: str, days: int = 120) -> pd.DataFrame:
        """获取指数数据"""
        raise NotImplementedError
    
    def get_realtime_quote(self, code: str) -> Dict:
        """获取实时行情"""
        raise NotImplementedError
    
    def _normalize_kline_df(self, df: pd.DataFrame) -> pd.DataFrame:
        """标准化K线数据格式"""
        required_columns = ['date', 'open', 'close', 'high', 'low', 'volume']
        
        # 确保所有列存在（使用字典方式避免属性名冲突）
        for col in required_columns:
            if col not in df.columns:
                df[col] = 0
        
        # 标准化列名 - 使用 .loc 避免列名与属性冲突
        extra_cols = [c for c in df.columns if c not in required_columns]
        cols = required_columns + extra_cols
        df = df[cols].copy()
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)
        
        return df


class TickFlowAdapter(BaseDataAdapter):
    """TickFlow 数据源适配器 - 支持 A股/ETF/港股/美股 实时+历史行情"""
    
    BASE_URL = "https://api.tickflow.org"
    
    def __init__(self, config: DataSourceConfig):
        super().__init__(config)
        self.api_key = config.api_key or config.token or os.environ.get("TICKFLOW_API_KEY", "")
        if not self.api_key:
            self.status.record_error("TickFlow api_key 未配置")
    
    def _headers(self) -> dict:
        return {
            "x-api-key": self.api_key,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }
    
    def _request(self, path: str, params: dict = None) -> dict:
        """发起 GET 请求并返回 JSON，带基础限流保护"""
        import urllib.parse, urllib.error
        params = params or {}
        url = f"{self.BASE_URL}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=self._headers())
        
        # 简单限流：连续请求间隔至少 0.2 秒
        now = time.time()
        elapsed = now - getattr(self, "_last_request_time", 0)
        if elapsed < 0.2:
            time.sleep(0.2 - elapsed)
        
        try:
            resp = urllib.request.urlopen(req, timeout=self.config.timeout)
            self._last_request_time = time.time()
            return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            self._last_request_time = time.time()
            # 遇到 429 时读取错误体，避免异常信息为空
            try:
                err_body = e.read().decode("utf-8", errors="ignore")
            except Exception:
                err_body = ""
            raise urllib.error.HTTPError(url, e.code, e.reason, e.headers, None) from e
        except Exception:
            self._last_request_time = time.time()
            raise
    
    def _to_tickflow_symbol(self, code: str) -> str:
        """将内部代码转换为 TickFlow 标的格式"""
        # 已经是 TickFlow/交易所格式
        if "." in code and any(code.endswith(s) for s in (".SH", ".SZ", ".BJ", ".HK")):
            return code
        # 美股代码（纯字母，如 QQQ, SPY, TSLA）
        if code.isalpha() or (len(code) <= 5 and code.replace("-", "").isalnum() and not code.isdigit()):
            return code
        # 港股代码 - TickFlow 使用 4 位数字 + .HK
        if code.endswith(".HK"):
            numeric = code[:-3]
            # 去除前导零，保留最多4位
            numeric = numeric.lstrip("0")
            if len(numeric) < 4:
                numeric = numeric.zfill(4)
            return numeric + ".HK"
        # A股/ETF
        if code.startswith("6") or code.startswith("5"):
            return f"{code}.SH"
        elif code.startswith("0") or code.startswith("3") or code.startswith("1"):
            return f"{code}.SZ"
        elif code.startswith("4") or code.startswith("8"):
            return f"{code}.BJ"
        return code
    
    def _klines_to_df(self, resp: dict) -> pd.DataFrame:
        """将 TickFlow 列式 K线响应转换为 DataFrame"""
        data = resp.get("data")
        if not data:
            raise ValueError("TickFlow 返回空数据")
        
        timestamps = data.get("timestamp", [])
        if not timestamps:
            raise ValueError("TickFlow K线数据无时间戳")
        
        records = []
        n = len(timestamps)
        for i in range(n):
            records.append({
                "date": datetime.fromtimestamp(timestamps[i] / 1000),
                "open": data.get("open", [0] * n)[i],
                "high": data.get("high", [0] * n)[i],
                "low": data.get("low", [0] * n)[i],
                "close": data.get("close", [0] * n)[i],
                "volume": int(data.get("volume", [0] * n)[i]) if data.get("volume") else 0,
                "amount": data.get("amount", [0] * n)[i] if data.get("amount") else 0,
            })
        
        df = pd.DataFrame(records)
        return self._normalize_kline_df(df)
    
    def get_etf_kline(self, etf_code: str, days: int = 120) -> pd.DataFrame:
        start_time = time.time()
        try:
            if not self.api_key:
                raise ValueError("TickFlow api_key 未配置")
            
            symbol = self._to_tickflow_symbol(etf_code)
            # 多获取一些数据，防止节假日缺失
            resp = self._request("/v1/klines", {
                "symbol": symbol,
                "period": "1d",
                "count": min(days * 2, 10000)
            })
            
            df = self._klines_to_df(resp)
            if len(df) > days:
                df = df.tail(days).reset_index(drop=True)
            
            self.status.record_success(time.time() - start_time)
            return df
            
        except Exception as e:
            self.status.record_error(str(e))
            raise
    
    def get_stock_kline(self, stock_code: str, days: int = 120) -> pd.DataFrame:
        # ETF 和个股接口一致
        return self.get_etf_kline(stock_code, days)
    
    def get_index_data(self, index_code: str, days: int = 120) -> pd.DataFrame:
        return self.get_etf_kline(index_code, days)
    
    def get_realtime_quote(self, code: str) -> Dict:
        start_time = time.time()
        try:
            if not self.api_key:
                raise ValueError("TickFlow api_key 未配置")
            
            symbol = self._to_tickflow_symbol(code)
            resp = self._request("/v1/quotes", {"symbols": symbol})
            quotes = resp.get("data", [])
            if not quotes:
                raise ValueError("TickFlow 返回空行情")
            
            q = quotes[0]
            ext = q.get("ext") or {}
            result = {
                "code": code,
                "symbol": q.get("symbol"),
                "price": float(q.get("last_price", 0)),
                "open": float(q.get("open", 0)),
                "high": float(q.get("high", 0)),
                "low": float(q.get("low", 0)),
                "prev_close": float(q.get("prev_close", 0)),
                "volume": int(q.get("volume", 0)),
                "amount": float(q.get("amount", 0)),
                "change_pct": float(ext.get("change_pct", 0)) if isinstance(ext, dict) else 0,
                "name": ext.get("name", "") if isinstance(ext, dict) else "",
                "source": "tickflow"
            }
            self.status.record_success(time.time() - start_time)
            return result
            
        except Exception as e:
            self.status.record_error(str(e))
            raise
    
    def get_fund_flow(self, code: str) -> Dict:
        # TickFlow 暂无资金流向接口，返回空
        return {"source": "tickflow", "note": "资金流向数据暂不支持"}


class AkShareAdapter(BaseDataAdapter):
    """AkShare 数据源适配器"""
    
    def __init__(self, config: DataSourceConfig):
        super().__init__(config)
        self._ak = None
        self._init_client()
    
    def _init_client(self):
        try:
            import akshare as ak
            self._ak = ak
        except ImportError:
            self.status.record_error("AkShare 未安装")
    
    def get_etf_kline(self, etf_code: str, days: int = 120) -> pd.DataFrame:
        start_time = time.time()
        try:
            if self._ak is None:
                raise ValueError("AkShare 客户端未初始化")
            
            start_date = (datetime.now() - timedelta(days=days * 2)).strftime('%Y%m%d')
            df = self._ak.fund_etf_hist_em(
                symbol=etf_code, 
                period="daily", 
                start_date=start_date, 
                adjust="qfq"
            )
            
            if len(df) == 0:
                raise ValueError("AkShare 返回空数据")
            
            df = df.rename(columns={
                '日期': 'date', '开盘': 'open', '收盘': 'close',
                '最高': 'high', '最低': 'low', '成交量': 'volume', '成交额': 'amount'
            })
            
            df = self._normalize_kline_df(df)
            
            if len(df) > days:
                df = df.tail(days).reset_index(drop=True)
            
            self.status.record_success(time.time() - start_time)
            return df
            
        except Exception as e:
            self.status.record_error(str(e))
            raise
    
    def get_stock_kline(self, stock_code: str, days: int = 120) -> pd.DataFrame:
        start_time = time.time()
        try:
            if self._ak is None:
                raise ValueError("AkShare 客户端未初始化")
            
            start_date = (datetime.now() - timedelta(days=days * 2)).strftime('%Y%m%d')
            df = self._ak.stock_zh_a_hist(
                symbol=stock_code, 
                period="daily", 
                start_date=start_date, 
                adjust="qfq"
            )
            
            if len(df) == 0:
                raise ValueError("AkShare 返回空数据")
            
            df = df.rename(columns={
                '日期': 'date', '开盘': 'open', '收盘': 'close',
                '最高': 'high', '最低': 'low', '成交量': 'volume', '成交额': 'amount'
            })
            
            df = self._normalize_kline_df(df)
            
            if len(df) > days:
                df = df.tail(days).reset_index(drop=True)
            
            self.status.record_success(time.time() - start_time)
            return df
            
        except Exception as e:
            self.status.record_error(str(e))
            raise
    
    def get_fund_flow(self, code: str) -> Dict:
        start_time = time.time()
        try:
            if self._ak is None:
                raise ValueError("AkShare 客户端未初始化")
            
            # 尝试获取ETF资金流向
            try:
                df = self._ak.fund_etf_hist_em(symbol=code, period="daily", 
                                               start_date=(datetime.now() - timedelta(days=5)).strftime('%Y%m%d'),
                                               adjust="")
                if len(df) > 0:
                    latest = df.iloc[-1]
                    result = {
                        "main_inflow": 0,  # AkShare ETF数据不直接提供主力流向
                        "volume": int(latest.get('成交量', 0)),
                        "amount": float(latest.get('成交额', 0)),
                        "source": "akshare"
                    }
                    self.status.record_success(time.time() - start_time)
                    return result
            except Exception:
                pass
            
            # 个股资金流向
            df = self._ak.stock_individual_fund_flow(symbol=code)
            if len(df) > 0:
                latest = df.iloc[-1]
                result = {
                    "main_inflow": float(latest.get('主力净流入', 0)),
                    "small_inflow": float(latest.get('小单净流入', 0)),
                    "medium_inflow": float(latest.get('中单净流入', 0)),
                    "large_inflow": float(latest.get('大单净流入', 0)),
                    "source": "akshare"
                }
                self.status.record_success(time.time() - start_time)
                return result
            
            raise ValueError("无法获取资金流向数据")
            
        except Exception as e:
            self.status.record_error(str(e))
            raise
    
    def get_index_data(self, index_code: str, days: int = 120) -> pd.DataFrame:
        start_time = time.time()
        try:
            if self._ak is None:
                raise ValueError("AkShare 客户端未初始化")
            
            # 指数代码映射
            index_map = {
                "000001": "sh000001",  # 上证指数
                "399001": "sz399001",  # 深证成指
                "399006": "sz399006",  # 创业板指
                "000300": "sh000300",  # 沪深300
                "000016": "sh000016",  # 上证50
                "000905": "sh000905",  # 中证500
            }
            
            symbol = index_map.get(index_code, f"sh{index_code}")
            start_date = (datetime.now() - timedelta(days=days * 2)).strftime('%Y%m%d')
            
            df = self._ak.index_zh_a_hist(symbol=symbol, period="daily", 
                                          start_date=start_date)
            
            if len(df) == 0:
                raise ValueError("AkShare 返回空数据")
            
            df = df.rename(columns={
                '日期': 'date', '开盘': 'open', '收盘': 'close',
                '最高': 'high', '最低': 'low', '成交量': 'volume', '成交额': 'amount'
            })
            
            df = self._normalize_kline_df(df)
            
            if len(df) > days:
                df = df.tail(days).reset_index(drop=True)
            
            self.status.record_success(time.time() - start_time)
            return df
            
        except Exception as e:
            self.status.record_error(str(e))
            raise
    
    def get_realtime_quote(self, code: str) -> Dict:
        start_time = time.time()
        try:
            if self._ak is None:
                raise ValueError("AkShare 客户端未初始化")
            
            # 尝试获取实时行情
            df = self._ak.fund_etf_hist_em(symbol=code, period="daily", 
                                           start_date=(datetime.now() - timedelta(days=1)).strftime('%Y%m%d'),
                                           adjust="")
            if len(df) > 0:
                latest = df.iloc[-1]
                result = {
                    "code": code,
                    "price": float(latest.get('收盘', 0)),
                    "open": float(latest.get('开盘', 0)),
                    "high": float(latest.get('最高', 0)),
                    "low": float(latest.get('最低', 0)),
                    "volume": int(latest.get('成交量', 0)),
                    "amount": float(latest.get('成交额', 0)),
                    "change_pct": float(latest.get('涨跌幅', 0)),
                    "source": "akshare"
                }
                self.status.record_success(time.time() - start_time)
                return result
            
            raise ValueError("无法获取实时行情")
            
        except Exception as e:
            self.status.record_error(str(e))
            raise


class EastMoneyAdapter(BaseDataAdapter):
    """东方财富 API 适配器"""
    
    def __init__(self, config: DataSourceConfig):
        super().__init__(config)
    
    def get_etf_kline(self, etf_code: str, days: int = 120) -> pd.DataFrame:
        start_time = time.time()
        try:
            secid = f"1.{etf_code}"
            url = f"https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57&klt=101&fqt=1&end=20500101&lmt={days}"
            
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': 'https://quote.eastmoney.com/'
            })
            resp = urllib.request.urlopen(req, timeout=self.config.timeout)
            data = json.loads(resp.read().decode('utf-8'))
            
            klines = data['data']['klines']
            records = []
            for line in klines:
                parts = line.split(',')
                records.append({
                    'date': parts[0],
                    'open': float(parts[1]),
                    'close': float(parts[2]),
                    'high': float(parts[3]),
                    'low': float(parts[4]),
                    'volume': int(parts[5]),
                    'amount': float(parts[6])
                })
            
            df = pd.DataFrame(records)
            df = self._normalize_kline_df(df)
            
            self.status.record_success(time.time() - start_time)
            return df
            
        except Exception as e:
            self.status.record_error(str(e))
            raise
    
    def get_stock_kline(self, stock_code: str, days: int = 120) -> pd.DataFrame:
        start_time = time.time()
        try:
            # 判断市场
            if stock_code.startswith('6') or stock_code.startswith('5'):
                secid = f"1.{stock_code}"
            else:
                secid = f"0.{stock_code}"
            
            url = f"https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57&klt=101&fqt=1&end=20500101&lmt={days}"
            
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': 'https://quote.eastmoney.com/'
            })
            resp = urllib.request.urlopen(req, timeout=self.config.timeout)
            data = json.loads(resp.read().decode('utf-8'))
            
            klines = data['data']['klines']
            records = []
            for line in klines:
                parts = line.split(',')
                records.append({
                    'date': parts[0],
                    'open': float(parts[1]),
                    'close': float(parts[2]),
                    'high': float(parts[3]),
                    'low': float(parts[4]),
                    'volume': int(parts[5]),
                    'amount': float(parts[6])
                })
            
            df = pd.DataFrame(records)
            df = self._normalize_kline_df(df)
            
            self.status.record_success(time.time() - start_time)
            return df
            
        except Exception as e:
            self.status.record_error(str(e))
            raise
    
    def get_fund_flow(self, code: str) -> Dict:
        # 东方财富不直接提供资金流向API，返回空
        return {"source": "eastmoney", "note": "资金流向数据暂不支持"}
    
    def get_index_data(self, index_code: str, days: int = 120) -> pd.DataFrame:
        return self.get_stock_kline(index_code, days)
    
    def get_realtime_quote(self, code: str) -> Dict:
        return {"source": "eastmoney", "note": "实时行情暂不支持"}


class TushareAdapter(BaseDataAdapter):
    """Tushare 数据源适配器 - 专业级金融数据接口"""
    
    def __init__(self, config: DataSourceConfig):
        super().__init__(config)
        self._pro = None
        self._init_client()
    
    def _init_client(self):
        try:
            import tushare as ts
            if self.config.token:
                ts.set_token(self.config.token)
                self._pro = ts.pro_api()
            else:
                self.status.record_error("Tushare token 未配置")
        except ImportError:
            self.status.record_error("Tushare 未安装")
        except Exception as e:
            self.status.record_error(f"Tushare 初始化失败: {e}")
    
    def get_etf_kline(self, etf_code: str, days: int = 120) -> pd.DataFrame:
        start_time = time.time()
        try:
            if self._pro is None:
                raise ValueError("Tushare 客户端未初始化")
            
            # Tushare ETF代码格式: 516150.SH
            if not etf_code.endswith('.SH') and not etf_code.endswith('.SZ'):
                if etf_code.startswith('5'):
                    ts_code = f"{etf_code}.SH"
                else:
                    ts_code = f"{etf_code}.SZ"
            else:
                ts_code = etf_code
            
            end_date = datetime.now().strftime('%Y%m%d')
            start_date = (datetime.now() - timedelta(days=days * 2)).strftime('%Y%m%d')
            
            # fund_daily 需要付费权限，降级使用 stock_daily 作为 ETF 日线来源
            # 大多数 ETF 在 Tushare 中可以通过 daily 接口获取
            try:
                df = self._pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
            except Exception:
                df = self._pro.fund_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
            
            if len(df) == 0:
                raise ValueError("Tushare 返回空数据")
            
            df = df.rename(columns={
                'trade_date': 'date', 'open': 'open', 'close': 'close',
                'high': 'high', 'low': 'low', 'vol': 'volume', 'amount': 'amount'
            })
            
            df['date'] = pd.to_datetime(df['date'])
            df = self._normalize_kline_df(df)
            
            if len(df) > days:
                df = df.tail(days).reset_index(drop=True)
            
            self.status.record_success(time.time() - start_time)
            return df
            
        except Exception as e:
            self.status.record_error(str(e))
            raise
    
    def get_stock_kline(self, stock_code: str, days: int = 120) -> pd.DataFrame:
        start_time = time.time()
        try:
            if self._pro is None:
                raise ValueError("Tushare 客户端未初始化")
            
            # Tushare 股票代码格式
            if not stock_code.endswith('.SH') and not stock_code.endswith('.SZ') and not stock_code.endswith('.BJ'):
                if stock_code.startswith('6'):
                    ts_code = f"{stock_code}.SH"
                elif stock_code.startswith('0') or stock_code.startswith('3'):
                    ts_code = f"{stock_code}.SZ"
                elif stock_code.startswith('4') or stock_code.startswith('8'):
                    ts_code = f"{stock_code}.BJ"
                else:
                    ts_code = f"{stock_code}.SZ"
            else:
                ts_code = stock_code
            
            end_date = datetime.now().strftime('%Y%m%d')
            start_date = (datetime.now() - timedelta(days=days * 2)).strftime('%Y%m%d')
            
            df = self._pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
            
            if len(df) == 0:
                raise ValueError("Tushare 返回空数据")
            
            df = df.rename(columns={
                'trade_date': 'date', 'open': 'open', 'close': 'close',
                'high': 'high', 'low': 'low', 'vol': 'volume', 'amount': 'amount'
            })
            
            df['date'] = pd.to_datetime(df['date'])
            df = self._normalize_kline_df(df)
            
            if len(df) > days:
                df = df.tail(days).reset_index(drop=True)
            
            self.status.record_success(time.time() - start_time)
            return df
            
        except Exception as e:
            self.status.record_error(str(e))
            raise
    
    def get_fund_flow(self, code: str) -> Dict:
        start_time = time.time()
        try:
            if self._pro is None:
                raise ValueError("Tushare 客户端未初始化")
            
            # 尝试获取资金流向
            try:
                # 使用 moneyflow 接口获取个股资金流向
                if not code.endswith('.SH') and not code.endswith('.SZ') and not code.endswith('.BJ'):
                    if code.startswith('6'):
                        ts_code = f"{code}.SH"
                    elif code.startswith('0') or code.startswith('3'):
                        ts_code = f"{code}.SZ"
                    else:
                        ts_code = f"{code}.SZ"
                else:
                    ts_code = code
                
                end_date = datetime.now().strftime('%Y%m%d')
                start_date = (datetime.now() - timedelta(days=5)).strftime('%Y%m%d')
                
                df = self._pro.moneyflow(ts_code=ts_code, start_date=start_date, end_date=end_date)
                
                if len(df) > 0:
                    latest = df.iloc[0]  # Tushare 返回最新数据在前
                    result = {
                        "main_inflow": float(latest.get('net_mf_amount', 0)),  # 主力净流入
                        "buy_sm_amount": float(latest.get('buy_sm_amount', 0)),  # 小单买入
                        "sell_sm_amount": float(latest.get('sell_sm_amount', 0)),  # 小单卖出
                        "buy_lg_amount": float(latest.get('buy_lg_amount', 0)),  # 大单买入
                        "sell_lg_amount": float(latest.get('sell_lg_amount', 0)),  # 大单卖出
                        "source": "tushare"
                    }
                    self.status.record_success(time.time() - start_time)
                    return result
            except Exception:
                pass
            
            raise ValueError("无法获取资金流向数据")
            
        except Exception as e:
            self.status.record_error(str(e))
            raise
    
    def get_index_data(self, index_code: str, days: int = 120) -> pd.DataFrame:
        start_time = time.time()
        try:
            if self._pro is None:
                raise ValueError("Tushare 客户端未初始化")
            
            # Tushare 指数代码格式
            if not index_code.endswith('.SH') and not index_code.endswith('.SZ'):
                if index_code.startswith('0'):
                    ts_code = f"{index_code}.SZ"
                else:
                    ts_code = f"{index_code}.SH"
            else:
                ts_code = index_code
            
            end_date = datetime.now().strftime('%Y%m%d')
            start_date = (datetime.now() - timedelta(days=days * 2)).strftime('%Y%m%d')
            
            df = self._pro.index_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
            
            if len(df) == 0:
                raise ValueError("Tushare 返回空数据")
            
            df = df.rename(columns={
                'trade_date': 'date', 'open': 'open', 'close': 'close',
                'high': 'high', 'low': 'low', 'vol': 'volume', 'amount': 'amount'
            })
            
            df['date'] = pd.to_datetime(df['date'])
            df = self._normalize_kline_df(df)
            
            if len(df) > days:
                df = df.tail(days).reset_index(drop=True)
            
            self.status.record_success(time.time() - start_time)
            return df
            
        except Exception as e:
            self.status.record_error(str(e))
            raise
    
    def get_realtime_quote(self, code: str) -> Dict:
        start_time = time.time()
        try:
            if self._pro is None:
                raise ValueError("Tushare 客户端未初始化")
            
            # 使用 daily 接口获取最新数据
            if not code.endswith('.SH') and not code.endswith('.SZ'):
                if code.startswith('6'):
                    ts_code = f"{code}.SH"
                else:
                    ts_code = f"{code}.SZ"
            else:
                ts_code = code
            
            end_date = datetime.now().strftime('%Y%m%d')
            start_date = (datetime.now() - timedelta(days=5)).strftime('%Y%m%d')
            
            df = self._pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
            
            if len(df) > 0:
                latest = df.iloc[0]
                result = {
                    "code": code,
                    "price": float(latest.get('close', 0)),
                    "open": float(latest.get('open', 0)),
                    "high": float(latest.get('high', 0)),
                    "low": float(latest.get('low', 0)),
                    "volume": int(latest.get('vol', 0)),
                    "amount": float(latest.get('amount', 0)),
                    "change_pct": float(latest.get('pct_chg', 0)),
                    "source": "tushare"
                }
                self.status.record_success(time.time() - start_time)
                return result
            
            raise ValueError("无法获取实时行情")
            
        except Exception as e:
            self.status.record_error(str(e))
            raise


class BaostockAdapter(BaseDataAdapter):
    """Baostock 数据源适配器 - 完全免费，无需注册"""
    
    def __init__(self, config: DataSourceConfig):
        super().__init__(config)
        self._bs = None
        self._init_client()
    
    def _init_client(self):
        try:
            import baostock as bs
            self._bs = bs
            # 登录
            lg = bs.login()
            if lg.error_code != '0':
                self.status.record_error(f"Baostock 登录失败: {lg.error_msg}")
            else:
                self.status.record_success(0.1)
        except ImportError:
            self.status.record_error("Baostock 未安装")
        except Exception as e:
            self.status.record_error(f"Baostock 初始化失败: {e}")
    
    def _format_code(self, code: str) -> str:
        """格式化代码为 Baostock 格式"""
        if code.startswith('sh.') or code.startswith('sz.'):
            return code
        
        if code.startswith('6'):
            return f"sh.{code}"
        elif code.startswith('0') or code.startswith('3'):
            return f"sz.{code}"
        elif code.startswith('5'):
            return f"sh.{code}"
        elif code.startswith('1'):
            return f"sz.{code}"
        else:
            return f"sh.{code}"
    
    def get_etf_kline(self, etf_code: str, days: int = 120) -> pd.DataFrame:
        start_time = time.time()
        try:
            if self._bs is None:
                raise ValueError("Baostock 客户端未初始化")
            
            code = self._format_code(etf_code)
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=days * 2)).strftime('%Y-%m-%d')
            
            rs = self._bs.query_history_k_data_plus(
                code,
                "date,open,high,low,close,volume,amount",
                start_date=start_date,
                end_date=end_date,
                frequency="d",
                adjustflag="3"  # 复权
            )
            
            data_list = []
            while rs.error_code == '0' and rs.next():
                row_data = rs.get_row_data()
                if not row_data or len(row_data) < 7:
                    continue
                data_list.append({
                    'date': row_data[0],
                    'open': float(row_data[1]) if row_data[1] else 0,
                    'high': float(row_data[2]) if row_data[2] else 0,
                    'low': float(row_data[3]) if row_data[3] else 0,
                    'close': float(row_data[4]) if row_data[4] else 0,
                    'volume': int(float(row_data[5])) if row_data[5] else 0,
                    'amount': float(row_data[6]) if row_data[6] else 0
                })
            
            if len(data_list) == 0:
                raise ValueError("Baostock 返回空数据")
            
            df = pd.DataFrame(data_list)
            df = self._normalize_kline_df(df)
            
            if len(df) > days:
                df = df.tail(days).reset_index(drop=True)
            
            self.status.record_success(time.time() - start_time)
            return df
            
        except Exception as e:
            self.status.record_error(str(e))
            raise
    
    def get_stock_kline(self, stock_code: str, days: int = 120) -> pd.DataFrame:
        return self.get_etf_kline(stock_code, days)
    
    def get_fund_flow(self, code: str) -> Dict:
        # Baostock 不直接提供资金流向
        return {"source": "baostock", "note": "资金流向数据暂不支持"}
    
    def get_index_data(self, index_code: str, days: int = 120) -> pd.DataFrame:
        return self.get_etf_kline(index_code, days)
    
    def get_realtime_quote(self, code: str) -> Dict:
        return {"source": "baostock", "note": "实时行情暂不支持"}


class YFinanceAdapter(BaseDataAdapter):
    """Yahoo Finance 适配器 - 国际备用数据源"""
    
    def __init__(self, config: DataSourceConfig):
        super().__init__(config)
        self._yf = None
        self._init_client()
    
    def _init_client(self):
        try:
            import yfinance as yf
            self._yf = yf
        except ImportError:
            self.status.record_error("yfinance 未安装")
    
    def _convert_code(self, code: str) -> str:
        """转换A股/港股/美股代码为 Yahoo Finance 格式"""
        # 港股代码 (如 0700.HK, 9988.HK)
        if code.endswith('.HK'):
            # yfinance 对港股代码要求：5位数字去掉首位0，保留为4位
            numeric = code[:-3]
            if len(numeric) > 4 and numeric.startswith('0'):
                numeric = numeric.lstrip('0')
                if len(numeric) < 4:
                    numeric = numeric.zfill(4)
            return numeric + '.HK'
        
        # 美股代码 (纯字母或常见美股代码，如 QQQ, SPY, AAPL, BRK-B)
        if code.isalpha() or (len(code) <= 5 and code.replace('-', '').isalnum() and not code.isdigit()):
            return code
        
        # ETF代码
        if code.startswith('5'):
            return f"{code}.SS"  # 上海
        elif code.startswith('1'):
            return f"{code}.SZ"  # 深圳
        # 股票代码
        elif code.startswith('6'):
            return f"{code}.SS"
        elif code.startswith('0') or code.startswith('3'):
            return f"{code}.SZ"
        # 已经是 Yahoo 格式
        elif '.' in code:
            return code
        else:
            return code
    
    def get_etf_kline(self, etf_code: str, days: int = 120) -> pd.DataFrame:
        start_time = time.time()
        try:
            if self._yf is None:
                raise ValueError("yfinance 客户端未初始化")
            
            symbol = self._convert_code(etf_code)
            ticker = self._yf.Ticker(symbol)
            
            # 获取历史数据
            period = f"{days * 2}d"  # 多获取一些数据
            df = ticker.history(period=period)
            
            if len(df) == 0:
                raise ValueError("yfinance 返回空数据")
            
            # 重置索引，将日期变为列
            df = df.reset_index()
            
            df = df.rename(columns={
                'Date': 'date', 'Open': 'open', 'Close': 'close',
                'High': 'high', 'Low': 'low', 'Volume': 'volume'
            })
            
            # 删除不需要的列
            if 'Dividends' in df.columns:
                df = df.drop(columns=['Dividends'])
            if 'Stock Splits' in df.columns:
                df = df.drop(columns=['Stock Splits'])
            
            df['date'] = pd.to_datetime(df['date']).dt.tz_localize(None)
            df = self._normalize_kline_df(df)
            
            if len(df) > days:
                df = df.tail(days).reset_index(drop=True)
            
            self.status.record_success(time.time() - start_time)
            return df
            
        except Exception as e:
            self.status.record_error(str(e))
            raise
    
    def get_stock_kline(self, stock_code: str, days: int = 120) -> pd.DataFrame:
        return self.get_etf_kline(stock_code, days)
    
    def get_fund_flow(self, code: str) -> Dict:
        # yfinance 不直接提供资金流向
        return {"source": "yfinance", "note": "资金流向数据暂不支持"}
    
    def get_index_data(self, index_code: str, days: int = 120) -> pd.DataFrame:
        return self.get_etf_kline(index_code, days)
    
    def get_realtime_quote(self, code: str) -> Dict:
        start_time = time.time()
        try:
            if self._yf is None:
                raise ValueError("yfinance 客户端未初始化")
            
            symbol = self._convert_code(code)
            ticker = self._yf.Ticker(symbol)
            info = ticker.info
            
            result = {
                "code": code,
                "price": info.get('regularMarketPrice', info.get('currentPrice', 0)),
                "open": info.get('regularMarketOpen', info.get('open', 0)),
                "high": info.get('regularMarketDayHigh', info.get('dayHigh', 0)),
                "low": info.get('regularMarketDayLow', info.get('dayLow', 0)),
                "volume": info.get('regularMarketVolume', info.get('volume', 0)),
                "change_pct": info.get('regularMarketChangePercent', 0),
                "source": "yfinance"
            }
            
            self.status.record_success(time.time() - start_time)
            return result
            
        except Exception as e:
            self.status.record_error(str(e))
            raise


# ============ 统一数据源管理器 ============

class DataSourceManager:
    """
    统一数据源管理器
    
    功能:
    - 多数据源优先级管理
    - 故障自动切换
    - 健康状态监控
    - 统一数据接口
    """
    
    def __init__(self, config_path: str = None, config_dict: dict = None):
        """
        初始化数据源管理器
        
        Args:
            config_path: 配置文件路径
            config_dict: 配置字典（优先于文件）
        """
        self.adapters: Dict[DataSourceType, BaseDataAdapter] = {}
        self.source_configs: List[DataSourceConfig] = []
        self._load_config(config_path, config_dict)
        self._init_adapters()
    
    def _load_config(self, config_path: str = None, config_dict: dict = None):
        """加载配置"""
        if config_dict and "data_sources" in config_dict:
            # 从字典加载
            for ds_config in config_dict["data_sources"]:
                self.source_configs.append(DataSourceConfig.from_dict(ds_config))
        elif config_path and os.path.exists(config_path):
            # 从文件加载
            with open(config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for ds_config in data.get("data_sources", []):
                    self.source_configs.append(DataSourceConfig.from_dict(ds_config))
        else:
            # 使用默认配置
            self.source_configs = self._get_default_configs()
        
        # 按优先级排序
        self.source_configs.sort(key=lambda x: x.priority)
    
    def _get_default_configs(self) -> List[DataSourceConfig]:
        """获取默认配置"""
        return [
            DataSourceConfig(name="tickflow", source_type=DataSourceType.TICKFLOW, priority=1, api_key=""),
            DataSourceConfig(name="akshare", source_type=DataSourceType.AKSHARE, priority=2),
            DataSourceConfig(name="eastmoney", source_type=DataSourceType.EASTMONEY, priority=3),
            DataSourceConfig(name="tushare", source_type=DataSourceType.TUSHARE, priority=4, token=""),
            DataSourceConfig(name="baostock", source_type=DataSourceType.BAOSTOCK, priority=5),
            DataSourceConfig(name="yfinance", source_type=DataSourceType.YFINANCE, priority=6),
        ]
    
    def _init_adapters(self):
        """初始化适配器"""
        adapter_map = {
            DataSourceType.TICKFLOW: TickFlowAdapter,
            DataSourceType.AKSHARE: AkShareAdapter,
            DataSourceType.EASTMONEY: EastMoneyAdapter,
            DataSourceType.TUSHARE: TushareAdapter,
            DataSourceType.BAOSTOCK: BaostockAdapter,
            DataSourceType.YFINANCE: YFinanceAdapter,
        }
        
        for config in self.source_configs:
            if not config.enabled:
                continue
            
            adapter_class = adapter_map.get(config.source_type)
            if adapter_class:
                try:
                    adapter = adapter_class(config)
                    self.adapters[config.source_type] = adapter
                    print(f"  [DataSourceManager] 初始化 {config.name} 成功 (优先级: {config.priority})")
                except Exception as e:
                    print(f"  [DataSourceManager] 初始化 {config.name} 失败: {e}")
    
    def _get_available_adapters(self) -> List[BaseDataAdapter]:
        """获取可用的适配器列表（按优先级排序）"""
        available = []
        for config in self.source_configs:
            if not config.enabled:
                continue
            adapter = self.adapters.get(config.source_type)
            if adapter and adapter.status.is_available:
                available.append(adapter)
        return available
    
    def _try_adapters(self, method_name: str, *args, **kwargs) -> any:
        """
        尝试多个适配器执行方法
        
        按优先级尝试，直到成功
        """
        adapters = self._get_available_adapters()
        
        if not adapters:
            raise RuntimeError("没有可用的数据源")
        
        last_error = None
        
        for adapter in adapters:
            config_name = "unknown"
            for cfg in self.source_configs:
                if cfg.source_type == next(
                    (k for k, v in self.adapters.items() if v == adapter), None
                ):
                    config_name = cfg.name
                    break
            
            for attempt in range(adapter.config.retry_count):
                try:
                    print(f"  [DataSourceManager] 尝试从 {config_name} 获取数据...")
                    method = getattr(adapter, method_name)
                    result = method(*args, **kwargs)
                    print(f"  [DataSourceManager] {config_name} 数据获取成功")
                    return result
                except Exception as e:
                    last_error = e
                    print(f"  [DataSourceManager] {config_name} 第 {attempt + 1} 次尝试失败: {e}")
                    if attempt < adapter.config.retry_count - 1:
                        time.sleep(adapter.config.retry_delay)
        
        raise RuntimeError(f"所有数据源均失败: {last_error}")
    
    # ============ 公共接口 ============
    
    def get_etf_kline(self, etf_code: str, days: int = 120) -> pd.DataFrame:
        """获取 ETF K线数据"""
        return self._try_adapters("get_etf_kline", etf_code, days)
    
    def get_stock_kline(self, stock_code: str, days: int = 120) -> pd.DataFrame:
        """获取个股 K线数据"""
        return self._try_adapters("get_stock_kline", stock_code, days)
    
    def get_fund_flow(self, code: str) -> Dict:
        """获取资金流向数据"""
        return self._try_adapters("get_fund_flow", code)
    
    def get_index_data(self, index_code: str, days: int = 120) -> pd.DataFrame:
        """获取指数数据"""
        return self._try_adapters("get_index_data", index_code, days)
    
    def get_realtime_quote(self, code: str) -> Dict:
        """获取实时行情"""
        return self._try_adapters("get_realtime_quote", code)
    
    def get_all_source_status(self) -> Dict:
        """获取所有数据源状态"""
        status = {}
        for source_type, adapter in self.adapters.items():
            status[source_type.value] = {
                "available": adapter.status.is_available,
                "health_score": adapter.status.get_health_score(),
                "success_count": adapter.status.success_count,
                "error_count": adapter.status.error_count,
                "consecutive_errors": adapter.status.consecutive_errors,
                "last_error": adapter.status.last_error_message,
                "avg_response_time": round(adapter.status.avg_response_time, 3)
            }
        return status
    
    def enable_source(self, source_type: str):
        """启用数据源"""
        for config in self.source_configs:
            if config.source_type.value == source_type:
                config.enabled = True
                if config.source_type not in self.adapters:
                    self._init_adapters()
                break
    
    def disable_source(self, source_type: str):
        """禁用数据源"""
        for config in self.source_configs:
            if config.source_type.value == source_type:
                config.enabled = False
                break
    
    def set_source_priority(self, source_type: str, priority: int):
        """设置数据源优先级"""
        for config in self.source_configs:
            if config.source_type.value == source_type:
                config.priority = priority
                break
        self.source_configs.sort(key=lambda x: x.priority)
    
    def update_token(self, source_type: str, token: str):
        """更新数据源 token"""
        for config in self.source_configs:
            if config.source_type.value == source_type:
                config.token = token
                # 重新初始化适配器
                if config.source_type in self.adapters:
                    del self.adapters[config.source_type]
                self._init_adapters()
                break
    
    def save_config(self, path: str):
        """保存配置到文件"""
        config_data = {
            "data_sources": [config.to_dict() for config in self.source_configs]
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, ensure_ascii=False, indent=2)
    
    def __repr__(self):
        sources = [f"{cfg.name}(p={cfg.priority})" for cfg in self.source_configs if cfg.enabled]
        return f"DataSourceManager(sources=[{', '.join(sources)}])"


# ============ 便捷函数 ============

def create_data_source_manager(config_path: str = None, config_dict: dict = None) -> DataSourceManager:
    """创建数据源管理器的便捷函数"""
    return DataSourceManager(config_path, config_dict)


# ============ 测试代码 ============

if __name__ == "__main__":
    print("=" * 60)
    print("统一数据源管理器测试")
    print("=" * 60)
    
    # 创建管理器
    dsm = DataSourceManager()
    print(f"\n管理器: {dsm}")
    
    # 查看状态
    print("\n数据源状态:")
    for name, status in dsm.get_all_source_status().items():
        print(f"  {name}: 健康度={status['health_score']:.1f}, 可用={status['available']}")
    
    # 测试获取ETF数据
    print("\n测试获取 516150 ETF 数据:")
    try:
        df = dsm.get_etf_kline("516150", days=30)
        print(f"  成功: {len(df)} 条数据")
        print(f"  最新: {df.iloc[-1]['date']} 收盘={df.iloc[-1]['close']}")
    except Exception as e:
        print(f"  失败: {e}")
    
    # 测试获取个股数据
    print("\n测试获取 300054 个股数据:")
    try:
        df = dsm.get_stock_kline("300054", days=30)
        print(f"  成功: {len(df)} 条数据")
        print(f"  最新: {df.iloc[-1]['date']} 收盘={df.iloc[-1]['close']}")
    except Exception as e:
        print(f"  失败: {e}")
    
    print("\n测试完成!")
