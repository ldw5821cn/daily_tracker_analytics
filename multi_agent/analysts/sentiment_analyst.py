#!/usr/bin/env python3
"""情绪分析器：利用涨停股池 + 龙虎榜 + 市场广度计算个股情绪评分。

评分逻辑：
  1. 涨停股池 → 涨停加分(+20) / 炸板扣分(-10) / 连板加分(连板数×5)
  2. 龙虎榜 → 机构净买额评分（归一化到[-20,+20]）
  3. 市场总体涨停占比 → 广度情绪偏置（对所有品种微调）
  4. 默认值为 50（中性）

用法：
  from analysts.sentiment_analyst import compute_sentiment_score
  score = compute_sentiment_score('600028', date='2026-07-15')
"""
import json, os, sys
from datetime import datetime
from typing import Dict, List, Optional

import akshare as ak
import pandas as pd

# ─── 缓存（避免同一交易日多次调用 API）────────────────────────
_CACHE: Dict[str, dict] = {}

def _get_zt_pool(date_str: str) -> pd.DataFrame:
    """获取涨停股池（带缓存）。"""
    key = f'zt_{date_str}'
    if key not in _CACHE:
        try:
            _CACHE[key] = ak.stock_zt_pool_em(date=date_str)
        except Exception:
            _CACHE[key] = pd.DataFrame()
    return _CACHE[key]


def _get_lhb(date_str: str) -> pd.DataFrame:
    """获取龙虎榜（带缓存）。"""
    key = f'lhb_{date_str}'
    if key not in _CACHE:
        try:
            _CACHE[key] = ak.stock_lhb_detail_em(start_date=date_str, end_date=date_str)
        except Exception:
            _CACHE[key] = pd.DataFrame()
    return _CACHE[key]


def _ticker_to_code(ticker: str) -> str:
    """标准化 ticker 到 6 位代码（去掉后缀 .SZ/.SH）。"""
    code = ticker.upper().replace('.SZ', '').replace('.SH', '').replace('.BJ', '')
    return code.zfill(6)[:6]


def compute_sentiment_score(
    ticker: str,
    date: Optional[str] = None,
) -> float:
    """计算个股情绪评分 0-100。

    输入：
      ticker: 股票代码（如 '600028' 或 '600028.SH'）
      date: 交易日（默认今天）
    返回：
      0-100 的评分，50=中性，>50=偏多，<50=偏空
    """
    if date is None:
        date = datetime.now().strftime('%Y%m%d')
    date_str = date.replace('-', '')[:8]

    code = _ticker_to_code(ticker)
    score = 50.0

    # ── 因子1：涨停/跌停 ──
    try:
        zt_df = _get_zt_pool(date_str)
        if not zt_df.empty and '代码' in zt_df.columns:
            match = zt_df[zt_df['代码'] == code]
            if not match.empty:
                row = match.iloc[0]
                score += 20  # 涨停加分
                # 连板加分
                lianban = int(row.get('连板数', 0)) if pd.notna(row.get('连板数', 0)) else 0
                score += min(lianban * 5, 15)  # 最多加15
                # 炸板扣分（炸板=首次封板后有打开）
                zhaban = int(row.get('炸板次数', 0)) if pd.notna(row.get('炸板次数', 0)) else 0
                if zhaban > 0:
                    score -= min(zhaban * 5, 10)
    except Exception:
        pass

    # ── 因子2：龙虎榜机构买卖 ──
    try:
        lhb_df = _get_lhb(date_str)
        if not lhb_df.empty and '代码' in lhb_df.columns:
            match = lhb_df[lhb_df['代码'] == code]
            if not match.empty:
                # 取净买额最大的记录（同一股票可能多次上榜）
                net_buy = match['龙虎榜净买额'].max()
                # 归一化：1亿=+5分，上限+20/-20
                score += max(-20, min(20, net_buy / 1e8 * 5))
    except Exception:
        pass

    # ── 因子3：市场总体情绪（涨停占比越高，整体偏多）──
    try:
        zt_df = _get_zt_pool(date_str)
        if not zt_df.empty:
            zt_count = len(zt_df)
            # 72 只涨停 ≈ 中性基准。>100 偏多，<40 偏空
            market_bias = (zt_count - 72) / 3  # 每3只偏离1分
            score += max(-5, min(5, market_bias))
    except Exception:
        pass

    return max(0, min(100, round(score, 1)))


def batch_sentiment(tickers: List[str], date: Optional[str] = None) -> Dict[str, float]:
    """批量计算多只股票的情绪评分（复用缓存）。"""
    return {t: compute_sentiment_score(t, date) for t in tickers}


if __name__ == '__main__':
    tests = ['600028', '600019', '002432', '000021', '002558', '688981', '516150']
    print(f'=== 情绪评分测试 (2026-07-15) ===')
    for t in tests:
        s = compute_sentiment_score(t, '2026-07-15')
        print(f'  {t}: {s:.1f}/100')
