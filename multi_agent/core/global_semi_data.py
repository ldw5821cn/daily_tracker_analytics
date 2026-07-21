"""全球半导体数据获取层

跟踪美日韩半导体指数与龙头个股，为 A 股半导体/芯片/机器人板块提供外部因子。
"""
from __future__ import annotations

import yfinance as yf
import pandas as pd
from datetime import datetime
from typing import Dict, Optional

US_SEMI_ETFS = {
    'SMH': '美国半导体ETF-VanEck',
    'SOXX': '美国半导体ETF-iShares',
}

US_SEMI_STOCKS = {
    'NVDA': '英伟达',
    'AMD': 'AMD',
    'INTC': '英特尔',
    'AVGO': '博通',
    'QCOM': '高通',
    'MU': '美光科技',
    'LRCX': '拉姆研究',
    'KLAC': '科磊',
    'AMAT': '应用材料',
}

JP_SEMI_STOCKS = {
    '8035.T': '东京电子',
    '6857.T': 'Advantest',
    '4063.T': '信越化学',
    '7735.T': 'Screen Holdings',
    '6146.T': 'DISCO',
}

KR_SEMI_STOCKS = {
    '005930.KS': '三星电子',
    '000660.KS': 'SK海力士',
    '042700.KS': '韩华半导体',
    '009150.KS': '三星电机',
}

ALL_TICKERS = {**US_SEMI_ETFS, **US_SEMI_STOCKS, **JP_SEMI_STOCKS, **KR_SEMI_STOCKS}


def _download(ticker: str, period: str = '90d') -> Optional[pd.DataFrame]:
    try:
        df = yf.download(ticker, period=period, interval='1d', progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [' '.join(col).strip() if isinstance(col, tuple) else col for col in df.columns.values]
            close_col = [c for c in df.columns if c.startswith('Close')][0]
            open_col = [c for c in df.columns if c.startswith('Open')][0]
            high_col = [c for c in df.columns if c.startswith('High')][0]
            low_col = [c for c in df.columns if c.startswith('Low')][0]
            vol_col = [c for c in df.columns if c.startswith('Volume')][0]
        else:
            close_col = 'Close'
            open_col = 'Open'
            high_col = 'High'
            low_col = 'Low'
            vol_col = 'Volume'
        df = pd.DataFrame({
            'open': df[open_col],
            'high': df[high_col],
            'low': df[low_col],
            'close': df[close_col],
            'volume': df[vol_col],
        })
        df.index = pd.to_datetime(df.index)
        df = df.dropna()
        if len(df) < 6:
            return None
        return df
    except Exception as e:
        print(f'  ⚠️ yfinance 下载 {ticker} 失败: {e}')
        return None


def _get_latest(ticker: str) -> Optional[Dict]:
    df = _download(ticker)
    if df is None or len(df) < 6:
        return None
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    close = float(latest['close'])
    ret1 = (close / float(prev['close']) - 1) * 100
    ret5 = (close / float(df['close'].iloc[-6]) - 1) * 100
    ret20 = (close / float(df['close'].iloc[-21]) - 1) * 100 if len(df) >= 21 else 0.0
    ma20 = df['close'].rolling(20).mean().iloc[-1]
    ma60 = df['close'].rolling(60).mean().iloc[-1] if len(df) >= 60 else None
    return {
        'ticker': ticker,
        'name': ALL_TICKERS.get(ticker, ticker),
        'close': round(close, 2),
        'date': str(df.index[-1].date()),
        'ret_1d': round(ret1, 2),
        'ret_5d': round(ret5, 2),
        'ret_20d': round(ret20, 2),
        'above_ma20': bool(close > ma20),
        'above_ma60': bool(close > ma60) if ma60 is not None else None,
    }


def get_global_semi_momentum() -> Dict:
    details = {}
    for t in ALL_TICKERS:
        d = _get_latest(t)
        if d:
            details[t] = d

    def _region_score(tickers: list):
        scores = []
        ret5s = []
        ret20s = []
        for t in tickers:
            d = details.get(t)
            if d is None:
                continue
            s = 50.0
            if d['above_ma20']:
                s += 8
            if d['above_ma60']:
                s += 8
            if d['ret_5d'] > 0:
                s += 4
            if d['ret_20d'] > 0:
                s += 4
            s = max(0, min(100, s))
            scores.append(s)
            ret5s.append(d['ret_5d'])
            ret20s.append(d['ret_20d'])
        if not scores:
            return {'score': 50, 'ret_5d_avg': 0, 'ret_20d_avg': 0, 'count': 0}
        return {
            'score': round(sum(scores) / len(scores), 1),
            'ret_5d_avg': round(sum(ret5s) / len(ret5s), 2),
            'ret_20d_avg': round(sum(ret20s) / len(ret20s), 2),
            'count': len(scores),
        }

    us = _region_score(list(US_SEMI_ETFS.keys()) + list(US_SEMI_STOCKS.keys()))
    jp = _region_score(list(JP_SEMI_STOCKS.keys()))
    kr = _region_score(list(KR_SEMI_STOCKS.keys()))

    if us['count'] == 0:
        composite = 50
    else:
        weights = []
        scores = []
        if us['count'] > 0:
            weights.append(0.5); scores.append(us['score'])
        if jp['count'] > 0:
            weights.append(0.25); scores.append(jp['score'])
        if kr['count'] > 0:
            weights.append(0.25); scores.append(kr['score'])
        total_w = sum(weights)
        composite = sum(s * w for s, w in zip(scores, weights)) / total_w if total_w > 0 else 50
    composite = round(max(0, min(100, composite)), 1)

    if composite >= 55:
        signal = 'bullish'
    elif composite <= 45:
        signal = 'bearish'
    else:
        signal = 'neutral'

    return {
        'us': us,
        'jp': jp,
        'kr': kr,
        'composite_score': composite,
        'composite_signal': signal,
        'details': details,
        'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


if __name__ == '__main__':
    r = get_global_semi_momentum()
    print(f"composite_score: {r['composite_score']}, signal: {r['composite_signal']}")
    print('US:', r['us'])
    print('JP:', r['jp'])
    print('KR:', r['kr'])
    print('details:', list(r['details'].items())[:3])
