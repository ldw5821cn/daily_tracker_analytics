"""
多 Agent 共享数据层 - 股票数据获取工具
智能数据源选择 + 偏差校验

数据源优先级：
  个股: Tushare(主) > 新浪(备) > yfinance(备)
  ETF:  akshare前复权(主) > 新浪(备) > yfinance(备)
  TickFlow: K线/实时行情/财务数据（配置后启用，做主源校验）
"""

import yfinance as yf
import pandas as pd
import numpy as np
import os
import json
import urllib.request
import urllib.parse
import warnings
import tushare as ts
from datetime import datetime, timedelta

# ── 优先从 .env 读 API key（gitignored，防泄漏）──
try:
    from dotenv import load_dotenv
    _env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), '.env')
    if os.path.exists(_env_path):
        load_dotenv(_env_path)
except ImportError:
    pass

warnings.filterwarnings('ignore')

# ── Tushare ──
_TUSHARE_TOKEN = os.environ.get('TUSHARE_TOKEN') or None
if not _TUSHARE_TOKEN:
    for _cfg_path in [
        os.path.expanduser('~/daily_tracker_analytics/etf_tracker/config.json'),
        os.path.expanduser('~/daily-_tracker_analytics/etf_tracker/config.json'),
        os.path.expanduser('~/github/daily_tracker_analytics/etf_tracker/config.json'),
    ]:
        if os.path.exists(_cfg_path):
            try:
                with open(_cfg_path) as _f:
                    _cfg = json.load(_f)
                for _ds in _cfg.get('data_sources', []):
                    if _ds.get('name') == 'tushare' and _ds.get('token'):
                        _TUSHARE_TOKEN = _ds['token']
                        break
            except Exception:
                pass
        if _TUSHARE_TOKEN:
            break

if _TUSHARE_TOKEN:
    ts.set_token(_TUSHARE_TOKEN)
TUSHARE_PRO = ts.pro_api()

# ── TickFlow ──
TICKFLOW_API_KEY = os.environ.get('TICKFLOW_API_KEY') or None
if not TICKFLOW_API_KEY:
    _tf_repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for _tf_config_path in [
        os.path.join(_tf_repo_root, 'etf_tracker', 'config.json'),
        os.path.expanduser('~/github/daily_tracker_analytics/etf_tracker/config.json'),
        os.path.expanduser('~/daily_tracker_analytics/etf_tracker/config.json'),
    ]:
        try:
            with open(_tf_config_path) as _f:
                _cfg = json.load(_f)
            for _ds in _cfg.get('data_sources', []):
                if _ds.get('name') == 'tickflow' and _ds.get('api_key'):
                    TICKFLOW_API_KEY = _ds['api_key']
                    break
        except Exception:
            pass

TICKFLOW_BASE = "https://api.tickflow.org"
TICKFLOW_AVAILABLE = bool(TICKFLOW_API_KEY)


def _tickflow_request(path, params=None):
    """通用 TickFlow API 请求"""
    if not TICKFLOW_API_KEY:
        return None
    try:
        if params:
            qs = urllib.parse.urlencode(params)
            url = f"{TICKFLOW_BASE}{path}?{qs}"
        else:
            url = f"{TICKFLOW_BASE}{path}"
        req = urllib.request.Request(url, headers={
            "x-api-key": TICKFLOW_API_KEY,
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 401:
            # API key 未激活，静默跳过
            return None
        return None
    except Exception:
        return None


# ══════════════════════════════════════════════════
# TickFlow 新API (v1)
# 文档: https://docs.tickflow.org
# 需去 tickflow.org 注册获取新API key
# ══════════════════════════════════════════════════


def tf_klines(ticker, period='1d', count=120):
    """TickFlow K线数据
    period: 1d(日) 1w(周) 1M(月) 1m(分) 5m 15m 30m 60m
    返回: DataFrame 或 None
    """
    if not TICKFLOW_AVAILABLE:
        return None
    prefix = "SH" if ticker.startswith(('6', '5')) else "SZ"
    symbol = f"{ticker}.{prefix}"
    data = _tickflow_request("/v1/klines", {"symbol": symbol, "period": period, "count": count})
    if not data or 'data' not in data:
        return None
    d = data['data']
    timestamps = d.get('timestamp', [])
    if not timestamps:
        return None

    records = []
    n = len(timestamps)
    for i in range(n):
        records.append({
            'date': datetime.fromtimestamp(timestamps[i] / 1000),
            'open': d.get('open', [0] * n)[i],
            'high': d.get('high', [0] * n)[i],
            'low': d.get('low', [0] * n)[i],
            'close': d.get('close', [0] * n)[i],
            'volume': int(d.get('volume', [0] * n)[i]),
            'amount': d.get('amount', [0] * n)[i],
        })
    df = pd.DataFrame(records)
    df.set_index('date', inplace=True)
    return df


def tf_quotes(tickers):
    """TickFlow 批量实时行情
    tickers: list of str (如 ['300002', '000001']) — 纯数字代码
    返回: dict {ticker: {price, change_pct, volume, turnover, ...}}
    """
    if not TICKFLOW_AVAILABLE or not tickers:
        return {}
    symbols = []
    for t in tickers:
        prefix = "SH" if t.startswith(('6', '5')) else "SZ"
        symbols.append(f"{t}.{prefix}")
    data = _tickflow_request("/v1/quotes", {"symbols": ','.join(symbols)})
    if not data or 'data' not in data:
        return {}
    result = {}
    for item in data['data']:
        symbol = item.get('symbol', '')
        local_code = symbol.replace('.SH', '').replace('.SZ', '').replace('.BJ', '')
        ext = item.get('ext', {})
        result[local_code] = {
            'price': item.get('last_price', 0),
            'change_pct': ext.get('change_pct', 0),
            'volume': item.get('volume', 0),
            'amount': item.get('amount', 0),
            'high': item.get('high', 0),
            'low': item.get('low', 0),
            'open': item.get('open', 0),
            'pre_close': item.get('prev_close', 0),
            'turnover_rate': ext.get('turnover_rate', 0),
            'amplitude': ext.get('amplitude', 0),
            'change_amount': ext.get('change_amount', 0),
        }
    return result


def tf_universe(universe_id='CN_Equity_A'):
    """获取 TickFlow 标的池
    CN_Equity_A: 沪深A股
    CN_ETF: 沪深ETF
    CN_Index: 沪深指数
    US_Equity: 美股
    HK_Equity: 港股
    """
    data = _tickflow_request(f"/v1/universes/{universe_id}")
    if data and 'data' in data:
        return data['data'].get('instruments', [])
    return []


def get_tickflow_fund_flow(ticker, days=5):
    """占位: TickFlow 移除了资金流向API，待反馈"""
    return None


def get_tickflow_realtime(ticker):
    """通过 TickFlow quotes 获取单只实时行情"""
    quotes = tf_quotes([ticker])
    return quotes.get(ticker)


# ══════════════════════════════════════════════════
# 原有数据源
# ══════════════════════════════════════════════════


def is_stock(ticker):
    """判断是否为个股（非ETF）"""
    return ticker.startswith(('0', '3', '6')) and not ticker.startswith(('5', '1'))


def is_etf(ticker):
    """判断是否为ETF"""
    return ticker.startswith(('5', '1'))


def _get_akshare_etf_data(ticker, start_date='20200101', end_date=None):
    """用 akshare 获取 ETF 前复权历史日线"""
    try:
        import akshare as ak
        if end_date is None:
            end_date = datetime.now().strftime('%Y%m%d')
        df = ak.fund_etf_hist_em(symbol=ticker, period='daily',
                                 start_date=start_date, end_date=end_date, adjust='qfq')
        if df is None or len(df) < 20:
            return None
        df = df.rename(columns={
            '日期': 'date', '开盘': 'open', '收盘': 'close',
            '最高': 'high', '最低': 'low', '成交量': 'volume',
        })
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        df = df[['open', 'high', 'low', 'close', 'volume']].astype(float)
        df.sort_index(inplace=True)
        return df
    except Exception:
        return None


def _get_tushare_data(ticker):
    """Tushare 个股日线（前复权，最准确）"""
    ts_code = f"{ticker}.SH" if ticker.startswith('6') else f"{ticker}.SZ"
    try:
        df = TUSHARE_PRO.daily(ts_code=ts_code)
        if df is None or len(df) < 20:
            return None
        df = df.rename(columns={
            'trade_date': 'date', 'open': 'open', 'high': 'high',
            'low': 'low', 'close': 'close', 'vol': 'volume',
        })
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        df.sort_index(inplace=False)
        df = df.iloc[::-1]
        df = df[['open', 'high', 'low', 'close', 'volume']]
        for c in df.columns:
            df[c] = df[c].astype(float)
        if pd.isna(df['close'].iloc[-1]):
            return None
        return df
    except Exception:
        return None


def _get_sina_data(ticker, datalen=500):
    """新浪财经（对ETF较准确）"""
    try:
        prefix = "sh" if ticker.startswith(('6', '5')) else "sz"
        url = (f"https://quotes.sina.cn/cn/api/jsonp_v2.php/"
               f"var%20_{ticker}_1_7_1=/CN_MarketData.getKLineData"
               f"?symbol={prefix}{ticker}&scale=240&ma=no&datalen={datalen}")
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode('utf-8')
        import re
        json_match = re.search(r'\[.*\]', text)
        if not json_match:
            return None
        data = json.loads(json_match.group())
        if len(data) < 20:
            return None
        rows = [{
            'date': item['day'], 'open': float(item['open']),
            'high': float(item['high']), 'low': float(item['low']),
            'close': float(item['close']), 'volume': float(item['volume']),
        } for item in data]
        df = pd.DataFrame(rows)
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        df.sort_index(inplace=True)
        return df
    except Exception:
        return None


def _get_yfinance_data(ticker, period="2y"):
    """yfinance（通用备用）"""
    try:
        yf_ticker = f"{ticker}.SS" if ticker.startswith(('6', '5')) else f"{ticker}.SZ"
        stock = yf.Ticker(yf_ticker)
        df = stock.history(period=period)
        if len(df) < 20:
            return None
        df = df.rename(columns={
            'Open': 'open', 'High': 'high', 'Low': 'low',
            'Close': 'close', 'Volume': 'volume',
        })
        df.index = pd.to_datetime(df.index)
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        df.index.name = 'date'
        return df
    except Exception:
        return None


def get_realtime_price(ticker):
    """腾讯证券实时价格"""
    try:
        prefix = "sh" if ticker.startswith(('6', '5')) else "sz"
        url = f"http://qt.gtimg.cn/q={prefix}{ticker}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode('gbk')
        parts = text.split('~')
        if len(parts) > 5:
            return {
                'name': parts[1],
                'price': float(parts[3]) if parts[3] else 0,
                'prev_close': float(parts[4]) if parts[4] else 0,
                'open': float(parts[5]) if parts[5] else 0,
                'volume': float(parts[6]) if parts[6] else 0,
                'high': float(parts[33]) if len(parts) > 33 and parts[33] else 0,
                'low': float(parts[34]) if len(parts) > 34 and parts[34] else 0,
                'change_pct': float(parts[32]) if len(parts) > 32 and parts[32] else 0,
                'turnover_rate': float(parts[38]) if len(parts) > 38 and parts[38] else 0,
                'pe': float(parts[39]) if len(parts) > 39 and parts[39] else 0,
                'mkt_cap': float(parts[45]) if len(parts) > 45 and parts[45] else 0,
            }
    except Exception:
        pass
    return None


def get_stock_data(ticker, period="2y", calibrate=True):
    """
    智能获取数据：自动选择最优数据源
    个股: Tushare(主) -> 新浪(备) -> yfinance(备)
    ETF:  akshare前复权(主) -> 新浪(备) -> yfinance(备)
    """
    df = None
    source = None
    info = {}

    if is_etf(ticker):
        df = _get_akshare_etf_data(ticker)
        if df is not None:
            source = "akshare_etf"
            print(f"  📡 数据源: akshare ETF 前复权 {len(df)}天")
        if df is None:
            df = _get_sina_data(ticker)
            if df is not None:
                source = "sina"
                print(f"  📡 数据源: 新浪财经 {len(df)}天")
        if df is None:
            df = _get_yfinance_data(ticker, period)
            if df is not None:
                source = "yfinance"
                print(f"  📡 数据源: yfinance {len(df)}天")
    else:
        df = _get_tushare_data(ticker)
        if df is not None:
            source = "tushare"
            print(f"  📡 数据源: Tushare {len(df)}天")
        if df is None:
            df = _get_sina_data(ticker)
            if df is not None:
                source = "sina"
                print(f"  📡 数据源: 新浪财经 {len(df)}天 (Tushare不可用)")
        if df is None:
            df = _get_yfinance_data(ticker, period)
            if df is not None:
                source = "yfinance"
                print(f"  📡 数据源: yfinance {len(df)}天")

    if df is None or len(df) < 20:
        raise ValueError(f"所有数据源均不可用: {ticker}")

    if calibrate and source is not None:
        _verify_data(ticker, df, source)

    return df, info


def _verify_data(ticker, primary_df, primary_source):
    """用辅助数据源校验主数据源"""
    cache_key = f"{ticker}_verify"
    if cache_key in CALIBRATION_CACHE:
        return

    verify_df = _get_yfinance_data(ticker)
    if verify_df is None and primary_source != "sina":
        verify_df = _get_sina_data(ticker)
    if verify_df is None:
        return

    common_dates = sorted(set(primary_df.index.date) & set(verify_df.index.date))
    if len(common_dates) < 10:
        return

    deviations = []
    for d in common_dates[-120:]:
        p = primary_df[primary_df.index.date == d]['close']
        v = verify_df[verify_df.index.date == d]['close']
        p_close = float(p.iloc[-1]) if len(p) > 0 else None
        v_close = float(v.iloc[-1]) if len(v) > 0 else None
        if p_close and v_close and v_close > 0:
            dev = abs(p_close / v_close - 1) * 100
            deviations.append(dev)

    if not deviations:
        return

    avg_dev = np.mean(deviations)
    max_dev = np.max(deviations)
    print(f"  🔍 数据校验: yfinance vs {primary_source}")
    print(f"     平均偏差: {avg_dev:.2f}% | 最大偏差: {max_dev:.2f}%")
    if avg_dev > 2.0:
        print(f"  ⚠️ 偏差较大!")

    last_p = float(primary_df['close'].iloc[-1])
    last_v_dates = verify_df[verify_df.index.date == primary_df.index[-1].date()]
    if len(last_v_dates) > 0:
        last_v = float(last_v_dates['close'].iloc[-1])
        today_dev = abs(last_p / last_v - 1) * 100
        if today_dev > 2.0:
            print(f"  ⚠️ 今日({primary_df.index[-1].date()})价格偏差 {today_dev:.2f}%")

    CALIBRATION_CACHE[cache_key] = {
        'primary_source': primary_source,
        'avg_deviation': round(avg_dev, 2),
        'max_deviation': round(max_dev, 2),
        'check_days': len(deviations),
    }


CALIBRATION_CACHE = {}


def calc_technical_indicators(df):
    """计算技术指标库"""
    df = df.copy()
    for ma in [5, 10, 20, 30, 60, 120, 250]:
        df[f'ma{ma}'] = df['close'].rolling(window=min(ma, len(df))).mean()
    ema12 = df['close'].ewm(span=12, adjust=False).mean()
    ema26 = df['close'].ewm(span=26, adjust=False).mean()
    df['macd_dif'] = ema12 - ema26
    df['macd_dea'] = df['macd_dif'].ewm(span=9, adjust=False).mean()
    df['macd_hist'] = 2 * (df['macd_dif'] - df['macd_dea'])
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['rsi_6'] = 100 - (100 / (1 + gain.rolling(6).mean() / loss.rolling(6).mean().replace(0, np.nan)))
    df['rsi_14'] = 100 - (100 / (1 + rs))
    df['rsi_24'] = 100 - (100 / (1 + gain.rolling(24).mean() / loss.rolling(24).mean().replace(0, np.nan)))
    low_9 = df['low'].rolling(9).min()
    high_9 = df['high'].rolling(9).max()
    rsv = (df['close'] - low_9) / (high_9 - low_9) * 100
    df['kdj_k'] = rsv.ewm(com=2, adjust=False).mean()
    df['kdj_d'] = df['kdj_k'].ewm(com=2, adjust=False).mean()
    df['kdj_j'] = 3 * df['kdj_k'] - 2 * df['kdj_d']
    df['boll_mid'] = df['close'].rolling(20).mean()
    boll_std = df['close'].rolling(20).std()
    df['boll_up'] = df['boll_mid'] + 2 * boll_std
    df['boll_down'] = df['boll_mid'] - 2 * boll_std
    df['vol_ma5'] = df['volume'].rolling(5).mean()
    df['vol_ma20'] = df['volume'].rolling(20).mean()
    df['vol_ratio'] = df['volume'] / df['vol_ma20'].replace(0, np.nan)
    for d in [5, 10, 20, 30, 60]:
        df[f'momentum_{d}d'] = df['close'].pct_change(d) * 100
    tr = pd.concat([
        df['high'] - df['low'],
        abs(df['high'] - df['close'].shift(1)),
        abs(df['low'] - df['close'].shift(1))
    ], axis=1).max(axis=1)
    df['atr_14'] = tr.rolling(14).mean()
    df['annual_vol_20d'] = df['close'].pct_change().rolling(20).std() * np.sqrt(252) * 100
    return df


def multi_period_backtest(df, periods=[30, 60, 90, 120, 200, 365]):
    """多周期回测"""
    results = []
    for days in periods:
        if len(df) < days:
            continue
        pdf = df.iloc[-days:]
        sp = float(pdf.iloc[0]['close'])
        ep = float(pdf.iloc[-1]['close'])
        ret = (ep / sp - 1) * 100
        max_dd = 0
        peak = sp
        for p in pdf['close']:
            p = float(p)
            if p > peak: peak = p
            dd = (p - peak) / peak * 100
            if dd < max_dd: max_dd = dd
        daily_ret = pdf['close'].pct_change().dropna()
        vol = float(daily_ret.std() * np.sqrt(252) * 100)
        sharpe = (daily_ret.mean() * 252 - 0.02) / (daily_ret.std() * np.sqrt(252)) if daily_ret.std() > 0 else 0
        win_rate = (daily_ret > 0).sum() / len(daily_ret) * 100
        results.append({
            'period_name': f'近{days}天',
            'days': days,
            'start_date': pdf.index[0].strftime('%Y-%m-%d'),
            'end_date': pdf.index[-1].strftime('%Y-%m-%d'),
            'start_price': round(sp, 2),
            'end_price': round(ep, 2),
            'high': round(float(pdf['high'].max()), 2),
            'low': round(float(pdf['low'].min()), 2),
            'total_return': round(ret, 2),
            'max_drawdown': round(max_dd, 2),
            'volatility': round(vol, 2),
            'sharpe': round(sharpe, 2),
            'win_rate': round(win_rate, 1),
        })
    return results


def get_dividend_info(ticker, name=""):
    """获取股息/估值信息"""
    try:
        if is_stock(ticker):
            ts_code = f"{ticker}.SH" if ticker.startswith('6') else f"{ticker}.SZ"
            df = TUSHARE_PRO.daily_basic(ts_code=ts_code, start_date='20260101')
            latest = df.iloc[0] if df is not None and len(df) > 0 else None
            if latest is not None:
                return {'pe_ratio': float(latest.get('pe', 0)), 'pb_ratio': float(latest.get('pb', 0))}
        yf_ticker = f"{ticker}.SS" if ticker.startswith(('6', '5')) else f"{ticker}.SZ"
        stock = yf.Ticker(yf_ticker)
        info = stock.info
        return {
            'dividend_yield': round(info.get('dividendYield', 0) * 100, 2) if info.get('dividendYield') else 0,
            'pe_ratio': info.get('trailingPE', info.get('forwardPE', 0)),
            'pb_ratio': info.get('priceToBook', 0),
        }
    except:
        return {}
