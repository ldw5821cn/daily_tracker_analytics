#!/usr/bin/env python3
"""基于历史 K 线回算每日特征快照 - Polars 高速版。

计算逻辑与 backfill_feature_snapshots.py 保持一致，但用 Polars 做整表向量化计算，
避免逐个 ticker 循环 + pandas；适合全量回刷。
"""
from __future__ import annotations

import os
import sys
import json
import argparse
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any

import polars as pl
import numpy as np
import pandas as pd

PR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PR / "multi_agent"))

from core.warehouse import get_warehouse_conn, save_features, init_warehouse_db


def _calc_technical_indicators_polars(df: pl.DataFrame) -> pl.DataFrame:
    """Polars 版技术指标，与 data_layer.calc_technical_indicators 对齐。"""
    # 确保按 ticker 内时间升序
    df = df.sort(['ticker', 'date'])

    # 累计行数：与 pandas 版 len(df[df.index <= date]) >= 60 对齐
    df = df.with_columns(pl.col('date').cum_count().over('ticker').alias('_history_len'))

    # MA（与 pandas rolling(window=min(ma, len(df))).mean() 等效：min_samples=1）
    for ma in [5, 10, 20, 30, 60, 120, 250]:
        df = df.with_columns(
            pl.col('close').rolling_mean(window_size=ma, min_samples=1).over('ticker').alias(f'ma{ma}')
        )

    # MACD
    df = df.with_columns(
        pl.col('close').ewm_mean(span=12, adjust=False).over('ticker').alias('ema12'),
        pl.col('close').ewm_mean(span=26, adjust=False).over('ticker').alias('ema26'),
    )
    df = df.with_columns(
        (pl.col('ema12') - pl.col('ema26')).alias('macd_dif'),
    )
    df = df.with_columns(
        pl.col('macd_dif').ewm_mean(span=9, adjust=False).over('ticker').alias('macd_dea'),
    )
    df = df.with_columns(
        (2 * (pl.col('macd_dif') - pl.col('macd_dea'))).alias('macd_hist'),
    )

    # RSI14
    delta = pl.col('close').diff().over('ticker')
    gain = pl.when(delta > 0).then(delta).otherwise(0.0)
    loss = pl.when(delta < 0).then(-delta).otherwise(0.0)
    avg_gain = gain.rolling_mean(window_size=14, min_samples=1).over('ticker')
    avg_loss = loss.rolling_mean(window_size=14, min_samples=1).over('ticker').replace(0, np.nan)
    rs = avg_gain / avg_loss
    df = df.with_columns(
        (100 - (100 / (1 + rs))).alias('rsi_14'),
    )

    # RSI6 / RSI24
    df = df.with_columns(
        (100 - (100 / (1 + gain.rolling_mean(window_size=6, min_samples=1).over('ticker') /
                       loss.rolling_mean(window_size=6, min_samples=1).over('ticker').replace(0, np.nan))))
        .alias('rsi_6'),
        (100 - (100 / (1 + gain.rolling_mean(window_size=24, min_samples=1).over('ticker') /
                       loss.rolling_mean(window_size=24, min_samples=1).over('ticker').replace(0, np.nan))))
        .alias('rsi_24'),
    )

    # KDJ
    low_9 = pl.col('low').rolling_min(window_size=9, min_samples=1).over('ticker')
    high_9 = pl.col('high').rolling_max(window_size=9, min_samples=1).over('ticker')
    rsv = (pl.col('close') - low_9) / (high_9 - low_9).replace(0, np.nan) * 100
    df = df.with_columns(
        rsv.ewm_mean(com=2, adjust=False).over('ticker').alias('kdj_k'),
    )
    df = df.with_columns(
        pl.col('kdj_k').ewm_mean(com=2, adjust=False).over('ticker').alias('kdj_d'),
    )
    df = df.with_columns(
        (3 * pl.col('kdj_k') - 2 * pl.col('kdj_d')).alias('kdj_j'),
    )

    # BOLL
    df = df.with_columns(
        pl.col('close').rolling_mean(window_size=20, min_samples=1).over('ticker').alias('boll_mid'),
        pl.col('close').rolling_std(window_size=20, min_samples=1).over('ticker').alias('boll_std'),
    )
    df = df.with_columns(
        (pl.col('boll_mid') + 2 * pl.col('boll_std')).alias('boll_up'),
        (pl.col('boll_mid') - 2 * pl.col('boll_std')).alias('boll_down'),
    )

    # Volume
    df = df.with_columns(
        pl.col('volume').rolling_mean(window_size=5, min_samples=1).over('ticker').alias('vol_ma5'),
        pl.col('volume').rolling_mean(window_size=20, min_samples=1).over('ticker').alias('vol_ma20'),
    )
    df = df.with_columns(
        (pl.col('volume') / pl.col('vol_ma20').replace(0, np.nan)).alias('vol_ratio'),
    )

    # Momentum
    for d in [5, 10, 20, 30, 60]:
        df = df.with_columns(
            ((pl.col('close') / pl.col('close').shift(d).over('ticker') - 1) * 100).alias(f'momentum_{d}d')
        )

    # ATR14
    prev_close = pl.col('close').shift(1).over('ticker')
    tr = pl.max_horizontal([
        pl.col('high') - pl.col('low'),
        (pl.col('high') - prev_close).abs(),
        (pl.col('low') - prev_close).abs(),
    ])
    df = df.with_columns(
        tr.rolling_mean(window_size=14, min_samples=1).over('ticker').alias('atr_14'),
    )

    # Annual vol
    df = df.with_columns(
        (pl.col('close').pct_change().rolling_std(window_size=20, min_samples=1).over('ticker') * math.sqrt(252) * 100)
        .alias('annual_vol_20d'),
    )

    # 过滤：至少 60 个交易日历史（与 pandas 版 len(df) >= 60 对齐）
    df = df.filter(pl.col('_history_len') >= 60)

    return df


def _build_score(row: pl.Struct) -> int:
    score = 50
    cp = row['close']
    ma60 = row['ma60']
    if ma60 is not None and not math.isnan(ma60) and ma60 > 0:
        ma60_dist = (cp / ma60 - 1) * 100
        if ma60_dist > 0:
            score += 8
        else:
            score -= 5
    macd_hist = row['macd_hist']
    if macd_hist is not None and not math.isnan(macd_hist):
        if macd_hist > 0:
            score += 6
        else:
            score -= 4
    rsi = row['rsi_14']
    if rsi is not None and not math.isnan(rsi):
        if 30 < rsi < 70:
            score += 3
        elif rsi < 30:
            score += 4
        else:
            score -= 3
    vol = row['annual_vol_20d']
    if vol is not None and not math.isnan(vol):
        if vol < 30:
            score += 3
        elif vol > 60:
            score -= 2
    return max(0, min(100, score))


def _build_snapshot_struct(row: Dict[str, Any]) -> Dict[str, Any]:
    cp = row['close']
    m5 = row.get('momentum_5d')
    m20 = row.get('momentum_20d')
    if m5 is None or math.isnan(m5):
        m5 = 0.0
    if m20 is None or math.isnan(m20):
        m20 = 0.0
    avg_daily = (m5 / 5 + m20 / 20) / 2 if m5 or m20 else 0.0
    score = _build_score(row)
    return {
        'date': row['date'],
        'ticker': row['ticker'],
        'category': row.get('category') or '个股',
        'features': {
            'close': round(cp, 3) if cp is not None and not math.isnan(cp) else None,
            'ma5': _f(row.get('ma5')),
            'ma20': _f(row.get('ma20')),
            'ma60': _f(row.get('ma60')),
            'macd_hist': _f(row.get('macd_hist')),
            'rsi_14': _f(row.get('rsi_14')),
            'boll_up': _f(row.get('boll_up')),
            'boll_down': _f(row.get('boll_down')),
            'momentum_5d': round(m5, 3),
            'momentum_20d': round(m20, 3),
            'avg_daily_return': round(avg_daily, 4),
            'annual_vol_20d': round(row.get('annual_vol_20d') or 0, 2),
        },
        'score': round(score, 1),
        'signal': 'bullish' if score >= 58 else 'bearish' if score <= 42 else 'neutral',
        'confidence': round(max(0.5, min(0.95, 0.5 + abs(score - 50) / 50 * 0.45)), 2),
        'source': 'tech_snapshot_backfill_polars',
    }


def _f(v):
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    return float(v)


def backfill_features_polars(start: str, end: str, tickers: List[str] = None, batch_size: int = 10000):
    init_warehouse_db()
    conn = get_warehouse_conn()
    try:
        # 获取目标 ticker/date
        if not tickers:
            rows = conn.execute(
                "SELECT DISTINCT ticker, category FROM daily_bar WHERE date BETWEEN ? AND ?",
                (start, end)
            ).fetchall()
            ticker_map = {r['ticker']: r['category'] for r in rows}
            tickers = list(ticker_map.keys())
        else:
            ticker_map = {t: None for t in tickers}

        dates = pl.date_range(start=pl.date(int(start[:4]), int(start[5:7]), int(start[8:10])),
                              end=pl.date(int(end[:4]), int(end[5:7]), int(end[8:10])),
                              interval='1d', eager=True).to_list()
        # 只保留工作日（与 pandas bdate_range 近似）
        dates = [d.strftime('%Y-%m-%d') for d in dates if d.weekday() < 5]
        print(f"[polars] {len(tickers)} 个标的，{len(dates)} 个交易日")

        # 批量读入所有 bar（用 pandas 读 + Polars 计算，避免 pl.read_database 参数绑定兼容性问题）
        # 同时按 ticker/date 去重：保留 category 非空/最大的一条（'US' > 'us' 空）
        placeholders = ','.join('?' * len(tickers))
        query = f"""
            SELECT date, ticker, open, high, low, close, volume, MAX(COALESCE(category,'')) as category
            FROM daily_bar
            WHERE ticker IN ({placeholders})
            GROUP BY ticker, date
            ORDER BY ticker, date
        """
        pdf = pd.read_sql_query(query, conn, params=tickers)
        pdf['category'] = pdf['category'].replace('', None)
        # 与 pandas 版对齐：先剔除无效 close 行，避免后续前向填充取到 NaN 行
        num_cols = ['open', 'high', 'low', 'close', 'volume']
        for c in num_cols:
            pdf[c] = pd.to_numeric(pdf[c], errors='coerce')
        pdf = pdf.dropna(subset=['close'])
        pdf = pdf[pdf['close'] > 0]
        if pdf.empty:
            print('[polars] 无 bar 数据')
            return
        lf = pl.from_pandas(pdf)
        lf = lf.with_columns(pl.col('date').str.to_date('%Y-%m-%d'))
        lf = lf.cast({'open': pl.Float64, 'high': pl.Float64, 'low': pl.Float64,
                      'close': pl.Float64, 'volume': pl.Float64})

        # 技术指标（Polars 向量化）
        df = _calc_technical_indicators_polars(lf)

        # 转回 pandas 做与原版一致的日期前向填充逻辑
        pdf = df.to_pandas()[['ticker', 'date', 'category', 'close', 'ma5', 'ma20', 'ma60', 'macd_hist',
                              'rsi_14', 'boll_up', 'boll_down', 'momentum_5d',
                              'momentum_20d', 'annual_vol_20d', '_history_len']]
        pdf = pdf.sort_values(['ticker', 'date']).reset_index(drop=True)
        pdf['date'] = pd.to_datetime(pdf['date']).dt.strftime('%Y-%m-%d')

        dates = pd.bdate_range(start=start, end=end).strftime('%Y-%m-%d').tolist()

        # 生成每个标的在每个工作日的快照（日期<=d 的前向填充，与 pandas 版行为一致）
        all_snaps = []
        for ticker, group in pdf.groupby('ticker'):
            group = group.reset_index(drop=True)
            cat = group['category'].iloc[-1] if not group['category'].isna().all() else None
            for d in dates:
                sub = group[group['date'] <= d]
                if len(sub) < 60:
                    continue
                latest = sub.iloc[-1]
                cp = float(latest['close'])
                if cp <= 0 or pd.isna(cp):
                    continue
                score = 50
                ma60 = latest['ma60']
                if pd.notna(ma60):
                    ma60_dist = (cp / float(ma60) - 1) * 100
                    if ma60_dist > 0:
                        score += 8
                    else:
                        score -= 5
                macd_hist = latest['macd_hist']
                if pd.notna(macd_hist):
                    if macd_hist > 0:
                        score += 6
                    else:
                        score -= 4
                rsi = latest['rsi_14']
                if pd.notna(rsi):
                    if 30 < rsi < 70:
                        score += 3
                    elif rsi < 30:
                        score += 4
                    else:
                        score -= 3
                vol = float(latest['annual_vol_20d']) if pd.notna(latest['annual_vol_20d']) else 0
                if vol < 30:
                    score += 3
                elif vol > 60:
                    score -= 2
                score = max(0, min(100, score))

                m5 = float(latest['momentum_5d']) if pd.notna(latest['momentum_5d']) else 0
                m20 = float(latest['momentum_20d']) if pd.notna(latest['momentum_20d']) else 0
                avg_daily = (m5 / 5 + m20 / 20) / 2 if m5 or m20 else 0

                all_snaps.append({
                    'date': d,
                    'ticker': ticker,
                    'category': cat or '个股',
                    'features': {
                        'close': round(cp, 3),
                        'ma5': float(latest['ma5']) if pd.notna(latest['ma5']) else None,
                        'ma20': float(latest['ma20']) if pd.notna(latest['ma20']) else None,
                        'ma60': float(latest['ma60']) if pd.notna(latest['ma60']) else None,
                        'macd_hist': float(latest['macd_hist']) if pd.notna(latest['macd_hist']) else None,
                        'rsi_14': float(latest['rsi_14']) if pd.notna(latest['rsi_14']) else None,
                        'boll_up': float(latest['boll_up']) if pd.notna(latest['boll_up']) else None,
                        'boll_down': float(latest['boll_down']) if pd.notna(latest['boll_down']) else None,
                        'momentum_5d': round(m5, 3),
                        'momentum_20d': round(m20, 3),
                        'avg_daily_return': round(avg_daily, 4),
                        'annual_vol_20d': round(vol, 2),
                    },
                    'score': round(score, 1),
                    'signal': 'bullish' if score >= 58 else 'bearish' if score <= 42 else 'neutral',
                    'confidence': round(max(0.5, min(0.95, 0.5 + abs(score - 50) / 50 * 0.45)), 2),
                    'source': 'tech_snapshot_backfill_polars',
                })

        if all_snaps:
            stats = save_features(all_snaps)
            print(f"\n[done] 保存 {stats['saved']} 条，失败 {stats['errors']} 条")
        else:
            print('\n[done] 无快照生成')
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', type=str, default=(datetime.now() - timedelta(days=120)).strftime('%Y-%m-%d'))
    parser.add_argument('--end', type=str, default=datetime.now().strftime('%Y-%m-%d'))
    parser.add_argument('--ticker', type=str, nargs='+', default=None)
    args = parser.parse_args()
    backfill_features_polars(args.start, args.end, args.ticker)


if __name__ == '__main__':
    main()
