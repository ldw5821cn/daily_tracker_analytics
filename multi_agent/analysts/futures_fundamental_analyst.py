"""
期货基本面分析师
数据源（免费）：akshare
- 库存：东方财富 futures_inventory_em
- 现货/基差：生意社 futures_spot_price_daily
- 外盘期货：新浪外盘 futures_foreign_hist
- 仓单：郑商所/大商所/上期所 futures_warehouse_receipt_*
"""
import sys
sys.path.insert(0, '/home/liudawei/github/daily_tracker_analytics/multi_agent')

import json
from datetime import datetime, timedelta
from typing import Dict, Optional
from dataclasses import dataclass

import warnings
warnings.filterwarnings('ignore')


# 品种代码映射：系统代码 -> akshare 名称/代码
VAR_MAP = {
    'RM0': {'name': '菜粕', 'spot_symbol': 'RM', 'exchange': 'CZCE'},
    'M0':  {'name': '豆粕', 'spot_symbol': 'M',  'exchange': 'DCE'},
    'C0':  {'name': '玉米', 'spot_symbol': 'C',  'exchange': 'DCE'},
    'SR0': {'name': '白糖', 'spot_symbol': 'SR', 'exchange': 'CZCE'},
    'CF0': {'name': '棉花', 'spot_symbol': 'CF', 'exchange': 'CZCE'},
    'TA0': {'name': 'PTA',  'spot_symbol': 'TA', 'exchange': 'CZCE'},
    'MA0': {'name': '甲醇', 'spot_symbol': 'MA', 'exchange': 'CZCE'},
    'FG0': {'name': '玻璃', 'spot_symbol': 'FG', 'exchange': 'CZCE'},
    'RB0': {'name': '螺纹钢','spot_symbol': 'RB', 'exchange': 'SHFE'},
    'HC0': {'name': '热卷', 'spot_symbol': 'HC', 'exchange': 'SHFE'},
    'I0':  {'name': '铁矿石','spot_symbol': 'I',  'exchange': 'DCE'},
    'CU0': {'name': '沪铜', 'spot_symbol': 'CU', 'exchange': 'SHFE'},
    'AU0': {'name': '沪金', 'spot_symbol': 'AU', 'exchange': 'SHFE'},
    'AG0': {'name': '沪银', 'spot_symbol': 'AG', 'exchange': 'SHFE'},
    'SC0': {'name': '原油', 'spot_symbol': 'SC', 'exchange': 'INE'},
}

# 外盘相关映射：品种 -> 外盘代码/影响方向
FOREIGN_MAP = {
    'M0':  [{'symbol': 'SM', 'name': '美豆粕', 'weight': 0.6}, {'symbol': 'S', 'name': '美豆', 'weight': 0.4}],
    'RM0': [{'symbol': 'SM', 'name': '美豆粕', 'weight': 0.5}, {'symbol': 'S', 'name': '美豆', 'weight': 0.3}],
    'C0':  [{'symbol': 'C', 'name': '美玉米', 'weight': 1.0}],
    'SR0': [{'symbol': 'SB', 'name': '原糖', 'weight': 1.0}],
    'CF0': [{'symbol': 'CT', 'name': '美棉', 'weight': 1.0}],
}


def _try_akshare(fn, default=None, **kwargs):
    try:
        import akshare as ak
        return fn(ak, **kwargs)
    except Exception as e:
        return default


def _get_inventory(name: str, lookback: int = 20) -> Optional[Dict]:
    """获取库存趋势。"""
    def fetch(ak):
        df = ak.futures_inventory_em(symbol=name)
        if df is None or df.empty:
            return None
        df = df.tail(lookback).copy()
        # 库存最近变化
        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else latest
        change = latest['库存'] - prev['库存']
        pct_change = change / prev['库存'] if prev['库存'] else 0
        return {
            'latest_inventory': float(latest['库存']),
            'inventory_change': float(change),
            'inventory_change_pct': round(float(pct_change), 4),
            'inventory_date': str(latest['日期']),
        }
    return _try_akshare(fetch, default=None)


def _get_basis(spot_symbol: str, end_date: str = None) -> Optional[Dict]:
    """获取基差。"""
    def fetch(ak, _end_date=None):
        if _end_date is None:
            _end_date = datetime.now().strftime('%Y%m%d')
        start = (datetime.now() - timedelta(days=30)).strftime('%Y%m%d')
        df = ak.futures_spot_price_daily(start_day=start, end_day=_end_date, vars_list=[spot_symbol])
        if df is None or df.empty:
            return None
        df = df.sort_values('date').tail(10)
        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else latest
        basis_chg = float(latest['dom_basis_rate']) - float(prev['dom_basis_rate'])
        return {
            'spot_price': float(latest['spot_price']),
            'dom_price': float(latest['dominant_contract_price']),
            'dom_basis_rate': float(latest['dom_basis_rate']),
            'basis_change_rate': round(basis_chg, 4),
            'basis_date': str(latest['date']),
        }
    return _try_akshare(fetch, default=None, _end_date=end_date)


def _get_foreign(ticker: str, lookback: int = 5) -> Optional[Dict]:
    """获取外盘相关期货涨跌。"""
    specs = FOREIGN_MAP.get(ticker)
    if not specs:
        return None
    def fetch(ak):
        total_return = 0.0
        total_weight = 0.0
        details = []
        for spec in specs:
            df = ak.futures_foreign_hist(symbol=spec['symbol'])
            if df is None or df.empty or len(df) < 2:
                continue
            df = df.tail(lookback)
            latest = df.iloc[-1]
            prev = df.iloc[-2]
            ret = (latest['close'] - prev['close']) / prev['close'] if prev['close'] else 0
            total_return += ret * spec['weight']
            total_weight += spec['weight']
            details.append({
                'name': spec['name'],
                'symbol': spec['symbol'],
                'daily_return': round(float(ret), 4),
                'close': float(latest['close']),
            })
        if total_weight == 0:
            return None
        return {
            'weighted_daily_return': round(total_return / total_weight, 4),
            'details': details,
        }
    return _try_akshare(fetch, default=None)


def _get_warehouse_receipt(ticker: str) -> Optional[Dict]:
    """获取仓单数据。"""
    spec = VAR_MAP.get(ticker)
    if not spec:
        return None
    exchange = spec['exchange']
    def fetch(ak):
        if exchange == 'CZCE':
            r = ak.futures_warehouse_receipt_czce()
        elif exchange == 'DCE':
            r = ak.futures_warehouse_receipt_dce()
        elif exchange == 'SHFE':
            r = ak.futures_shfe_warehouse_receipt()
        else:
            return None
        key = spec['spot_symbol']
        if key not in r:
            return None
        df = r[key]
        # 找到总计行
        total_row = df[df['仓库编号'] == '总计']
        if total_row.empty:
            total_row = df[df['仓库编号'] == '小计']
        if total_row.empty:
            return None
        qty = total_row.iloc[0].get('仓单数量', 0)
        change = total_row.iloc[0].get('当日增减', 0)
        return {
            'warehouse_receipt': float(qty) if qty else 0,
            'warehouse_change': float(change) if change else 0,
        }
    return _try_akshare(fetch, default=None)


def _score_fundamentals(inv: Dict, basis: Dict, foreign: Dict, wh: Dict) -> Dict:
    """
    基本面打分，0-100，50 为中性。
    偏空信号：库存增加、基差走弱/深度贴水扩大、外盘跌、仓单增加
    偏多信号：库存减少、基差走强/升水扩大、外盘涨、仓单减少
    """
    scores = []
    weights = []
    reasons = []

    # 库存：库存增加利空，库存减少利多；使用 5 日变化率平滑单日跳变
    if inv:
        pct = inv['inventory_change_pct']
        if pct < -0.05:
            scores.append(60); weights.append(1.0); reasons.append('库存下降')
        elif pct < -0.02:
            scores.append(55); weights.append(1.0); reasons.append('库存下降')
        elif pct > 0.05:
            scores.append(40); weights.append(1.0); reasons.append('库存上升')
        elif pct > 0.02:
            scores.append(45); weights.append(1.0); reasons.append('库存上升')
        else:
            scores.append(50); weights.append(1.0); reasons.append('库存持平')

    # 基差：基差率上升（现货相对走强）利多，下降利空；走弱是重要现货疲弱信号
    if basis:
        br = basis['dom_basis_rate']
        bc = basis['basis_change_rate']
        if bc > 0.005:
            scores.append(65); weights.append(1.5); reasons.append('基差走强')
        elif bc < -0.005:
            scores.append(30); weights.append(1.5); reasons.append('基差走弱')
        elif bc > 0.001:
            scores.append(55); weights.append(1.5); reasons.append('基差微强')
        elif bc < -0.001:
            scores.append(45); weights.append(1.5); reasons.append('基差微弱')
        else:
            scores.append(50); weights.append(1.5); reasons.append('基差持平')
        # 深度贴水（期货大幅低于现货）偏空，且走弱扩大贴水更利空
        if br < -0.05:
            scores[-1] = max(0, scores[-1] - 12)
            reasons[-1] += ' 深度贴水'
        elif br < -0.03:
            scores[-1] = max(0, scores[-1] - 5)
            reasons[-1] += ' 中度贴水'

    # 外盘：外盘上涨利多，下跌利空；权重提高，对下跌趋势更敏感
    if foreign:
        r = foreign['weighted_daily_return']
        if r > 0.01:
            scores.append(65); weights.append(1.5); reasons.append(f'外盘上涨 {r:.2%}')
        elif r < -0.01:
            scores.append(35); weights.append(1.5); reasons.append(f'外盘下跌 {r:.2%}')
        elif r > 0.003:
            scores.append(55); weights.append(1.5); reasons.append(f'外盘微涨 {r:.2%}')
        elif r < -0.003:
            scores.append(45); weights.append(1.5); reasons.append(f'外盘微跌 {r:.2%}')
        else:
            scores.append(50); weights.append(1.5); reasons.append('外盘持平')

    # 仓单：仓单增加利空，减少利多
    if wh:
        if wh['warehouse_change'] < 0:
            scores.append(60); weights.append(0.6); reasons.append('仓单减少')
        elif wh['warehouse_change'] > 0:
            scores.append(40); weights.append(0.6); reasons.append('仓单增加')
        else:
            scores.append(50); weights.append(0.6); reasons.append('仓单持平')

    if not scores:
        return {'score': 50, 'bias': 'neutral', 'reasons': ['无基本面数据']}

    total_weight = sum(weights)
    score = sum(s * w for s, w in zip(scores, weights)) / total_weight
    score = max(0, min(100, score))

    if score > 55:
        bias = 'bullish'
    elif score < 45:
        bias = 'bearish'
    else:
        bias = 'neutral'

    return {
        'score': round(score, 1),
        'bias': bias,
        'reasons': reasons,
    }


def analyze(ticker: str, name: str = "") -> Dict:
    """
    分析单个期货品种的基本面。
    """
    spec = VAR_MAP.get(ticker)
    if not spec:
        return {
            'ticker': ticker,
            'name': name,
            'score': 50,
            'bias': 'neutral',
            'reasons': ['未映射品种'],
            'data': {},
        }

    inv = _get_inventory(spec['name'])
    basis = _get_basis(spec['spot_symbol'])
    foreign = _get_foreign(ticker)
    wh = _get_warehouse_receipt(ticker)

    result = _score_fundamentals(inv, basis, foreign, wh)

    return {
        'ticker': ticker,
        'name': name or spec['name'],
        'score': result['score'],
        'bias': result['bias'],
        'reasons': result['reasons'],
        'data': {
            'inventory': inv,
            'basis': basis,
            'foreign': foreign,
            'warehouse': wh,
        },
    }


if __name__ == '__main__':
    for tk in ['RM0', 'M0', 'C0']:
        r = analyze(tk)
        print(f"\n{tk}: {r['name']}")
        print(f"  score={r['score']} bias={r['bias']}")
        print(f"  reasons: {r['reasons']}")
        print(f"  data: {json.dumps(r['data'], ensure_ascii=False, indent=2, default=str)[:500]}")
