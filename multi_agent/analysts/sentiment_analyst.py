#!/usr/bin/env python3
"""情绪分析器：利用涨停股池 + 龙虎榜 + 海外社媒/搜索计算个股情绪评分。

评分逻辑：
  1. 涨停股池 → 涨停加分(+20) / 炸板扣分(-10) / 连板加分(连板数×5)
  2. 龙虎榜 → 机构净买额评分（归一化到[-20,+20]）
  3. 市场总体涨停占比 → 广度情绪偏置（对所有品种微调）
  4. 海外社媒/搜索 → 对美股/ETF 做 overlay（30% 权重）
  5. 同花顺特色数据 → 热股榜/涨停池/连板天梯/龙虎榜叠加（当 akshare 不可用时 fallback）
  6. 默认值为 50（中性）

用法：
  from analysts.sentiment_analyst import compute_sentiment_score
  score, detail = compute_sentiment_score('600028', date='2026-07-15', category='个股')
"""
import json, os, sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import akshare as ak
import pandas as pd

# 加载海外社媒情绪模块
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

MULTI_AGENT = Path(PROJECT_ROOT) / 'multi_agent'
HITHINK_CACHE = MULTI_AGENT / 'data' / 'hithink_cache'

# ─── 缓存（避免同一交易日多次调用 API）────────────────────────
_CACHE: Dict[str, dict] = {}


def _today() -> str:
    return datetime.now().strftime('%Y-%m-%d')


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


def _load_hithink_special(date_str: str) -> Dict[str, Dict]:
    """加载同花顺特色数据并按 6 位代码索引。"""
    # date_str 是 20260827 格式；文件名为 2026-08-27
    date_file = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    code_map: Dict[str, Dict] = {}
    if not HITHINK_CACHE.exists():
        return code_map

    files = {
        'limit_up': HITHINK_CACHE / f'limit_up_pool_{date_file}.json',
        'limit_break': HITHINK_CACHE / f'limit_break_pool_{date_file}.json',
        'limit_down': HITHINK_CACHE / f'limit_down_pool_{date_file}.json',
        'limit_ladder': HITHINK_CACHE / f'limit_up_ladder_{date_file}.json',
        'hot_stock': HITHINK_CACHE / f'hot_stock_list_{date_file}.json',
        'dragon_tiger': HITHINK_CACHE / f'dragon_tiger_list_{date_file}.json',
    }

    def _add(code: str, tag: str, payload: Dict):
        code = code or ''
        code = code.replace('.SH', '').replace('.SZ', '').replace('.BJ', '').zfill(6)[:6]
        if not code.isdigit() or len(code) != 6:
            return
        if code not in code_map:
            code_map[code] = {}
        code_map[code][tag] = payload

    for tag, path in files.items():
        if not path.exists():
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            items = data.get('item', [])
            if not isinstance(items, list):
                continue
            for it in items:
                ticker = str(it.get('ticker', ''))
                if tag == 'limit_ladder':
                    ticker = str(it.get('code', ''))
                if not ticker:
                    continue
                _add(ticker, tag, dict(it))
        except Exception:
            continue
    return code_map


def _ticker_to_code(ticker: str) -> str:
    """标准化 ticker 到可用代码。A股补零到 6 位；美股/ETF 保留原样。"""
    code = ticker.upper().replace('.SZ', '').replace('.SH', '').replace('.BJ', '')
    if code.isdigit():
        return code.zfill(6)[:6]
    return code


def _is_a_share(ticker: str, category: Optional[str]) -> bool:
    if category in ('个股', 'A股', 'a-share'):
        return True
    if category in ('US', 'ETF', '期货', 'futures', '美股'):
        return False
    # 通过代码格式判断
    code = _ticker_to_code(ticker)
    return code.isdigit() and len(code) == 6


def _fetch_social_overlay(ticker: str, name: Optional[str], category: str) -> Tuple[float, Dict]:
    """获取海外社媒情绪 overlay，默认权重从参数文件读取。"""
    try:
        from multi_agent.analysts.social_sentiment_analyst import get_social_sentiment
    except ImportError:
        try:
            from analysts.social_sentiment_analyst import get_social_sentiment
        except ImportError:
            return 50.0, {'note': 'module_not_found'}
    res = get_social_sentiment(ticker, name=name, category=category)
    return res.get('social_score', 50.0), res


def _hithink_sentiment(code: str, date_str: str) -> Tuple[float, Dict]:
    """基于同花顺特色数据计算情绪分增量与细节。"""
    hmap = _load_hithink_special(date_str)
    info = hmap.get(code, {})
    if not info:
        return 0.0, {}

    delta = 0.0
    detail: Dict[str, any] = {}

    # 热股榜
    hot = info.get('hot_stock')
    if hot:
        rank = hot.get('rank') or hot.get('serial_number') or 999
        try:
            rank = int(rank)
        except Exception:
            rank = 999
        # Top10 +15, Top20 +10, Top30 +5
        if rank <= 10:
            delta += 15
        elif rank <= 20:
            delta += 10
        elif rank <= 30:
            delta += 5
        detail['hot_stock_rank'] = rank

    # 涨停池
    lu = info.get('limit_up')
    if lu:
        delta += 20
        lianban = lu.get('limit_up_days') or lu.get('limit_up_day') or 1
        try:
            lianban = int(lianban)
        except Exception:
            lianban = 1
        delta += min(lianban * 5, 15)
        detail['limit_up_days'] = lianban

    # 连板天梯
    ladder = info.get('limit_ladder')
    if ladder:
        delta += 20
        days = ladder.get('limit_up_days') or ladder.get('limit_up_day') or 1
        try:
            days = int(days)
        except Exception:
            days = 1
        delta += min(days * 5, 20)
        detail['ladder_days'] = days

    # 炸板池
    lb = info.get('limit_break')
    if lb:
        delta -= 12
        detail['limit_break'] = True

    # 跌停池
    ld = info.get('limit_down')
    if ld:
        delta -= 25
        detail['limit_down'] = True

    # 龙虎榜：按净买入额加分/减分
    dt = info.get('dragon_tiger')
    if dt:
        net_buy = dt.get('net_buy') or dt.get('net_buy_amount') or 0
        try:
            net_buy = float(net_buy)
        except Exception:
            net_buy = 0.0
        # 1亿 = ±5分，上限 ±20
        delta += max(-20, min(20, net_buy / 1e8 * 5))
        detail['dragon_tiger_net_buy'] = net_buy

    return delta, detail


def compute_sentiment_score(
    ticker: str,
    date: Optional[str] = None,
    category: Optional[str] = None,
    name: Optional[str] = None,
    social_overlay_weight: float = 0.30,
    use_hithink: bool = True,
) -> Tuple[float, Dict]:
    """计算个股情绪评分 0-100。

    输入：
      ticker: 股票代码（如 '600028' 或 '600028.SH'）
      date: 交易日（默认今天）
      category: 类别，用于判断是否使用海外社媒 overlay
      name: 中文/英文名称，用于社媒查询
      social_overlay_weight: 海外社媒情绪 overlay 权重
      use_hithink: 是否使用同花顺特色数据增强 A 股情绪
    返回：
      (0-100 的评分, detail dict)
    """
    if date is None:
        date = datetime.now().strftime('%Y%m%d')
    date_str = date.replace('-', '')[:8]

    code = _ticker_to_code(ticker)
    local_score = 50.0
    detail = {'local': {}, 'social': {}}

    a_share = _is_a_share(ticker, category)

    # ── 因子1：涨停/跌停 ──
    try:
        zt_df = _get_zt_pool(date_str)
        if not zt_df.empty and '代码' in zt_df.columns:
            match = zt_df[zt_df['代码'] == code]
            if not match.empty:
                row = match.iloc[0]
                local_score += 20  # 涨停加分
                # 连板加分
                lianban = int(row.get('连板数', 0)) if pd.notna(row.get('连板数', 0)) else 0
                local_score += min(lianban * 5, 15)  # 最多加15
                # 炸板扣分（炸板=首次封板后有打开）
                zhaban = int(row.get('炸板次数', 0)) if pd.notna(row.get('炸板次数', 0)) else 0
                if zhaban > 0:
                    local_score -= min(zhaban * 5, 10)
                detail['local']['zt'] = True
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
                local_score += max(-20, min(20, net_buy / 1e8 * 5))
                detail['local']['lhb'] = True
    except Exception:
        pass

    # ── 因子3：市场总体情绪（涨停占比越高，整体偏多）──
    try:
        zt_df = _get_zt_pool(date_str)
        if not zt_df.empty:
            zt_count = len(zt_df)
            # 72 只涨停 ≈ 中性基准。>100 偏多，<40 偏空
            market_bias = (zt_count - 72) / 3  # 每3只偏离1分
            local_score += max(-5, min(5, market_bias))
            detail['local']['zt_count'] = zt_count
    except Exception:
        pass

    # ── 因子4：同花顺特色数据增强（A股）──
    hithink_delta = 0.0
    hithink_detail = {}
    if a_share and use_hithink:
        try:
            hithink_delta, hithink_detail = _hithink_sentiment(code, date_str)
            local_score += hithink_delta
            detail['local']['hithink'] = hithink_detail
        except Exception:
            pass

    local_score = max(0, min(100, round(local_score, 1)))

    # ── 因子5：海外社媒/搜索 overlay（仅非 A股/港股，可选）──
    social_score = 50.0
    social_detail = {'note': 'skipped_for_a_share' if a_share else 'not_called'}
    if not a_share:
        try:
            social_score, social_detail = _fetch_social_overlay(code, name, category or 'US')
        except Exception as e:
            social_detail = {'note': f'error:{type(e).__name__}'}

    # 合并：local 为主，social 作为 overlay
    if a_share or social_score == 50.0:
        final_score = local_score
    else:
        # 从参数文件读取 overlay 权重，默认 0.15
        try:
            import json as _json
            _pp = os.path.join(PROJECT_ROOT, 'multi_agent', 'config', 'predictor_params.json')
            with open(_pp, 'r', encoding='utf-8') as _f:
                _p = _json.load(_f)
            _w = _p.get(category, _p.get('_default', {})).get('social_overlay_weight', 0.15)
        except Exception:
            _w = 0.15
        w = max(0.0, min(1.0, _w))
        final_score = (1 - w) * local_score + w * social_score

    detail['local']['score'] = local_score
    detail['social'] = social_detail

    return _clamp(final_score), detail


def _clamp(x: float) -> float:
    return max(0.0, min(100.0, round(x, 1)))


def batch_sentiment(tickers: List[str], date: Optional[str] = None, category: Optional[str] = None) -> Dict[str, Tuple[float, Dict]]:
    """批量计算多只股票的情绪评分（复用缓存）。"""
    return {t: compute_sentiment_score(t, date, category=category) for t in tickers}


if __name__ == '__main__':
    tests = [
        ('600028', '个股', '中国石化'),
        ('NVDA', 'US', 'NVIDIA'),
        ('SMH', 'ETF', 'VanEck Semiconductor ETF'),
    ]
    print(f'=== 情绪评分测试 ({_today()}) ===')
    for t, cat, name in tests:
        s, d = compute_sentiment_score(t, _today(), category=cat, name=name)
        print(f'  {t} ({cat}): {s:.1f}/100  detail={json.dumps(d, ensure_ascii=False)}')
