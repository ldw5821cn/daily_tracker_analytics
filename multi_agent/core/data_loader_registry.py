"""统一数据源加载器注册表（借鉴 Vibe-Trading loader registry 思路）。

目标：
- 自注册装饰器：新增数据源只需实现 loader 并加装饰器
- 市场级 fallback：A 股/期货/美股/ETF 各自 fallback 链
- 动态 is_available：检查依赖/密钥/网络可用性
- 与现有 core.data_layer 共存，逐步替换 get_stock_data

参考 Vibe-Trading 改进点：
1. 缓存升级为内容寻址 + 版本号 + metadata.json（类似 base.py 的 loader cache）。
2. _validate_ohlc 与 Vibe-Trading validate_ohlc 对齐，检查所有价格 > 0 与 K 线包含关系。
3. fallback 链按 IP 封禁风险重排：公开免费接口在前，爬虫/限速源在后。
4. 每个 loader 增加 timeout 与简化版 retry_with_budget 重试。
5. 保持 fetch_market_data / resolve_loader / list_loaders 等对外接口不变。
"""
from __future__ import annotations

import functools
import hashlib
import importlib
import inspect
import json
import logging
import os
import time
import uuid
import warnings
from dataclasses import dataclass, field
from datetime import date as dt_date
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
# Retry / timeout helpers（Vibe-Trading retry_with_budget 的简化实现）
# ---------------------------------------------------------------------------
DEFAULT_TIMEOUT: float = 10.0
DEFAULT_MAX_RETRIES: int = 3
DEFAULT_BACKOFF: Tuple[float, ...] = (0.5, 1.5, 4.0)


class NoAvailableSourceError(Exception):
    """没有可用数据源时抛出。"""


def run_with_retry(
    fn: Callable[[], Any],
    *,
    label: str,
    timeout: Optional[float] = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    backoff: Tuple[float, ...] = DEFAULT_BACKOFF,
    transient: Tuple[Type[BaseException], ...] = (Exception,),
) -> Any:
    """在声明的瞬态异常上执行有超时预算的有限重试。

    与 Vibe-Trading retry_with_budget 思路一致但做了简化：
    - 用 wall-clock deadline 取代复杂的预算拆分；
    - 只在声明的 transient 异常上重试；
    -  backoff 被剩余时间裁剪，避免超时后仍空等。
    """
    timeout = timeout or DEFAULT_TIMEOUT
    deadline = time.monotonic() + timeout
    if len(backoff) < max_retries:
        raise ValueError(f"backoff has {len(backoff)} entries; need >= max_retries ({max_retries})")

    last_exc: Optional[BaseException] = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except transient as exc:
            last_exc = exc
            remaining = deadline - time.monotonic()
            if attempt == max_retries or remaining <= 0:
                raise TimeoutError(f"{label} failed after {attempt + 1} attempt(s): {exc}") from exc
            time.sleep(min(backoff[attempt], max(0.0, remaining)))
    raise TimeoutError(f"{label} failed: {last_exc}")


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
# 内容寻址缓存（借鉴 Vibe-Trading loader cache）
# ---------------------------------------------------------------------------
_LOADER_CACHE_VERSION: int = 1


def _fmt_date(d: Any) -> str:
    if d is None:
        return ''
    if isinstance(d, str):
        return d.replace('-', '')
    if isinstance(d, datetime):
        return d.strftime('%Y%m%d')
    return str(d)


def _normalize_cache_date(value: str) -> str:
    return pd.Timestamp(value).strftime('%Y-%m-%d')


def _sanitize_cache_segment(value: str) -> str:
    cleaned = ''.join(ch if ch.isalnum() or ch in {'-', '_'} else '_' for ch in value.strip().lower())
    return cleaned or 'unknown'


def _loader_cache_payload(
    *,
    source: str,
    symbol: str,
    timeframe: str,
    start_date: str,
    end_date: str,
    fields: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        'version': _LOADER_CACHE_VERSION,
        'source': str(source),
        'symbol': str(symbol),
        'timeframe': str(timeframe),
        'start_date': _normalize_cache_date(start_date),
        'end_date': _normalize_cache_date(end_date),
        'fields': [str(field) for field in (fields or ())],
    }


def _make_cache_key(
    *,
    source: str,
    symbol: str,
    timeframe: str,
    start_date: str,
    end_date: str,
    fields: Optional[List[str]] = None,
) -> str:
    """基于请求参数构建稳定的内容寻址 key。"""
    payload = _loader_cache_payload(
        source=source,
        symbol=symbol,
        timeframe=timeframe,
        start_date=start_date,
        end_date=end_date,
        fields=fields,
    )
    blob = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(blob).hexdigest()


def _loader_cache_path(
    *,
    source: str,
    symbol: str,
    timeframe: str,
    start_date: str,
    end_date: str,
    fields: Optional[List[str]] = None,
) -> Path:
    key = _make_cache_key(
        source=source,
        symbol=symbol,
        timeframe=timeframe,
        start_date=start_date,
        end_date=end_date,
        fields=fields,
    )
    source_dir = _sanitize_cache_segment(source)
    return CACHE_DIR / source_dir / f'{key}.parquet'


def _loader_cache_metadata_path(cache_path: Path) -> Path:
    return cache_path.with_suffix(cache_path.suffix + '.json')


def _cache_range_is_final(end_date: str) -> bool:
    """仅当 end_date 严格早于今天时才缓存，避免固化未收盘的 K 线。"""
    try:
        end = pd.Timestamp(end_date).normalize().date()
    except Exception:
        return False
    return end < dt_date.today()


def _frame_for_loader_cache(frame: pd.DataFrame) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    cache_frame = frame.copy()
    original_index_names = list(cache_frame.index.names)
    columns_name = cache_frame.columns.name
    index_dtypes = [
        str(cache_frame.index.get_level_values(level).dtype)
        for level in range(cache_frame.index.nlevels)
    ]
    index_columns = _cache_index_columns(cache_frame)
    cache_frame.index = cache_frame.index.set_names(index_columns)
    metadata: Dict[str, Any] = {
        'version': _LOADER_CACHE_VERSION,
        'index_columns': index_columns,
        'index_names': original_index_names,
        'columns_name': None if columns_name is None else str(columns_name),
        'index_dtypes': index_dtypes,
    }
    return cache_frame.reset_index(), metadata


def _cache_index_columns(frame: pd.DataFrame) -> List[str]:
    columns = {str(column) for column in frame.columns}
    used: set = set()
    index_columns: List[str] = []
    for pos, name in enumerate(frame.index.names):
        base = str(name) if name is not None else f'__loader_index_{pos}__'
        candidate = base
        suffix = 1
        while candidate in columns or candidate in used:
            candidate = f'{base}_{suffix}'
            suffix += 1
        index_columns.append(candidate)
        used.add(candidate)
    return index_columns


def _restore_cache_index_dtypes(frame: pd.DataFrame, index_dtypes: Any) -> pd.DataFrame:
    if not isinstance(index_dtypes, list) or frame.index.nlevels != len(index_dtypes):
        return frame
    try:
        if frame.index.nlevels == 1:
            frame.index = frame.index.astype(index_dtypes[0])
        else:
            for level, dtype in enumerate(index_dtypes):
                frame.index = frame.index.set_levels(
                    frame.index.levels[level].astype(dtype), level=level
                )
    except Exception:
        logger.debug('cache index dtype restore skipped: %s', index_dtypes)
    return frame


def _load_cache(
    source: str,
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    fields: Optional[List[str]] = None,
) -> Optional[pd.DataFrame]:
    """读取内容寻址缓存；损坏或版本不匹配时安全地返回 None。"""
    if not _cache_range_is_final(end):
        return None
    cache_path = _loader_cache_path(
        source=source,
        symbol=symbol,
        timeframe=timeframe,
        start_date=start,
        end_date=end,
        fields=fields,
    )
    if not cache_path.is_file():
        return None

    metadata_path = _loader_cache_metadata_path(cache_path)
    try:
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
    except Exception as exc:
        logger.warning('loader cache metadata read failed for %s: %s', cache_path.name, exc)
        return None

    if metadata.get('version') != _LOADER_CACHE_VERSION:
        return None

    try:
        frame = pd.read_parquet(cache_path)
    except Exception as exc:
        logger.warning('loader cache read failed for %s: %s', cache_path.name, exc)
        return None

    index_columns = metadata.get('index_columns') or []
    if index_columns:
        missing = [column for column in index_columns if column not in frame.columns]
        if missing:
            logger.warning('loader cache %s missing index column(s): %s', cache_path.name, missing)
            return None
        frame = frame.set_index(index_columns)
        frame.index.names = metadata.get('index_names') or index_columns
        frame = _restore_cache_index_dtypes(frame, metadata.get('index_dtypes'))
    frame.columns.name = metadata.get('columns_name')
    return frame


def _save_cache(
    df: pd.DataFrame,
    source: str,
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    fields: Optional[List[str]] = None,
) -> None:
    """写入内容寻址缓存；使用临时文件 + os.replace 保证原子性。"""
    if not _cache_range_is_final(end):
        return
    if not isinstance(df, pd.DataFrame) or df.empty:
        return

    cache_path = _loader_cache_path(
        source=source,
        symbol=symbol,
        timeframe=timeframe,
        start_date=start,
        end_date=end,
        fields=fields,
    )
    metadata_path = _loader_cache_metadata_path(cache_path)
    unique = f'{os.getpid()}.{uuid.uuid4().hex}'
    tmp_path = cache_path.with_name(f'{cache_path.name}.{unique}.tmp')
    tmp_metadata_path = metadata_path.with_name(f'{metadata_path.name}.{unique}.tmp')

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_frame, metadata = _frame_for_loader_cache(df)
        cache_frame.to_parquet(tmp_path, index=False)
        tmp_metadata_path.write_text(
            json.dumps(metadata, sort_keys=True, separators=(',', ':')),
            encoding='utf-8',
        )
        os.replace(tmp_path, cache_path)
        os.replace(tmp_metadata_path, metadata_path)
    except Exception as exc:
        logger.warning('loader cache write failed for %s: %s', cache_path.name, exc)
        for path in (tmp_path, tmp_metadata_path):
            try:
                path.unlink()
            except (FileNotFoundError, OSError):
                pass


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


def _validate_ohlc(df: pd.DataFrame, *, strategy: str = 'drop') -> pd.DataFrame:
    """剔除脏 bar：所有价格 > 0、high >= max(open, close)、low <= min(open, close)。

    与 Vibe-Trading validate_ohlc 对齐，作为加载器边界检查，避免脏数据进入下游。
    """
    required = ('open', 'high', 'low', 'close')
    if df.empty or not all(col in df.columns for col in required):
        return df

    open_, high, low, close = (df[c] for c in required)
    invalid = (
        (high < low)
        | (high < open_)
        | (high < close)
        | (low > open_)
        | (low > close)
        | (open_ <= 0)
        | (high <= 0)
        | (low <= 0)
        | (close <= 0)
    )
    n_invalid = int(invalid.sum())
    if n_invalid == 0:
        return df

    if strategy == 'raise':
        raise ValueError(f'{n_invalid} bar(s) violate OHLC invariants')
    if strategy == 'warn':
        logger.warning('OHLC validation: %d bar(s) violate invariants (kept)', n_invalid)
        return df
    logger.warning('OHLC validation: dropping %d invalid bar(s)', n_invalid)
    return df[~invalid]


# ---------------------------------------------------------------------------
# A 股 Loader：tencent
# ---------------------------------------------------------------------------
@register_loader('tencent', ['a_share'])
class TencentLoader:
    """腾讯财经 A 股日 K。"""

    def __init__(self, timeout: Optional[float] = None):
        self.timeout = timeout or DEFAULT_TIMEOUT

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
                df = self._fetch_one(code, start_date, end_date, interval)
                if df is not None and not df.empty:
                    result[code] = df
            except Exception as e:
                logger.warning('tencent loader %s failed: %s', code, e)
        return result

    def _fetch_one(self, code: str, start_date: str, end_date: str, interval: str) -> Optional[pd.DataFrame]:
        import urllib.request
        import json as _json

        def _fetch() -> Optional[pd.DataFrame]:
            pure = _normalize_a_share(code)
            if _is_index_code(code):
                return None
            prefix = 'sh' if pure.startswith(('6', '5', '8', '9', '11', '13')) else 'sz'
            if pure.startswith('68') or pure.startswith('30'):
                prefix = 'sz' if pure.startswith('30') else 'sh'
            tf = 'day' if interval in ('1D', '1d') else 'week' if interval in ('1W', '1w') else 'month'
            url = (
                f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param='
                f'{prefix}{pure},{tf},{start_date},{end_date},640,qfq'
            )
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                text = resp.read().decode()
            data = _json.loads(text)
            key = f'{prefix}{pure}'
            market_data = data.get('data', {}).get(key, {})
            raw = None
            for try_key in [f'qfq{tf}', f'hfq{tf}', tf]:
                raw = market_data.get(try_key)
                if raw:
                    break
            if not raw:
                return None
            rows = []
            for item in raw:
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
            return df if not df.empty else None

        return run_with_retry(
            _fetch,
            label=f'tencent {code}',
            timeout=self.timeout,
        )


# ---------------------------------------------------------------------------
# A 股 Loader：mootdx
# ---------------------------------------------------------------------------
@register_loader('mootdx', ['a_share'])
class MootdxLoader:
    """通达信 mootdx。"""

    def __init__(self, timeout: Optional[float] = None):
        self.timeout = timeout or DEFAULT_TIMEOUT

    def is_available(self) -> bool:
        try:
            from mootdx.quotes import Quotes
            Quotes.factory(market='std', timeout=3)
            return True
        except Exception:
            return False

    def fetch(self, codes: List[str], start_date: str, end_date: str, *,
              interval: str = '1D', fields: Optional[List[str]] = None) -> Dict[str, pd.DataFrame]:
        result = {}
        for code in codes:
            try:
                df = self._fetch_one(code, start_date, end_date, interval)
                if df is not None and not df.empty:
                    result[code] = df
            except Exception as e:
                logger.warning('mootdx loader %s failed: %s', code, e)
        return result

    def _fetch_one(self, code: str, start_date: str, end_date: str, interval: str) -> Optional[pd.DataFrame]:
        from mootdx.quotes import Quotes

        def _fetch() -> Optional[pd.DataFrame]:
            pure = _normalize_a_share(code)
            if pure in ('000001', '399006', '000300', '000905', '000016', '000688', '000852', '000009', '000010', '000015'):
                return None
            client = Quotes.factory(market='std', timeout=int(self.timeout))
            freq_map = {'1D': 9, '1W': 5, '1M': 6, '1m': 8, '5m': 0, '15m': 1, '30m': 2, '60m': 3}
            freq = freq_map.get(interval, 9)
            df = client.bars(symbol=pure, frequency=freq, start=0, offset=800)
            if df is None or df.empty:
                return None
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
            return df if not df.empty else None

        return run_with_retry(
            _fetch,
            label=f'mootdx {code}',
            timeout=self.timeout,
        )


def _is_index_code(code: str) -> bool:
    """判断是否为 A 股主要指数代码。

    规则：
    - 深圳指数：399/3 开头 6 位数字
    - 上海指数：000/950/930/931/932 开头且为明确指数代码
    - 同花顺行业指数：88 开头 6 位数字
    - 个股带 .SZ 后缀的 000XXX 不视为指数（如 000001.SZ 平安银行）
    """
    c = code.upper().strip()
    pure = _normalize_a_share(code)
    if not pure.isdigit() or len(pure) != 6:
        return False
    # 深圳指数（399/3 开头）
    if pure.startswith(('399', '30', '32')):
        return True
    # 同花顺行业指数（88 开头）
    if pure.startswith('88'):
        return True
    # 带 .SZ 后缀的 000 开头代码视为个股，不视为指数
    if c.endswith('.SZ') and pure.startswith('000'):
        return False
    # 上海常见指数代码（最小化集合，避免误伤个股）
    sh_index_codes = {
        '000001', '000002', '000003', '000004', '000005', '000006', '000007', '000008', '000009', '000010',
        '000015', '000016', '000017', '000018', '000020', '000043', '000050', '000133', '000134',
        '000300', '000303', '000903', '000904', '000905', '000906', '000852', '000820', '000827',
        '000836', '000688', '000850',
    }
    if pure in sh_index_codes:
        return True
    # 中证行业/风格指数（930/931/932/950 开头）
    if pure.startswith(('930', '931', '932', '950')):
        return True
    return False


def _index_to_sina_symbol(pure: str) -> str:
    """将 A 股指数代码转换为新浪 symbol。"""
    if pure.startswith('88'):
        return pure
    if pure.startswith(('399', '30', '32')):
        return f'sz{pure}'
    return f'sh{pure}'


# ---------------------------------------------------------------------------
# A 股 Loader：akshare
# ---------------------------------------------------------------------------
@register_loader('akshare', ['a_share', 'index', 'futures', 'fund', 'macro'])
class AkshareLoader:
    """AKShare 免费数据。"""

    def __init__(self, timeout: Optional[float] = None):
        self.timeout = timeout or DEFAULT_TIMEOUT

    def is_available(self) -> bool:
        try:
            import akshare
            return True
        except Exception:
            return False

    def fetch(self, codes: List[str], start_date: str, end_date: str, *,
              interval: str = '1D', fields: Optional[List[str]] = None) -> Dict[str, pd.DataFrame]:
        result = {}
        for code in codes:
            try:
                df = self._fetch_one(code, start_date, end_date)
                if df is not None and not df.empty:
                    result[code] = df
            except Exception as e:
                logger.warning('akshare loader %s failed: %s', code, e)
        return result

    def _fetch_one(self, code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        import akshare as ak

        def _fetch() -> Optional[pd.DataFrame]:
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
                    return df if not df.empty else None
                return None
            if _is_etf(code):
                df = ak.fund_etf_hist_em(
                    symbol=pure,
                    period='daily',
                    start_date=start_date.replace('-', ''),
                    end_date=end_date.replace('-', ''),
                    adjust='qfq',
                )
            else:
                df = ak.stock_zh_a_hist(
                    symbol=pure,
                    period='daily',
                    start_date=start_date.replace('-', ''),
                    end_date=end_date.replace('-', ''),
                    adjust='qfq',
                )
            if df is None or df.empty:
                return None
            df.columns = [str(c).lower() for c in df.columns]
            df['date'] = pd.to_datetime(df['date'])
            df.set_index('date', inplace=True)
            df = df.rename(columns={'amount': 'turnover'})
            df = _validate_ohlc(df)
            return df if not df.empty else None

        return run_with_retry(
            _fetch,
            label=f'akshare {code}',
            timeout=self.timeout,
        )


# ---------------------------------------------------------------------------
# A 股 Loader：eastmoney
# ---------------------------------------------------------------------------
@register_loader('eastmoney', ['a_share', 'index', 'hk_equity', 'us_equity'])
class EastmoneyLoader:
    """东方财富。"""

    def __init__(self, timeout: Optional[float] = None):
        self.timeout = timeout or DEFAULT_TIMEOUT

    def is_available(self) -> bool:
        try:
            import akshare
            return True
        except Exception:
            return False

    def fetch(self, codes: List[str], start_date: str, end_date: str, *,
              interval: str = '1D', fields: Optional[List[str]] = None) -> Dict[str, pd.DataFrame]:
        result = {}
        for code in codes:
            try:
                df = self._fetch_one(code, start_date, end_date)
                if df is not None and not df.empty:
                    result[code] = df
            except Exception as e:
                logger.warning('eastmoney loader %s failed: %s', code, e)
        return result

    def _fetch_one(self, code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        import akshare as ak

        def _fetch() -> Optional[pd.DataFrame]:
            pure = _normalize_a_share(code)
            if _is_etf(code):
                df = ak.fund_etf_hist_em(
                    symbol=pure,
                    period='daily',
                    start_date=start_date.replace('-', ''),
                    end_date=end_date.replace('-', ''),
                    adjust='qfq',
                )
            else:
                df = ak.stock_zh_a_hist(
                    symbol=pure,
                    period='daily',
                    start_date=start_date.replace('-', ''),
                    end_date=end_date.replace('-', ''),
                    adjust='qfq',
                )
            if df is None or df.empty:
                return None
            df.columns = [str(c).lower() for c in df.columns]
            df['date'] = pd.to_datetime(df['date'])
            df.set_index('date', inplace=True)
            df = _validate_ohlc(df)
            return df if not df.empty else None

        return run_with_retry(
            _fetch,
            label=f'eastmoney {code}',
            timeout=self.timeout,
        )


# ---------------------------------------------------------------------------
# 美股 / 港股 / A股 Loader：tickflow
# ---------------------------------------------------------------------------
@register_loader('tickflow', ['a_share', 'index', 'us_equity', 'hk_equity'], requires_auth=True)
class TickFlowLoader:
    """TickFlow REST API 日 K 加载器。"""

    _MIN_INTERVAL = 60.0 / 10  # 10 次/分钟，每次至少间隔 6 秒
    _last_request_time: float = 0.0

    def __init__(self, timeout: Optional[float] = None):
        self.timeout = timeout or DEFAULT_TIMEOUT

    def is_available(self) -> bool:
        try:
            import tickflow
            api_key = os.environ.get('TICKFLOW_API_KEY')
            if not api_key:
                return False
            client = tickflow.TickFlow(api_key=api_key, timeout=10)
            _ = client.klines.get('510300.SH', period='1d', count=2, as_dataframe=True)
            client.close()
            return True
        except Exception:
            return False

    @classmethod
    def _sleep_rate_limit(cls) -> None:
        """串行请求间按 10 次/分钟限速。"""
        elapsed = time.monotonic() - cls._last_request_time
        if elapsed < cls._MIN_INTERVAL:
            time.sleep(cls._MIN_INTERVAL - elapsed)
        cls._last_request_time = time.monotonic()

    @staticmethod
    def _normalize_tickflow_symbol(code: str) -> str:
        """将内部代码转换为 TickFlow 标准 symbol。"""
        c = code.upper().strip()
        # 美股：纯字母或已带 .US 的代码
        if c.endswith('.US'):
            return c
        alpha_only = ''.join(ch for ch in c if ch.isalpha())
        if alpha_only and alpha_only == c:
            return f'{c}.US'
        # 港股：已带 .HK 直接使用；5 位数字补零为 HKxxxxx
        if c.endswith('.HK'):
            return c
        if c.isdigit() and len(c) == 5:
            return f'HK{c.zfill(5)}'
        # A 股 / 指数：支持 000001.SZ / 510300.SH 等
        if '.' in c:
            return c
        # 纯 6 位数字，按规则加后缀
        if c.isdigit() and len(c) == 6:
            if c.startswith(('5', '6', '8', '9', '11', '13', '68')):
                return f'{c}.SH'
            return f'{c}.SZ'
        return code

    def fetch(self, codes: List[str], start_date: str, end_date: str, *,
              interval: str = '1D', fields: Optional[List[str]] = None) -> Dict[str, pd.DataFrame]:
        import tickflow
        result = {}
        api_key = os.environ.get('TICKFLOW_API_KEY')
        if not api_key:
            logger.warning('tickflow loader skipped: TICKFLOW_API_KEY not set')
            return result

        def _fetch_one(raw_code: str) -> Optional[pd.DataFrame]:
            symbol = self._normalize_tickflow_symbol(raw_code)
            self._sleep_rate_limit()
            client = tickflow.TickFlow(api_key=api_key, timeout=int(self.timeout))
            try:
                period = '1d' if interval in ('1D', '1d') else interval.lower()
                start_dt = pd.Timestamp(start_date)
                end_dt = pd.Timestamp(end_date)
                calendar_days = max((end_dt - start_dt).days + 5, 30)
                count = min(calendar_days * 2, 10000)
                df = client.klines.get(symbol, period=period, count=count, as_dataframe=True)
            finally:
                try:
                    client.close()
                except Exception:
                    pass
            if df is None or df.empty:
                return None
            df = df.rename(columns=str.lower)
            df['date'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('date', inplace=True)
            df = df[(df.index >= start_dt) & (df.index <= end_dt)]
            if 'amount' in df.columns:
                df['turnover'] = df['amount']
            keep = ['open', 'high', 'low', 'close', 'volume', 'turnover']
            available = [c for c in keep if c in df.columns]
            df = df[available].astype(float)
            df = _validate_ohlc(df)
            return df if not df.empty else None

        for raw_code in codes:
            try:
                df = run_with_retry(
                    lambda: _fetch_one(raw_code),
                    label=f'tickflow {raw_code}',
                    timeout=self.timeout,
                )
                if df is not None and not df.empty:
                    result[raw_code] = df
            except Exception as e:
                logger.warning('tickflow loader %s failed: %s', raw_code, e)
        return result


# ---------------------------------------------------------------------------
# 美股 Loader：yfinance
# ---------------------------------------------------------------------------
@register_loader('yfinance', ['us_equity', 'hk_equity'])
class YfinanceLoader:
    """yfinance。"""

    def __init__(self, timeout: Optional[float] = None):
        self.timeout = timeout or DEFAULT_TIMEOUT

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

                def _fetch() -> Optional[pd.DataFrame]:
                    df = ticker.history(start=start_date, end=end_date, auto_adjust=False)
                    if df is None or df.empty:
                        return None
                    df = df.rename(columns=str.lower).rename(
                        columns={'adj close': 'adj_close', 'stock splits': 'splits'}
                    )
                    df = df[['open', 'high', 'low', 'close', 'volume']]
                    df = _validate_ohlc(df)
                    return df if not df.empty else None

                df = run_with_retry(
                    _fetch,
                    label=f'yfinance {code}',
                    timeout=self.timeout,
                )
                if df is not None and not df.empty:
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

    def __init__(self, timeout: Optional[float] = None):
        self.timeout = timeout or DEFAULT_TIMEOUT

    def is_available(self) -> bool:
        try:
            import akshare
            return True
        except Exception:
            return False

    def fetch(self, codes: List[str], start_date: str, end_date: str, *,
              interval: str = '1D', fields: Optional[List[str]] = None) -> Dict[str, pd.DataFrame]:
        result = {}
        for code in codes:
            try:
                df = self._fetch_one(code, start_date, end_date)
                if df is not None and not df.empty:
                    result[code] = df
            except Exception as e:
                logger.warning('akshare futures loader %s failed: %s', code, e)
        return result

    def _fetch_one(self, code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        import akshare as ak

        def _fetch() -> Optional[pd.DataFrame]:
            symbol = code
            df = ak.futures_zh_daily_sina(symbol=symbol)
            if df is None or df.empty:
                return None
            df.columns = [str(c).lower() for c in df.columns]
            df['date'] = pd.to_datetime(df['date'])
            df.set_index('date', inplace=True)
            df = df[(df.index >= pd.Timestamp(start_date)) & (df.index <= pd.Timestamp(end_date))]
            df = _validate_ohlc(df)
            return df if not df.empty else None

        return run_with_retry(
            _fetch,
            label=f'akshare_futures {code}',
            timeout=self.timeout,
        )


# 新浪期货 Loader 直接复用 akshare 的 sina 接口
@register_loader('sina_futures', ['futures'])
class SinaFuturesLoader:
    """新浪期货历史数据。"""

    def __init__(self, timeout: Optional[float] = None):
        self.timeout = timeout or DEFAULT_TIMEOUT

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

                def _fetch() -> Optional[pd.DataFrame]:
                    symbol = code if code.endswith('0') else f'{code}0'
                    url = (
                        f'https://stock.finance.sina.com.cn/futures/api/jsonp_v2.php/'
                        f'var_data_/InnerFuturesNewService.getDailyKLine?symbol={symbol}'
                    )
                    req = urllib.request.Request(url, headers={
                        'User-Agent': 'Mozilla/5.0',
                        'Referer': 'https://finance.sina.com.cn',
                    })
                    ctx = ssl.create_default_context()
                    with urllib.request.urlopen(req, timeout=self.timeout, context=ctx) as resp:
                        text = resp.read().decode('utf-8')
                    json_match = _re.search(r'var_data_\s*\(\s*(\[.*?\])\s*\)', text)
                    if not json_match:
                        json_match = _re.search(r'\[.*?\]', text)
                    if not json_match:
                        return None
                    data = _json.loads(json_match.group(1) if json_match.groups() else json_match.group())
                    if len(data) < 20:
                        return None
                    rows = [{
                        'date': item['d'],
                        'open': float(item['o']),
                        'high': float(item['h']),
                        'low': float(item['l']),
                        'close': float(item['c']),
                        'volume': float(item['v']),
                    } for item in data]
                    df = pd.DataFrame(rows)
                    df['date'] = pd.to_datetime(df['date'])
                    df.set_index('date', inplace=True)
                    df = df[(df.index >= pd.Timestamp(start_date)) & (df.index <= pd.Timestamp(end_date))]
                    df = _validate_ohlc(df)
                    return df if not df.empty else None

                df = run_with_retry(
                    _fetch,
                    label=f'sina_futures {code}',
                    timeout=self.timeout,
                )
                if df is not None and not df.empty:
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
                df = _load_cache(source, code, interval, start_date, end_date, fields=fields)
                if df is not None and not df.empty:
                    result[code] = df
                    break
        return result


# ---------------------------------------------------------------------------
# Fallback 链与解析
# ---------------------------------------------------------------------------
# 按 IP 封禁风险排序：公开、低频、免登录的接口在前；爬虫密集/限速源在后。
FALLBACK_CHAINS = {
    # A 股：TickFlow（付费稳定）优先 -> 腾讯公开接口 -> 东方财富 -> mootdx -> akshare -> 本地缓存
    'a_share': ['tickflow', 'tencent', 'eastmoney', 'mootdx', 'akshare', 'local'],
    # 指数：TickFlow 优先 -> 东方财富 -> akshare -> 本地
    'index': ['tickflow', 'eastmoney', 'akshare', 'local'],
    # 期货：新浪期货公开接口优先，akshare 兜底
    'futures': ['sina_futures', 'akshare_futures', 'local'],
    # 美股/港股：TickFlow（付费稳定）优先 -> yfinance 兜底 -> 本地
    'us_equity': ['tickflow', 'yfinance', 'local'],
    'hk_equity': ['tickflow', 'yfinance', 'local'],
    # 基金：akshare 基金历史接口优先，东方财富次之，mootdx 兜底
    'fund': ['akshare', 'eastmoney', 'mootdx', 'local'],
    # 宏观：akshare 宏观数据优先
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
                df = _load_cache(candidate_names[0], symbol, interval, start_date, end_date, fields=fields)
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
                        _save_cache(df, name, symbol, interval, start_date, end_date, fields=fields)
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
