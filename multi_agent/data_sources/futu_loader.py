"""富途 OpenD 行情数据加载器。

接入说明：
- 富途 API 通过本地 OpenD 网关访问富途后台。OpenD 需要以牛牛号登录并订阅行情权限。
- 本 loader 只读行情数据（历史 K 线 + 实时快照），不做交易。
- 环境变量：
    FUTU_OPEND_HOST  (默认 127.0.0.1)
    FUTU_OPEND_PORT  (默认 11111)
    FUTU_SECURITY_FIRM (可选，默认 FUTU)

支持市场：
- A 股（SH/SZ）
- 港股（HK）
- 美股（US）
- 港股/美股期货、商品期货（视 OpenD 权限）

代码映射：
- 内部 000001.SZ / 600000.SH -> 富途 Market.SZ / Market.SH
- AAPL -> US.AAPL
- 00700.HK -> HK.00700
- 期货 M0 -> 转换为富途期货代码（需要品种映射）
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

# 复用 registry 已有的 helpers
from core.data_loader_registry import (
    BaseLoader,
    register_loader,
    run_with_retry,
    DEFAULT_TIMEOUT,
)


FUTU_HOST = os.environ.get('FUTU_OPEND_HOST', '127.0.0.1')
FUTU_PORT = int(os.environ.get('FUTU_OPEND_PORT', '11111'))
FUTU_SECURITY_FIRM = os.environ.get('FUTU_SECURITY_FIRM', 'FUTU')

# 商品期货主连代码 -> 富途期货代码（示例，按需扩展）
# 富途期货代码格式为 HK.{code} / US.{code} / SG.{code} 等，具体以品种所在市场为准
FUTURES_VAR_MAP: Dict[str, Tuple[str, str]] = {
    # 内盘商品期货在富途的支持有限；以下为主要国际期货示例
    'M0': ('US', 'ZSX25'),      # 豆粕期货示例（实际代码需按月份确认）
    'CU0': ('HK', 'CAU24'),     # 铜期货示例
    'AU0': ('HK', 'GCA24'),     # 黄金
    'AG0': ('HK', 'SIA24'),     # 白银
    'ZN0': ('HK', 'ZNA24'),     # 锌
    'NI0': ('HK', 'NIA24'),     # 镍
    'AL0': ('HK', 'AAU24'),     # 铝
    'RB0': ('SG', ''),          # 螺纹钢暂无，占位
    'HC0': ('SG', ''),          # 热卷
    'I0': ('SG', ''),           # 铁矿
    'J0': ('SG', ''),           # 焦炭
    'JM0': ('SG', ''),          # 焦煤
    'SC0': ('US', 'CLU24'),     # 原油对应 WTI
    'TA0': ('SG', ''),          # PTA
    'MA0': ('SG', ''),          # 甲醇
    'FG0': ('SG', ''),          # 玻璃
    'SA0': ('SG', ''),          # 纯碱
    'CF0': ('US', 'CTZ24'),     # 棉花对应美棉
    'OI0': ('US', ''),          # 菜油
    'RM0': ('US', ''),          # 菜粕
    'SR0': ('US', 'SBV24'),     # 白糖
    'LH0': ('US', 'HEZ24'),     # 生猪对应瘦肉猪
    'AP0': ('US', ''),          # 苹果
    'CJ0': ('US', ''),          # 红枣
    'EB0': ('US', ''),          # 苯乙烯
    'PF0': ('US', ''),          # 短纤
    'FU0': ('US', ''),          # 燃油
    'BU0': ('US', ''),          # 沥青
    'RU0': ('SG', ''),          # 橡胶
    'L0': ('US', ''),           # 塑料
    'PP0': ('US', ''),          # 聚丙烯
    'V0': ('US', ''),           # PVC
    'EG0': ('US', ''),          # 乙二醇
    'UR0': ('US', ''),          # 尿素
    'SM0': ('US', ''),          # 硅锰
    'SF0': ('US', ''),          # 硅铁
    'SP0': ('US', ''),          # 纸浆
}


def _import_futu():
    """延迟导入，避免无 OpenD 时破坏模块加载。"""
    import futu
    return futu


def _map_security_firm(firm_name: str):
    """将字符串券商标识映射为 futu.SecurityFirm 枚举。"""
    futu = _import_futu()
    mapping = {
        'FUTU': futu.SecurityFirm.FUTU,
        'FUTUSECURITIES': futu.SecurityFirm.FUTUSECURITIES,
        'FUTUINC': futu.SecurityFirm.FUTUINC,
        'FUTUSG': futu.SecurityFirm.FUTUSG,
        'FUTUAU': futu.SecurityFirm.FUTUAU,
        'FUTUCA': futu.SecurityFirm.FUTUCA,
        'FUTUMY': futu.SecurityFirm.FUTUMY,
        'FUTUJP': futu.SecurityFirm.FUTUJP,
    }
    return mapping.get(firm_name.upper(), futu.SecurityFirm.FUTU)


def _to_futu_symbol(code: str) -> Tuple[str, str]:
    """将内部代码转换为 (market, futu_code) 元组。

    market 为 futu.Market 枚举字符串名，用于构造 code 参数。
    futu_code 为传给 API 的完整代码如 'SZ.000001' / 'US.AAPL'。
    """
    futu = _import_futu()
    c = code.upper().strip()
    pure = c.split('.')[0]

    # 港股
    if c.endswith('.HK'):
        return 'HK', f'HK.{pure}'

    # 美股
    if c.endswith('.US') or (pure.isalpha() and len(pure) <= 5):
        return 'US', f'US.{pure}'

    # 商品期货主连（单零/双零结尾）
    if pure.endswith('0') and len(pure) in (2, 3, 4):
        mapping = FUTURES_VAR_MAP.get(code, None)
        if mapping:
            market, futu_ticker = mapping
            if futu_ticker:
                return market, f'{market}.{futu_ticker}'
        # 未映射时尝试按 SG 期货主连构造（大概率失败，留作兜底）
        return 'SG', f'SG.{pure[:-1]}main'

    # A 股 / 指数：按后缀或 6 位规则
    if c.endswith('.SH') or (pure.isdigit() and len(pure) == 6 and pure.startswith(('6', '5', '8', '9', '11', '13', '68', '88'))):
        return 'SH', f'SH.{pure}'
    if c.endswith('.SZ') or (pure.isdigit() and len(pure) == 6):
        return 'SZ', f'SZ.{pure}'

    # 兜底：按 A 股处理
    return 'SZ', f'SZ.{pure}'


def _to_futu_market_enum(market_str: str):
    futu = _import_futu()
    return getattr(futu.Market, market_str, futu.Market.NONE)


def _to_futu_kl_type(interval: str):
    futu = _import_futu()
    mapping = {
        '1m': futu.KLType.K_1M,
        '5m': futu.KLType.K_5M,
        '15m': futu.KLType.K_15M,
        '30m': futu.KLType.K_30M,
        '60m': futu.KLType.K_60M,
        '1D': futu.KLType.K_DAY,
        '1d': futu.KLType.K_DAY,
        '1W': futu.KLType.K_WEEK,
        '1w': futu.KLType.K_WEEK,
        '1M': futu.KLType.K_MON,
        '1Mo': futu.KLType.K_MON,
    }
    return mapping.get(interval, futu.KLType.K_DAY)


class FutuQuoteContext:
    """OpenQuoteContext 的上下文管理器包装，支持复用连接。"""

    _instance: Optional[Any] = None
    _ref_count: int = 0

    def __init__(self, host: Optional[str] = None, port: Optional[int] = None,
                 security_firm: Optional[str] = None):
        self.host = host or FUTU_HOST
        self.port = port or FUTU_PORT
        self.security_firm = security_firm or FUTU_SECURITY_FIRM
        self._ctx: Optional[Any] = None

    def __enter__(self):
        futu = _import_futu()
        self._ctx = futu.OpenQuoteContext(
            host=self.host,
            port=self.port,
            security_firm=_map_security_firm(self.security_firm),
        )
        FutuQuoteContext._ref_count += 1
        return self._ctx

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._ctx is not None:
            try:
                self._ctx.close()
            except Exception as e:
                logger.debug('close futu quote context failed: %s', e)
            FutuQuoteContext._ref_count -= 1
            self._ctx = None


def _normalize_history_kl(ret_code: int, data: pd.DataFrame, code: str,
                          start_date: str, end_date: str) -> Optional[pd.DataFrame]:
    """将富途返回的历史 K 线 DataFrame 标准化为 loader 通用列。"""
    if ret_code != 0 or data is None or data.empty:
        return None
    df = data.copy()
    # 富途返回列名可能为：code, time_key, open, close, high, low, volume, turnover, ...
    df.columns = [str(c).lower() for c in df.columns]
    if 'time_key' not in df.columns:
        logger.warning('futu history_kl missing time_key for %s', code)
        return None
    df['date'] = pd.to_datetime(df['time_key'])
    df.set_index('date', inplace=True)

    # 统一列名
    rename_map = {}
    for col in ['open', 'high', 'low', 'close', 'volume', 'turnover']:
        if col in df.columns:
            rename_map[col] = col
    df = df.rename(columns=rename_map)
    keep = [c for c in ['open', 'high', 'low', 'close', 'volume', 'turnover'] if c in df.columns]
    if not {'open', 'high', 'low', 'close'}.issubset(set(keep)):
        logger.warning('futu history_kl missing OHLC for %s', code)
        return None
    df = df[keep].astype(float)

    # 按日期过滤
    start_dt = pd.Timestamp(start_date)
    end_dt = pd.Timestamp(end_date)
    df = df[(df.index >= start_dt) & (df.index <= end_dt)]
    return df if not df.empty else None


def _normalize_snapshot(data: pd.DataFrame, code: str) -> Optional[pd.DataFrame]:
    """将富途返回的市场快照 DataFrame 标准化为 loader 通用单条 K 线。"""
    if data is None or data.empty:
        return None
    row = data.iloc[0]
    price = row.get('last_price') or row.get('cur_price')
    if price is None or pd.isna(price):
        return None
    open_ = row.get('open_price', price)
    high = row.get('high_price', price)
    low = row.get('low_price', price)
    volume = row.get('volume', 0)
    df = pd.DataFrame([{
        'open': float(open_),
        'high': float(high),
        'low': float(low),
        'close': float(price),
        'volume': float(volume),
    }])
    df.index = [pd.Timestamp.now().normalize()]
    df.index.name = 'date'
    return df


@register_loader('futu', ['a_share', 'index', 'hk_equity', 'us_equity', 'futures'], requires_auth=True)
class FutuLoader(BaseLoader):
    """富途 OpenD 行情加载器。"""

    def __init__(self, timeout: Optional[float] = None, host: Optional[str] = None,
                 port: Optional[int] = None, security_firm: Optional[str] = None):
        self.timeout = timeout or DEFAULT_TIMEOUT
        self.host = host or FUTU_HOST
        self.port = port or FUTU_PORT
        self.security_firm = security_firm or FUTU_SECURITY_FIRM

    def is_available(self) -> bool:
        """检查是否能连上 OpenD 并取到全局状态。"""
        try:
            with FutuQuoteContext(self.host, self.port, self.security_firm) as ctx:
                ret, data = ctx.get_global_state()
                return ret == 0
        except Exception as e:
            logger.debug('futu loader not available: %s', e)
            return False

    def fetch(self, codes: List[str], start_date: str, end_date: str, *,
              interval: str = '1D', fields: Optional[List[str]] = None) -> Dict[str, pd.DataFrame]:
        """批量拉取历史 K 线；若 start_date == end_date == 今天，则尝试用快照补实时价格。"""
        if not codes:
            return {}

        result: Dict[str, pd.DataFrame] = {}
        try:
            with FutuQuoteContext(self.host, self.port, self.security_firm) as ctx:
                for code in codes:
                    try:
                        df = self._fetch_one(ctx, code, start_date, end_date, interval)
                        if df is not None and not df.empty:
                            result[code] = df
                    except Exception as e:
                        logger.warning('futu loader %s failed: %s', code, e)
        except Exception as e:
            logger.warning('futu loader connect failed: %s', e)
        return result

    def _fetch_one(self, ctx: Any, code: str, start_date: str, end_date: str,
                   interval: str) -> Optional[pd.DataFrame]:
        market_str, futu_code = _to_futu_symbol(code)
        market_enum = _to_futu_market_enum(market_str)
        if market_enum is None or market_enum.value == 0:
            logger.warning('futu loader unknown market for %s', code)
            return None

        kl_type = _to_futu_kl_type(interval)

        # 富途 get_history_klines 参数
        # (code, start=None, end=None, ktype=KLType.K_DAY, autype=AuType.QFQ, fields=[KL_FIELD.ALL])
        futu = _import_futu()
        start_dt = pd.Timestamp(start_date)
        end_dt = pd.Timestamp(end_date)
        today = pd.Timestamp.now().normalize()

        # 对历史区间请求历史 K 线；对当日单条请求快照补充最新价
        if start_dt == today and end_dt == today:
            return self._fetch_snapshot(ctx, futu_code)

        def _fetch() -> Optional[pd.DataFrame]:
            ret, data, _ = ctx.get_history_klines(
                code=futu_code,
                start=start_date,
                end=end_date,
                ktype=kl_type,
                autype=futu.AuType.QFQ,
            )
            return _normalize_history_kl(ret, data, code, start_date, end_date)

        return run_with_retry(
            _fetch,
            label=f'futu {code}',
            timeout=self.timeout,
        )

    def _fetch_snapshot(self, ctx: Any, futu_code: str) -> Optional[pd.DataFrame]:
        def _fetch() -> Optional[pd.DataFrame]:
            ret, data = ctx.get_market_snapshot([futu_code])
            if ret != 0 or data is None or data.empty:
                return None
            return _normalize_snapshot(data, futu_code)

        return run_with_retry(
            _fetch,
            label=f'futu snapshot {futu_code}',
            timeout=self.timeout,
        )


def get_futu_realtime_prices(codes: List[str], *, host: Optional[str] = None,
                             port: Optional[int] = None,
                             security_firm: Optional[str] = None) -> Dict[str, float]:
    """便捷函数：获取实时最新价。"""
    loader = FutuLoader(host=host, port=port, security_firm=security_firm)
    today = datetime.now().strftime('%Y-%m-%d')
    dfs = loader.fetch(codes, today, today)
    return {code: float(df['close'].iloc[-1]) for code, df in dfs.items() if not df.empty and 'close' in df.columns}


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO)
    loader = FutuLoader()
    print('available:', loader.is_available())
    print('loaders:', [cls.name for cls in [FutuLoader]])
