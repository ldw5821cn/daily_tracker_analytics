#!/usr/bin/env python3
"""每日市场状态（横截面）特征回填到 warehouse。

独立于标的的 regime 特征，供参数优化器学习时 join 到预测样本。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional

import pandas as pd
import numpy as np

PR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PR / "multi_agent"))

from core.warehouse import get_warehouse_conn, init_warehouse_db
import akshare as ak


def _latest_trading_day(date: str) -> Optional[str]:
    """若 date 非交易日，回退到最近交易日。"""
    dt = pd.Timestamp(date)
    for _ in range(7):
        if dt.weekday() < 5:
            return dt.strftime('%Y-%m-%d')
        dt -= pd.Timedelta(days=1)
    return None


def _parse_amount(x) -> float:
    if pd.isna(x) or x == '' or x is None:
        return 0.0
    s = str(x).strip().replace(',', '')
    sign = -1 if s.startswith('-') else 1
    s = s.replace('-', '').replace('+', '')
    if s.endswith('亿'):
        return sign * float(s[:-1]) * 10000
    elif s.endswith('万'):
        return sign * float(s[:-1])
    elif s.endswith('%'):
        return sign * float(s[:-1])
    try:
        return sign * float(s)
    except Exception:
        return 0.0


def _load_macro_indicators(date: str) -> Optional[Dict]:
    data_dir = Path(__file__).resolve().parent.parent / "data" / "macro_indicators"
    if not data_dir.exists():
        return None
    for f in sorted(data_dir.glob('*.json'), reverse=True):
        if f.stem <= date:
            try:
                return json.loads(f.read_text(encoding='utf-8'))
            except Exception:
                continue
    return None


def _get_zt_stats(date: str) -> Dict[str, Any]:
    """基于 akshare 涨停池统计涨停/跌停/炸板数量。"""
    d = date.replace('-', '')
    try:
        df = ak.stock_zt_pool_em(date=d)
        if df is None or df.empty or '代码' not in df.columns:
            return {}
        limit_up = len(df)
        zhaban = int(df['炸板次数'].sum()) if '炸板次数' in df.columns else 0
        lianban = int(df['连板数'].sum()) if '连板数' in df.columns else 0
        return {
            'limit_up': limit_up,
            'limit_up_zhaban': zhaban,
            'limit_up_lianban': lianban,
        }
    except Exception as e:
        return {'error': f'zt_pool:{type(e).__name__}'}


def _get_industry_top5(date: str) -> Dict[str, Any]:
    try:
        df = ak.stock_fund_flow_industry()
        if df is None or df.empty or '行业' not in df.columns:
            return {}
        df['净额_亿元'] = df['净额'].apply(lambda x: _parse_amount(str(x)) / 10000)
        df = df.sort_values('净额_亿元', ascending=False)
        top5 = df.head(5)[['行业', '净额_亿元']].to_dict('records')
        bot5 = df.tail(5)[['行业', '净额_亿元']].to_dict('records')
        total_net = df['净额_亿元'].sum()
        return {
            'industry_top5': top5,
            'industry_bottom5': bot5,
            'industry_total_net': round(total_net, 2),
        }
    except Exception as e:
        return {'error': f'industry_flow:{type(e).__name__}'}


def _get_concept_top5(date: str) -> Dict[str, Any]:
    try:
        df = ak.stock_fund_flow_concept()
        if df is None or df.empty or '行业' not in df.columns:
            return {}
        df['净额_亿元'] = df['净额'].apply(lambda x: _parse_amount(str(x)) / 10000)
        df = df.sort_values('净额_亿元', ascending=False)
        top5 = df.head(5)[['行业', '净额_亿元']].to_dict('records')
        return {
            'concept_top5': top5,
            'concept_total_net': round(df['净额_亿元'].sum(), 2),
        }
    except Exception as e:
        return {'error': f'concept_flow:{type(e).__name__}'}


def _get_index_returns(date: str) -> Dict[str, Any]:
    """从 warehouse 读取主要指数 1/5/20 日收益。"""
    indices = {
        '000001.SH': '上证指数', '000300.SH': '沪深300',
        '000905.SH': '中证500', '399006.SZ': '创业板指',
    }
    result = {}
    conn = get_warehouse_conn()
    try:
        for ticker, name in indices.items():
            rows = conn.execute(
                "SELECT date, close FROM daily_bar WHERE ticker=? AND date <= ? ORDER BY date DESC LIMIT 25",
                (ticker, date)
            ).fetchall()
            if len(rows) < 6:
                continue
            closes = [r['close'] for r in rows]
            result[name] = {
                'ret_1d': round((closes[0] / closes[1] - 1) * 100, 2) if len(closes) >= 2 else None,
                'ret_5d': round((closes[0] / closes[5] - 1) * 100, 2) if len(closes) >= 6 else None,
                'ret_20d': round((closes[0] / closes[20] - 1) * 100, 2) if len(closes) >= 21 else None,
            }
    finally:
        conn.close()
    return result


def _build_regime(date: str) -> Optional[Dict[str, Any]]:
    date = _latest_trading_day(date)
    if not date:
        return None

    features = {
        'date': date,
        'zt_stats': _get_zt_stats(date),
        'industry_flow': _get_industry_top5(date),
        'concept_flow': _get_concept_top5(date),
        'index_returns': _get_index_returns(date),
        'macro_indicators': _load_macro_indicators(date),
    }

    # 计算汇总标量
    scalar = {}
    zt = features.get('zt_stats') or {}
    if 'limit_up' in zt:
        scalar['limit_up'] = zt['limit_up']
        scalar['limit_up_zhaban'] = zt.get('limit_up_zhaban', 0)
        scalar['limit_up_lianban'] = zt.get('limit_up_lianban', 0)

    ind = features.get('industry_flow') or {}
    if 'industry_total_net' in ind:
        scalar['industry_total_net'] = ind['industry_total_net']
    con = features.get('concept_flow') or {}
    if 'concept_total_net' in con:
        scalar['concept_total_net'] = con['concept_total_net']

    idx = features.get('index_returns') or {}
    if '沪深300' in idx and idx['沪深300'].get('ret_1d') is not None:
        scalar['hs300_ret_1d'] = idx['沪深300']['ret_1d']
    if '沪深300' in idx and idx['沪深300'].get('ret_5d') is not None:
        scalar['hs300_ret_5d'] = idx['沪深300']['ret_5d']

    macro = features.get('macro_indicators') or {}
    if macro:
        nb = macro.get('northbound') or {}
        if nb.get('net_buy') is not None:
            scalar['northbound_net_buy'] = nb['net_buy']
        mg = macro.get('margin') or {}
        if mg.get('total_balance') is not None:
            scalar['margin_total_balance'] = mg['total_balance']
        pcr = macro.get('option_pcr') or {}
        records = pcr.get('pcr_records', [])
        sse_vals = [r['pcr_volume'] for r in records if r.get('market') == 'SSE' and r.get('pcr_volume') is not None]
        if sse_vals:
            scalar['option_pcr_avg'] = round(sum(sse_vals) / len(sse_vals), 2)

    features['scalar'] = scalar
    return features


def backfill(start: str, end: str):
    init_warehouse_db()
    conn = get_warehouse_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS market_regime_features (
                date TEXT PRIMARY KEY,
                feature_json TEXT,
                updated_at TEXT
            )
        """)
        dates = pd.bdate_range(start=start, end=end).strftime('%Y-%m-%d').tolist()
        saved = 0
        for d in dates:
            try:
                feat = _build_regime(d)
                if not feat:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO market_regime_features (date, feature_json, updated_at) VALUES (?, ?, ?)",
                    (d, json.dumps(feat, ensure_ascii=False), datetime.now().isoformat()),
                )
                saved += 1
                print(f"  ✅ {d} regime 特征保存")
            except Exception as e:
                print(f"  ⚠️ {d} 失败: {e}")
        conn.commit()
        print(f"\n[done] 保存 {saved}/{len(dates)} 天 regime 特征")
    finally:
        conn.close()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', type=str, default=(datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d'))
    parser.add_argument('--end', type=str, default=datetime.now().strftime('%Y-%m-%d'))
    args = parser.parse_args()
    backfill(args.start, args.end)
