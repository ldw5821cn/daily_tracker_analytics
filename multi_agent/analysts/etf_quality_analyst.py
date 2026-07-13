#!/usr/bin/env python3
"""ETF 质量/筛选因子分析：费率、规模、持仓集中度、跟踪误差。"""
from __future__ import annotations
import re
import warnings
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd
import numpy as np

warnings.filterwarnings('ignore')


def _safe_float(val, default=0.0):
    try:
        if pd.isna(val) or val is None:
            return default
        s = str(val).replace('%', '').replace(',', '').strip()
        return float(s) if s else default
    except Exception:
        return default


def _extract_size(text: str) -> Optional[float]:
    """从文本中提取规模数字（单位：亿元）。"""
    if not text:
        return None
    m = re.search(r'([\d.]+)\s*[万亿]?份', text)
    if m:
        # 份额规模，转换为亿元需要净值，这里先直接返回份额（亿份）
        return round(float(m.group(1)), 2)
    m = re.search(r'([\d.]+)\s*亿元', text)
    if m:
        return round(float(m.group(1)), 2)
    return None


def _parse_pct(text: str) -> Optional[float]:
    """从文本中提取百分比数字。"""
    if not text:
        return None
    m = re.search(r'([\d.]+)\s*%', str(text))
    return round(float(m.group(1)), 4) if m else None


def fetch_etf_profile(ticker: str) -> Dict:
    """获取 ETF 基本信息（同花顺）。"""
    import akshare as ak
    try:
        df = ak.fund_info_ths(symbol=ticker)
        if df is None or df.empty or df.shape[1] < 2:
            return {}
        # 实际返回两列：字段、值
        if '字段' in df.columns and '值' in df.columns:
            data = {}
            for _, row in df.iterrows():
                k = str(row['字段']).strip()
                v = row['值']
                data[k] = v
            return data
        # 兼容转置形式
        col = df.columns[0]
        return {str(k).strip(): v for k, v in df[col].items()}
    except Exception as e:
        return {'error': str(e)}


def fetch_etf_holdings(ticker: str, year: str = '2024') -> Optional[pd.DataFrame]:
    """获取 ETF 持仓明细。"""
    import akshare as ak
    try:
        df = ak.fund_portfolio_hold_em(symbol=ticker, date=year)
        if df is not None and not df.empty:
            return df
    except Exception:
        pass
    return None


def calc_concentration(df: Optional[pd.DataFrame]) -> Dict:
    """计算持仓集中度（前10/前20/前30占比），仅取最新季度。"""
    result = {'top10': None, 'top20': None, 'top30': None, 'top_holding': None}
    if df is None or df.empty:
        return result
    try:
        #  fund_portfolio_hold_em 返回多年多季度，需筛选最新季度
        if '季度' in df.columns:
            latest_quarter = sorted(df['季度'].dropna().unique())[-1]
            df_latest = df[df['季度'] == latest_quarter]
        else:
            df_latest = df

        if '占净值比例' in df_latest.columns:
            ratios = pd.to_numeric(df_latest['占净值比例'], errors='coerce').dropna().sort_values(ascending=False)
        else:
            ratios = None
            for idx in df.index:
                if '占净值比例' in str(idx):
                    ratios = pd.to_numeric(df.loc[idx], errors='coerce').dropna().sort_values(ascending=False)
                    break
        if ratios is None or ratios.empty:
            return result
        result['top10'] = round(float(ratios.head(10).sum()), 2)
        result['top20'] = round(float(ratios.head(20).sum()), 2)
        result['top30'] = round(float(ratios.head(30).sum()), 2)
        result['top_holding'] = {str(k): round(float(v), 2) for k, v in ratios.head(1).items()}
    except Exception:
        pass
    return result


def calc_tracking_error(etf_df: pd.DataFrame, benchmark_df: Optional[pd.DataFrame] = None, lookback_days: int = 120) -> Dict:
    """计算年化跟踪误差（ETF日收益率与基准日收益率之差的标准差 * sqrt(252)）。
    如果基准数据不可用，则返回 ETF 自身20日年化波动率作为 proxy，并标记 is_proxy=True。
    """
    result = {'tracking_error': None, 'correlation': None, 'beta': None, 'volatility_proxy': None, 'is_proxy': False}
    if etf_df is None or len(etf_df) < 20:
        return result
    try:
        etf_ret = etf_df['close'].pct_change().dropna().tail(lookback_days)
        result['volatility_proxy'] = round(etf_ret.std() * np.sqrt(252) * 100, 4)
        if benchmark_df is not None and len(benchmark_df) >= 20:
            etf_close = etf_df['close'].rename('etf')
            bench_close = benchmark_df['close'].rename('bench')
            df = pd.concat([etf_close, bench_close], axis=1).dropna().tail(lookback_days)
            if len(df) >= 20:
                returns = df.pct_change().dropna()
                diff = returns['etf'] - returns['bench']
                result['tracking_error'] = round(diff.std() * np.sqrt(252) * 100, 4)
                result['correlation'] = round(returns['etf'].corr(returns['bench']), 4)
                var = returns['bench'].var()
                result['beta'] = round(returns['etf'].cov(returns['bench']) / var, 4) if var > 0 else None
        else:
            result['is_proxy'] = True
    except Exception:
        pass
    return result


def _get_benchmark_ticker(ticker: str, full_name: str = '') -> Optional[str]:
    """根据 ETF 名称推断对应 A 股指数代码。"""
    name = (full_name or ticker).upper()
    mapping = {
        '沪深300': '000300', '中证500': '000905', '上证50': '000016',
        '创业板指': '399006', '创业板50': '399673', '中证1000': '000852',
        '科创50': '000688', '上证指数': '000001', '深证成指': '399001',
        '中证红利': '000922', '恒生': 'HSI', '纳斯达克': 'IXIC', '标普500': 'SPX',
    }
    for key, code in mapping.items():
        if key in name:
            return code
    # ETF 代码常用开头推断
    if ticker.startswith('510300'):
        return '000300'
    if ticker.startswith('510500'):
        return '000905'
    if ticker.startswith('510050'):
        return '000016'
    if ticker.startswith('159915'):
        return '399006'
    if ticker.startswith('159949'):
        return '399673'
    return None


def analyze_etf_quality(ticker: str, name: str = '') -> Dict:
    """主入口：分析 ETF 质量因子。"""
    import sys
    import os
    PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
    sys.path.insert(0, MULTI_AGENT)
    from core.data_layer import get_stock_data

    profile = fetch_etf_profile(ticker)
    if not profile or 'error' in profile:
        return {'error': profile.get('error', '无法获取 ETF 信息'), 'ticker': ticker, 'name': name}

    mgmt_fee = _parse_pct(profile.get('管理费', profile.get('管理费率', '')))
    cust_fee = _parse_pct(profile.get('托管费', profile.get('托管费率', '')))
    total_fee = round((mgmt_fee or 0) + (cust_fee or 0), 4)
    scale = _extract_size(profile.get('份额规模', profile.get('成立规模', '')))
    establish_date = profile.get('成立日期', '')

    # 计算成立年限
    years_since_establish = None
    if establish_date and isinstance(establish_date, str):
        try:
            dt = datetime.strptime(establish_date, '%Y-%m-%d')
            years_since_establish = round((datetime.now() - dt).days / 365.25, 2)
        except Exception:
            pass

    # 持仓集中度
    holdings = fetch_etf_holdings(ticker)
    concentration = calc_concentration(holdings)

    # 跟踪误差
    benchmark = _get_benchmark_ticker(ticker, profile.get('基金全称', name))
    tracking = {'tracking_error': None, 'correlation': None, 'beta': None, 'volatility_proxy': None}
    try:
        from core.data_layer import get_stock_data
        etf_df, _ = get_stock_data(ticker, period='180d', calibrate=False)
        bench_df = None
        if benchmark and not benchmark.startswith(('HSI', 'IXIC', 'SPX')):
            try:
                bench_df, _ = get_stock_data(benchmark, period='180d', calibrate=False)
            except Exception:
                pass
        tracking = calc_tracking_error(etf_df, bench_df, lookback_days=120)
    except Exception:
        pass

    # 评分：越低费率越好，越大规模越好，越低跟踪误差越好，越分散越好
    quality_score = 50
    reasons = []
    if total_fee is not None:
        if total_fee <= 0.2:
            quality_score += 8; reasons.append(f"费率低({total_fee}%)")
        elif total_fee >= 0.8:
            quality_score -= 6; reasons.append(f"费率高({total_fee}%)")
        elif total_fee >= 0.5:
            quality_score -= 3; reasons.append(f"费率偏高({total_fee}%)")
    if scale is not None:
        if scale >= 50:
            quality_score += 6; reasons.append(f"规模大({scale}亿)")
        elif scale <= 5:
            quality_score -= 4; reasons.append(f"规模小({scale}亿)")
    te = tracking.get('tracking_error') if not tracking.get('is_proxy') else tracking.get('volatility_proxy')
    if te is not None:
        if te <= 2.0:
            quality_score += 5; reasons.append(f"跟踪误差低({te}%)")
        elif te >= 5.0:
            quality_score -= 4; reasons.append(f"跟踪误差高({te}%)")
    top10 = concentration.get('top10')
    if top10 is not None:
        if top10 >= 60:
            quality_score -= 3; reasons.append(f"持仓集中({top10}%)")
        elif top10 <= 30:
            quality_score += 2; reasons.append(f"持仓分散({top10}%)")
    if years_since_establish is not None:
        if years_since_establish >= 3:
            quality_score += 2; reasons.append(f"成立时间长({years_since_establish}年)")

    quality_score = max(0, min(100, quality_score))

    return {
        'ticker': ticker,
        'name': name or profile.get('基金简称', ticker),
        'quality_score': quality_score,
        'fee': {
            'management': mgmt_fee,
            'custody': cust_fee,
            'total': total_fee,
        },
        'scale': scale,
        'establish_date': establish_date,
        'years_since_establish': years_since_establish,
        'concentration': concentration,
        'tracking': tracking,
        'benchmark': benchmark,
        'reasons': reasons,
    }


if __name__ == '__main__':
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    for t, n in [('510300', '沪深300ETF'), ('515880', '通信ETF'), ('516150', '稀土ETF')]:
        r = analyze_etf_quality(t, n)
        print(f"\n{t} {r.get('name')}:")
        print(f"  质量评分: {r.get('quality_score')}")
        print(f"  费率: {r.get('fee')}")
        print(f"  规模: {r.get('scale')} 亿")
        print(f"  跟踪误差: {r.get('tracking', {}).get('tracking_error')}%")
        print(f"  集中度: {r.get('concentration', {})}")
        print(f"  原因: {' | '.join(r.get('reasons', []))}")
