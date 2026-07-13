"""美股数据获取层（基于 yfinance）"""
from __future__ import annotations

import akshare as ak
import yfinance as yf
import pandas as pd
import os
from datetime import datetime, timedelta

US_WATCHLIST = [
    ('SPY', 'SPDR标普500ETF', 'US'),
    ('QQQ', '纳斯达克100ETF', 'US'),
    ('DIA', '道琼斯ETF', 'US'),
    ('IWM', '罗素2000ETF', 'US'),
    ('TSLA', '特斯拉', 'US'),
    ('NVDA', '英伟达', 'US'),
    ('AAPL', '苹果', 'US'),
    ('MSFT', '微软', 'US'),
    ('GOOGL', '谷歌A', 'US'),
    ('AMZN', '亚马逊', 'US'),
    ('META', 'Meta', 'US'),
    ('AMD', 'AMD', 'US'),
    ('NFLX', '奈飞', 'US'),
    ('BABA', '阿里巴巴', 'US'),
    ('PDD', '拼多多', 'US'),
]


def get_us_stock_data(ticker: str, period: str = '2y') -> pd.DataFrame:
    """获取美股数据：优先 akshare 新浪前复权，失败用 yfinance。"""
    # 1. akshare 新浪美股前复权
    try:
        df = ak.stock_us_daily(symbol=ticker, adjust='qfq')
        if df is not None and not df.empty and len(df) >= 20:
            df = df.rename(columns={
                'date': 'date',
                'open': 'open',
                'high': 'high',
                'low': 'low',
                'close': 'close',
                'volume': 'volume',
            })
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date')
            # 只保留最近2年
            cutoff = datetime.now() - timedelta(days=730)
            df = df[df.index >= cutoff]
            return df
    except Exception as e:
        print(f'  ⚠️ akshare 新浪美股失败: {e}')

    # 2. yfinance 备用（可能受网络限制）
    df = yf.download(ticker, period=period, interval='1d', progress=False, auto_adjust=True)
    if df is None or df.empty:
        raise ValueError(f'无法获取美股数据: {ticker}')

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [' '.join(col).strip() if isinstance(col, tuple) else col for col in df.columns.values]
        close_col = [c for c in df.columns if c.startswith('Close')]
        open_col = [c for c in df.columns if c.startswith('Open')]
        high_col = [c for c in df.columns if c.startswith('High')]
        low_col = [c for c in df.columns if c.startswith('Low')]
        vol_col = [c for c in df.columns if c.startswith('Volume')]
    else:
        close_col = ['Close']
        open_col = ['Open']
        high_col = ['High']
        low_col = ['Low']
        vol_col = ['Volume']

    df = pd.DataFrame({
        'open': df[open_col[0]] if open_col else df['Close'],
        'high': df[high_col[0]] if high_col else df['Close'],
        'low': df[low_col[0]] if low_col else df['Close'],
        'close': df[close_col[0]],
        'volume': df[vol_col[0]] if vol_col else 0,
    })
    df.index = pd.to_datetime(df.index)
    df = df.dropna()
    return df


def get_us_price(ticker: str) -> float:
    """获取最新美股收盘价。"""
    df = get_us_stock_data(ticker, period='5d')
    return float(df['close'].iloc[-1])


def is_us_ticker(ticker: str) -> bool:
    return bool(ticker) and ticker.isalpha() and len(ticker) <= 5
