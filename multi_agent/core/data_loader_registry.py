"""统一数据源加载器注册表（借鉴 Vibe-Trading loader registry 思路）。

目标：
- 自注册装饰器：新增数据源只需实现 loader 并加装饰器
- 市场级 fallback：A 股/期货/美股/ETF 各自 fallback 链
- 动态 is_available：检查依赖/密钥/网络可用性
- 与现有 core.data_layer 共存，逐步替换 get_stock_data
"""
from __future__ import annotations

import functools
import hashlib
import importlib
import inspect
import json
import logging
import os
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple, Type

import pandas as pd

warnings.filterwarnings('ignore')
logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
CACHE_DIR = Path(PROJECT_ROOT) / 'multi_agent' / 'data' / 'loader_cache'
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------
LOADER_REGISTRY: Dict[str, Type['BaseLoader']] = {}


def register_loader(name: str, markets: List[str], requires_auth: bool = False):
    """装饰器：注册数据源加载器。"""
    def wrapper(cls: Type['BaseLoader']) -> Type['BaseLoader']:
        cls.name = name
        cls.markets = markets
        cls.requires_auth = requires_auth
        LOADER_REGISTRY[name] = cls
        return cls
    return wrapper


class BaseLoader(Protocol):
    """加载器协议。"""
    name: str = ''
    markets: List[str] = []
    requires_auth: bool = False

    def is_available(self) -> bool:
        ...

    def fetch(self, codes: List[str], start_date: str, end_date: str, *,
              interval: str = '1D', fields: Optional[List[str]] = None) -> Dict[str, pd.DataFrame]:
        ...


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _fmt_date(d: Any) -> str:
    if d is None:
        return ''
    if isinstance(d, str):
        return d.replace('-', '')
    if isinstance(d, datetime):
        return d.strftime('%Y%m%d')
    return str(d)


def _cache_key(source: str, symbol: str, timeframe: str, start: str, end: str) -> str:
    s = f'{source}:{symbol}:{timeframe}:{start}:{end}'
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def _load_cache(source: str, symbol: str, timeframe: str, start: str, end: str) -> Optional[pd.DataFrame]:
    """仅加载 end_date < 今天 的缓存。"""
    try:
        end_dt = datetime.strptime(end, '%Y-%m-%d')
        if end_dt.date() >= datetime.now().date():
            return None
        key = _cache_key(source, symbol, timeframe, start, end)
        path = CACHE_DIR / f'{key}.parquet'
        if path.exists():
            return pd.read_parquet(path)
    except Exception as e:
        logger.debug('cache load failed: %s', e)
    return None


def _save_cache(df: pd.DataFrame, source: str, symbol: str, timeframe: str, start: str, end: str) -> None:
    try:
        end_dt = datetime.strptime(end, '%Y-%m-%d')
        if end_dt.date() >= datetime.now().date():
            return
        key = _cache_key(source, symbol, timeframe, start, end)
        path = CACHE_DIR / f'{key}.parquet'
        df.to_parquet(path)
    except Exception as e:
        logger.debug('cache save failed: %s', e)


def _normalize_a_share(code: str) -> str:
    """去掉 .SH/.SZ/.BJ 后缀，用于部分数据源。"""
    return code.split('.')[0]


def _is_a_share(code: str) -> bool:
    c = code.upper()
    pure = c.split('.')[0]
    # A 股/ETF：5/6 位数字，且不以 0 开头的非指数代码（0 开头为深圳主板也是 A 股）
    if not pure.isdigit():
        return False
    if len(pure) not in (5, 6):
        return False
    # A 股个股/ETF/指数 常见开头
    return pure.startswith(('0', '3', '6', '5', '1', '8', '9'))


def _is_etf(code: str) -> bool:
    c = code.split('.')[0]
    return len(c) == 6 and c.startswith(('51', '15', '56', '58', '50', '16', '18'))


def _validate_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """剔除脏 bar：high<low、非正价格、K线异常。"""
    if 'close' in df.columns:
        df = df[df['close'] > 0]
    if all(c in df.columns for c in ['high', 'low']):
        df = df[df['high'] >= df['low']]
    if all(c in df.columns for c in ['open', 'high', 'low', 'close']):
        df = df[(df['high'] >= df[['open', 'close']].max(axis=1)) &
                (df['low'] <= df[['open', 'close']].min(axis=1))]
    return df


# ---------------------------------------------------------------------------
# A 股 Loader：tencent
# ---------------------------------------------------------------------------
@register_loader('tencent', ['a_share'])
class TencentLoader:
    """腾讯财经 A 股日 K。"""

    def is_available(self) -> bool:
        try:
            import urllib.request
            return True
        except Exception:
            return False

    def fetch(self, codes: List[str], start_date: str, end_date: str, *,
              interval: str = '1D', fields: Optional[List[str]] = None) -> Dict[str, pd.DataFrame]:
        import urllib.request
        import json as _json
        result = {}
        start_dt = datetime.strptime(start_date, '%Y-%m-%d')
        end_dt = datetime.strptime(end_date, '%Y-%m-%d')
        for code in codes:
            try:
                pure = _normalize_a_share(code)
                if _is_index_code(code):
                    continue
                # 腾讯接口：code 前缀规则
                prefix = 'sh' if pure.startswith('6') or pure.startswith('5') or pure.startswith('8') or pure.startswith('9') or pure.startswith('11') or pure.startswith('13') else 'sz'
                if pure.startswith('68') or pure.startswith('30'):
                    prefix = 'sz' if pure.startswith('30') else 'sh'
                # 日 K：day, 周 K：week, 月 K：month
                tf = 'day' if interval in ('1D', '1d') else 'week' if interval in ('1W', '1w') else 'month'
                url = f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{pure},{tf},{start_date},{end_date},640,qfq'
                with urllib.request.urlopen(url, timeout=10) as resp:
                    text = resp.read().decode()
                data = _json.loads(text)
                key = f'{prefix}{pure}'
                # 腾讯复权字段可能是 day/qfqday/hfqday
                market_data = data.get('data', {}).get(key, {})
                for try_key in [f'qfq{tf}', f'hfq{tf}', tf]:
                    raw = market_data.get(try_key)
                    if raw:
                        break
                if not raw:
                    continue
                rows = []
                for item in raw:
                    # 腾讯格式：['date', 'open', 'close', 'high', 'low', 'volume']
                    rows.append({
                        'date': item[0],
                        'open': float(item[1]),
                        'close': float(item[2]),
                        'high': float(item[3]),
                        'low': float(item[4]),
                        'volume': float(item[5]) if item[5] else 0,
                    })
                df = pd.DataFrame(rows)
                df['date'] = pd.to_datetime(df['date'])
                df.set_index('date', inplace=True)
                df = df[(df.index >= pd.Timestamp(start_date)) & (df.index <= pd.Timestamp(end_date))]
                df = _validate_ohlc(df)
                if not df.empty:
                    result[code] = df
            except Exception as e:
                logger.warning('tencent loader %s failed: %s', code, e)
        return result


# ---------------------------------------------------------------------------
# A 股 Loader：mootdx
# ---------------------------------------------------------------------------
@register_loader('mootdx', ['a_share'])
class MootdxLoader:
    """通达信 mootdx。"""

    def is_available(self) -> bool:
        try:
            from mootdx.quotes import Quotes
            Quotes.factory(market='std', timeout=3)
            return True
        except Exception:
            return False

    def fetch(self, codes: List[str], start_date: str, end_date: str, *,
              interval: str = '1D', fields: Optional[List[str]] = None) -> Dict[str, pd.DataFrame]:
        from mootdx.quotes import Quotes
        result = {}
        client = Quotes.factory(market='std', timeout=10)
        freq_map = {'1D': 9, '1W': 5, '1M': 6, '1m': 8, '5m': 0, '15m': 1, '30m': 2, '60m': 3}
        freq = freq_map.get(interval, 9)
        for code in codes:
            try:
                pure = _normalize_a_share(code)
                # mootdx 对常见指数返回脏数据，跳过
                if pure in ('000001', '399006', '000300', '000905', '000016', '000688', '000852', '000009', '000010', '000015'):
                    continue
                df = client.bars(symbol=pure, frequency=freq, start=0, offset=800)
                if df is None or df.empty:
                    continue
                df = df.drop(
                    columns=['datetime', 'year', 'month', 'day', 'hour', 'minute', 'volume'],
                    errors='ignore',
                )
                df = df.reset_index()
                df = df.rename(
                    columns={
                        'datetime': 'date',
                        'open': 'open',
                        'close': 'close',
                        'high': 'high',
                        'low': 'low',
                        'vol': 'volume',
                    }
                )
                df['date'] = pd.to_datetime(df['date'], errors='coerce')
                df = df.dropna(subset=['date'])
                df.set_index('date', inplace=True)
                df = df[['open', 'high', 'low', 'close', 'volume']].astype(float)
                df = df[(df.index >= pd.Timestamp(start_date)) & (df.index <= pd.Timestamp(end_date))]
                df = _validate_ohlc(df)
                if not df.empty:
                    result[code] = df
            except Exception as e:
                logger.warning('mootdx loader %s failed: %s', code, e)
        return result


def _is_index_code(code: str) -> bool:
    """判断是否为 A 股主要指数代码（不含 .SH/.SZ 后缀）。"""
    pure = _normalize_a_share(code)
    # 常见指数前缀与代码段
    if pure in (
        '000001', '000002', '000003', '000004', '000005', '000006', '000007', '000008', '000009', '000010',
        '000016', '000050', '000043', '000300', '000303', '000688', '000850', '000903', '000904', '000905',
        '000906', '000852', '000819', '000827', '000836', '000820', '000010', '000015', '000018', '000020',
        '399001', '399002', '399003', '399004', '399005', '399006', '399007', '399008', '399009', '399010',
        '399011', '399012', '399013', '399015', '399016', '399017', '399018', '399019', '399020', '399021',
        '399106', '399107', '399108', '399101', '399102', '399103', '399104', '399105', '399330', '399333',
        '399341', '399344', '399346', '399348', '399351', '399353', '399357', '399361', '399364', '399366',
        '399367', '399369', '399370', '399371', '399372', '399373', '399376', '399377', '399379', '399381',
        '399382', '399384', '399385', '399386', '399387', '399388', '399389', '399390', '399391', '399392',
        '399393', '399394', '399395', '399396', '399397', '399398', '399399', '399905', '399997', '399998',
        '399999', '930050', '930300', '931580', '931643', '932000', '932006', '932066', '932100', '932200',
        '950090', '950106', '950111', '950305', '950330', '950660', '950886', '950996', '000932', '000933',
    ):
        return True
    if len(pure) == 6 and pure.startswith('88') and pure.isdigit():
        return True  # 同花顺行业指数
    return False


def _index_to_sina_symbol(pure: str) -> str:
    """将 A 股指数代码转换为新浪 symbol。"""
    if pure.startswith('88'):
        return pure
    if pure.startswith('399') or pure.startswith('3') or pure.startswith('2'):
        return f'sz{pure}'
    return f'sh{pure}'


# ---------------------------------------------------------------------------
# A 股 Loader：akshare
# ---------------------------------------------------------------------------
@register_loader('akshare', ['a_share', 'index', 'futures', 'fund', 'macro'])
class AkshareLoader:
    """AKShare 免费数据。"""

    def is_available(self) -> bool:
        try:
            import akshare
            return True
        except Exception:
            return False

    def fetch(self, codes: List[str], start_date: str, end_date: str, *,
              interval: str = '1D', fields: Optional[List[str]] = None) -> Dict[str, pd.DataFrame]:
        import akshare as ak
        result = {}
        for code in codes:
            try:
                pure = _normalize_a_share(code)
                if _is_index_code(code):
                    sina_symbol = _index_to_sina_symbol(pure)
                    df = ak.stock_zh_index_daily(symbol=sina_symbol)
                    if df is not None and not df.empty:
                        df.columns = [str(c).lower() for c in df.columns]
                        df['date'] = pd.to_datetime(df['date'])
                        df.set_index('date', inplace=True)
                        df = df[(df.index >= pd.Timestamp(start_date)) & (df.index <= pd.Timestamp(end_date))]
                        df = df.rename(columns={'amount': 'turnover'})
                        df = _validate_ohlc(df)
                        if not df.empty:
                            result[code] = df
                    continue
                if _is_etf(code):
                    df = ak.fund_etf_hist_em(symbol=pure, period='daily', start_date=start_date.replace('-', ''),
                                              end_date=end_date.replace('-', ''), adjust='qfq')
                else:
                    df = ak.stock_zh_a_hist(symbol=pure, period='daily', start_date=start_date.replace('-', ''),
                                            end_date=end_date.replace('-', ''), adjust='qfq')
                if df is None or df.empty:
                    continue
                df.columns = [str(c).lower() for c in df.columns]
                df['date'] = pd.to_datetime(df['date'])
                df.set_index('date', inplace=True)
                df = df.rename(columns={'amount': 'turnover'})
                df = _validate_ohlc(df)
                if not df.empty:
                    result[code] = df
            except Exception as e:
                logger.warning('akshare loader %s failed: %s', code, e)
        return result


# ---------------------------------------------------------------------------
# A 股 Loader：eastmoney
# ---------------------------------------------------------------------------
@register_loader('eastmoney', ['a_share', 'index', 'hk_equity', 'us_equity'])
class EastmoneyLoader:
    """东方财富。"""

    def is_available(self) -> bool:
        try:
            import akshare
            return True
        except Exception:
            return False

    def fetch(self, codes: List[str], start_date: str, end_date: str, *,
              interval: str = '1D', fields: Optional[List[str]] = None) -> Dict[str, pd.DataFrame]:
        import akshare as ak
        result = {}
        for code in codes:
            try:
                pure = _normalize_a_share(code)
                # 东方财富 ETF 也走 stock_zh_a_spot_em 不太对，这里用 akshare 的 eastmoney hist 接口
                if _is_etf(code):
                    df = ak.fund_etf_hist_em(symbol=pure, period='daily', start_date=start_date.replace('-', ''),
                                              end_date=end_date.replace('-', ''), adjust='qfq')
                else:
                    df = ak.stock_zh_a_hist(symbol=pure, period='daily', start_date=start_date.replace('-', ''),
                                            end_date=end_date.replace('-', ''), adjust='qfq')
                if df is None or df.empty:
                    continue
                df.columns = [str(c).lower() for c in df.columns]
                df['date'] = pd.to_datetime(df['date'])
                df.set_index('date', inplace=True)
                df = _validate_ohlc(df)
                if not df.empty:
                    result[code] = df
            except Exception as e:
                logger.warning('eastmoney loader %s failed: %s', code, e)
        return result


# ---------------------------------------------------------------------------
# 美股 Loader：yfinance
# ---------------------------------------------------------------------------
@register_loader('yfinance', ['us_equity', 'hk_equity'])
class YfinanceLoader:
    """yfinance（当前不稳定，放 fallback 链后面）。"""

    def is_available(self) -> bool:
        try:
            import yfinance
            return True
        except Exception:
            return False

    def fetch(self, codes: List[str], start_date: str, end_date: str, *,
              interval: str = '1D', fields: Optional[List[str]] = None) -> Dict[str, pd.DataFrame]:
        import yfinance as yf
        result = {}
        for code in codes:
            try:
                ticker = yf.Ticker(code)
                df = ticker.history(start=start_date, end=end_date, auto_adjust=False)
                if df is None or df.empty:
                    continue
                df = df.rename(columns=str.lower).rename(columns={'adj close': 'adj_close', 'stock splits': 'splits'})
                df = df[['open', 'high', 'low', 'close', 'volume']]
                df = _validate_ohlc(df)
                if not df.empty:
                    result[code] = df
            except Exception as e:
                logger.warning('yfinance loader %s failed: %s', code, e)
        return result


# ---------------------------------------------------------------------------
# 期货 Loader：akshare 期货
# ---------------------------------------------------------------------------
@register_loader('akshare_futures', ['futures'])
class AkshareFuturesLoader:
    """AKShare 期货数据。"""

    def is_available(self) -> bool:
        try:
            import akshare
            return True
        except Exception:
            return False

    def fetch(self, codes: List[str], start_date: str, end_date: str, *,
              interval: str = '1D', fields: Optional[List[str]] = None) -> Dict[str, pd.DataFrame]:
        import akshare as ak
        result = {}
        for code in codes:
            try:
                symbol = code  # 如 RM0, M0, RB0 直接作为新浪连续合约代码
                df = ak.futures_zh_daily_sina(symbol=symbol)
                if df is None or df.empty:
                    continue
                df.columns = [str(c).lower() for c in df.columns]
                df['date'] = pd.to_datetime(df['date'])
                df.set_index('date', inplace=True)
                df = df[(df.index >= pd.Timestamp(start_date)) & (df.index <= pd.Timestamp(end_date))]
                df = _validate_ohlc(df)
                if not df.empty:
                    result[code] = df
            except Exception as e:
                logger.warning('akshare futures loader %s failed: %s', code, e)
        return result


# 新浪期货 Loader 直接复用 akshare 的 sina 接口
@register_loader('sina_futures', ['futures'])
class SinaFuturesLoader:
    """新浪期货历史数据。"""

    def is_available(self) -> bool:
        try:
            import urllib.request
            return True
        except Exception:
            return False

    def fetch(self, codes: List[str], start_date: str, end_date: str, *,
              interval: str = '1D', fields: Optional[List[str]] = None) -> Dict[str, pd.DataFrame]:
        import urllib.request
        import ssl
        import re as _re
        import json as _json
        result = {}
        for code in codes:
            try:
                symbol = code if code.endswith('0') else f"{code}0"
                url = (f"https://stock.finance.sina.com.cn/futures/api/jsonp_v2.php/"
                       f"var_data_/InnerFuturesNewService.getDailyKLine?symbol={symbol}")
                req = urllib.request.Request(url, headers={
                    'User-Agent': 'Mozilla/5.0',
                    'Referer': 'https://finance.sina.com.cn',
                })
                ctx = ssl.create_default_context()
                with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                    text = resp.read().decode('utf-8')
                json_match = _re.search(r'var_data_\s*\(\s*(\[.*?\])\s*\)', text)
                if not json_match:
                    json_match = _re.search(r'\[.*?\]', text)
                if not json_match:
                    continue
                data = _json.loads(json_match.group(1) if json_match.groups() else json_match.group())
                if len(data) < 20:
                    continue
                rows = [{
                    'date': item['d'], 'open': float(item['o']),
                    'high': float(item['h']), 'low': float(item['l']),
                    'close': float(item['c']), 'volume': float(item['v']),
                } for item in data]
                df = pd.DataFrame(rows)
                df['date'] = pd.to_datetime(df['date'])
                df.set_index('date', inplace=True)
                df = df[(df.index >= pd.Timestamp(start_date)) & (df.index <= pd.Timestamp(end_date))]
                df = _validate_ohlc(df)
                if not df.empty:
                    result[code] = df
            except Exception as e:
                logger.warning('sina futures loader %s failed: %s', code, e)
        return result


# ---------------------------------------------------------------------------
# 本地 Loader
# ---------------------------------------------------------------------------
@register_loader('local', ['a_share', 'futures', 'fund', 'macro'])
class LocalLoader:
    """从本地缓存读取。"""

    def is_available(self) -> bool:
        return True

    def fetch(self, codes: List[str], start_date: str, end_date: str, *,
              interval: str = '1D', fields: Optional[List[str]] = None) -> Dict[str, pd.DataFrame]:
        result = {}
        for code in codes:
            for source in LOADER_REGISTRY:
                if source == 'local':
                    continue
                df = _load_cache(source, code, interval, start_date, end_date)
                if df is not None and not df.empty:
                    result[code] = df
                    break
        return result


# ---------------------------------------------------------------------------
# Fallback 链与解析
# ---------------------------------------------------------------------------
FALLBACK_CHAINS = {
    'a_share': ['mootdx', 'tencent', 'akshare', 'eastmoney', 'local'],
    'index': ['akshare', 'eastmoney', 'local'],
    'futures': ['akshare_futures', 'sina_futures', 'local'],
    'us_equity': ['yfinance', 'local'],
    'hk_equity': ['yfinance', 'local'],
    'fund': ['mootdx', 'akshare', 'eastmoney', 'local'],
    'macro': ['akshare', 'local'],
}


# ---------------------------------------------------------------------------
# Fallback 链与解析
# ---------------------------------------------------------------------------
def _detect_market(code: str) -> str:
    """根据代码识别市场。"""
    c = code.upper().strip()
    if c.endswith('.US') or (c.isalpha() and len(c) <= 5):
        return 'us_equity'
    if c.endswith('.HK'):
        return 'hk_equity'
    if _is_index_code(code):
        return 'index'
    pure = c.split('.')[0]
    # ETF：6 位数字且以 51/15/56/58/50/16/18 开头，归为 a_share，与个股共享 loader 和 fallback
    if _is_a_share(code):
        return 'a_share'
    if pure.endswith('0') and len(pure) in (2, 3, 4):
        return 'futures'
    return 'a_share'


def resolve_loader(market: str, source: Optional[str] = None) -> Optional[BaseLoader]:
    """解析市场对应的可用 loader。"""
    if source == 'auto':
        source = None
    candidates = [source] if source else FALLBACK_CHAINS.get(market, [])
    if source and source not in candidates:
        candidates = [source] + candidates
    for name in candidates:
        cls = LOADER_REGISTRY.get(name)
        if not cls:
            continue
        try:
            loader = cls()
            if loader.is_available():
                return loader
        except Exception as e:
            logger.debug('loader %s not available: %s', name, e)
    return None


# ---------------------------------------------------------------------------
# 统一 fetch 入口
# ---------------------------------------------------------------------------
def fetch_market_data(codes: List[str], start_date: str, end_date: str, *,
                      market: Optional[str] = None, source: Optional[str] = None,
                      interval: str = '1D', fields: Optional[List[str]] = None,
                      use_cache: bool = True) -> Dict[str, pd.DataFrame]:
    """统一获取市场数据，带 fallback。"""
    if not codes:
        return {}
    start_date = start_date.replace('/', '-')
    end_date = end_date.replace('/', '-')

    if market is None:
        markets = {}
        for code in codes:
            m = _detect_market(code)
            markets.setdefault(m, []).append(code)
    else:
        markets = {market: codes}

    result: Dict[str, pd.DataFrame] = {}
    for m, symbols in markets.items():
        candidate_names = []
        if source and source != 'auto':
            candidate_names = [source] + [n for n in FALLBACK_CHAINS.get(m, []) if n != source]
        else:
            candidate_names = FALLBACK_CHAINS.get(m, [])
        if not candidate_names:
            logger.warning('no fallback chain for market %s', m)
            continue

        for symbol in symbols:
            # 先尝试缓存（以第一个候选 loader 名义）
            if use_cache:
                df = _load_cache(candidate_names[0], symbol, interval, start_date, end_date)
                if df is not None and not df.empty:
                    result[symbol] = df
                    continue
            for name in candidate_names:
                cls = LOADER_REGISTRY.get(name)
                if not cls:
                    continue
                try:
                    loader = cls()
                    if not loader.is_available():
                        continue
                    fetched = loader.fetch([symbol], start_date, end_date, interval=interval, fields=fields)
                    df = fetched.get(symbol)
                    if df is not None and not df.empty:
                        _save_cache(df, name, symbol, interval, start_date, end_date)
                        result[symbol] = df
                        break
                except Exception as e:
                    logger.debug('loader %s failed for %s: %s', name, symbol, e)
    return result


def list_loaders() -> List[Dict[str, Any]]:
    """列出所有已注册 loader。"""
    return [
        {
            'name': cls.name,
            'markets': cls.markets,
            'requires_auth': cls.requires_auth,
            'available': cls().is_available(),
        }
        for cls in LOADER_REGISTRY.values()
    ]


if __name__ == '__main__':
    print('registered loaders:', list_loaders())
