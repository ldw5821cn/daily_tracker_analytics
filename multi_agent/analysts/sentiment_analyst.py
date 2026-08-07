#!/usr/bin/env python3
"""情绪分析器：利用涨停股池 + 龙虎榜 + 海外社媒/搜索计算个股情绪评分。

评分逻辑：
  1. 涨停股池 → 涨停加分(+20) / 炸板扣分(-10) / 连板加分(连板数×5)
  2. 龙虎榜 → 机构净买额评分（归一化到[-20,+20]）
  3. 市场总体涨停占比 → 广度情绪偏置（对所有品种微调）
  4. 海外社媒/搜索 → 对美股/ETF 做 overlay（30% 权重）
  5. 默认值为 50（中性）

用法：
  from analysts.sentiment_analyst import compute_sentiment_score
  score, detail = compute_sentiment_score('600028', date='2026-07-15', category='个股')
"""
import json, os, sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import akshare as ak
import pandas as pd

# 加载海外社媒情绪模块
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

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


def compute_sentiment_score(
    ticker: str,
    date: Optional[str] = None,
    category: Optional[str] = None,
    name: Optional[str] = None,
    social_overlay_weight: float = 0.30,
) -> Tuple[float, Dict]:
    """计算个股情绪评分 0-100。

    输入：
      ticker: 股票代码（如 '600028' 或 '600028.SH'）
      date: 交易日（默认今天）
      category: 类别，用于判断是否使用海外社媒 overlay
      name: 中文/英文名称，用于社媒查询
      social_overlay_weight: 海外社媒情绪 overlay 权重
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

    local_score = max(0, min(100, round(local_score, 1)))

    # ── 因子4：海外社媒/搜索 overlay（仅非 A股/港股，可选）──
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
    print(f'=== 情绪评分测试 (2026-07-15) ===')
    for t, cat, name in tests:
        s, d = compute_sentiment_score(t, '2026-07-15', category=cat, name=name)
        print(f'  {t} ({cat}): {s:.1f}/100  detail={json.dumps(d, ensure_ascii=False)}')
