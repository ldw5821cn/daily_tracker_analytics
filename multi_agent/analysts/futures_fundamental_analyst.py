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
from typing import Dict, List, Optional
from dataclasses import dataclass

import warnings
warnings.filterwarnings('ignore')


# 舆情基本面辅助函数
def _get_news_sentiment(name: str, pagesize: int = 10) -> Dict:
    """获取期货品种新闻舆情，返回情绪分和原始新闻列表。"""
    try:
        from core.news_engine import fetch_futures_news
        from analysts.news_analyst import _calc_sentiment
        items = fetch_futures_news(name, pagesize=pagesize)
        if not items:
            return {'score': 0.0, 'count': 0, 'items': []}
        sentiment = _calc_sentiment(items)
        return {
            'score': sentiment['score'],
            'count': len(items),
            'positive_keywords': sentiment.get('positive_keywords', []),
            'negative_keywords': sentiment.get('negative_keywords', []),
            'items': items[:5],
        }
    except Exception:
        return {'score': 0.0, 'count': 0, 'items': []}


def _score_news_sentiment(sentiment: Dict) -> float:
    """将新闻舆情得分 [-1, 1] 映射到基本面得分 [35, 65]。"""
    score = 50.0 + sentiment['score'] * 15.0
    return max(0.0, min(100.0, score))


# 品种代码映射：系统代码 -> akshare 名称/代码
VAR_MAP = {
    'AG0': {'name': '白银', 'spot_symbol': 'AG', 'exchange': 'SHFE'},
    'AU0': {'name': '沪金', 'spot_symbol': 'AU', 'exchange': 'SHFE'},
    'C0':  {'name': '玉米', 'spot_symbol': 'C',  'exchange': 'DCE'},
    'CF0': {'name': '棉花', 'spot_symbol': 'CF', 'exchange': 'CZCE'},
    'CU0': {'name': '沪铜', 'spot_symbol': 'CU', 'exchange': 'SHFE'},
    'HC0': {'name': '热卷', 'spot_symbol': 'HC', 'exchange': 'SHFE'},
    'I0':  {'name': '铁矿石','spot_symbol': 'I',  'exchange': 'DCE'},
    'J0':  {'name': '焦炭', 'spot_symbol': 'J',  'exchange': 'DCE'},
    'JM0': {'name': '焦煤', 'spot_symbol': 'JM', 'exchange': 'DCE'},
    'MA0': {'name': '甲醇', 'spot_symbol': 'MA', 'exchange': 'CZCE'},
    'M0':  {'name': '豆粕', 'spot_symbol': 'M',  'exchange': 'DCE'},
    'OI0': {'name': '菜油', 'spot_symbol': 'OI', 'exchange': 'CZCE'},
    'P0':  {'name': '棕榈油','spot_symbol': 'P',  'exchange': 'DCE'},
    'RB0': {'name': '螺纹钢','spot_symbol': 'RB', 'exchange': 'SHFE'},
    'RM0': {'name': '菜粕', 'spot_symbol': 'RM', 'exchange': 'CZCE'},
    'SC0': {'name': '原油', 'spot_symbol': 'SC', 'exchange': 'INE'},
    'SM0': {'name': '硅锰', 'spot_symbol': 'SM', 'exchange': 'CZCE'},
    'SR0': {'name': '白糖', 'spot_symbol': 'SR', 'exchange': 'CZCE'},
    'TA0': {'name': 'PTA',  'spot_symbol': 'TA', 'exchange': 'CZCE'},
    'V0':  {'name': 'PVC', 'spot_symbol': 'V',  'exchange': 'DCE'},
    'AL0': {'name': '沪铝', 'spot_symbol': 'AL', 'exchange': 'SHFE'},
    'ZN0': {'name': '沪锌', 'spot_symbol': 'ZN', 'exchange': 'SHFE'},
    'FG0': {'name': '玻璃', 'spot_symbol': 'FG', 'exchange': 'CZCE'},
    'SA0': {'name': '纯碱', 'spot_symbol': 'SA', 'exchange': 'CZCE'},
    'EB0': {'name': '苯乙烯','spot_symbol': 'EB', 'exchange': 'DCE'},
    'EG0': {'name': '乙二醇','spot_symbol': 'EG', 'exchange': 'DCE'},
    'LU0': {'name': '低硫燃油','spot_symbol': 'LU', 'exchange': 'INE'},
    'FU0': {'name': '燃油', 'spot_symbol': 'FU', 'exchange': 'SHFE'},
    'PG0': {'name': 'LPG', 'spot_symbol': 'PG', 'exchange': 'DCE'},
    'PP0': {'name': '聚丙烯','spot_symbol': 'PP', 'exchange': 'DCE'},
    'L0':  {'name': '塑料', 'spot_symbol': 'L',  'exchange': 'DCE'},
    'BU0': {'name': '沥青', 'spot_symbol': 'BU', 'exchange': 'SHFE'},
    'RU0': {'name': '橡胶', 'spot_symbol': 'RU', 'exchange': 'SHFE'},
    'NR0': {'name': '20号胶','spot_symbol': 'NR', 'exchange': 'INE'},
    'SP0': {'name': '纸浆', 'spot_symbol': 'SP', 'exchange': 'SHFE'},
    'PF0': {'name': '短纤', 'spot_symbol': 'PF', 'exchange': 'CZCE'},
    'UR0': {'name': '尿素', 'spot_symbol': 'UR', 'exchange': 'CZCE'},
    'AP0': {'name': '苹果', 'spot_symbol': 'AP', 'exchange': 'CZCE'},
    'CJ0': {'name': '红枣', 'spot_symbol': 'CJ', 'exchange': 'CZCE'},
    'CY0': {'name': '棉纱', 'spot_symbol': 'CY', 'exchange': 'CZCE'},
    'JD0': {'name': '鸡蛋', 'spot_symbol': 'JD', 'exchange': 'DCE'},
    'LH0': {'name': '生猪', 'spot_symbol': 'LH', 'exchange': 'DCE'},
    'PB0': {'name': '沪铅', 'spot_symbol': 'PB', 'exchange': 'SHFE'},
    'NI0': {'name': '沪镍', 'spot_symbol': 'NI', 'exchange': 'SHFE'},
    'SN0': {'name': '沪锡', 'spot_symbol': 'SN', 'exchange': 'SHFE'},
    'SS0': {'name': '不锈钢','spot_symbol': 'SS', 'exchange': 'SHFE'},
    'BC0': {'name': '国际铜','spot_symbol': 'BC', 'exchange': 'INE'},
    'AO0': {'name': '氧化铝','spot_symbol': 'AO', 'exchange': 'SHFE'},
    'SI0': {'name': '工业硅','spot_symbol': 'SI', 'exchange': 'GFEX'},
    'LC0': {'name': '碳酸锂','spot_symbol': 'LC', 'exchange': 'GFEX'},
}

# 外盘相关映射：品种 -> 外盘代码/影响方向
FOREIGN_MAP = {
    'M0':  [{'symbol': 'SM', 'name': '美豆粕', 'weight': 0.6}, {'symbol': 'S', 'name': '美豆', 'weight': 0.4}],
    'RM0': [{'symbol': 'SM', 'name': '美豆粕', 'weight': 0.5}, {'symbol': 'S', 'name': '美豆', 'weight': 0.3}],
    'C0':  [{'symbol': 'C', 'name': '美玉米', 'weight': 1.0}],
    'SR0': [{'symbol': 'SB', 'name': '原糖', 'weight': 1.0}],
    'CF0': [{'symbol': 'CT', 'name': '美棉', 'weight': 1.0}],
    'CU0': [{'symbol': 'HG', 'name': '美精铜', 'weight': 1.0}],
    'AL0': [{'symbol': 'AL', 'name': '伦铝', 'weight': 1.0}],
    'ZN0': [{'symbol': 'ZN', 'name': '伦锌', 'weight': 1.0}],
    'AU0': [{'symbol': 'GC', 'name': '美黄金', 'weight': 1.0}],
    'AG0': [{'symbol': 'SI', 'name': '美白银', 'weight': 1.0}],
    'SC0': [{'symbol': 'CL', 'name': 'WTI原油', 'weight': 0.5}, {'symbol': 'BZ', 'name': '布伦特原油', 'weight': 0.5}],
    'TA0': [{'symbol': 'CL', 'name': 'WTI原油', 'weight': 0.6}, {'symbol': 'BZ', 'name': '布伦特原油', 'weight': 0.4}],
    'MA0': [{'symbol': 'NG', 'name': '美天然气', 'weight': 0.4}, {'symbol': 'CL', 'name': 'WTI原油', 'weight': 0.4}, {'symbol': 'BZ', 'name': '布伦特原油', 'weight': 0.2}],
    'RB0': [{'symbol': 'GC', 'name': '美黄金', 'weight': 0.0}],  # 螺纹钢暂无直接外盘，用0权重占位
    'I0':  [{'symbol': 'GC', 'name': '美黄金', 'weight': 0.0}],
    'HC0': [{'symbol': 'GC', 'name': '美黄金', 'weight': 0.0}],
    'P0':  [{'symbol': 'GC', 'name': '美黄金', 'weight': 0.0}],
    'OI0': [{'symbol': 'GC', 'name': '美黄金', 'weight': 0.0}],
    'J0':  [{'symbol': 'GC', 'name': '美黄金', 'weight': 0.0}],
    'JM0': [{'symbol': 'GC', 'name': '美黄金', 'weight': 0.0}],
    'SM0': [{'symbol': 'GC', 'name': '美黄金', 'weight': 0.0}],
    'V0':  [{'symbol': 'GC', 'name': '美黄金', 'weight': 0.0}],
    'FG0': [{'symbol': 'GC', 'name': '美黄金', 'weight': 0.0}],
    'SA0': [{'symbol': 'GC', 'name': '美黄金', 'weight': 0.0}],
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


def _score_fundamentals(inv: Dict, basis: Dict, foreign: Dict, wh: Dict, news_sentiment: Dict = None) -> Dict:
    """
    基本面打分，0-100，50 为中性。
    偏空信号：库存增加、基差走弱/深度贴水扩大、外盘跌、仓单增加、舆情偏空
    偏多信号：库存减少、基差走强/升水扩大、外盘涨、仓单减少、舆情偏多
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

    # 新闻舆情：作为增量信号，权重 0.3；有 5 条以上新闻才参与
    if news_sentiment and news_sentiment.get('count', 0) >= 5:
        news_score = _score_news_sentiment(news_sentiment)
        scores.append(news_score)
        weights.append(0.3)
        news_bias = '舆情偏多' if news_sentiment['score'] > 0.1 else '舆情偏空' if news_sentiment['score'] < -0.1 else '舆情中性'
        reasons.append(f"{news_bias}({news_sentiment['score']:+.2f})")

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
    news_sentiment = _get_news_sentiment(spec['name'])

    result = _score_fundamentals(inv, basis, foreign, wh, news_sentiment)

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
            'news_sentiment': news_sentiment,
        },
    }


if __name__ == '__main__':
    for tk in ['RM0', 'M0', 'C0']:
        r = analyze(tk)
        print(f"\n{tk}: {r['name']}")
        print(f"  score={r['score']} bias={r['bias']}")
        print(f"  reasons: {r['reasons']}")
        print(f"  data: {json.dumps(r['data'], ensure_ascii=False, indent=2, default=str)[:500]}")
