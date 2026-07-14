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
    """获取 ETF 基本信息（同花顺 + 东方财富双 fallback）。"""
    import akshare as ak
    result = {}
    # 1) 先尝试同花顺，信息最完整
    try:
        df = ak.fund_info_ths(symbol=ticker)
        if df is not None and not df.empty and df.shape[1] >= 2:
            if '字段' in df.columns and '值' in df.columns:
                for _, row in df.iterrows():
                    k = str(row['字段']).strip()
                    v = row['值']
                    result[k] = v
            else:
                col = df.columns[0]
                result = {str(k).strip(): v for k, v in df[col].items()}
    except Exception as e:
        result['_ths_error'] = str(e)

    # 2) 东方财富实时行情补规模/名称/涨跌幅
    try:
        df_spot = ak.fund_etf_spot_em()
        row = df_spot[df_spot['代码'] == ticker]
        if not row.empty:
            result['基金代码'] = ticker
            result['基金简称'] = str(row.iloc[0]['名称'])
            # 总市值/流通市值单位为人民币元，转成亿元
            for k in ['总市值', '流通市值']:
                if k in row.columns:
                    try:
                        val = float(row.iloc[0][k])
                        result[f'{k}_亿元'] = round(val / 1e8, 2)
                    except Exception:
                        pass
            # 换手率、涨跌幅
            for k in ['换手率', '涨跌幅', '振幅', '成交额']:
                if k in row.columns:
                    result[k] = row.iloc[0][k]
    except Exception as e:
        result['_em_spot_error'] = str(e)

    # 3) 东方财富基金名称表补基金类型/名称
    if not result.get('基金简称'):
        try:
            df_name = ak.fund_name_em()
            row = df_name[df_name['基金代码'] == ticker]
            if not row.empty:
                result['基金简称'] = str(row.iloc[0]['基金简称'])
                result['基金类型'] = str(row.iloc[0]['基金类型'])
        except Exception as e:
            result['_em_name_error'] = str(e)

    return result


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
    """根据 ETF 名称推断对应 A 股指数代码（sina 格式）。"""
    name = (full_name or ticker).upper()
    mapping = {
        '沪深300': 'sh000300', '中证500': 'sh000905', '上证50': 'sh000016',
        '创业板指': 'sz399006', '创业板50': 'sz399673', '中证1000': 'sh000852',
        '科创50': 'sh000688', '上证指数': 'sh000001', '深证成指': 'sz399001',
        '中证红利': 'sh000922', '恒生': 'HSI', '纳斯达克': 'IXIC', '标普500': 'SPX',
        '稀土': 'sh000831', '通信': 'sh000936', '芯片': 'shH30007', '半导体': 'shH30184',
        '人工智能': 'sh930711', '机器人': 'shH30552', '算力': 'sh000948', '有色金属': 'sh000819',
        '钢铁': 'sh000928', '煤炭': 'sh000820', '银行': 'sh000886', '红利': 'sh000922',
        '军工': 'sz399967', '医药': 'sh000933', '证券': 'sz399975', '新能源车': 'sh930997',
        '光伏': 'sh000931', '传媒': 'sh000952', '5G': 'sh000938', '计算机': 'sh000977',
    }
    for key, code in mapping.items():
        if key in name:
            return code
    # ETF 代码常用开头推断
    if ticker.startswith('510300'):
        return 'sh000300'
    if ticker.startswith('510500'):
        return 'sh000905'
    if ticker.startswith('510050'):
        return 'sh000016'
    if ticker.startswith('159915'):
        return 'sz399006'
    if ticker.startswith('159949'):
        return 'sz399673'
    return None


def _benchmark_to_data_layer_symbol(bm: str) -> Optional[str]:
    """把 sina 指数代码转成 get_stock_data 可识别的 symbol。"""
    if not bm:
        return None
    if bm.startswith('sh'):
        return bm[2:]  # 上证指数代码
    if bm.startswith('sz'):
        return bm[2:]
    return bm


def fetch_index_ths(name: str) -> Optional[pd.DataFrame]:
    """用同花顺行业指数获取基准数据。"""
    import akshare as ak
    try:
        df = ak.stock_board_industry_index_ths(symbol=name, start_date='20230101', end_date='20260714')
        if df is None or df.empty:
            return None
        df['日期'] = pd.to_datetime(df['日期'])
        df = df.sort_values('日期').set_index('日期')
        df.rename(columns={'开盘价': 'open', '最高价': 'high', '最低价': 'low', '收盘价': 'close', '成交量': 'volume'}, inplace=True)
        return df
    except Exception:
        return None


def _benchmark_to_ths_name(ticker: str, full_name: str) -> Optional[str]:
    """把 ETF 映射到同花顺行业指数名称。"""
    name = (full_name or ticker).upper()
    mapping = {
        '稀土': '小金属', '永磁': '金属新材料', '通信': '通信设备', '半导体': '半导体', '芯片': '半导体',
        '机器人': '自动化设备', '人工智能': '软件开发', '算力': '计算机设备', '电池': '电池', '光伏': '光伏设备',
        '有色': '工业金属', '有色金属': '工业金属', '煤炭': '煤炭开采加工', '钢铁': '钢铁', '电力': '电力',
        '银行': '银行', '证券': '证券', '保险': '保险', '房地产': '房地产开发', '医药': '化学制药',
        '军工': '军工电子', '航空航天': '航天装备', '传媒': '传媒', '5G': '通信设备', '计算机': '计算机设备',
        '新能源车': '汽车整车', '锂电': '电池', '储能': '电池', '食品饮料': '食品加工制造', '白酒': '白酒',
        '沪深300': 'None', '中证500': 'None', '上证50': 'None', '创业板': 'None', '中证1000': 'None',
    }
    for key, code in mapping.items():
        if key in name:
            return code if code != 'None' else None
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
    if scale is None:
        # 用东方财富总市值或流通市值（亿元）兜底
        for k in ['总市值_亿元', '流通市值_亿元', '规模']:
            if k in profile:
                scale = _safe_float(profile.get(k))
                if scale >= 0.1:
                    break
    establish_date = profile.get('成立日期', '')
    full_name = profile.get('基金全称', name)

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
    benchmark = _get_benchmark_ticker(ticker, full_name)
    tracking = {'tracking_error': None, 'correlation': None, 'beta': None, 'volatility_proxy': None}
    try:
        from core.data_layer import get_stock_data
        etf_df, _ = get_stock_data(ticker, period='180d', calibrate=False)
        bench_df = None
        if benchmark and not benchmark.startswith(('HSI', 'IXIC', 'SPX')):
            # 优先用同花顺行业指数
            ths_name = _benchmark_to_ths_name(ticker, full_name)
            if ths_name:
                bench_df = fetch_index_ths(ths_name)
            else:
                bench_symbol = _benchmark_to_data_layer_symbol(benchmark)
                if bench_symbol:
                    try:
                        bench_df, _ = get_stock_data(bench_symbol, period='180d', calibrate=False)
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
    te = tracking.get('tracking_error')
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
