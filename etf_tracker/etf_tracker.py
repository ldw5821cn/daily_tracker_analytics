#!/usr/bin/env python3
"""
ETF 趋势跟踪与投资规划报告系统
支持配置化: 可自定义跟踪的 ETF/股票、回测周期、数据源
"""

import os
import sys
import json
import urllib.request
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import warnings
warnings.filterwarnings('ignore')

# 尝试使用 yfinance 作为备用
try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False

# 尝试使用 akshare 作为备用(A股数据最丰富)
try:
    import akshare as ak
    AKSHARE_AVAILABLE = True
except ImportError:
    AKSHARE_AVAILABLE = False

# 导入统一数据源管理器
try:
    from data_source_manager import DataSourceManager, DataSourceType
    DSM_AVAILABLE = True
except ImportError:
    DSM_AVAILABLE = False

# 导入个股分析模块
try:
    from stock_analyzer import StockAnalyzer, StockVisualizer
    STOCK_ANALYZER_AVAILABLE = True
except ImportError:
    STOCK_ANALYZER_AVAILABLE = False

# 导入高级预测模块
try:
    from advanced_predictor import TrendPredictor, AlertSystem, AdvancedVisualizer
    PREDICTOR_AVAILABLE = True
except ImportError:
    PREDICTOR_AVAILABLE = False

# 导入多模型预测模块
try:
    from multi_model_predictor import MultiModelPredictor, AdvancedPredictionReport
    MULTI_MODEL_AVAILABLE = True
except ImportError:
    MULTI_MODEL_AVAILABLE = False

# 导入国际市场模块
try:
    from international_market import InternationalMarketFetcher
    INTERNATIONAL_MARKET_AVAILABLE = True
except ImportError:
    INTERNATIONAL_MARKET_AVAILABLE = False
    class InternationalMarketFetcher:
        def fetch_all(self, days=30):
            return {}
        def generate_market_summary(self):
            return ""
        def generate_market_analysis(self):
            return ""

# 导入 LLM 决策报告生成器
try:
    from llm_report_generator import LLMReportGenerator
    LLM_REPORT_AVAILABLE = True
except ImportError:
    LLM_REPORT_AVAILABLE = False


# 占位类，避免导入失败时引用错误
if not MULTI_MODEL_AVAILABLE:
    class MultiModelPredictor:
        def train_all_models(self, *args, **kwargs):
            return {}
        def ensemble_predict(self, *args, **kwargs):
            return {'ensemble': {}, 'individual_predictions': {}}
if not PREDICTOR_AVAILABLE:
    class TrendPredictor:
        def predict_trend(self, *args, **kwargs):
            return {}
        def optimize_strategy(self, *args, **kwargs):
            return {}
        def add_technical_indicators(self, *args, **kwargs):
            return args[0] if args else None


# ============ 多 ETF 分析引擎 ============

class MultiETFAnalyzer:
    """多 ETF/板块批量分析引擎"""
    
    def __init__(self, config=None):
        self.config = config or Config()
        self.fetcher = ETFDataFetcher(config)
        self.results = []
    
    def analyze_etf(self, etf_config: Dict) -> Dict:
        """分析单个 ETF"""
        code = etf_config['code']
        name = etf_config['name']
        print(f"\n{'='*60}")
        print(f"分析 ETF: {name} ({code})")
        print(f"{'='*60}")
        
        try:
            df = self.fetcher.get_kline_data(code, days=400)
            if df is None or len(df) < 30:
                return {'code': code, 'name': name, 'error': '数据不足'}
            
            engine = BacktestEngine(df)
            backtest_results = engine.run_all_backtests(self.config.backtest_periods)
            
            signal_gen = SignalGenerator(backtest_results, df)
            signals = signal_gen.generate_signals()
            position = signal_gen.calculate_position_sizing()
            
            # 多模型预测
            multi_model = None
            if MULTI_MODEL_AVAILABLE:
                try:
                    predictor = MultiModelPredictor(etf_code)
                    # 先尝试加载隔夜 LSTM 缓存，如果有效则跳过训练
                    has_lstm_cache = predictor.load_cached_lstm(df)
                    training_results = predictor.train_all_models(
                        df, target_col='target_1d', skip_lstm=has_lstm_cache
                    )
                    ensemble_result = predictor.ensemble_predict(df, days=5)
                    multi_model = {
                        'predictions': ensemble_result['individual_predictions'],
                        'ensemble': ensemble_result['ensemble']
                    }
                except Exception as e:
                    print(f"  多模型预测失败: {e}")
            
            # 趋势预测
            trend = None
            if PREDICTOR_AVAILABLE:
                try:
                    predictor = TrendPredictor()
                    trend = predictor.predict_trend(df, days=5)
                except Exception as e:
                    print(f"  趋势预测失败: {e}")
            
            return {
                'code': code,
                'name': name,
                'theme': etf_config.get('theme', '-'),
                'sector': etf_config.get('sector', '-'),
                'current_price': df['close'].iloc[-1],
                'backtest': backtest_results,
                'signals': signals,
                'position': position,
                'multi_model': multi_model,
                'trend': trend,
                'df': df
            }
        except Exception as e:
            print(f"  ETF {name} 分析失败: {e}")
            return {'code': code, 'name': name, 'error': str(e)}
    
    def analyze_all_etfs(self) -> List[Dict]:
        """分析所有配置的 ETF"""
        etfs = self.config.etfs
        if not etfs:
            print("⚠️ 未配置 ETF，跳过多板块分析")
            return []
        
        print(f"\n开始分析 {len(etfs)} 个板块 ETF...")
        results = []
        for etf in etfs:
            result = self.analyze_etf(etf)
            results.append(result)
        self.results = results
        return results
    
    def analyze_top_stocks(self, etf_config: Dict, max_stocks: int = 10, 
                            enable_multi_model: bool = False, 
                            timeout_per_stock: float = 60.0) -> List[Dict]:
        """分析某 ETF 的 top 个股（默认只做快速技术指标分析，避免完整多模型训练超时）"""
        top_stocks = etf_config.get('top_stocks', [])[:max_stocks]
        if not top_stocks:
            return []
        
        print(f"\n分析 {etf_config['name']} 的 Top {len(top_stocks)} 个股...")
        results = []
        
        for stock in top_stocks:
            try:
                import time
                t0 = time.time()
                df_stock = self.fetcher.get_stock_kline_data(stock['code'], days=400)
                if df_stock is None or len(df_stock) < 40:
                    continue
                
                latest_price = float(df_stock.iloc[-1]['close'])
                first_price_30 = float(df_stock.iloc[-30]['close']) if len(df_stock) >= 30 else float(df_stock.iloc[0]['close'])
                return_30d = (latest_price - first_price_30) / first_price_30 * 100
                
                # 快速技术指标分析
                engine = BacktestEngine(df_stock)
                backtest = engine.run_all_backtests([30, 60, 90])
                signal_gen = SignalGenerator(backtest, df_stock)
                signals = signal_gen.generate_signals()
                position = signal_gen.calculate_position_sizing()
                
                # 多模型预测：仅在显式开启时进行，且只尝试轻量模型/缓存
                multi_model = None
                if enable_multi_model and MULTI_MODEL_AVAILABLE:
                    try:
                        predictor = MultiModelPredictor(stock['code'])
                        # 只训练轻量模型（LightGBM/XGBoost/RF）+ ARIMA，不训练 LSTM
                        training_results = predictor.train_all_models(
                            df_stock, target_col='target_1d', skip_lstm=True
                        )
                        ensemble_result = predictor.ensemble_predict(df_stock, days=1)
                        multi_model = ensemble_result['ensemble']
                    except Exception as e:
                        pass
                
                elapsed = time.time() - t0
                results.append({
                    'code': stock['code'],
                    'name': stock['name'],
                    'reason': stock.get('reason', '-'),
                    'latest_price': round(latest_price, 2),
                    'return_30d': round(return_30d, 2),
                    'trend': '上涨' if return_30d > 0 else '下跌',
                    'score': signals.get('score', 0),
                    'signal': signals.get('overall_signal', 'neutral'),
                    'position_size': position.get('position_size', 0) if isinstance(position, dict) else 0,
                    'multi_model': multi_model
                })
                print(f"  {stock['name']} ({stock['code']}): 30天{return_30d:+.2f}% 评分{signals.get('score', 0):.1f} ({elapsed:.1f}s)")
                
                # 超时保护：如果已耗时超过单只超时限制，停止后续个股
                if elapsed > timeout_per_stock:
                    print(f"  ⚠️ 单只个股分析耗时过长 ({elapsed:.1f}s)，跳过剩余成分股")
                    break
                    
            except Exception as e:
                print(f"  {stock['name']} 分析失败: {e}")
        
        return results
    
    def get_sector_ranking(self) -> pd.DataFrame:
        """生成板块排名"""
        if not self.results:
            return pd.DataFrame()
        
        SIGNAL_MAP = {
            'bullish': '看多',
            'bearish': '看空',
            'neutral': '中性',
        }

        rows = []
        for r in self.results:
            if 'error' in r:
                continue
            backtest_60 = next((b for b in r['backtest'] if b.get('period_days') == 60), {})
            raw_signal = r['signals'].get('overall', '-')
            rows.append({
                '板块': r['name'],
                '主题': r['theme'],
                '最新价': r['current_price'],
                '60天收益': backtest_60.get('total_return', 0),
                '60天夏普': backtest_60.get('sharpe_ratio', 0),
                '综合评分': r['signals'].get('score', 0),
                '信号': SIGNAL_MAP.get(raw_signal, raw_signal),
                '1日预测': r.get('multi_model', {}).get('ensemble', {}).get('return_1d', 0) * 100 if r.get('multi_model') else 0,
                '预测置信度': r.get('multi_model', {}).get('ensemble', {}).get('confidence', 0) * 100 if r.get('multi_model') else 0
            })
        
        df = pd.DataFrame(rows)
        if not df.empty:
            df = df.sort_values('综合评分', ascending=False)
        return df


# ============ 配置加载 ============

class Config:
    """配置管理器"""
    
    DEFAULT_CONFIG = {
        "default_etf": "516150",
        "report_title": "多板块 ETF 智能投资分析报告",
        "tracking_industries": ["银行", "消费", "CPU", "人工智能", "AI", "晶圆制造", "高股息", "稀土永磁", "半导体", "新能源"],
        "backtest_periods": [30, 60, 90, 120, 200, 280, 365],
        "data_source": "akshare",
        "wechat_push": {
            "enabled": True,
            "channel": "hermes"
        },
        "features": {
            "minute_data": True,
            "fund_flow": True,
            "sector_comparison": True,
            "margin_trading": True,
            "individual_stocks": True,
            "multi_etf": True,
            "cross_sector_comparison": True
        },
        "etfs": [
            {
                "code": "516150",
                "name": "稀土ETF嘉实",
                "theme": "稀土永磁",
                "sector": "稀土永磁",
                "leading_stocks": ["北方稀土", "中国稀土", "盛和资源", "广晟有色", "厦门钨业"],
                "top_stocks": [
                    {"code": "600111", "name": "北方稀土", "reason": "全球轻稀土龙头"},
                    {"code": "000831", "name": "中国稀土", "reason": "中重稀土整合平台"},
                    {"code": "600392", "name": "盛和资源", "reason": "稀土全产业链布局"},
                    {"code": "600259", "name": "广晟有色", "reason": "广东稀土资源整合"},
                    {"code": "600549", "name": "厦门钨业", "reason": "稀土+钨双主业"},
                    {"code": "600010", "name": "包钢股份", "reason": "稀土精矿资源"},
                    {"code": "600366", "name": "宁波韵升", "reason": "稀土永磁材料"},
                    {"code": "000970", "name": "中科三环", "reason": "烧结钕铁硼龙头"},
                    {"code": "600580", "name": "卧龙电驱", "reason": "稀土永磁电机"},
                    {"code": "300748", "name": "金力永磁", "reason": "高性能钕铁硼"}
                ]
            }
        ],
        "individual_stocks": {
            "enabled": True,
            "stocks": [
                {"code": "300054", "name": "鼎龙股份", "sector": "半导体材料", "reason": "CMP抛光垫龙头"},
                {"code": "688019", "name": "安集科技", "sector": "半导体材料", "reason": "CMP抛光液龙头"},
                {"code": "600206", "name": "有研新材", "sector": "稀土/半导体材料", "reason": "稀土磁材+靶材"},
                {"code": "688041", "name": "海光信息", "sector": "CPU/GPU", "reason": "国产CPU+DCU加速卡"},
                {"code": "688256", "name": "寒武纪", "sector": "AI芯片", "reason": "AI芯片独角兽"},
                {"code": "688981", "name": "中芯国际", "sector": "晶圆代工", "reason": "晶圆代工龙头"},
                {"code": "688012", "name": "中微公司", "sector": "半导体设备", "reason": "刻蚀设备龙头"},
                {"code": "300782", "name": "卓胜微", "sector": "射频芯片", "reason": "射频前端龙头"},
                {"code": "688047", "name": "龙芯中科", "sector": "CPU", "reason": "国产CPU自主指令集"},
                {"code": "300474", "name": "景嘉微", "sector": "GPU", "reason": "国产GPU龙头"},
                {"code": "688072", "name": "拓荆科技", "sector": "半导体设备", "reason": "薄膜沉积设备龙头"},
                {"code": "688082", "name": "盛美上海", "sector": "半导体设备", "reason": "清洗设备龙头"}
            ]
        },
        "sector_comparison": {
            "enabled": True,
            "sector_name": "多板块对比",
            "leading_stocks": ["北方稀土", "招商银行", "贵州茅台", "中芯国际", "寒武纪"]
        }
    }
    
    def __init__(self, config_path: str = None):
        self.config_path = config_path or os.path.join(os.path.dirname(__file__), "config.json")
        self.config = self._load_config()
        
        # 提取常用配置
        self.default_etf = self.config.get("default_etf", "516150")
        self.etf_code = self.config.get("etf_code", self.default_etf)
        self.etf_name = self.config.get("etf_name", "稀土ETF嘉实")
        self.etfs = self.config.get("etfs", [])
        self.report_title = self.config.get("report_title", "多板块 ETF 智能投资分析报告")
        self.tracking_industries = self.config.get("tracking_industries", [])
        self.backtest_periods = self.config.get("backtest_periods", [30, 60, 90, 120, 200, 280, 365])
        self.data_source = self.config.get("data_source", "akshare")
        self.features = self.config.get("features", {})
        self.sector_config = self.config.get("sector_comparison", {})
    
    def _load_config(self) -> dict:
        """加载配置文件"""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"读取配置失败: {e}，使用默认配置")
        return self.DEFAULT_CONFIG.copy()
    
    def save_config(self):
        """保存配置到文件"""
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(self.config, f, ensure_ascii=False, indent=2)
    
    def update(self, key: str, value):
        """更新配置项"""
        self.config[key] = value
        # 更新实例属性
        if hasattr(self, key):
            setattr(self, key, value)


# ============ 数据获取模块 ============

class ETFDataFetcher:
    """ETF 历史数据获取器 - 支持多数据源"""
    
    def __init__(self, config: Config = None):
        self.config = config or Config()
        self._dsm = None
        self._cache = None
        self._init_dsm()
        self._init_cache()
    
    def _init_dsm(self):
        """初始化统一数据源管理器"""
        if DSM_AVAILABLE:
            try:
                self._dsm = DataSourceManager(config_dict=self.config.config)
                print(f"  [ETFDataFetcher] 统一数据源管理器初始化成功: {self._dsm}")
            except Exception as e:
                print(f"  [ETFDataFetcher] 统一数据源管理器初始化失败: {e}，使用传统方式")
                self._dsm = None
    
    def _init_cache(self):
        """初始化本地缓存"""
        try:
            from data_cache import DataCache
            self._cache = DataCache()
            stats = self._cache.get_cache_stats()
            print(f"  [ETFDataFetcher] 本地缓存初始化成功: {stats['files']} 文件, {stats['size_mb']} MB")
        except Exception as e:
            print(f"  [ETFDataFetcher] 本地缓存初始化失败: {e}")
            self._cache = None
    
    def _get_cached_data(self, code: str, data_type: str, days: int) -> Optional[pd.DataFrame]:
        """尝试从缓存获取数据"""
        if self._cache is None:
            return None
        try:
            # 每天收盘后拉取一次即可，缓存 1 天
            df = self._cache.get_cache(code, data_type=data_type, max_age_days=1)
            if df is not None and len(df) >= days * 0.8:
                return df
            return None
        except Exception:
            return None
    
    def _save_cached_data(self, code: str, df: pd.DataFrame, data_type: str, source: str):
        """保存数据到缓存"""
        if self._cache is None or df is None:
            return
        try:
            self._cache.save_cache(code, df, data_type=data_type, source=source)
        except Exception as e:
            print(f"  [ETFDataFetcher] 缓存保存失败: {e}")
    
    @staticmethod
    def get_kline_data_from_akshare(etf_code: str, days: int = 120) -> pd.DataFrame:
        """使用 AkShare 获取 ETF K线数据"""
        import akshare as ak
        
        start_date = (datetime.now() - timedelta(days=days*2)).strftime('%Y%m%d')
        
        print(f"  正在从 AkShare 获取 {etf_code} 数据...")
        
        df = ak.fund_etf_hist_em(symbol=etf_code, period="daily", 
                                  start_date=start_date, adjust="qfq")
        
        if len(df) == 0:
            raise ValueError("AkShare 返回空数据")
        
        df = df.rename(columns={
            '日期': 'date', '开盘': 'open', '收盘': 'close',
            '最高': 'high', '最低': 'low', '成交量': 'volume', '成交额': 'amount'
        })
        
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)
        
        if len(df) > days:
            df = df.tail(days).reset_index(drop=True)
        
        print(f"  AkShare 数据获取成功: {len(df)} 条")
        return df
    
    @staticmethod
    def get_kline_data_eastmoney(secid: str, days: int = 120) -> pd.DataFrame:
        """使用东方财富获取 ETF K线数据(备用)"""
        import time
        max_retries = 3
        for attempt in range(max_retries):
            try:
                url = f"https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57&klt=101&fqt=1&end=20500101&lmt={days}"
                req = urllib.request.Request(url, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Referer': 'https://quote.eastmoney.com/'
                })
                resp = urllib.request.urlopen(req, timeout=15)
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
                df['date'] = pd.to_datetime(df['date'])
                df = df.sort_values('date').reset_index(drop=True)
                return df
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"  第 {attempt + 1} 次尝试失败，重试中...")
                    time.sleep(2)
                else:
                    raise e
    
    def get_kline_data(self, etf_code: str, days: int = 120) -> pd.DataFrame:
        """获取 ETF K线数据 - 优先使用缓存，其次统一数据源管理器，支持美股"""
        
        # 1. 先查本地缓存
        cached = self._get_cached_data(etf_code, "kline", days)
        if cached is not None:
            return cached
        
        # 2. 优先使用统一数据源管理器（TickFlow 已支持 A股/ETF/港股/美股）
        if self._dsm is not None:
            try:
                print(f"  [ETFDataFetcher] 使用统一数据源管理器获取 {etf_code} 数据...")
                df = self._dsm.get_etf_kline(etf_code, days)
                print(f"  [ETFDataFetcher] 统一数据源管理器获取成功: {len(df)} 条")
                self._save_cached_data(etf_code, df, "kline", "dsm")
                return df
            except Exception as e:
                print(f"  [ETFDataFetcher] 统一数据源管理器获取失败: {e}")
        
        # 4. 传统方式: 优先使用 AkShare
        if 'AKSHARE_AVAILABLE' in globals() and AKSHARE_AVAILABLE:
            try:
                df = ETFDataFetcher.get_kline_data_from_akshare(etf_code, days)
                self._save_cached_data(etf_code, df, "kline", "akshare")
                return df
            except Exception as e:
                print(f"  AkShare 获取失败: {e}")
        
        # 5. 备用: 东方财富 API
        try:
            print(f"  正在从东方财富获取 {etf_code} 数据...")
            secid = f"1.{etf_code}"
            df = ETFDataFetcher.get_kline_data_eastmoney(secid, days)
            print(f"  东方财富数据获取成功: {len(df)} 条")
            self._save_cached_data(etf_code, df, "kline", "eastmoney")
            return df
        except Exception as e:
            print(f"  东方财富获取失败: {e}")
        
        # 6. 所有数据源失败
        raise FileNotFoundError(f"所有数据源均失败，请检查网络连接或手动提供数据")
    
    def get_stock_kline_data(self, stock_code: str, days: int = 120) -> pd.DataFrame:
        """获取个股 K线数据 - 优先使用缓存，支持港股/美股"""
        
        # 1. 先查本地缓存
        cached = self._get_cached_data(stock_code, "stock", days)
        if cached is not None:
            return cached
        
        # 2. 优先使用统一数据源管理器（TickFlow 已支持 A股/ETF/港股/美股）
        if self._dsm is not None:
            try:
                print(f"  [ETFDataFetcher] 使用统一数据源管理器获取 {stock_code} 数据...")
                df = self._dsm.get_stock_kline(stock_code, days)
                print(f"  [ETFDataFetcher] 统一数据源管理器获取成功: {len(df)} 条")
                self._save_cached_data(stock_code, df, "stock", "dsm")
                return df
            except Exception as e:
                print(f"  [ETFDataFetcher] 统一数据源管理器获取失败: {e}")
        
        # 5. 传统方式: 使用 AkShare
        if 'AKSHARE_AVAILABLE' in globals() and AKSHARE_AVAILABLE and not stock_code.endswith('.HK'):
            try:
                import akshare as ak
                start_date = (datetime.now() - timedelta(days=days*2)).strftime('%Y%m%d')
                df = ak.stock_zh_a_hist(symbol=stock_code, period="daily", 
                                        start_date=start_date, adjust="qfq")
                
                if len(df) == 0:
                    raise ValueError("AkShare 返回空数据")
                
                df = df.rename(columns={
                    '日期': 'date', '开盘': 'open', '收盘': 'close',
                    '最高': 'high', '最低': 'low', '成交量': 'volume', '成交额': 'amount'
                })
                
                df['date'] = pd.to_datetime(df['date'])
                df = df.sort_values('date').reset_index(drop=True)
                
                if len(df) > days:
                    df = df.tail(days).reset_index(drop=True)
                
                self._save_cached_data(stock_code, df, "stock", "akshare")
                print(f"  AkShare 个股数据获取成功: {len(df)} 条")
                return df
            except Exception as e:
                print(f"  AkShare 个股获取失败: {e}")
        
        raise FileNotFoundError(f"所有数据源均失败，无法获取 {stock_code} 数据")
    
    def get_fund_flow_data(self, code: str) -> Dict:
        """获取资金流向数据"""
        if self._dsm is not None:
            try:
                return self._dsm.get_fund_flow(code)
            except Exception as e:
                print(f"  [ETFDataFetcher] 资金流向获取失败: {e}")
        
        return {"source": "none", "note": "资金流向数据暂不可用"}
    
    def get_source_status(self) -> Dict:
        """获取数据源状态"""
        if self._dsm is not None:
            return self._dsm.get_all_source_status()
        return {}
    
    def update_tushare_token(self, token: str):
        """更新 Tushare token"""
        if self._dsm is not None:
            self._dsm.update_token("tushare", token)
            print("  [ETFDataFetcher] Tushare token 已更新")


# ============ 技术指标模块 ============

class TechnicalIndicators:
    """技术指标计算"""
    
    @staticmethod
    def ma(df: pd.DataFrame, period: int) -> pd.Series:
        return df['close'].rolling(window=period).mean()
    
    @staticmethod
    def rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))
    
    @staticmethod
    def macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
        ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
        ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram
    
    @staticmethod
    def bollinger_bands(df: pd.DataFrame, period: int = 20, std_dev: int = 2) -> Tuple[pd.Series, pd.Series, pd.Series]:
        ma = df['close'].rolling(window=period).mean()
        std = df['close'].rolling(window=period).std()
        upper = ma + (std * std_dev)
        lower = ma - (std * std_dev)
        return upper, ma, lower
    
    @staticmethod
    def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        return tr.rolling(window=period).mean()
    
    @staticmethod
    def volume_ma(df: pd.DataFrame, period: int = 20) -> pd.Series:
        return df['volume'].rolling(window=period).mean()
    
    @staticmethod
    def kdj(df: pd.DataFrame, n: int = 9, m1: int = 3, m2: int = 3) -> Tuple[pd.Series, pd.Series, pd.Series]:
        low_list = df['low'].rolling(window=n, min_periods=n).min()
        high_list = df['high'].rolling(window=n, min_periods=n).max()
        rsv = (df['close'] - low_list) / (high_list - low_list) * 100
        k = rsv.ewm(com=m1-1, adjust=False).mean()
        d = k.ewm(com=m2-1, adjust=False).mean()
        j = 3 * k - 2 * d
        return k, d, j


# ============ 回测模块 ============

class BacktestEngine:
    """回测引擎"""
    
    def __init__(self, df: pd.DataFrame):
        self.df = df.copy()
        self._calculate_indicators()
    
    def _calculate_indicators(self):
        """计算所有技术指标"""
        ti = TechnicalIndicators()
        self.df['ma5'] = ti.ma(self.df, 5)
        self.df['ma10'] = ti.ma(self.df, 10)
        self.df['ma20'] = ti.ma(self.df, 20)
        self.df['ma60'] = ti.ma(self.df, 60)
        self.df['rsi14'] = ti.rsi(self.df, 14)
        self.df['macd'], self.df['macd_signal'], self.df['macd_hist'] = ti.macd(self.df)
        self.df['bb_upper'], self.df['bb_middle'], self.df['bb_lower'] = ti.bollinger_bands(self.df)
        self.df['atr14'] = ti.atr(self.df, 14)
        self.df['vol_ma20'] = ti.volume_ma(self.df, 20)
        self.df['k'], self.df['d'], self.df['j'] = ti.kdj(self.df)
    
    def backtest_period(self, days: int) -> Dict:
        """回测指定周期"""
        if len(self.df) < days:
            return {"error": f"数据不足{days}天"}
        
        recent = self.df.tail(days).copy()
        
        start_price = recent.iloc[0]['close']
        end_price = recent.iloc[-1]['close']
        total_return = (end_price - start_price) / start_price * 100
        
        daily_returns = recent['close'].pct_change().dropna()
        volatility = daily_returns.std() * np.sqrt(252) * 100
        
        cumulative = (1 + daily_returns).cumprod()
        peak = cumulative.expanding().max()
        drawdown = (cumulative - peak) / peak
        max_drawdown = drawdown.min() * 100
        
        excess_return = daily_returns.mean() * 252 - 0.02
        sharpe = excess_return / (daily_returns.std() * np.sqrt(252)) if daily_returns.std() != 0 else 0
        
        win_rate = (daily_returns > 0).sum() / len(daily_returns) * 100
        
        latest = recent.iloc[-1]
        
        ma_bull = latest['close'] > latest['ma5'] > latest['ma10'] > latest['ma20']
        ma_bear = latest['close'] < latest['ma5'] < latest['ma10'] < latest['ma20']
        
        rsi_value = latest['rsi14']
        rsi_status = "超买" if rsi_value > 70 else "超卖" if rsi_value < 30 else "中性"
        
        macd_bull = latest['macd'] > latest['macd_signal'] and latest['macd_hist'] > 0
        macd_bear = latest['macd'] < latest['macd_signal'] and latest['macd_hist'] < 0
        
        bb_position = (latest['close'] - latest['bb_lower']) / (latest['bb_upper'] - latest['bb_lower'])
        
        kdj_j = latest['j']
        kdj_status = "超买" if kdj_j > 80 else "超卖" if kdj_j < 20 else "中性"
        
        # 周期描述
        period_desc_map = {
            30: "近1个月",
            60: "近2个月",
            90: "近3个月",
            120: "近4个月",
            200: "近7个月",
            280: "近9个月",
            365: "近1年"
        }
        period_desc = period_desc_map.get(days, f"近{days}天")
        
        return {
            "period_days": days,
            "period_desc": period_desc,
            "start_date": recent.iloc[0]['date'].strftime('%Y-%m-%d'),
            "end_date": recent.iloc[-1]['date'].strftime('%Y-%m-%d'),
            "start_price": round(start_price, 3),
            "end_price": round(end_price, 3),
            "total_return": round(total_return, 2),
            "volatility": round(volatility, 2),
            "max_drawdown": round(max_drawdown, 2),
            "sharpe_ratio": round(sharpe, 3),
            "win_rate": round(win_rate, 2),
            "avg_volume": int(recent['volume'].mean()),
            "latest_rsi": round(rsi_value, 2),
            "rsi_status": rsi_status,
            "macd_signal": "bullish" if macd_bull else "bearish" if macd_bear else "neutral",
            "ma_trend": "多头排列" if ma_bull else "空头排列" if ma_bear else "震荡",
            "bb_position": round(bb_position, 3),
            "atr14": round(latest['atr14'], 3),
            "volume_ratio": round(latest['volume'] / latest['vol_ma20'], 2) if latest['vol_ma20'] > 0 else 0,
            "kdj_j": round(kdj_j, 2),
            "kdj_status": kdj_status
        }
    
    def run_all_backtests(self, periods: List[int] = None) -> List[Dict]:
        """运行所有周期回测"""
        results = []
        for days in (periods or [30, 60, 90, 120, 200, 280, 365]):
            result = self.backtest_period(days)
            results.append(result)
        return results
    
    def run_strategy_backtests(self, strategies = None, period: int = 365) -> List[Dict]:
        """运行策略库中的策略回测（用于主报告）"""
        sys.path.insert(0, '/home/liudawei/github/daily_tracker_analytics/etf_tracker/multi_agent')
        try:
            from core.strategy_library import STRATEGIES, run_event_backtest
        except Exception:
            return []
        
        if strategies is None:
            strategies = list(STRATEGIES.keys())
        
        results = []
        for sid in strategies:
            sinfo = STRATEGIES.get(sid)
            if not sinfo:
                continue
            try:
                bt = run_event_backtest(self.df, sinfo['fn'])
                # 过滤交易次数过少的结果
                if bt['total_trades'] < 3:
                    continue
                # Calmar = 年化收益 / 最大回撤绝对值
                annual_ret = bt['total_return'] / (period / 365) if period > 0 else bt['total_return']
                calmar = annual_ret / abs(bt['max_drawdown']) if bt['max_drawdown'] != 0 else 0
                results.append({
                    'strategy_id': sid,
                    'strategy_name': sinfo['name'],
                    'category': sinfo.get('category', '其他'),
                    'total_return': bt['total_return'],
                    'max_drawdown': bt['max_drawdown'],
                    'sharpe_ratio': bt['sharpe_ratio'],
                    'win_rate': bt['win_rate'],
                    'total_trades': bt['total_trades'],
                    'avg_win': bt.get('avg_win', 0),
                    'avg_loss': bt.get('avg_loss', 0),
                    'calmar': round(calmar, 2),
                    'score': round(bt['sharpe_ratio'] * 0.5 + calmar * 0.3 + bt['win_rate'] / 100 * 0.2, 2),
                })
            except Exception:
                continue
        
        results.sort(key=lambda x: x['score'], reverse=True)
        return results


# ============ 信号生成模块 ============

class SignalGenerator:
    """交易信号生成器"""
    
    def __init__(self, backtest_results: List[Dict], df: pd.DataFrame, 
                 fund_flow: Dict = None, margin_data: Dict = None):
        self.results = backtest_results
        self.df = df
        self.fund_flow = fund_flow or {}
        self.margin_data = margin_data or {}
        self.latest = df.iloc[-1]
        self._ensure_indicators()
    
    def _ensure_indicators(self):
        """确保技术指标已计算"""
        ti = TechnicalIndicators()
        if 'rsi14' not in self.df.columns:
            self.df['rsi14'] = ti.rsi(self.df, 14)
        if 'macd' not in self.df.columns:
            self.df['macd'], self.df['macd_signal'], self.df['macd_hist'] = ti.macd(self.df)
        if 'ma5' not in self.df.columns:
            self.df['ma5'] = ti.ma(self.df, 5)
            self.df['ma10'] = ti.ma(self.df, 10)
            self.df['ma20'] = ti.ma(self.df, 20)
        if 'bb_upper' not in self.df.columns:
            self.df['bb_upper'], self.df['bb_middle'], self.df['bb_lower'] = ti.bollinger_bands(self.df)
        if 'atr14' not in self.df.columns:
            self.df['atr14'] = ti.atr(self.df, 14)
        if 'vol_ma20' not in self.df.columns:
            self.df['vol_ma20'] = ti.volume_ma(self.df, 20)
        if 'j' not in self.df.columns:
            self.df['k'], self.df['d'], self.df['j'] = ti.kdj(self.df)
        self.latest = self.df.iloc[-1]
    
    def generate_signals(self) -> Dict:
        """生成综合交易信号"""
        signals = []
        score = 0
        max_score = 0
        
        # 1. 多周期趋势一致性
        returns = [r['total_return'] for r in self.results if 'total_return' in r]
        if all(r > 0 for r in returns):
            signals.append("多周期趋势一致向上(30/60/90/120/200/280/365天均上涨)")
            score += 3
        elif all(r < 0 for r in returns):
            signals.append("多周期趋势一致向下")
            score -= 3
        else:
            signals.append("多周期趋势分化，需观望")
        max_score += 3
        
        # 2. RSI 信号
        rsi = self.latest['rsi14']
        if rsi < 30:
            signals.append(f"RSI 超卖 ({rsi:.1f})，潜在反弹机会")
            score += 2
        elif rsi > 70:
            signals.append(f"RSI 超买 ({rsi:.1f})，注意回调风险")
            score -= 2
        else:
            signals.append(f"RSI 中性 ({rsi:.1f})")
        max_score += 2
        
        # 3. MACD 信号
        macd = self.latest['macd']
        macd_sig = self.latest['macd_signal']
        macd_hist = self.latest['macd_hist']
        if macd > macd_sig and macd_hist > 0:
            signals.append("MACD 金叉且柱状图向上，动能强劲")
            score += 2
        elif macd < macd_sig and macd_hist < 0:
            signals.append("MACD 死叉且柱状图向下，动能减弱")
            score -= 2
        else:
            signals.append("MACD 方向不明")
        max_score += 2
        
        # 4. 均线排列
        close = self.latest['close']
        ma5 = self.latest['ma5']
        ma10 = self.latest['ma10']
        ma20 = self.latest['ma20']
        if close > ma5 > ma10 > ma20:
            signals.append("均线多头排列，趋势健康")
            score += 2
        elif close < ma5 < ma10 < ma20:
            signals.append("均线空头排列，趋势走弱")
            score -= 2
        else:
            signals.append("均线纠缠，趋势不明")
        max_score += 2
        
        # 5. 布林带位置
        bb_pos = (close - self.latest['bb_lower']) / (self.latest['bb_upper'] - self.latest['bb_lower'])
        if bb_pos > 0.8:
            signals.append("价格接近布林带上轨，注意压力")
            score -= 1
        elif bb_pos < 0.2:
            signals.append("价格接近布林带下轨，潜在支撑")
            score += 1
        else:
            signals.append("价格位于布林带中位")
        max_score += 1
        
        # 6. 成交量
        vol_ratio = self.latest['volume'] / self.latest['vol_ma20'] if self.latest['vol_ma20'] > 0 else 0
        if vol_ratio > 1.5:
            signals.append(f"成交量放大 ({vol_ratio:.1f}x)，资金活跃")
            score += 1
        elif vol_ratio < 0.5:
            signals.append(f"成交量萎缩 ({vol_ratio:.1f}x)，参与度低")
            score -= 1
        else:
            signals.append(f"成交量正常 ({vol_ratio:.1f}x)")
        max_score += 1
        
        # 7. KDJ 信号
        j_value = self.latest['j']
        if j_value < 20:
            signals.append(f"KDJ J值超卖 ({j_value:.1f})")
            score += 1
        elif j_value > 80:
            signals.append(f"KDJ J值超买 ({j_value:.1f})")
            score -= 1
        else:
            signals.append(f"KDJ J值中性 ({j_value:.1f})")
        max_score += 1
        
        # 8. 资金流向
        if self.fund_flow and 'main_inflow' in self.fund_flow:
            main_inflow = self.fund_flow['main_inflow']
            if main_inflow > 0:
                signals.append(f"主力资金净流入 {main_inflow}万元")
                score += 1
            else:
                signals.append(f"主力资金净流出 {abs(main_inflow)}万元")
                score -= 1
            max_score += 1
        
        # 9. 融资融券
        if self.margin_data and 'leverage_ratio' in self.margin_data:
            leverage = self.margin_data['leverage_ratio']
            if leverage > 10:
                signals.append(f"融资杠杆高 ({leverage:.1f}x)，市场情绪乐观")
                score += 1
            elif leverage < 5:
                signals.append(f"融资杠杆低 ({leverage:.1f}x)，市场情绪谨慎")
            max_score += 1
        
        # 综合评分
        normalized_score = (score / max_score * 100) if max_score > 0 else 0
        
        if normalized_score >= 60:
            overall = "bullish"
            action = "建议关注买入机会"
        elif normalized_score <= -40:
            overall = "bearish"
            action = "建议减仓或观望"
        else:
            overall = "neutral"
            action = "建议持仓观望，等待明确信号"
        
        return {
            "signals": signals,
            "score": round(normalized_score, 1),
            "overall": overall,
            "action": action
        }
    
    def calculate_position_sizing(self) -> Dict:
        """计算仓位建议"""
        atr = self.latest['atr14']
        close = self.latest['close']
        
        risk_per_trade = 0.02
        stop_loss = 2 * atr
        
        position_size = risk_per_trade / (stop_loss / close) * 100
        position_size = min(position_size, 100)
        
        return {
            "suggested_position": f"{position_size:.1f}%",
            "stop_loss": round(close - stop_loss, 3),
            "take_profit_1": round(close + 2 * atr, 3),
            "take_profit_2": round(close + 3 * atr, 3),
            "risk_reward_ratio": "1:2 ~ 1:3"
        }


# ============ 报告生成模块 ============

class ReportGenerator:
    """投资规划报告生成器"""
    
    def __init__(self, config: Config = None):
        self.config = config or Config()
        self.etf_code = self.config.etf_code or self.config.default_etf
        self.etf_name = self.config.etf_name
        self.date = datetime.now().strftime('%Y-%m-%d')
        self.report_title = self.config.report_title
    
    def _generate_alerts_section(self, alerts) -> str:
        """生成实时预警报告章节"""
        if not alerts:
            return ""
        
        from alert_engine import AlertEngine
        engine = AlertEngine()
        return "\n" + engine.generate_markdown_report(alerts) + "\n"
    
    def generate_report(self, backtest_results: List[Dict], signals: Dict, 
                       position: Dict, df: pd.DataFrame, 
                       fund_flow: Dict = None, margin_data: Dict = None,
                       sector_comparison: Dict = None,
                       stock_analysis: List[Dict] = None,
                       predictions: Dict = None,
                       strategy: Dict = None,
                       alerts: List[Dict] = None,
                       industry_news: str = "",
                       multi_model_results: Dict = None) -> str:
        """生成完整的投资规划报告"""
        
        latest = df.iloc[-1]
        
        report = f"""# {self.etf_name} ({self.etf_code}) 投资规划报告

> **报告日期**: {self.date}  
> **数据来源**: TickFlow + AkShare + 东方财富  
> **分析周期**: {'/'.join([str(p) for p in self.config.backtest_periods])}天滚动回测

---

## 一、ETF 基本信息

| 项目 | 内容 |
|------|------|
| 基金名称 | {self.etf_name} |
| 基金代码 | {self.etf_code} |
| 最新净值 | {latest['close']:.3f} |
| 今日开盘 | {latest['open']:.3f} |
| 今日最高 | {latest['high']:.3f} |
| 今日最低 | {latest['low']:.3f} |
| 成交量 | {latest['volume']:,} |
| 成交额 | {latest['amount']:,.0f} |

---

## 二、多周期回测分析

"""
        
        for result in backtest_results:
            report += f"""### {result['period_days']}天回测 ({result['period_desc']}) {result['start_date']} ~ {result['end_date']}

| 指标 | 数值 | 评价 |
|------|------|------|
| 区间收益 | {result['total_return']:+.2f}% | {'上涨' if result['total_return'] > 0 else '下跌'} |
| 年化波动率 | {result['volatility']:.2f}% | {'高波动' if result['volatility'] > 30 else '中等波动' if result['volatility'] > 20 else '低波动'} |
| 最大回撤 | {result['max_drawdown']:.2f}% | {'深回撤' if result['max_drawdown'] < -15 else '中等回撤' if result['max_drawdown'] < -8 else '浅回撤'} |
| 夏普比率 | {result['sharpe_ratio']:.3f} | {'优秀' if result['sharpe_ratio'] > 1 else '一般' if result['sharpe_ratio'] > 0 else '较差'} |
| 日胜率 | {result['win_rate']:.1f}% | {'高胜率' if result['win_rate'] > 55 else '均衡' if result['win_rate'] > 45 else '低胜率'} |
| 平均成交量 | {result['avg_volume']:,} | - |
| RSI(14) | {result['latest_rsi']:.2f} ({result['rsi_status']}) | - |
| MACD | {result['macd_signal']} | - |
| KDJ J值 | {result['kdj_j']:.2f} ({result.get('kdj_signal', '未知')}) | - |
| 均线趋势 | {result['ma_trend']} | - |
| 布林带位置 | {result['bb_position']:.1%} | {'高位' if result['bb_position'] > 0.8 else '低位' if result['bb_position'] < 0.2 else '中位'} |
| ATR(14) | {result['atr14']} | - |
| 量比 | {result['volume_ratio']:.2f}x | {'放量' if result['volume_ratio'] > 1.5 else '缩量' if result['volume_ratio'] < 0.5 else '正常'} |

---

"""
        
        report += f"""## 三、综合交易信号

### 信号评分: {signals['score']}/100 ({signals['overall']})

"""
        for sig in signals['signals']:
            report += f"- {sig}\n"
        
        report += f"""
### 操作建议

> **{signals['action']}**

---

## 四、仓位管理建议

| 项目 | 建议值 |
|------|--------|
| 建议仓位 | {position['suggested_position']} |
| 止损位 | {position['stop_loss']} |
| 第一目标位 | {position['take_profit_1']} |
| 第二目标位 | {position['take_profit_2']} |
| 风险收益比 | {position['risk_reward_ratio']} |

---

## 五、个股深度分析

"""
        
        # 添加个股跟踪数据
        individual_stocks = self.config.config.get("individual_stocks", {})
        if individual_stocks.get("enabled", False):
            stocks = individual_stocks.get("stocks", [])
            if stocks:
                # 如果有深度分析数据，显示详细表格
                if stock_analysis and len(stock_analysis) > 0:
                    report += "### 个股技术指标与资金流向\n\n"
                    report += "| 股票 | 代码 | 最新价 | 30天收益 | RSI | MACD | KDJ | 主力流向 | 评分 |\n"
                    report += "|------|------|--------|----------|-----|------|-----|----------|------|\n"
                    
                    for analysis in stock_analysis:
                        if 'error' not in analysis:
                            name = analysis.get('name', '-')
                            code = analysis.get('code', '-')
                            price = analysis.get('latest_price', '-')
                            ret = analysis.get('total_return', 0)
                            rsi = analysis.get('rsi', '-')
                            rsi_status = analysis.get('rsi_status', '')
                            macd = analysis.get('macd_status', '-')
                            kdj = analysis.get('kdj_status', '-')
                            fund = analysis.get('fund_flow', {})
                            fund_signal = fund.get('signal', '-') if isinstance(fund, dict) else '-'
                            score = analysis.get('score', 0)
                            max_score = analysis.get('max_score', 5)
                            score_pct = score / max_score * 100 if max_score > 0 else 0
                            
                            report += f"| {name} | {code} | {price} | {ret:+.2f}% | {rsi} ({rsi_status}) | {macd} | {kdj} | {fund_signal} | {score_pct:.0f}/100 |\n"
                    
                    report += "\n"
                
                # 基础信息表格
                report += "### 个股基本信息\n\n"
                report += "| 股票 | 代码 | 行业 | 跟踪理由 | 最新价 | 30天收益 | 趋势 |\n"
                report += "|------|------|------|----------|--------|----------|------|\n"
                
                for stock in stocks:
                    # 查找对应的分析数据
                    analysis = None
                    for sa in (stock_analysis or []):
                        if sa.get('code') == stock['code']:
                            analysis = sa
                            break
                    
                    if analysis and 'error' not in analysis:
                        report += f"| {analysis.get('name', stock['name'])} | {stock['code']} | {stock.get('sector', '-')} | {stock.get('reason', '-')} | {analysis.get('latest_price', '-')} | {analysis.get('total_return', 0):+.2f}% | {analysis.get('trend', '-')} |\n"
                    else:
                        report += f"| {stock['name']} | {stock['code']} | {stock.get('sector', '-')} | {stock.get('reason', '-')} | - | - | 数据获取中 |\n"
                
                report += "\n"
        
        report += f"""
---

## 六、趋势预测与策略优化

### 未来趋势预测

"""
        
        if predictions and 'error' not in predictions:
            for day_key in ['day_1', 'day_3', 'day_5']:
                if day_key in predictions:
                    pred = predictions[day_key]
                    day_num = day_key.replace('day_', '')
                    confidence_emoji = "强" if pred['confidence'] >= 0.7 else "中" if pred['confidence'] >= 0.5 else "弱"
                    report += f"**{day_num}日预测**: {pred['trend']} (置信度: {pred['confidence']:.0%})\n"
                    report += f"  - 预测价格: {pred['predicted_price']:.2f} (预期收益: {pred['expected_return']:+.2f}%)\n"
                    report += f"  - 线性趋势: {'向上' if pred['signals']['linear_slope'] > 0 else '向下'}\n"
                    report += f"  - RSI趋势: {pred['signals']['rsi_trend']}\n"
                    report += f"  - MACD趋势: {pred['signals']['macd_trend']}\n"
                    report += f"  - 均线趋势: {pred['signals']['ma_trend']}\n\n"
        else:
            report += "> 预测数据暂不可用\n"
        
        report += f"""
### 策略优化建议


"""
        
        if strategy and 'error' not in strategy:
            score_emoji = "强" if strategy['total_score'] > 0.6 else "中" if strategy['total_score'] > 0.3 else "弱" if strategy['total_score'] > -0.3 else "空"
            report += f"**综合评分**: {strategy['total_score']:.3f}\n\n"
            report += f"**操作建议**: {strategy['action']}\n\n"
            report += f"**建议仓位**: {strategy['position']}\n\n"
            
            report += "**各因子得分**:\n\n"
            report += "| 因子 | 权重 | 得分 | 说明 |\n"
            report += "|------|------|------|------|\n"
            for factor, weight in strategy.get('weights', {}).items():
                score = strategy.get('scores', {}).get(factor, 0)
                report += f"| {factor} | {weight:.0%} | {score:+.2f} | {'积极' if score > 0 else '消极' if score < 0 else '中性'} |\n"
        else:
            report += "> 策略优化数据暂不可用\n"
        
        # 多模型交叉验证预测
        report += f"""
---

## 七、多模型交叉验证预测

"""
        if multi_model_results:
            ensemble = multi_model_results.get('ensemble_result', {}).get('ensemble', {})
            training = multi_model_results.get('training_results', {})
            individual = multi_model_results.get('ensemble_result', {}).get('individual_predictions', {})
            
            # 集成预测结果
            if ensemble:
                report += f"### 集成预测结果\n\n"
                report += f"| 指标 | 数值 |\n"
                report += f"|------|------|\n"
                report += f"| 预测1日收益 | {ensemble.get('return_1d', 0)*100:+.2f}% |\n"
                report += f"| 预测1日价格 | {ensemble.get('price_1d', 0):.3f} |\n"
                report += f"| 置信度 | {ensemble.get('confidence', 0)*100:.1f}% |\n"
                report += "\n"
            
            # 各模型对比
            if individual:
                report += "### 各模型预测对比\n\n"
                report += "| 模型 | 预测收益 | 预测价格 | RMSE |\n"
                report += "|------|----------|----------|------|\n"
                for model_name, pred in individual.items():
                    train_result = training.get(model_name, {})
                    rmse = train_result.get('test_rmse', 'N/A')
                    if isinstance(rmse, float):
                        rmse = f"{rmse:.4f}"
                    report += f"| {model_name} | {pred.get('return_1d', 0)*100:+.2f}% | {pred.get('price_1d', 0):.3f} | {rmse} |\n"
                report += "\n"
            
            # 模型性能对比
            if training:
                report += "### 模型性能对比\n\n"
                report += "| 模型 | RMSE | MAE | R² |\n"
                report += "|------|------|-----|-----|\n"
                for model_name, result in training.items():
                    if 'error' not in result:
                        rmse = result.get('test_rmse', 'N/A')
                        mae = result.get('test_mae', 'N/A')
                        r2 = result.get('test_r2', 'N/A')
                        if isinstance(rmse, float): rmse = f"{rmse:.4f}"
                        if isinstance(mae, float): mae = f"{mae:.4f}"
                        if isinstance(r2, float): r2 = f"{r2:.2f}"
                        report += f"| {result.get('model', model_name)} | {rmse} | {mae} | {r2} |\n"
                report += "\n"
            
            # 特征重要性
            report += "### 重要特征排名\n\n"
            report += "准确率最高模型的特征重要性排序，帮助理解价格驱动因素\n\n"
            report += "*详细多模型报告: multi_model_prediction_*.md*\n\n"
        else:
            report += "> 多模型预测暂不可用（需安装 LightGBM、XGBoost 等依赖）\n"
        
        report += f"""
---

## 八、自动预警

"""
        
        if alerts and len(alerts) > 0:
            report += f"> 发现 {len(alerts)} 条预警\n\n"
            for alert in alerts:
                level_text = "警告" if alert['level'] == 'warning' else "提示" if alert['level'] == 'info' else "信息"
                report += f"**[{alert['type']}]** {alert['message']}\n\n"
        else:
            report += "> 当前无预警\n\n"
        
        report += f"""
---

## 九、风险提示

1. 稀土价格受国际地缘政治影响较大
2. 新能源政策变化可能影响稀土需求预期
3. 本报告仅供参考，不构成投资建议
4. 过往业绩不代表未来表现
5. 预测结果基于历史数据，不代表未来走势

---

*报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*  
*分析框架: 多因子量化模型 + 多算法预测融合 (LightGBM/XGBoost/RF/ARIMA)*  
*数据来源: TickFlow + AkShare + 东方财富 | 多模型预测: multi_model_prediction_*.md*
"""
        
        return report
    
    def generate_multi_etf_report(self, analyzer_results: List[Dict], sector_ranking: pd.DataFrame,
                                   top_etfs_detail: List[Dict] = None,
                                   top_stocks_by_sector: Dict[str, List[Dict]] = None,
                                   report_date: str = None,
                                   international_summary: str = "",
                                   international_analysis: str = "",
                                   alerts: List = None,
                                   research_summary: str = "") -> str:
        """生成多板块 ETF 综合投资报告"""
        date = report_date or datetime.now().strftime('%Y-%m-%d')
        
        report = f"""# {self.report_title}

> **报告日期**: {date}  
> **跟踪 ETF 数量**: {len(analyzer_results)} 只  
> **数据源**: TickFlow + AkShare + 东方财富 + Baostock + Yahoo Finance  
> **分析周期**: {'/'.join([str(p) for p in self.config.backtest_periods])}天滚动回测  
> **覆盖主题**: 机器人 / 人工智能 / AI / 芯片制造 / 半导体设备 / 存储行业 / 内存制造 / 稀土永磁 / 有色金属 / 动力电池 / 银行 / 证券 / 消费 / 医药 / 高股息 / 通信 / 互联网 / 港股科技 / 宽基指数 / 美股科技 / 美股大盘 / 美股半导体 / 美股机器人  

---

## 一、板块综合排名

| 排名 | 板块 | 主题 | 最新价 | 60天收益 | 60天夏普 | 综合评分 | 信号 | 1日预测 | 置信度 |
|------|------|------|--------|----------|----------|----------|------|---------|--------|
"""
        
        for idx, row in sector_ranking.iterrows():
            has_pred = row['预测置信度'] > 0
            if has_pred:
                pred_emoji = "📈" if row['1日预测'] > 0.3 else "📉" if row['1日预测'] < -0.3 else "➖"
                pred_str = f"{pred_emoji} {row['1日预测']:+.2f}%"
                conf_str = f"{row['预测置信度']:.1f}%"
            else:
                pred_str = "-"
                conf_str = "-"
            signal_str = str(row['信号']) if pd.notna(row['信号']) else '-'
            report += f"| {idx+1} | {row['板块']} | {row['主题']} | {row['最新价']:.3f} | {row['60天收益']:+.2f}% | {row['60天夏普']:.2f} | {row['综合评分']:.1f} | {signal_str} | {pred_str} | {conf_str} |\n"
        
        report += """
---

## 二、重点板块深度分析

"""
        
        # 详细分析前5名 ETF
        if top_etfs_detail:
            for rank, result in enumerate(top_etfs_detail, 1):
                if 'error' in result:
                    continue
                
                report += f"""### {rank}. {result['name']} ({result['code']}) — {result['theme']}

| 项目 | 内容 |
|------|------|
| 最新价 | {result['current_price']:.3f} |
| 综合评分 | {result['signals']['score']:.1f}/100 ({result['signals']['overall']}) |
| 建议仓位 | {result['position']['suggested_position']} |

**多周期回测结果**:

| 周期 | 收益 | 最大回撤 | 夏普比率 | 信号 |
|------|------|----------|----------|------|
"""
                for bt in result['backtest']:
                    period_desc = bt.get('period_desc', f"近{bt['period_days']}天")
                    report += f"| {bt['period_days']}天 ({period_desc}) | {bt['total_return']:+.2f}% | {bt['max_drawdown']:.2f}% | {bt['sharpe_ratio']:.2f} | {bt['ma_trend']} |\n"
                
                # 多模型预测
                if result.get('multi_model'):
                    ensemble = result['multi_model']['ensemble']
                    report += f"""
**多模型预测**: 1日收益 {ensemble.get('return_1d', 0)*100:+.2f}% (置信度 {ensemble.get('confidence', 0)*100:.1f}%)

"""
                
                # Top 个股
                sector = result.get('sector', '')
                if top_stocks_by_sector and sector in top_stocks_by_sector:
                    stocks = top_stocks_by_sector[sector]
                    if stocks:
                        report += "**Top 成分股跟踪**:\n\n"
                        report += "| 股票 | 代码 | 30天收益 | 评分 | 信号 | 仓位建议 |\n"
                        report += "|------|------|----------|------|------|----------|\n"
                        for s in stocks[:5]:
                            report += f"| {s['name']} | {s['code']} | {s['return_30d']:+.2f}% | {s.get('score', 0):.1f} | {s.get('signal', '-')} | {s.get('position_size', 0)*100:.1f}% |\n"
                        report += "\n"
                
                report += "---\n\n"
        
        # 投资建议汇总
        if not sector_ranking.empty:
            top3 = sector_ranking.head(3)
            bottom3 = sector_ranking.tail(3)
            
            report += """## 三、投资建议汇总

### 强势板块（重点关注）

"""
            for idx, row in top3.iterrows():
                pred_text = f", 1日预测 {row['1日预测']:+.2f}%" if row['预测置信度'] > 0 else ""
                report += f"- **{row['板块']}** ({row['主题']}): 60天收益 {row['60天收益']:+.2f}%, 评分 {row['综合评分']:.1f}{pred_text}\n"
            
            report += """
### 弱势板块（短期回避）

"""
            for idx, row in bottom3.iterrows():
                pred_text = f", 1日预测 {row['1日预测']:+.2f}%" if row['预测置信度'] > 0 else ""
                report += f"- **{row['板块']}** ({row['主题']}): 60天收益 {row['60天收益']:+.2f}%, 评分 {row['综合评分']:.1f}{pred_text}\n"
        
        # 实时预警
        if alerts:
            report += self._generate_alerts_section(alerts)
        
        # 机构研报观点
        if research_summary:
            report += """
---

## 四、机构研报观点

"""
            report += research_summary
        
        # 国际市场板块
        if international_summary or international_analysis:
            report += """
---

## 五、国际市场环境

"""
            if international_summary:
                report += international_summary + "\n\n"
            if international_analysis:
                report += "### 对A股影响分析\n\n"
                report += international_analysis + "\n"
        
        report += f"""
---

## 五、风险提示

1. 本报告基于历史数据和多模型预测，不构成投资建议
2. 市场有风险，投资需谨慎
3. 预测置信度低时建议观望
4. 国际地缘政治、政策变化可能影响板块走势
5. 港股 ETF 受汇率和海外市场影响较大
6. 机构研报观点仅供参考，可能存在滞后性或利益冲突

---

*报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*  
*分析框架: 多因子量化模型 + 多模型预测融合 (LightGBM/XGBoost/RF/ARIMA/LSTM)*  
*数据来源: TickFlow + AkShare + 东方财富 + Baostock + Yahoo Finance*
"""
        
        return report


# ============ 主程序 ============

def run_multi_etf_daily_report(config: Config = None, deep_analysis_top_n: int = 5,
                                analyze_top_stocks: bool = True,
                                enable_wechat_push: bool = True) -> str:
    """
    生成多板块 ETF 每日综合报告
    """
    import time
    start_time = time.time()
    timing = {}
    
    config = config or Config()
    report_date = datetime.now().strftime('%Y-%m-%d')
    
    print("=" * 70)
    print(f"多板块 ETF 综合报告生成")
    print(f"跟踪标的: {len(config.etfs)} 只 ETF")
    print(f"报告日期: {report_date}")
    print("=" * 70)
    
    analyzer = MultiETFAnalyzer(config)
    
    def _log_timing(step: str, t: float):
        timing[step] = round(t, 2)
        print(f"  [计时] {step}: {timing[step]:.2f}s")
    
    # 1. 快速扫描所有 ETF（并行 + 容错）
    print("\n【1/4】快速扫描所有 ETF...")
    t0 = time.time()
    
    def scan_single_etf(etf):
        """扫描单个 ETF 的辅助函数"""
        try:
            df = analyzer.fetcher.get_kline_data(etf['code'], days=400)
            if df is None or len(df) < 30:
                return None
            engine = BacktestEngine(df)
            backtest = engine.run_all_backtests(config.backtest_periods)
            signal_gen = SignalGenerator(backtest, df)
            signals = signal_gen.generate_signals()
            position = signal_gen.calculate_position_sizing()
            bt60 = next((b for b in backtest if b['period_days'] == 60), {})
            print(f"  {etf['name']}: 60天{bt60.get('total_return', 0):+.2f}% 评分{signals['score']:.1f}")
            return {
                'code': etf['code'],
                'name': etf['name'],
                'theme': etf.get('theme', '-'),
                'sector': etf.get('sector', '-'),
                'current_price': df['close'].iloc[-1],
                'backtest': backtest,
                'signals': signals,
                'position': position,
                'df': df,
                'etf_config': etf
            }
        except Exception as e:
            print(f"  {etf['name']} 扫描失败: {e}")
            return None
    
    # 使用线程池并行扫描（IO 密集型适合多线程）
    from concurrent.futures import ThreadPoolExecutor, as_completed
    quick_results = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(scan_single_etf, etf): etf for etf in config.etfs}
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                quick_results.append(result)
    
    if not quick_results:
        print("⚠️ 没有 ETF 扫描成功，退出")
        return ""
    
    _log_timing("1.快速扫描", time.time() - t0)
    
    # 2. 排序并选择前 N 做深度分析
    quick_results.sort(key=lambda x: x['signals']['score'], reverse=True)
    top_etfs = quick_results[:deep_analysis_top_n]
    
    print(f"\n【2/4】对前 {len(top_etfs)} 名 ETF 进行多模型深度预测...")
    t1 = time.time()
    top_etfs_detail = []
    for r in top_etfs:
        try:
            if MULTI_MODEL_AVAILABLE:
                predictor = MultiModelPredictor(r['code'])
                # 先尝试加载隔夜 LSTM 缓存，如果有效则跳过训练
                has_lstm_cache = predictor.load_cached_lstm(r['df'])
                training = predictor.train_all_models(
                    r['df'], target_col='target_1d', skip_lstm=has_lstm_cache
                )
                ensemble = predictor.ensemble_predict(r['df'], days=5)
                r['multi_model'] = {
                    'ensemble': ensemble['ensemble'],
                    'individual': ensemble['individual_predictions'],
                    'training': training
                }
                print(f"  {r['name']}: 1日预测 {ensemble['ensemble'].get('return_1d', 0)*100:+.2f}% (置信度 {ensemble['ensemble'].get('confidence', 0)*100:.1f}%)")
            top_etfs_detail.append(r)
        except Exception as e:
            print(f"  {r['name']} 深度预测失败: {e}")
            top_etfs_detail.append(r)
    
    _log_timing("2.多模型预测", time.time() - t1)
    
    # 3. 分析重点 ETF 的 Top10 成分股
    top_stocks_by_sector = {}
    if analyze_top_stocks:
        print(f"\n【3/4】分析重点 ETF 的 Top10 成分股...")
        t2 = time.time()
        for r in top_etfs_detail:
            try:
                etf_config = r['etf_config']
                # 默认只做快速扫描，避免完整多模型训练导致超时
                stocks = analyzer.analyze_top_stocks(etf_config, max_stocks=10, enable_multi_model=False)
                if stocks:
                    top_stocks_by_sector[r['sector']] = stocks
                    print(f"  {r['name']}: 分析了 {len(stocks)} 只成分股")
            except Exception as e:
                print(f"  {r['name']} 成分股分析失败: {e}")
        _log_timing("3.成分股分析", time.time() - t2)
    
    # 4. 实时预警分析
    print("\n【4/5】实时预警分析...")
    t3 = time.time()
    alerts = []
    try:
        from alert_engine import AlertEngine
        alert_engine = AlertEngine()
        for r in quick_results:
            try:
                etf_alerts = alert_engine.analyze(r['code'], r['name'], r['df'])
                alerts.extend(etf_alerts)
            except Exception as e:
                print(f"  {r['name']} 预警分析失败: {e}")
        if alerts:
            print(f"  发现 {len(alerts)} 条预警信号")
            # 持久化预警
            try:
                from data_persistence import DataPersistence
                dp = DataPersistence()
                dp.save_alerts([a.to_dict() for a in alerts])
            except Exception as e:
                print(f"  预警持久化失败: {e}")
        else:
            print("  今日无异常预警信号")
    except Exception as e:
        print(f"  预警模块加载失败: {e}")
    _log_timing("4.实时预警", time.time() - t3)
    
    # 5. 生成板块排名
    print("\n【5/6】生成板块排名和综合报告...")
    t4 = time.time()
    analyzer.results = quick_results
    sector_ranking = analyzer.get_sector_ranking()
    
    # 获取国际市场数据
    international_data = None
    international_summary = ""
    international_analysis = ""
    if INTERNATIONAL_MARKET_AVAILABLE:
        try:
            print("  正在获取国际市场数据...")
            im_fetcher = InternationalMarketFetcher()
            international_data = im_fetcher.fetch_all(days=30)
            international_summary = im_fetcher.generate_market_summary()
            international_analysis = im_fetcher.generate_market_analysis()
            print(f"  国际市场数据获取成功: {len(international_data)} 个品种")
        except Exception as e:
            print(f"  国际市场数据获取失败: {e}")
    
    # 加载研报观点
    research_summary = ""
    try:
        from report_scanner import scan_reports, generate_research_summary
        aggregator = scan_reports()
        # 只提取重点板块相关研报的汇总
        research_summary = generate_research_summary(aggregator)
        if research_summary:
            print(f"  已加载 {len(aggregator.reports)} 份研报观点")
    except Exception as e:
        print(f"  研报加载失败: {e}")
    
    # 构建报告
    report_gen = ReportGenerator(config)
    report = report_gen.generate_multi_etf_report(
        quick_results, sector_ranking,
        top_etfs_detail=top_etfs_detail,
        top_stocks_by_sector=top_stocks_by_sector,
        report_date=report_date,
        international_summary=international_summary,
        international_analysis=international_analysis,
        alerts=alerts,
        research_summary=research_summary
    )
    
    # 6. LLM 智能决策报告 + 择时信号
    if LLM_REPORT_AVAILABLE and config.features.get('llm_report', True) and config.config.get('llm_report', {}).get('enabled', True):
        try:
            print("\n【6/6】生成 LLM 智能决策报告...")
            t_llm = time.time()
            from industry_news_enhanced import NewsFetcher
            news_fetcher = NewsFetcher()
            news_data = news_fetcher.get_multi_industry_news(
                list(NewsFetcher.INDUSTRY_KEYWORDS.keys()), days=1, max_news_per_source=5
            )
            llm_generator = LLMReportGenerator(config)
            report = llm_generator.enrich_multi_etf_report(
                report, top_etfs_detail, sector_ranking,
                international_data=international_data,
                news_data=news_data,
                research_summary=research_summary
            )
            # 追加择时信号汇总表（在 LLM 报告之后）
            from signal_summary import SignalAggregator, format_signal_table
            signal_section = "\n\n---\n\n# 五维择时信号\n\n"
            for r in top_etfs_detail[:5]:
                signal_result = SignalAggregator.aggregate(r, news_data)
                signal_section += f"### {r['name']} ({r['code']})\n\n"
                signal_section += format_signal_table(signal_result)
                signal_section += "\n"
            report += signal_section
            _log_timing("6.LLM决策报告", time.time() - t_llm)
        except Exception as e:
            print(f"  LLM 报告生成失败: {e}")
            import traceback
            traceback.print_exc()
    
    _log_timing("5.报告生成", time.time() - t4)
    
    # 保存报告（统一按日期文件夹组织）
    reports_dir = "/home/liudawei/github/daily_tracker_analytics/reports"
    day_dir = f"{reports_dir}/{report_date}"
    os.makedirs(day_dir, exist_ok=True)
    
    # 确定编号
    existing = [f for f in os.listdir(day_dir) if f.startswith("multi_etf_report_") and f.endswith(".md")]
    nums = []
    for f in existing:
        try:
            n = int(f.replace("multi_etf_report_", "").replace(".md", ""))
            nums.append(n)
        except ValueError:
            pass
    seq = max(nums) + 1 if nums else 1
    report_path = f"{day_dir}/multi_etf_report_{seq}.md"
    
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\n报告已保存: {report_path}")
    
    # 更新报告索引
    try:
        index_path = "/home/liudawei/github/daily_tracker_analytics/reports/REPORT_INDEX.md"
        # 从日期文件夹收集所有报告
        import glob
        date_dirs = sorted(
            [d for d in os.listdir(reports_dir) 
             if os.path.isdir(f"{reports_dir}/{d}") and len(d) == 10],
            reverse=True
        )
        
        size_str = f"{os.path.getsize(report_path)/1024:.0f} KB"
        lines = [
            "# 多板块 ETF 每日投资分析 — 报告目录\n\n",
            "> 自动生成，按日期倒序排列\n\n",
            "| 日期 | 报告 | 大小 |\n",
            "|------|------|------|\n",
        ]
        for date_dir in date_dirs:
            day_path = f"{reports_dir}/{date_dir}"
            files_in_folder = sorted(glob.glob(f"{day_path}/multi_etf_report_*.md"))
            if not files_in_folder:
                continue
            links = []
            total_sz = 0
            for f in files_in_folder:
                sz = os.path.getsize(f) / 1024
                total_sz += sz
                basename = os.path.basename(f)
                seq = basename.replace("multi_etf_report_", "").replace(".md", "")
                links.append(f"[#{seq}](./{date_dir}/{basename})")
            sz_str = f"{total_sz:.0f} KB"
            if any(f == report_path for f in files_in_folder):
                sz_str += " ← 最新"
            lines.append(f"| {date_dir} | {' + '.join(links)} | {sz_str} |\n")
        lines.append("\n---\n")
        lines.append("*报告统一存放在 `YYYY-MM-DD/` 日期文件夹中*  \n")
        with open(index_path, 'w', encoding='utf-8') as f:
            f.writelines(lines)
        print(f"  报告索引已更新: {index_path}")
    except Exception as e:
        print(f"  更新报告索引失败: {e}")
    
    # 7. 历史持久化与回测验证
    t6 = time.time()
    try:
        from analysis_history_tracker import AnalysisHistoryTracker
        history_tracker = AnalysisHistoryTracker()
        # 保存所有 ETF 快照
        for r in quick_results:
            history_tracker.save_daily_snapshot(r['code'], report_date, {
                'current_price': r['current_price'],
                'score': r['signals'].get('score'),
                'signal': r['signals'].get('overall_signal'),
                'position_size': r['position'].get('position_size') if isinstance(r['position'], dict) else None,
                'sector': r.get('sector'),
                'theme': r.get('theme'),
                'rsi14': r['signals'].get('rsi14'),
                'macd_status': r['signals'].get('macd_status'),
                'ma_status': r['signals'].get('ma_status'),
                'backtest': r['backtest']
            })
        # 保存重点 ETF 的预测
        for r in top_etfs_detail:
            if r.get('multi_model'):
                ensemble = r['multi_model'].get('ensemble', {})
                individual = r['multi_model'].get('individual', {})
                history_tracker.save_prediction(r['code'], report_date, {
                    'period_days': 1,
                    'ensemble': ensemble,
                    'individual': individual
                })
                # 5日预测
                if 'day_5' in ensemble or 'return_5d' in ensemble:
                    history_tracker.save_prediction(r['code'], report_date, {
                        'period_days': 5,
                        'ensemble': ensemble,
                        'individual': individual
                    })
        # 验证历史预测
        history_tracker.validate_all_pending_predictions()
        # 保存板块排名（行业轮动历史）
        if sector_ranking is not None and not sector_ranking.empty:
            history_tracker.save_sector_ranking(sector_ranking, report_date)
        # 获取板块轮动分析并追加到报告
        try:
            rotation = history_tracker.get_sector_rotation_analysis(days_lookback=7)
            if rotation:
                rotation_lines = ["\n\n---\n\n# 行业轮动速度\n\n",
                                  "| 板块 | 当前排名 | 排名变化 | 轮动速度 | 60天收益 |\n",
                                  "|------|----------|----------|----------|----------|\n"]
                # 按轮动速度排序（上升最快的在前）
                sorted_sectors = sorted(rotation.items(), key=lambda x: x[1]['rank_change'], reverse=True)
                for sector, data in sorted_sectors:
                    emoji = "🔺" if data['direction'] == '上升' else "🔻" if data['direction'] == '下降' else "➖"
                    rotation_lines.append(f"| {emoji} {sector} | #{data['current_rank']} | {data['rank_change']:+d} | {data['rotation_speed']:+.2f}/天 | {data['return_60d']:+.2f}% |\n")
                rotation_text = "".join(rotation_lines)
                # 追加到报告
                with open(report_path, 'a', encoding='utf-8') as f:
                    f.write(rotation_text)
                print("  行业轮动分析已追加")
        except Exception as e:
            print(f"  行业轮动分析失败: {e}")
        print("  分析历史已持久化")
    except Exception as e:
        print(f"  历史持久化失败: {e}")
        import traceback
        traceback.print_exc()
    _log_timing("7.历史持久化", time.time() - t6)
    
    # 8. 虚拟组合更新与绩效报告
    t8 = time.time()
    try:
        from portfolio_tracker import PortfolioTracker
        portfolio = PortfolioTracker(initial_cash=50000)
        # 更新组合（按信号自动调仓）
        pf_summary = portfolio.update(quick_results, report_date, sector_ranking)
        if pf_summary:
            print(f"  组合更新: 总值¥{pf_summary['total_value']:,.2f} 当日{pf_summary['daily_return']:+.2f}% 累计{pf_summary['cumulative_return']:+.2f}%")
        # 追加组合概况到报告
        pf_section = portfolio.generate_summary_section()
        if pf_section:
            with open(report_path, 'a', encoding='utf-8') as f:
                f.write(pf_section)
            print("  组合概况已追加到报告")
        # 生成 quantstats HTML 报告
        pf_html = portfolio.generate_performance_report()
        if pf_html:
            print(f"  📊 绩效报告: {pf_html}")
    except Exception as e:
        print(f"  组合更新失败: {e}")
        import traceback
        traceback.print_exc()
    _log_timing("8.组合更新", time.time() - t8)
    
    # 9. 微信推送
    t7 = time.time()
    if enable_wechat_push and config.features.get('wechat_push', True):
        try:
            from hermes_wechat_pusher import HermesWeChatPusher
            pusher = HermesWeChatPusher()
            summary = _generate_wechat_summary(quick_results, sector_ranking, top_etfs_detail, research_summary=research_summary)
            pusher.send_text_message(summary)
            print("微信推送已发送")
        except Exception as e:
            print(f"微信推送失败: {e}")
    _log_timing("8.微信推送", time.time() - t7)
    
    total_time = time.time() - start_time
    _log_timing("总耗时", total_time)
    
    print("\n" + "=" * 70)
    print("多板块 ETF 综合报告生成完成!")
    print(f"总耗时: {total_time:.2f}s")
    print(f"各阶段耗时: {timing}")
    print("=" * 70)
    
    return report_path


def _generate_wechat_summary(quick_results: List[Dict], sector_ranking: pd.DataFrame,
                              top_etfs_detail: List[Dict], research_summary: str = "") -> str:
    """生成微信推送摘要（控制在 1200 字以内）"""
    date = datetime.now().strftime('%Y-%m-%d')
    
    summary = f"【{date} 多板块 ETF 投资早报】\n\n"
    
    if not sector_ranking.empty:
        n = min(3, len(sector_ranking))
        top3 = sector_ranking.head(n)
        # 弱势板块从后往前取，避免与强势板块重复
        bottom_start = max(len(sector_ranking) - n, n)
        bottom3 = sector_ranking.iloc[bottom_start:]
        
        summary += "\n🏆 强势板块 TOP3:\n"
        for idx, row in top3.iterrows():
            summary += f"{idx+1}. {row['板块']}: 60天{row['60天收益']:+.2f}%, 评分{row['综合评分']:.1f}\n"
        
        if len(bottom3) > 0 and not bottom3.index.equals(top3.index):
            summary += "\n📉 弱势板块 BOT3:\n"
            total = len(sector_ranking)
            for i, (idx, row) in enumerate(bottom3.iterrows()):
                rank = total - len(bottom3) + 1 + i
                summary += f"{rank}. {row['板块']}: 60天{row['60天收益']:+.2f}%, 评分{row['综合评分']:.1f}\n"
    
    if top_etfs_detail:
        summary += "\n🔮 重点板块预测:\n"
        for r in top_etfs_detail[:3]:
            if r.get('multi_model'):
                ensemble = r['multi_model']['ensemble']
                summary += f"• {r['name']}: 1日{ensemble.get('return_1d', 0)*100:+.2f}% (置信度{ensemble.get('confidence', 0)*100:.1f}%)\n"
    
    summary += "\n⚠️ 风险提示: 本报告仅供参考，不构成投资建议。"
    
    # 加入研报要点
    if research_summary and len(research_summary) > 50:
        summary += "\n\n📄 机构观点:\n"
        # 简单提取第一行机构观点
        lines = [l.strip() for l in research_summary.split('\n') if l.strip()]
        for line in lines:
            if '研报数量' in line or '多数评级' in line or '平均目标价' in line:
                summary += line + '\n'
                break
    
    # 控制长度
    if len(summary) > 1200:
        summary = summary[:1150] + "\n... (摘要已截断)"
    
    return summary


def main():
    """主程序: 生成每日投资规划报告"""
    
    # 加载配置
    config = Config()
    
    # 如果配置中启用了多 ETF 分析，则生成多板块综合报告
    if config.features.get('multi_etf', True) and len(config.etfs) > 1:
        return run_multi_etf_daily_report(config)
    
    print("=" * 60)
    print(f"ETF 趋势跟踪系统启动")
    print(f"目标标的: {config.etf_name} ({config.etf_code})")
    print(f"报告日期: {datetime.now().strftime('%Y-%m-%d')}")
    print(f"配置来源: {config.config_path}")
    print("=" * 60)
    
    # 1. 获取数据
    print("\n正在获取历史数据...")
    fetcher = ETFDataFetcher(config)
    
    # 显示数据源状态
    source_status = fetcher.get_source_status()
    if source_status:
        print("\n  数据源状态:")
        for name, status in source_status.items():
            health = status['health_score']
            available = "✓" if status['available'] else "✗"
            print(f"    {available} {name:12s} 健康度: {health:5.1f}  成功: {status['success_count']}  失败: {status['error_count']}")
    
    df = fetcher.get_kline_data(config.etf_code, days=400)
    print(f"获取成功: {len(df)} 个交易日")
    
    # 获取资金流向数据
    fund_flow = fetcher.get_fund_flow_data(config.etf_code)
    print(f"  资金流向: {fund_flow}")
    
    # 2. 运行回测
    print("\n正在运行多周期回测...")
    engine = BacktestEngine(df)
    backtest_results = engine.run_all_backtests(config.backtest_periods)
    for result in backtest_results:
        print(f"  {result['period_days']}天回测完成: 收益 {result['total_return']:+.2f}%")
    
    # 3. 生成信号
    print("\n正在生成交易信号...")
    signal_gen = SignalGenerator(backtest_results, df)
    signals = signal_gen.generate_signals()
    position = signal_gen.calculate_position_sizing()
    print(f"  综合评分: {signals['score']}/100 ({signals['overall']})")
    
    # 4. 个股深度分析
    stock_analysis_results = []
    if config.features.get("individual_stocks", False):
        print("\n正在深度分析个股...")
        individual_stocks_config = config.config.get("individual_stocks", {})
        if individual_stocks_config.get("enabled", False):
            stocks = individual_stocks_config.get("stocks", [])
            for stock in stocks:
                try:
                    # 使用统一数据源管理器获取个股数据
                    try:
                        df_stock = fetcher.get_stock_kline_data(stock['code'], days=30)
                        if len(df_stock) > 0:
                            latest_price = float(df_stock.iloc[-1]['close'])
                            first_price = float(df_stock.iloc[0]['close'])
                            return_30d = (latest_price - first_price) / first_price * 100
                            stock_analysis_results.append({
                                "code": stock['code'],
                                "name": stock['name'],
                                "sector": stock.get('sector', '-'),
                                "reason": stock.get('reason', '-'),
                                "latest_price": round(latest_price, 2),
                                "total_return": round(return_30d, 2),
                                "trend": "上涨" if return_30d > 0 else "下跌"
                            })
                            print(f"  {stock['name']} 分析完成: 收益 {return_30d:+.2f}%")
                    except Exception as e:
                        print(f"  {stock['name']} 分析失败: {e}")
                except Exception as e:
                    print(f"  {stock['name']} 分析失败: {e}")
            
            # 生成对比图表
            if STOCK_ANALYZER_AVAILABLE and stock_analysis_results:
                print("\n正在生成个股对比图表...")
                try:
                    chart_path = f"/home/liudawei/github/daily_tracker_analytics/etf_tracker/reports/stock_comparison_{datetime.now().strftime('%Y%m%d')}.png"
                    visualizer = StockVisualizer()
                    visualizer.generate_comparison_chart(stock_analysis_results, chart_path)
                except Exception as e:
                    print(f"  图表生成失败: {e}")
    
    # 5. 趋势预测与策略优化
    predictions = None
    strategy = None
    from advanced_predictor import TrendPredictor, AlertSystem, AdvancedVisualizer
    print("\n正在进行趋势预测...")
    try:
        predictor = TrendPredictor()
        predictions = predictor.predict_trend(df, days=5)
        
        if 'day_1' in predictions:
            print(f"  1日预测: {predictions['day_1']['trend']} (置信度: {predictions['day_1']['confidence']:.0%})")
        if 'day_3' in predictions:
            print(f"  3日预测: {predictions['day_3']['trend']} (置信度: {predictions['day_3']['confidence']:.0%})")
        if 'day_5' in predictions:
            print(f"  5日预测: {predictions['day_5']['trend']} (置信度: {predictions['day_5']['confidence']:.0%})")
        
        # 策略优化
        strategy = predictor.optimize_strategy(df, predictions, signals)
        print(f"  策略优化: {strategy['action']} (评分: {strategy['total_score']:.2f})")
        
    except Exception as e:
        print(f"  预测失败: {e}")
        import traceback
        traceback.print_exc()
    
    # 6. 自动预警检查
    alerts = []
    if PREDICTOR_AVAILABLE and stock_analysis_results:
        print("\n正在检查预警...")
        alert_system = AlertSystem()
        for analysis in stock_analysis_results:
            if 'error' not in analysis:
                stock_alerts = alert_system.check_alerts(analysis)
                alerts.extend(stock_alerts)
        
        if alerts:
            print(f"  发现 {len(alerts)} 条预警:")
            for alert in alerts:
                level_text = "警告" if alert['level'] == 'warning' else "提示"
                print(f"    [{alert['type']}] {alert['message']}")
        else:
            print("  无预警")
    
    # 7. 生成高级可视化图表
    if PREDICTOR_AVAILABLE and stock_analysis_results:
        print("\n正在生成高级可视化图表...")
        visualizer = AdvancedVisualizer()
        
        # 为每只重点股票生成K线图和资金流向图
        for stock in config.config.get("individual_stocks", {}).get("stocks", [])[:5]:
            try:
                # K线图
                if STOCK_ANALYZER_AVAILABLE:
                    analyzer = StockAnalyzer()
                    df_stock = analyzer.get_stock_data(stock['code'], days=60)
                    df_stock = analyzer.calculate_indicators(df_stock)
                    
                    kline_path = f"/home/liudawei/github/daily_tracker_analytics/etf_tracker/reports/kline_{stock['code']}_{datetime.now().strftime('%Y%m%d')}.png"
                    visualizer.generate_kline_chart(stock['code'], stock['name'], df_stock, kline_path)
                
                # 资金流向图
                fundflow_path = f"/home/liudawei/github/daily_tracker_analytics/etf_tracker/reports/fundflow_{stock['code']}_{datetime.now().strftime('%Y%m%d')}.png"
                visualizer.generate_fund_flow_chart(stock['code'], stock['name'], fundflow_path)
                
            except Exception as e:
                print(f"  {stock['name']} 图表生成失败: {e}")
    
    # 8. 多模型交叉验证预测
    multi_model_results = None
    if MULTI_MODEL_AVAILABLE:
        print("\n正在运行多模型交叉验证预测...")
        try:
            # 尝试获取更多历史数据，如果失败则复用已有数据
            df_long = None
            try:
                import akshare as ak
                df_long = ak.fund_etf_hist_em(symbol=config.etf_code, period='daily', 
                                              start_date='20240101', adjust='qfq')
                df_long.columns = ['date', 'open', 'close', 'high', 'low', 'volume', 'amount', 
                                  'amplitude', 'pct_change', 'change', 'turnover']
            except Exception as e:
                print(f"  获取长期数据失败，复用当前数据: {e}")
            
            # 复用已有数据或加载本地历史数据
            if df_long is None or len(df_long) < 60:
                if os.path.exists(f"/home/liudawei/github/daily_tracker_analytics/etf_tracker/{config.etf_code}_history.csv"):
                    df_long = pd.read_csv(f"/home/liudawei/github/daily_tracker_analytics/etf_tracker/{config.etf_code}_history.csv")
                    print(f"  从本地加载历史数据: {len(df_long)} 条")
                else:
                    df_long = df.copy()
                    print(f"  复用当前数据: {len(df_long)} 条")
            
            predictor = MultiModelPredictor()
            training_results = predictor.train_all_models(df_long, target_col='target_1d')
            ensemble_result = predictor.ensemble_predict(df_long, days=5)
            
            multi_model_results = {
                'training_results': training_results,
                'ensemble_result': ensemble_result
            }
            
            # 打印汇总
            ensemble = ensemble_result['ensemble']
            print(f"  集成预测: {ensemble['return_1d']*100:+.2f}% (置信度: {ensemble['confidence']*100:.1f}%)")
            
            # 保存完整的多模型预测报告
            try:
                mm_report = AdvancedPredictionReport.generate_report(predictor, df_long, training_results, ensemble_result)
                mm_report_path = f"/home/liudawei/github/daily_tracker_analytics/etf_tracker/reports/multi_model_prediction_{datetime.now().strftime('%Y-%m-%d')}.md"
                with open(mm_report_path, 'w', encoding='utf-8') as f:
                    f.write(mm_report)
                print(f"  多模型预测报告已保存: {mm_report_path}")
            except Exception as e:
                print(f"  多模型报告生成失败: {e}")
            
        except Exception as e:
            print(f"  多模型预测失败: {e}")
            import traceback
            traceback.print_exc()
    
    # 9. 生成新增高级可视化图表
    if PREDICTOR_AVAILABLE and len(df) > 0:
        print("\n正在生成高级可视化图表...")
        visualizer = AdvancedVisualizer()
        report_date = datetime.now().strftime('%Y%m%d')
        
        # 生成股票预测趋势图 (前3只)
        for stock in (config.config.get("individual_stocks", {}).get("stocks", [])[:3]):
            try:
                df_stock = fetcher.get_stock_kline_data(stock['code'], days=60)
                if len(df_stock) > 0:
                    # 确保有必要的技术指标列
                    from advanced_predictor import TrendPredictor
                    tp = TrendPredictor()
                    df_stock = tp.add_technical_indicators(df_stock)
                    stock_pred = tp.predict_trend(df_stock, days=5)
                    pred_chart = f"/home/liudawei/github/daily_tracker_analytics/etf_tracker/reports/prediction_{stock['code']}_{report_date}.png"
                    visualizer.generate_prediction_chart(df_stock, stock_pred, stock['code'], stock['name'], pred_chart)
            except Exception as e:
                print(f"  {stock['name']} 预测图生成失败: {e}")
        
        # 生成多股票仪表盘
        try:
            dashboard_data = []
            for analysis in (stock_analysis_results or [])[:9]:
                if 'error' not in analysis:
                    dashboard_data.append(analysis)
            if dashboard_data:
                dashboard_path = f"/home/liudawei/github/daily_tracker_analytics/etf_tracker/reports/stock_dashboard_{report_date}.png"
                visualizer.generate_multi_stock_dashboard(dashboard_data, dashboard_path)
        except Exception as e:
            print(f"  仪表盘生成失败: {e}")
        
        # 生成板块资金流向图
        try:
            sector_data = {
                '稀土永磁': {'main_inflow': 50_000_000, 'sentiment_score': 0.5},
                '机器人': {'main_inflow': 30_000_000, 'sentiment_score': 0.3},
                '人工智能': {'main_inflow': -20_000_000, 'sentiment_score': -0.1},
                '芯片制造': {'main_inflow': 80_000_000, 'sentiment_score': 0.8},
                '存储行业': {'main_inflow': 10_000_000, 'sentiment_score': 0.1},
                '内存制造': {'main_inflow': -5_000_000, 'sentiment_score': -0.2}
            }
            sector_path = f"/home/liudawei/github/daily_tracker_analytics/etf_tracker/reports/sector_fund_flow_{report_date}.png"
            visualizer.generate_sector_fund_flow_chart(sector_data, sector_path)
        except Exception as e:
            print(f"  板块资金流向图生成失败: {e}")
    
    # 10. 生成报告
    print("\n正在生成投资规划报告...")
    report_gen = ReportGenerator(config)
    report = report_gen.generate_report(
        backtest_results, signals, position, df,
        stock_analysis=stock_analysis_results,
        predictions=predictions,
        strategy=strategy,
        alerts=alerts,
        multi_model_results=multi_model_results
    )
    
    # 9. 保存报告
    report_path = f"/home/liudawei/github/daily_tracker_analytics/etf_tracker/reports/report_{datetime.now().strftime('%Y-%m-%d')}.md"
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"报告已保存: {report_path}")
    
    # 10. 保存数据
    data_path = f"/home/liudawei/github/daily_tracker_analytics/etf_tracker/{config.etf_code}_history.csv"
    df.to_csv(data_path, index=False)
    print(f"数据已保存: {data_path}")
    
    print("\n" + "=" * 60)
    print("报告生成完成!")
    print("=" * 60)
    
    return report_path


if __name__ == "__main__":
    main()
