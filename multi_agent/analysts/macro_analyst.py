#!/usr/bin/env python3
"""
宏观市场分析师 (Market Macro Analyst)

负责获取和判断整体市场环境：
- 大盘指数（沪深300、中证500、上证指数）趋势
- 市场情绪（涨跌停家数、涨跌比）
- 北向资金流向（如可获取）
- 人民币汇率/美债/大宗商品（可选）
- 板块轮动热度
- 美股宏观代理：利率、CPI、失业率、VIX/DXY 代理、收益率曲线
- Risk-on / Risk-off 状态

输出统一的宏观评分和信号，供基金经理 Agent 在裁决时参考。
"""
from __future__ import annotations

import sys
import os
import json
import warnings
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

warnings.filterwarnings('ignore')

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, MULTI_AGENT)

from core.data_layer import get_stock_data, is_futures, is_etf, is_stock
import pandas as pd
import numpy as np

# 大盘指数代码
INDEX_TICKERS = {
    '000300': '沪深300',
    '000905': '中证500',
    '000001': '上证指数',
    '399006': '创业板指',
    '399673': '创业板50',
}

# Risk-on/off 阈值
RISK_ON_SCORE = 60
RISK_OFF_SCORE = 40

def _safe_float(val, default=0.0):
    try:
        return float(val)
    except Exception:
        return default

def _get_index_data(ticker: str, period: str = '30d') -> Optional[pd.DataFrame]:
    """获取指数数据。"""
    try:
        df, _ = get_stock_data(ticker, period=period, calibrate=False)
        if df is not None and len(df) >= 5:
            return df
    except Exception:
        return None
    return None

def _calc_index_score(ticker: str, name: str) -> Dict:
    """计算单个指数的技术得分。"""
    df = _get_index_data(ticker, '60d')
    if df is None or len(df) < 20:
        return {'ticker': ticker, 'name': name, 'score': 50, 'trend': 'unknown', 'close': 0, 'ma20': 0, 'ma60': 0, 'return_1d': 0, 'return_5d': 0, 'return_20d': 0}

    latest = df.iloc[-1]
    prev = df.iloc[-2]
    close = float(latest['close'])
    ma20 = float(df['close'].rolling(20).mean().iloc[-1])
    ma60 = float(df['close'].rolling(60).mean().iloc[-1])

    ret_5d = (close / float(df['close'].iloc[-6]) - 1) * 100 if len(df) >= 6 else 0
    ret_20d = (close / float(df['close'].iloc[-21]) - 1) * 100 if len(df) >= 21 else 0

    score = 50
    if close > ma20: score += 8
    if close > ma60: score += 8
    if ma20 > ma60: score += 6
    if ret_5d > 0: score += 4
    if ret_20d > 0: score += 4

    # 最近 1 日涨跌
    day_change = (close / float(prev['close']) - 1) * 100
    if day_change > 1: score += 3
    elif day_change < -1: score -= 3

    score = max(0, min(100, score))

    # 波动率惩罚：20日年化波动率>30 扣分
    vol = df['close'].pct_change().rolling(20).std().iloc[-1] * np.sqrt(252) * 100 if len(df) >= 20 else 0
    if vol > 30: score -= 2

    trend = 'bullish' if score >= 65 else 'bearish' if score <= 35 else 'neutral'
    return {
        'ticker': ticker, 'name': name, 'score': round(score, 1),
        'trend': trend, 'close': round(close, 2),
        'ma20': round(ma20, 2), 'ma60': round(ma60, 2),
        'return_1d': round(day_change, 2),
        'return_5d': round(ret_5d, 2), 'return_20d': round(ret_20d, 2),
        'vol_20d': round(vol, 2),
    }

def _get_market_breadth() -> Dict:
    """市场广度：基于沪深300/中证500/上证指数的涨跌情况 + 涨停池数量估算。"""
    breadth = {'advances': 0, 'declines': 0, 'score': 50, 'limit_up': 0, 'limit_down': 0}
    for ticker, name in INDEX_TICKERS.items():
        df = _get_index_data(ticker, '5d')
        if df is not None and len(df) >= 2:
            today = float(df['close'].iloc[-1])
            yesterday = float(df['close'].iloc[-2])
            if today > yesterday:
                breadth['advances'] += 1
            else:
                breadth['declines'] += 1
    total = breadth['advances'] + breadth['declines']
    if total > 0:
        breadth['score'] = round(breadth['advances'] / total * 100, 1)

    # 尝试获取涨停池作为情绪代理
    try:
        import akshare as ak
        date_str = datetime.now().strftime('%Y%m%d')
        zt = ak.stock_zt_pool_em(date=date_str)
        breadth['limit_up'] = len(zt) if zt is not None else 0
    except Exception:
        pass

    return breadth

def _get_us_macro_data() -> Dict:
    """获取美国宏观数据（利率、CPI、失业率、初请失业金）。"""
    data = {'fed_rate': None, 'cpi_yoy': None, 'unemployment': None, 'initial_jobless': None}
    try:
        import akshare as ak
        rate_df = ak.macro_bank_usa_interest_rate()
        if rate_df is not None and not rate_df.empty:
            latest = rate_df.dropna(subset=['今值']).iloc[-1]
            data['fed_rate'] = _safe_float(latest.get('今值'))
            data['fed_rate_date'] = str(latest.get('日期', ''))

        cpi_df = ak.macro_usa_cpi_yoy()
        if cpi_df is not None and not cpi_df.empty:
            latest = cpi_df.dropna(subset=['现值']).iloc[-1]
            data['cpi_yoy'] = _safe_float(latest.get('现值'))
            data['cpi_date'] = str(latest.get('发布日期', ''))

        unemp_df = ak.macro_usa_unemployment_rate()
        if unemp_df is not None and not unemp_df.empty:
            latest = unemp_df.dropna(subset=['今值']).iloc[-1]
            data['unemployment'] = _safe_float(latest.get('今值'))
            data['unemployment_date'] = str(latest.get('日期', ''))

        jobless_df = ak.macro_usa_initial_jobless()
        if jobless_df is not None and not jobless_df.empty:
            latest = jobless_df.dropna(subset=['今值']).iloc[-1]
            data['initial_jobless'] = _safe_float(latest.get('今值'))
            data['initial_jobless_date'] = str(latest.get('日期', ''))
    except Exception as e:
        data['error'] = str(e)
    return data

def _get_yield_curve() -> Dict:
    """获取中美国债收益率曲线代理数据。"""
    data = {'china_10y': None, 'china_2y': None, 'us_10y_proxy': None, 'spread': None}
    try:
        import akshare as ak
        df = ak.bond_zh_us_rate()
        if df is not None and not df.empty:
            # 列名：日期、中国国债收益率2年、中国国债收益率5年、中国国债收益率10年、中国国债收益率30年、...、美国国债收益率10年...
            latest = df.iloc[-1]
            data['china_10y'] = _safe_float(latest.get('中国国债收益率10年'))
            data['china_2y'] = _safe_float(latest.get('中国国债收益率2年'))
            data['us_10y_proxy'] = _safe_float(latest.get('美国国债收益率10年'))
            if data['china_10y'] and data['china_2y']:
                data['spread'] = round(data['china_10y'] - data['china_2y'], 2)
            data['date'] = str(latest.get('日期', ''))
    except Exception as e:
        data['error'] = str(e)
    return data

def _get_vix_proxy() -> Dict:
    """VIX 代理：基于沪深300 20日波动率。"""
    proxy = {'vix_proxy': None, 'level': 'normal'}
    df = _get_index_data('000300', '60d')
    if df is not None and len(df) >= 20:
        vol = df['close'].pct_change().rolling(20).std().iloc[-1] * np.sqrt(252) * 100
        proxy['vix_proxy'] = round(vol, 2)
        if vol < 15: proxy['level'] = 'low'
        elif vol > 30: proxy['level'] = 'high'
        elif vol > 45: proxy['level'] = 'extreme'
    return proxy

def _get_risk_on_off(macro_score: float, us_macro: Dict, china_macro: Dict, vix_proxy: Dict, yield_curve: Dict) -> Dict:
    """
    判断 Risk-on / Risk-off 状态。
    Risk-on：市场偏好风险资产（股市、商品、新兴市场）。
    Risk-off：市场偏好避险资产（美债、美元、黄金、现金）。
    """
    score = 0
    reasons = []

    # 1. 宏观评分
    if macro_score >= RISK_ON_SCORE:
        score += 2; reasons.append("A股宏观偏多")
    elif macro_score <= RISK_OFF_SCORE:
        score -= 2; reasons.append("A股宏观偏空")

    # 2. 美国利率：高利率压制风险资产
    fed_rate = us_macro.get('fed_rate')
    if fed_rate is not None:
        if fed_rate > 5.0:
            score -= 2; reasons.append(f"美联储利率高({fed_rate}%)")
        elif fed_rate < 3.0:
            score += 1; reasons.append(f"美联储利率低({fed_rate}%)")

    # 3. CPI：高通胀且未回落
    cpi = us_macro.get('cpi_yoy')
    if cpi is not None:
        if cpi > 4.0:
            score -= 1; reasons.append(f"美国CPI高({cpi}%)")
        elif cpi < 2.5:
            score += 1; reasons.append(f"美国CPI温和({cpi}%)")

    # 4. 中国货币政策：LPR/Shibor 下行偏松，Risk-on
    lpr_5y = china_macro.get('lpr_5y')
    if lpr_5y is not None and lpr_5y < 4.0:
        score += 1; reasons.append(f"中国LPR宽松({lpr_5y}%)")
    shibor_3m = china_macro.get('shibor_3m')
    if shibor_3m is not None and shibor_3m < 2.0:
        score += 1; reasons.append(f"Shibor低位({shibor_3m}%)")

    # 5. 中国 PMI
    pmi = china_macro.get('pmi')
    if pmi is not None:
        if pmi > 50:
            score += 1; reasons.append(f"PMI扩张({pmi})")
        else:
            score -= 1; reasons.append(f"PMI收缩({pmi})")

    # 6. 波动率
    if vix_proxy.get('level') == 'high':
        score -= 2; reasons.append("波动率高")
    elif vix_proxy.get('level') == 'low':
        score += 1; reasons.append("波动率低")

    # 7. 收益率曲线倒挂
    spread = yield_curve.get('spread')
    if spread is not None and spread < 0:
        score -= 2; reasons.append("收益率曲线倒挂")

    # 判定
    if score >= 2:
        state = 'risk_on'
        label = 'Risk-on（偏好风险资产）'
    elif score <= -2:
        state = 'risk_off'
        label = 'Risk-off（偏好避险资产）'
    else:
        state = 'neutral'
        label = '中性（风险/避险平衡）'

    return {
        'state': state,
        'label': label,
        'score': score,
        'reasons': reasons,
    }

def _get_china_macro_data() -> Dict:
    """获取中国宏观数据：LPR、存款准备金率、Shibor、M2、PMI、CPI、PPI、GDP。"""
    data = {'lpr_1y': None, 'lpr_5y': None, 'rrr_large': None, 'rrr_small': None,
            'shibor_1w': None, 'shibor_3m': None, 'm2_yoy': None, 'm1_yoy': None,
            'pmi': None, 'cpi_yoy': None, 'ppi_yoy': None, 'gdp_yoy': None}
    try:
        import akshare as ak
        # LPR
        lpr = ak.macro_china_lpr()
        if lpr is not None and not lpr.empty:
            latest = lpr.iloc[-1]
            data['lpr_1y'] = _safe_float(latest.get('LPR1Y'))
            data['lpr_5y'] = _safe_float(latest.get('LPR5Y'))
            data['lpr_date'] = str(latest.get('TRADE_DATE', ''))

        # 存款准备金率（akshare 最新在头部）
        rrr = ak.macro_china_reserve_requirement_ratio()
        if rrr is not None and not rrr.empty:
            latest = rrr.iloc[0]
            data['rrr_large'] = _safe_float(latest.get('大型金融机构-调整后'))
            data['rrr_small'] = _safe_float(latest.get('中小金融机构-调整后'))
            data['rrr_date'] = str(latest.get('生效时间', ''))

        # Shibor
        shibor = ak.macro_china_shibor_all()
        if shibor is not None and not shibor.empty:
            latest = shibor.iloc[-1]
            data['shibor_1w'] = _safe_float(latest.get('1W-定价'))
            data['shibor_3m'] = _safe_float(latest.get('3M-定价'))
            data['shibor_date'] = str(latest.get('日期', ''))

        # M2/M1（akshare 最新在头部）
        m2 = ak.macro_china_money_supply()
        if m2 is not None and not m2.empty:
            latest = m2.iloc[0]
            data['m2_yoy'] = _safe_float(latest.get('货币和准货币(M2)-同比增长'))
            data['m1_yoy'] = _safe_float(latest.get('货币(M1)-同比增长'))
            data['m2_date'] = str(latest.get('月份', ''))

        # PMI：按日期排序取最新有效值
        pmi = ak.macro_china_pmi_yearly()
        if pmi is not None and not pmi.empty:
            row = pmi[pmi['商品'] == '中国官方制造业PMI']
            if not row.empty and '日期' in row.columns:
                row = row.sort_values('日期', ascending=False)
                for _, r in row.iterrows():
                    v = _safe_float(r.get('今值'))
                    if v == v:  # 允许 0 但跳过 NaN
                        data['pmi'] = v
                        data['pmi_date'] = str(r.get('日期', ''))
                        break

        # CPI：按日期排序取最新有效值
        cpi = ak.macro_china_cpi_yearly()
        if cpi is not None and not cpi.empty:
            row = cpi[cpi['商品'] == '中国CPI年率报告']
            if not row.empty and '日期' in row.columns:
                row = row.sort_values('日期', ascending=False)
                for _, r in row.iterrows():
                    v = _safe_float(r.get('今值'))
                    if v == v:  # 允许 0 但跳过 NaN
                        data['cpi_yoy'] = v
                        data['china_cpi_date'] = str(r.get('日期', ''))
                        break

        # PPI：按日期排序取最新有效值
        ppi = ak.macro_china_ppi_yearly()
        if ppi is not None and not ppi.empty:
            row = ppi[ppi['商品'] == '中国PPI年率报告']
            if not row.empty and '日期' in row.columns:
                row = row.sort_values('日期', ascending=False)
                for _, r in row.iterrows():
                    v = _safe_float(r.get('今值'))
                    if v == v:  # 允许 0 但跳过 NaN
                        data['ppi_yoy'] = v
                        data['ppi_date'] = str(r.get('日期', ''))
                        break

        # GDP：按日期排序取最新有效值
        gdp = ak.macro_china_gdp_yearly()
        if gdp is not None and not gdp.empty:
            row = gdp[gdp['商品'] == '中国GDP年率报告']
            if not row.empty and '日期' in row.columns:
                row = row.sort_values('日期', ascending=False)
                for _, r in row.iterrows():
                    v = _safe_float(r.get('今值'))
                    if v == v:  # 允许 0 但跳过 NaN
                        data['gdp_yoy'] = v
                        data['gdp_date'] = str(r.get('日期', ''))
                        break
    except Exception as e:
        data['error'] = str(e)
    return data

def _get_sector_rotation_proxy() -> Dict:
    """板块轮动热度代理：基于主要行业 ETF / 指数相对强弱。"""
    sectors = {
        '512010': '医药ETF',
        '512480': '半导体ETF',
        '512760': '芯片ETF',
        '515030': '新能源车ETF',
        '515790': '光伏ETF',
        '510880': '红利ETF',
        '512800': '银行ETF',
        '512200': '房地产ETF',
    }
    heat = []
    for ticker, name in sectors.items():
        try:
            df = _get_index_data(ticker, '20d')
            if df is not None and len(df) >= 5:
                ret_5d = (df['close'].iloc[-1] / df['close'].iloc[-6] - 1) * 100 if len(df) >= 6 else 0
                ret_20d = (df['close'].iloc[-1] / df['close'].iloc[-21] - 1) * 100 if len(df) >= 21 else 0
                heat.append({'ticker': ticker, 'name': name, 'ret_5d': round(ret_5d, 2), 'ret_20d': round(ret_20d, 2)})
        except Exception:
            pass
    heat = sorted(heat, key=lambda x: x['ret_5d'], reverse=True)
    return {'heat': heat[:10], 'top_sector': heat[0]['name'] if heat else 'unknown'}

def _get_global_semi_momentum() -> Dict:
    """获取全球半导体（美日韩）综合动量。"""
    try:
        from core.global_semi_data import get_global_semi_momentum
        return get_global_semi_momentum()
    except Exception as e:
        return {'error': str(e), 'composite_score': 50, 'composite_signal': 'neutral'}


def _score_china_macro(china_macro: Dict, yield_curve: Dict) -> float:
    """基于中国宏观数据计算综合宏观偏置评分（0-100）。

    因子：
      - PMI: 50为荣枯线，偏离越大信号越强（权重30%）
      - Shibor 趋势: 3M利率趋势（权重20%）
      - 收益率曲线斜率: 10Y-2Y利差，倒挂=偏空（权重20%）
      - M2增速: 货币宽松=偏多，收紧=偏空（权重15%）
      - CPI/PPI: 通缩偏空，温和通胀中性，恶性通胀偏空（权重15%）
    """
    score = 50.0

    # 1. PMI (30%)
    pmi = china_macro.get('pmi')
    if pmi is not None:
        # PMI > 50 偏多，< 50 偏空，偏离越大信号越强
        pmi_bias = (pmi - 50) * 3  # PMI=49 → -3分；PMI=51 → +3分
        score += pmi_bias * 0.30

    # 2. Shibor 3M 趋势 (20%) - 用最近两次的差值判断趋势
    shibor_3m = china_macro.get('shibor_3m')
    # 该模块的数据是标量（最新值），无历史，所以用绝对值判断
    if shibor_3m is not None:
        if shibor_3m > 2.5:
            score -= 5 * 0.20  # 利率过高=紧缩偏空
        elif shibor_3m < 1.5:
            score += 3 * 0.20  # 利率过低=宽松偏多
        # 1.5-2.5 正常范围，不调整

    # 3. 收益率曲线斜率 (20%)
    spread = yield_curve.get('spread', 0)
    if spread is not None:
        if isinstance(spread, (int, float)):
            if spread < 0:
                score -= 10 * 0.20  # 倒挂=衰退信号
            elif spread < 0.5:
                score -= 3 * 0.20   # 平坦=中性偏空
            elif spread > 1.5:
                score += 5 * 0.20   # 陡峭=经济扩张

    # 4. M2增速 (15%)
    m2 = china_macro.get('m2_yoy')
    if m2 is not None:
        if m2 < 8:
            score -= 3 * 0.15  # 货币收紧
        elif m2 > 12:
            score += 3 * 0.15  # 大幅宽松

    # 5. CPI/PPI (15%)
    cpi = china_macro.get('cpi_yoy')
    ppi = china_macro.get('ppi_yoy')
    if cpi is not None:
        if cpi < 0:
            score -= 8 * 0.15  # 通缩=严重偏空
        elif cpi > 5:
            score -= 5 * 0.15  # 恶性通胀=偏空
        elif cpi > 3:
            score -= 2 * 0.15  # 通胀偏高=微偏空
    if ppi is not None:
        if ppi < -3:
            score -= 5 * 0.15  # 工业通缩
        elif ppi > 5:
            score -= 3 * 0.15  # 工业过热

    return max(0, min(100, round(score, 1)))

def analyze(current_date: Optional[str] = None) -> Dict:
    """
    宏观市场分析主入口。
    返回宏观评分、市场方向和关键指标。
    """
    if current_date is None:
        current_date = datetime.now().strftime('%Y-%m-%d')

    index_scores = []
    for ticker, name in INDEX_TICKERS.items():
        s = _calc_index_score(ticker, name)
        s['name'] = name
        index_scores.append(s)

    breadth = _get_market_breadth()
    us_macro = _get_us_macro_data()
    china_macro = _get_china_macro_data()
    yield_curve = _get_yield_curve()
    vix_proxy = _get_vix_proxy()
    risk_on_off = _get_risk_on_off(50, us_macro, china_macro, vix_proxy, yield_curve)
    sector_rotation = _get_sector_rotation_proxy()
    global_semi = _get_global_semi_momentum()

    # 综合宏观得分（指数动量40% + 市场广度15% + 中国宏观数据30% + 风险修正）
    avg_index_score = sum(s['score'] for s in index_scores) / len(index_scores) if index_scores else 50
    china_macro_bias = _score_china_macro(china_macro, yield_curve)
    risk_adjustment = 0
    if risk_on_off['state'] == 'risk_off':
        risk_adjustment = -5
    elif risk_on_off['state'] == 'risk_on':
        risk_adjustment = 3
    macro_score = round(avg_index_score * 0.40 + breadth['score'] * 0.15 + china_macro_bias * 0.30 + risk_adjustment, 1)
    macro_score = max(0, min(100, macro_score))

    # 重新计算 Risk-on/off 使用真实宏观评分
    risk_on_off = _get_risk_on_off(macro_score, us_macro, china_macro, vix_proxy, yield_curve)

    # 宏观信号：阈值设置较敏感，50 为中性
    if macro_score >= 55:
        macro_signal = 'bullish'
    elif macro_score <= 45:
        macro_signal = 'bearish'
    else:
        macro_signal = 'neutral'

    # 生成报告
    summary_lines = [
        f"# 宏观市场分析报告 ({current_date})",
        "",
        f"综合宏观评分: {macro_score}/100 | 信号: {macro_signal} | 状态: {risk_on_off['label']}",
        "",
        "## 大盘指数",
    ]
    for s in index_scores:
        summary_lines.append(
            f"- {s.get('name', s['ticker'])}({s['ticker']}): 评分{s['score']}, 趋势{s['trend']}, "
            f"1日{s['return_1d']:+.2f}%, 5日{s['return_5d']:+.2f}%, 20日{s['return_20d']:+.2f}%"
        )
    summary_lines.append("")
    summary_lines.append(f"## 市场广度")
    summary_lines.append(f"- 指数上涨: {breadth['advances']} / 下跌: {breadth['declines']}")
    summary_lines.append(f"- 广度得分: {breadth['score']}")
    summary_lines.append(f"- 涨停家数: {breadth['limit_up']}")
    summary_lines.append("")
    summary_lines.append("## 中国宏观（akshare）")
    summary_lines.append(f"- LPR 1Y: {china_macro.get('lpr_1y')}%, 5Y: {china_macro.get('lpr_5y')}% (日期: {china_macro.get('lpr_date')})")
    summary_lines.append(f"- 存款准备金率: 大型{china_macro.get('rrr_large')}%, 中小{china_macro.get('rrr_small')}% (日期: {china_macro.get('rrr_date')})")
    summary_lines.append(f"- Shibor 1W: {china_macro.get('shibor_1w')}%, 3M: {china_macro.get('shibor_3m')}% (日期: {china_macro.get('shibor_date')})")
    summary_lines.append(f"- M2同比: {china_macro.get('m2_yoy')}%, M1同比: {china_macro.get('m1_yoy')}% (日期: {china_macro.get('m2_date')})")
    summary_lines.append(f"- PMI: {china_macro.get('pmi')} (日期: {china_macro.get('pmi_date')})")
    summary_lines.append(f"- CPI同比: {china_macro.get('cpi_yoy')}%, PPI同比: {china_macro.get('ppi_yoy')}% (日期: {china_macro.get('china_cpi_date')})")
    summary_lines.append(f"- GDP同比: {china_macro.get('gdp_yoy')}% (日期: {china_macro.get('gdp_date')})")
    summary_lines.append("")
    summary_lines.append("## 美国宏观（akshare）")
    summary_lines.append(f"- 联邦利率: {us_macro.get('fed_rate')}% (日期: {us_macro.get('fed_rate_date')})")
    summary_lines.append(f"- CPI 同比: {us_macro.get('cpi_yoy')}% (日期: {us_macro.get('cpi_date')})")
    summary_lines.append(f"- 失业率: {us_macro.get('unemployment')}% (日期: {us_macro.get('unemployment_date')})")
    summary_lines.append(f"- 初请失业金: {us_macro.get('initial_jobless')}万 (日期: {us_macro.get('initial_jobless_date')})")
    summary_lines.append("")
    summary_lines.append("## 收益率曲线与波动率")
    summary_lines.append(f"- 中国10Y: {yield_curve.get('china_10y')}%, 2Y: {yield_curve.get('china_2y')}%, 利差: {yield_curve.get('spread')}")
    summary_lines.append(f"- 美国10Y代理: {yield_curve.get('us_10y_proxy')}%")
    summary_lines.append(f"- VIX 代理(A股波动率): {vix_proxy.get('vix_proxy')}, 状态: {vix_proxy.get('level')}")
    summary_lines.append("")
    summary_lines.append("## 全球半导体动量（美日韩）")
    summary_lines.append(f"- 综合得分: {global_semi.get('composite_score', 50)} / 信号: {global_semi.get('composite_signal', 'neutral')}")
    summary_lines.append(f"- 美股: {global_semi.get('us', {}).get('score', 50)} (5日 {global_semi.get('us', {}).get('ret_5d_avg', 0):+.2f}%, 20日 {global_semi.get('us', {}).get('ret_20d_avg', 0):+.2f}%)")
    summary_lines.append(f"- 日本: {global_semi.get('jp', {}).get('score', 50)} (5日 {global_semi.get('jp', {}).get('ret_5d_avg', 0):+.2f}%, 20日 {global_semi.get('jp', {}).get('ret_20d_avg', 0):+.2f}%)")
    summary_lines.append(f"- 韩国: {global_semi.get('kr', {}).get('score', 50)} (5日 {global_semi.get('kr', {}).get('ret_5d_avg', 0):+.2f}%, 20日 {global_semi.get('kr', {}).get('ret_20d_avg', 0):+.2f}%)")
    summary_lines.append("")
    summary_lines.append("## 板块轮动")
    if sector_rotation['heat']:
        summary_lines.append(f"- 领涨板块: {sector_rotation['top_sector']}")
        for h in sector_rotation['heat'][:5]:
            summary_lines.append(f"  - {h['name']}: 5日{h['ret_5d']:+.2f}%, 20日{h['ret_20d']:+.2f}%")

    return {
        'analyst': '宏观市场分析师',
        'macro_score': macro_score,
        'macro_signal': macro_signal,
        'risk_on_off': risk_on_off,
        'index_scores': index_scores,
        'market_breadth': breadth,
        'us_macro': us_macro,
        'china_macro': china_macro,
        'china_macro_bias': china_macro_bias,
        'yield_curve': yield_curve,
        'vix_proxy': vix_proxy,
        'sector_rotation': sector_rotation,
        'global_semi': global_semi,
        'summary': "\n".join(summary_lines),
    }

def get_macro_score_override(macro_report: Dict, individual_signal: str) -> float:
    """
    返回对单个标的信号的修正分数。
    - 如果宏观看多，bullish 信号加分，bearish 信号减分
    - 如果宏观看空，bearish 信号加分，bullish 信号减分
    - 如果宏观中性，修正小
    """
    macro_signal = macro_report.get('macro_signal', 'neutral')
    macro_score = macro_report.get('macro_score', 50)
    # 距离中性（50）越远，修正幅度越大；熊市环境下最高可修正 12 分
    strength = abs(macro_score - 50) / 50  # 0~1
    base = 8
    if macro_signal == 'bullish':
        return base * strength if individual_signal == 'bullish' else -base * strength if individual_signal == 'bearish' else 0
    elif macro_signal == 'bearish':
        return base * strength if individual_signal == 'bearish' else -base * strength if individual_signal == 'bullish' else 0
    return 0

if __name__ == '__main__':
    r = analyze()
    print(r['summary'])
    print(f"\nmacro_score: {r['macro_score']} | signal: {r['macro_signal']} | risk_on_off: {r['risk_on_off']['state']}")
    print('修正示例 bullish:', get_macro_score_override(r, 'bullish'))
    print('修正示例 bearish:', get_macro_score_override(r, 'bearish'))
