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


def _get_breadth_stats(date: str) -> Dict[str, Any]:
    """统计全市场涨跌家数与涨跌比（市场宽度）。

    优先东财快照（含涨跌幅），失败回退新浪快照。
    """
    try:
        df = ak.stock_zh_a_spot_em()
        col = '涨跌幅'
        if df is None or df.empty or col not in df.columns:
            raise ValueError('spot_em 无涨跌幅列')
    except Exception:
        try:
            df = ak.stock_zh_a_spot()
            col = '涨跌幅'
            if df is None or df.empty or col not in df.columns:
                return {}
        except Exception as e:
            return {'error': f'breadth:{type(e).__name__}'}
    try:
        pct = pd.to_numeric(df[col], errors='coerce').dropna()
        up = int((pct > 0).sum())
        down = int((pct < 0).sum())
        flat = int((pct == 0).sum())
        total = len(pct)
        ratio = round(up / down, 2) if down > 0 else (up if up > 0 else 0)
        return {
            'up_count': up,
            'down_count': down,
            'flat_count': flat,
            'total_count': total,
            'breadth_ratio': ratio,
        }
    except Exception as e:
        return {'error': f'breadth:{type(e).__name__}'}


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


def _get_limit_ladder_stats(date: str) -> Dict[str, Any]:
    """连板梯队统计：按连板数分层计数，并计算封板资金与主导行业/概念。"""
    d = date.replace('-', '')
    try:
        df = ak.stock_zt_pool_em(date=d)
        if df is None or df.empty or '代码' not in df.columns or '连板数' not in df.columns:
            return {}
        # 封板资金转亿元
        df['封板资金_亿元'] = pd.to_numeric(df.get('封板资金', 0), errors='coerce') / 1e8
        # 分层统计
        ladder_counts = df.groupby('连板数').size().to_dict()
        # 合并 5 板及以上
        ladder = {
            'ladder_1b': int(ladder_counts.get(1, 0)),
            'ladder_2b': int(ladder_counts.get(2, 0)),
            'ladder_3b': int(ladder_counts.get(3, 0)),
            'ladder_4b': int(ladder_counts.get(4, 0)),
            'ladder_5b_plus': int(sum(c for lv, c in ladder_counts.items() if lv >= 5)),
            'ladder_max_board': int(df['连板数'].max()) if not df['连板数'].empty else 0,
            'ladder_total': len(df),
            'ladder_avg_seal_capital_亿元': round(df['封板资金_亿元'].mean(), 3) if not df.empty else None,
            'ladder_total_seal_capital_亿元': round(df['封板资金_亿元'].sum(), 3) if not df.empty else None,
        }
        # 连板股最多前 3 行业
        if '所属行业' in df.columns and not df['所属行业'].dropna().empty:
            top_sectors = df['所属行业'].value_counts().head(3).to_dict()
            ladder['ladder_top3_sectors'] = {str(k): int(v) for k, v in top_sectors.items()}
        return ladder
    except Exception as e:
        return {'error': f'limit_ladder:{type(e).__name__}'}


def _get_concept_rps(date: str) -> Dict[str, Any]:
    """概念 RPS 轮动：基于当日同花顺概念涨跌幅计算 Top/Bottom 与分化度。"""
    try:
        df = ak.stock_fund_flow_concept()
        if df is None or df.empty or '行业-涨跌幅' not in df.columns or '行业' not in df.columns:
            return {}
        df['ret_pct'] = pd.to_numeric(df['行业-涨跌幅'], errors='coerce')
        df = df.dropna(subset=['ret_pct']).sort_values('ret_pct', ascending=False)
        top5 = df.head(5)[['行业', 'ret_pct']].to_dict('records')
        bot5 = df.tail(5)[['行业', 'ret_pct']].to_dict('records')
        top10_avg = df.head(10)['ret_pct'].mean()
        bot10_avg = df.tail(10)['ret_pct'].mean()
        return {
            'concept_top5': top5,
            'concept_bottom5': bot5,
            'concept_top5_avg_return': round(sum(r['ret_pct'] for r in top5) / len(top5), 2) if top5 else None,
            'concept_bottom5_avg_return': round(sum(r['ret_pct'] for r in bot5) / len(bot5), 2) if bot5 else None,
            'concept_rps_dispersion': round(top10_avg - bot10_avg, 2) if not pd.isna(top10_avg) and not pd.isna(bot10_avg) else None,
            'concept_leader': top5[0]['行业'] if top5 else None,
            'concept_leader_return': round(top5[0]['ret_pct'], 2) if top5 else None,
        }
    except Exception as e:
        return {'error': f'concept_rps:{type(e).__name__}'}


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


def _load_lhb_cache(date: str) -> Optional[Dict]:
    data_dir = Path(__file__).resolve().parent.parent / "data" / "lhb_cache"
    if not data_dir.exists():
        return None
    for f in sorted(data_dir.glob('*.json'), reverse=True):
        if f.stem <= date:
            try:
                return json.loads(f.read_text(encoding='utf-8'))
            except Exception:
                continue
    return None


def _build_regime(date: str) -> Optional[Dict[str, Any]]:
    date = _latest_trading_day(date)
    if not date:
        return None

    features = {
        'date': date,
        'breadth': _get_breadth_stats(date),
        'zt_stats': _get_zt_stats(date),
        'limit_ladder': _get_limit_ladder_stats(date),
        'concept_rps': _get_concept_rps(date),
        'industry_flow': _get_industry_top5(date),
        'concept_flow': {},  # 保留空槽位（资金流版概念Top5），已被 concept_rps 替代
        'index_returns': _get_index_returns(date),
        'macro_indicators': _load_macro_indicators(date),
        'lhb': _load_lhb_cache(date),
    }

    # 计算汇总标量
    scalar = {}
    bd = features.get('breadth') or {}
    if 'up_count' in bd:
        scalar['up_count'] = bd['up_count']
        scalar['down_count'] = bd['down_count']
        scalar['breadth_ratio'] = bd.get('breadth_ratio', 0)
    zt = features.get('zt_stats') or {}
    if zt and 'limit_up' in zt:
        scalar['limit_up'] = zt['limit_up']
        scalar['limit_up_zhaban'] = zt.get('limit_up_zhaban', 0)
        scalar['limit_up_lianban'] = zt.get('limit_up_lianban', 0)

    # 连板梯队标量
    lad = features.get('limit_ladder') or {}
    if lad and not lad.get('error'):
        for k in ['ladder_1b', 'ladder_2b', 'ladder_3b', 'ladder_4b', 'ladder_5b_plus',
                  'ladder_max_board', 'ladder_total', 'ladder_avg_seal_capital_亿元',
                  'ladder_total_seal_capital_亿元']:
            if k in lad:
                scalar[k] = lad[k]

    ind = features.get('industry_flow') or {}
    if 'industry_total_net' in ind:
        scalar['industry_total_net'] = ind['industry_total_net']
    con = features.get('concept_rps') or {}
    if con and not con.get('error'):
        for k in ['concept_top5_avg_return', 'concept_bottom5_avg_return',
                  'concept_rps_dispersion', 'concept_leader_return']:
            if k in con and con[k] is not None:
                scalar[k] = con[k]

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

    lhb = features.get('lhb') or {}
    if lhb and not lhb.get('error'):
        scalar['lhb_count'] = lhb.get('total_count', 0)
        scalar['lhb_inst_net_buy'] = lhb.get('inst_net_buy_total', 0.0)
        scalar['lhb_inst_buy'] = lhb.get('inst_buy_total', 0.0)
        scalar['lhb_inst_sell'] = lhb.get('inst_sell_total', 0.0)
        scalar['lhb_limit_up_with_inst'] = lhb.get('limit_up_with_inst', 0)
        scalar['lhb_limit_down_with_inst'] = lhb.get('limit_down_with_inst_sell', 0)

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
