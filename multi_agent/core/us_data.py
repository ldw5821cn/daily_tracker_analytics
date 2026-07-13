"""美股数据获取层（基于 yfinance）"""
from __future__ import annotations

import akshare as ak
import yfinance as yf
import pandas as pd
import os
from datetime import datetime, timedelta

US_WATCHLIST = [
    # 大盘指数 ETF
    ('SPY', 'SPDR标普500ETF', 'US'),
    ('QQQ', '纳斯达克100ETF', 'US'),
    ('DIA', '道琼斯ETF', 'US'),
    ('IWM', '罗素2000ETF', 'US'),
    ('VTI', '全美股票ETF', 'US'),
    # 科技
    ('AAPL', '苹果', 'Technology'),
    ('MSFT', '微软', 'Technology'),
    ('GOOGL', '谷歌A', 'Communication Services'),
    ('AMZN', '亚马逊', 'Consumer Discretionary'),
    ('META', 'Meta', 'Communication Services'),
    ('TSLA', '特斯拉', 'Consumer Discretionary'),
    ('NVDA', '英伟达', 'Technology'),
    ('AMD', 'AMD', 'Technology'),
    ('INTC', '英特尔', 'Technology'),
    ('NFLX', '奈飞', 'Communication Services'),
    ('CRM', 'Salesforce', 'Technology'),
    ('ADBE', 'Adobe', 'Technology'),
    ('ORCL', '甲骨文', 'Technology'),
    ('IBM', 'IBM', 'Technology'),
    ('UBER', 'Uber', 'Technology'),
    # 金融
    ('JPM', '摩根大通', 'Financials'),
    ('BAC', '美国银行', 'Financials'),
    ('WFC', '富国银行', 'Financials'),
    ('GS', '高盛', 'Financials'),
    ('V', 'Visa', 'Financials'),
    ('MA', '万事达', 'Financials'),
    # ('BRK-B', '伯克希尔B', 'Financials'),
    # 医疗健康
    ('JNJ', '强生', 'Health Care'),
    ('PFE', '辉瑞', 'Health Care'),
    ('UNH', '联合健康', 'Health Care'),
    ('ABBV', '艾伯维', 'Health Care'),
    ('MRK', '默沙东', 'Health Care'),
    ('LLY', '礼来', 'Health Care'),
    # 消费
    ('WMT', '沃尔玛', 'Consumer Staples'),
    ('COST', '开市客', 'Consumer Staples'),
    ('KO', '可口可乐', 'Consumer Staples'),
    ('PEP', '百事', 'Consumer Staples'),
    ('MCD', '麦当劳', 'Consumer Discretionary'),
    ('HD', '家得宝', 'Consumer Discretionary'),
    ('NKE', '耐克', 'Consumer Discretionary'),
    ('DIS', '迪士尼', 'Communication Services'),
    # 能源与工业
    ('XOM', '埃克森美孚', 'Energy'),
    ('CVX', '雪佛龙', 'Energy'),
    ('GE', '通用电气', 'Industrials'),
    ('CAT', '卡特彼勒', 'Industrials'),
    ('BA', '波音', 'Industrials'),
    # 材料与房地产
    ('LIN', '林德', 'Materials'),
    ('AMT', '美国塔', 'Real Estate'),
    # 中概股
    ('BABA', '阿里巴巴', 'China-US'),
    ('PDD', '拼多多', 'China-US'),
    ('JD', '京东', 'China-US'),
    ('NTES', '网易', 'China-US'),
    ('BIDU', '百度', 'China-US'),
    ('TCEHY', '腾讯ADR', 'China-US'),
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


def get_us_price(ticker: str, as_of_date: str = None) -> float:
    """获取美股收盘价。as_of_date 指定日期（YYYY-MM-DD），返回该日或之前最近一个交易日的收盘价。"""
    df = get_us_stock_data(ticker, period='2y')
    if as_of_date:
        d = pd.to_datetime(as_of_date)
        # 取该日期或之前最近的交易日
        df = df[df.index <= d]
        if df.empty:
            raise ValueError(f'{ticker} 在 {as_of_date} 前无数据')
    return float(df['close'].iloc[-1])


def is_us_ticker(ticker: str) -> bool:
    return bool(ticker) and ticker.isalpha() and len(ticker) <= 5
