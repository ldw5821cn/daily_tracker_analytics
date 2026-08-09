#!/usr/bin/env python3
"""每日宏观市场指标抓取：北向资金、融资融券、期权 PCR。

数据源：akshare
- 北向资金：ak.stock_hsgt_hist_em()
- 沪市融资融券：ak.stock_margin_sse(start_date, end_date)
- 深市融资融券：ak.stock_margin_szse(date)  # 单日期
- 上交所期权 PCR：ak.option_daily_stats_sse(date)
- 深交所期权 PCR：ak.option_daily_stats_szse(date)

输出到 multi_agent/data/macro_indicators/{date}.json，供 macro_analyst 与预测系统使用。
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, MULTI_AGENT)

import akshare as ak
import pandas as pd

DATA_DIR = os.path.join(MULTI_AGENT, 'data', 'macro_indicators')


def _save(date: str, data: Dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, f'{date}.json')
    # 将 pandas/NumPy 的 NaN 转成 None，确保 JSON 标准
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=lambda x: None if pd.isna(x) else x)
    print(f'[macro_indicators] saved to {path}')


def _load_latest() -> Optional[Dict]:
    if not os.path.exists(DATA_DIR):
        return None
    files = sorted([f for f in os.listdir(DATA_DIR) if f.endswith('.json')], reverse=True)
    if not files:
        return None
    with open(os.path.join(DATA_DIR, files[0]), 'r', encoding='utf-8') as f:
        return json.load(f)


def _retry(fn, retries: int = 2, sleep: float = 1.0):
    for i in range(retries + 1):
        try:
            return fn()
        except Exception as e:
            if i == retries:
                raise
            print(f'  retry {i+1}/{retries}: {e}')
            time.sleep(sleep * (i + 1))
    return None


def fetch_northbound(target_date: str) -> Optional[Dict]:
    """获取最近交易日北向资金净买入额（亿元）。"""
    try:
        df = _retry(ak.stock_hsgt_hist_em, retries=2)
        if df is None or df.empty:
            return None
        df['日期'] = pd.to_datetime(df['日期'], errors='coerce').dt.strftime('%Y-%m-%d')
        df = df.dropna(subset=['日期']).sort_values('日期')
        row = df[df['日期'] == target_date]
        if row.empty:
            row = df.iloc[[-1]]  # 回退到最新
        r = row.iloc[0]

        def _f(val):
            if val is None or (isinstance(val, float) and pd.isna(val)):
                return None
            try:
                return float(val)
            except Exception:
                return None

        return {
            'date': str(r['日期']),
            'net_buy': _f(r['当日成交净买额']),  # 亿元
            'buy_amount': _f(r['买入成交额']),
            'sell_amount': _f(r['卖出成交额']),
            'cumulative_net_buy': _f(r['历史累计净买额']),
            'hs300': _f(r['沪深300']),
        }
    except Exception as e:
        return {'date': target_date, 'error': str(e)}


def fetch_margin(target_date: str) -> Optional[Dict]:
    """获取最近交易日的两市融资融券余额（亿元）。"""
    try:
        # 沪市支持 start/end 范围，取最近 15 天再按目标日期匹配，若目标日期无数据则回退到最新
        end_dt = datetime.strptime(target_date, '%Y-%m-%d')
        start_dt = end_dt - timedelta(days=15)
        start = start_dt.strftime('%Y%m%d')
        end = end_dt.strftime('%Y%m%d')
        sh = _retry(lambda: ak.stock_margin_sse(start_date=start, end_date=end), retries=2)
        sh_balance = None
        if sh is not None and not sh.empty:
            sh['信用交易日期'] = pd.to_datetime(sh['信用交易日期'], errors='coerce').dt.strftime('%Y-%m-%d')
            sh = sh.dropna(subset=['信用交易日期'])
            sh_balance = None
            row = sh[sh['信用交易日期'] == target_date]
            if not row.empty:
                sh_balance = float(row.iloc[0]['融资融券余额']) / 1e8
            else:
                # 回退到最近一条
                latest = sh.iloc[-1]
                sh_balance = float(latest['融资融券余额']) / 1e8

        # 深市接口单日期查询，若目标日期失败，按交易日往前回退 5 天
        sz_balance = None
        for i in range(5):
            dt = (end_dt - timedelta(days=i)).strftime('%Y%m%d')
            try:
                sz = _retry(lambda d=dt: ak.stock_margin_szse(date=d), retries=1)
                if sz is not None and not sz.empty:
                    # 深市返回亿元
                    sz_balance = float(sz.iloc[0]['融资融券余额'])
                    break
            except Exception as e:
                print(f'  sz margin {dt} error: {e}')
                continue

        return {
            'date': target_date,
            'sh_balance': sh_balance,
            'sz_balance': sz_balance,
            'total_balance': (sh_balance + sz_balance) if sh_balance is not None and sz_balance is not None else None,
        }
    except Exception as e:
        return {'date': target_date, 'error': str(e)}


def fetch_option_pcr(target_date: str) -> Optional[Dict]:
    """获取最近交易日的 50ETF/300ETF/500ETF/创业板ETF 期权 PCR。"""
    try:
        end_dt = datetime.strptime(target_date, '%Y-%m-%d')
        pcr_records = []
        for i in range(5):
            d = (end_dt - timedelta(days=i)).strftime('%Y%m%d')
            pcr_records = []
            try:
                for name, market in [('option_daily_stats_sse', 'SSE'), ('option_daily_stats_szse', 'SZSE')]:
                    df = _retry(lambda fn=name, dt=d: getattr(ak, fn)(date=dt), retries=1)
                    if df is None or df.empty:
                        continue
                    for _, r in df.iterrows():
                        pcr_records.append({
                            'market': market,
                            'underlying_code': str(r['合约标的代码']),
                            'underlying_name': str(r['合约标的名称']),
                            'trade_date': str(r['交易日']) if '交易日' in df.columns else d,
                            'call_volume': int(r['认购成交量']) if '认购成交量' in df.columns else None,
                            'put_volume': int(r['认沽成交量']) if '认沽成交量' in df.columns else None,
                            'pcr_volume': float(r['认沽/认购']) if '认沽/认购' in df.columns else None,
                            'call_oi': int(r['未平仓认购合约数']) if '未平仓认购合约数' in df.columns else None,
                            'put_oi': int(r['未平仓认沽合约数']) if '未平仓认沽合约数' in df.columns else None,
                            'total_oi': int(r['未平仓合约总数']) if '未平仓合约总数' in df.columns else None,
                        })
            except Exception as e:
                print(f'  option pcr {d} error: {e}')
                continue
            if pcr_records:
                break

        if not pcr_records:
            return {'date': target_date, 'error': 'no option data for recent trade days'}
        return {'date': target_date, 'pcr_records': pcr_records}
    except Exception as e:
        return {'date': target_date, 'error': str(e)}


def fetch_all(date: Optional[str] = None) -> Dict:
    date = date or datetime.now().strftime('%Y-%m-%d')
    latest = _load_latest()
    if latest and latest.get('date') == date and 'northbound' in latest and 'margin' in latest and 'option_pcr' in latest:
        print(f'[macro_indicators] {date} already cached, skip')
        return latest

    result = {
        'date': date,
        'fetched_at': datetime.now().isoformat(),
        'northbound': fetch_northbound(date),
        'margin': fetch_margin(date),
        'option_pcr': fetch_option_pcr(date),
    }
    _save(date, result)
    return result


def compute_market_sentiment_score(data: Dict) -> float:
    """基于北向+融资融券+PCR 计算一个 0-100 的日度市场资金面情绪分。

    50 为中性，>50 偏乐观，<50 偏谨慎。
    """
    scores = []
    nb = data.get('northbound') or {}
    if nb and 'net_buy' in nb and nb['net_buy'] is not None:
        net = nb['net_buy']
        scores.append(50 + max(-15, min(15, net / 20)))

    mg = data.get('margin') or {}
    if mg and 'total_balance' in mg and mg['total_balance'] is not None:
        total = mg['total_balance']
        # 用两市余额相对前日变化（亿元）作为情绪信号，需历史数据
        scores.append(50)  # placeholder，后续接入历史比较

    pcr = data.get('option_pcr') or {}
    records = pcr.get('pcr_records', [])
    if records:
        # 取 50ETF + 300ETF 的成交量 PCR 平均
        pcr_vals = [r['pcr_volume'] for r in records if r.get('underlying_code') in ('510050', '510300') and r.get('pcr_volume')]
        if pcr_vals:
            avg_pcr = sum(pcr_vals) / len(pcr_vals)
            # PCR 越高，说明防御情绪越强，市场偏谨慎
            scores.append(50 - max(-15, min(15, (avg_pcr - 90) / 5)))

    if not scores:
        return 50.0
    return sum(scores) / len(scores)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', default=None, help='YYYY-MM-DD')
    parser.add_argument('--score', action='store_true', help='同时打印资金面情绪分')
    args = parser.parse_args()
    data = fetch_all(args.date)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    if args.score:
        print('资金面情绪分:', compute_market_sentiment_score(data))
