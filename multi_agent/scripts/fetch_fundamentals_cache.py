#!/usr/bin/env python3
"""预拉取 A 股个股财务/估值数据并缓存为 JSON，供稳健型评分与页面展示使用。

数据源（多源回退，借鉴 easy_investment_Agent_crewai 的 akshare 封装模式）：
1. stock_zh_a_spot_em（东财全市场快照）：总市值/流通市值/市盈率动态/市净率/股息率
2. stock_financial_abstract_ths（同花顺财务摘要，个股）：ROE/毛利率/净利率/营收与净利同比
失败时回退下一源，单标的失败不影响整体。

缓存目录：multi_agent/data/fundamentals_cache/{date}.json（已 .gitignore）
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, MULTI_AGENT)

import akshare as ak
import pandas as pd

CACHE_DIR = os.path.join(MULTI_AGENT, 'data', 'fundamentals_cache')

# 优先抓取的标的：watchlist + 预测库中出现过的个股代码
WATCHLIST_PATH = os.path.join(MULTI_AGENT, 'watchlist.json')


def _safe_float(x, default=None):
    try:
        if x is None:
            return default
        if isinstance(x, bool):
            return default
        if isinstance(x, float) and pd.isna(x):
            return default
        if isinstance(x, str):
            s = x.strip().replace(',', '').replace('%', '')
            sign = 1
            if s.startswith('-'):
                sign, s = -1, s[1:]
            if s.endswith('亿'):
                return sign * float(s[:-1]) * 100000000
            if s.endswith('万'):
                return sign * float(s[:-1]) * 10000
            v = float(s)
            return v if v == v else default  # NaN 检查
        v = float(x)
        return v if v == v else default
    except (TypeError, ValueError):
        return default


def _load_watchlist_codes() -> List[str]:
    codes = set()
    try:
        if os.path.exists(WATCHLIST_PATH):
            wl = json.load(open(WATCHLIST_PATH, encoding='utf-8'))
            items = wl if isinstance(wl, list) else wl.get('watchlist', wl.get('items', []))
            for it in items:
                if isinstance(it, dict):
                    c = it.get('ticker') or it.get('code') or ''
                else:
                    c = str(it)
                if c:
                    codes.add(c)
    except Exception:
        pass
    return sorted(codes)


def _load_prediction_codes() -> List[str]:
    """从 llm_predictions.db 读取出现过的个股代码。"""
    codes = set()
    try:
        import sqlite3
        conn = sqlite3.connect(os.path.join(MULTI_AGENT, 'data', 'llm_predictions.db'))
        for r in conn.execute("SELECT DISTINCT ticker FROM agentic_predictions WHERE category='个股'").fetchall():
            if r[0]:
                codes.add(str(r[0]).zfill(6))
        conn.close()
    except Exception:
        pass
    return sorted(codes)


def _fetch_spot_map(codes: Optional[List[str]] = None) -> Dict[str, dict]:
    """估值快照：优先东财全市场快照，失败回退腾讯行情接口（支持批量，含市值/PE/PB/股息率）。"""
    out = _fetch_spot_map_em()
    if out:
        return out
    print('[fundamentals] spot_em 失败，回退腾讯行情接口...')
    return _fetch_spot_map_tx(codes)


def _fetch_spot_map_em() -> Dict[str, dict]:
    """东财全市场快照：代码 -> 市值/PE/PB/股息率。"""
    out: Dict[str, dict] = {}
    try:
        df = ak.stock_zh_a_spot_em()
        for _, r in df.iterrows():
            code = str(r.get('代码', '')).zfill(6)
            if not code:
                continue
            out[code] = {
                'name': r.get('名称', ''),
                'market_cap': _safe_float(r.get('总市值')),
                'float_market_cap': _safe_float(r.get('流通市值')),
                'pe_ratio': _safe_float(r.get('市盈率-动态')),
                'pb_ratio': _safe_float(r.get('市净率')),
                'dividend_yield': _safe_float(r.get('股息率', r.get('股息率(%)'))),
                'close': _safe_float(r.get('最新价')),
            }
    except Exception as e:
        print(f'[fundamentals] spot_em 失败: {e}')
    return out


def _fetch_spot_map_tx(codes: Optional[List[str]] = None) -> Dict[str, dict]:
    """腾讯行情接口（qt.gtimg.cn）批量获取估值数据。

    字段索引：1名称 2代码 3现价 39市盈率TTM 44流通市值(亿) 45总市值(亿) 46市净率 52市盈率(动)
    单请求上限约 60 个代码，分批拉取。
    """
    import requests
    out: Dict[str, dict] = {}
    # 如果未指定代码，默认拉取全 A 股（优先上交所+深交所合并表）
    if codes is None or not codes:
        try:
            import pandas as pd
            all_path = os.path.join(MULTI_AGENT, 'data', 'a_share_universe_all.csv')
            sz_path = os.path.join(MULTI_AGENT, 'data', 'a_share_universe_sz.csv')
            codes = []
            if os.path.exists(all_path):
                df = pd.read_csv(all_path)
                codes = [str(c).strip().zfill(6) for c in df['code'].astype(str).tolist() if str(c).strip().isdigit()]
            elif os.path.exists(sz_path):
                df = pd.read_csv(sz_path)
                codes = [str(c).strip().zfill(6) for c in df['A股代码'].astype(str).tolist() if str(c).strip().isdigit()]
        except Exception:
            codes = []
    if not codes:
        return out
    prefix = lambda c: ('sh' if c.startswith(('6', '9', '5')) else 'sz') + c
    batch_size = 50
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i + batch_size]
        q = ','.join(prefix(c) for c in batch)
        try:
            r = requests.get(f'http://qt.gtimg.cn/q={q}', timeout=15)
            r.encoding = 'gbk'
            for line in r.text.strip().split(';'):
                if '=' not in line:
                    continue
                try:
                    fields = line.split('="')[1].rstrip('"').split('~')
                    if len(fields) < 53:
                        continue
                    code6 = fields[2].strip().zfill(6)
                    name = fields[1].strip()
                    close = _safe_float(fields[3])
                    float_mcap = _safe_float(fields[44])
                    mcap = _safe_float(fields[45])
                    pb = _safe_float(fields[46])
                    pe_ttm = _safe_float(fields[39])
                    pe_dyn = _safe_float(fields[52])
                    out[code6] = {
                        'name': name,
                        'market_cap': mcap * 1e8 if mcap else None,   # 亿 -> 元
                        'float_market_cap': float_mcap * 1e8 if float_mcap else None,
                        'pe_ratio': pe_dyn or pe_ttm,
                        'pb_ratio': pb,
                        'dividend_yield': None,  # 腾讯接口股息率字段不可靠
                        'close': close,
                    }
                except Exception:
                    continue
        except Exception as e:
            print(f'[fundamentals] tx 批次 {i//batch_size} 失败: {e}')
    return out


def _fetch_ths_abstract(code: str) -> dict:
    """同花顺财务摘要（个股维度），失败返回空 dict。

    返回结构：行=报告期，列=指标。取最新一期作为当前快照。
    """
    try:
        df = ak.stock_financial_abstract_ths(symbol=code)
        if df is None or df.empty:
            return {}
        latest = df.iloc[-1]
        data = {'报告期': latest.get('报告期', '')}
        for col in df.columns:
            if col == '报告期':
                continue
            val = latest.get(col)
            if val is not None and not (isinstance(val, bool)):
                data[col] = val
        return data
    except Exception as e:
        return {'_error': str(e)[:100]}


def _fetch_revenue_composition(code: str) -> dict:
    """东方财富个股主营构成（按产品/行业/地区）。取最新一期的按产品分类。"""
    try:
        prefix = 'SH' if code.startswith('6') else 'SZ'
        df = ak.stock_zygc_em(symbol=f'{prefix}{code}')
        if df is None or df.empty:
            return {}
        # 取最新报告期
        latest_date = df['报告日期'].max()
        df_latest = df[df['报告日期'] == latest_date]
        # 优先按产品分类，其次按行业
        product_df = df_latest[df_latest['分类类型'] == '按产品分类']
        if product_df.empty:
            product_df = df_latest[df_latest['分类类型'] == '按行业分类']
        items = []
        for _, r in product_df.iterrows():
            items.append({
                'name': str(r.get('主营构成', '')).strip(),
                'revenue_ratio': _safe_float(r.get('收入比例')),
                'gross_margin': _safe_float(r.get('毛利率')),
            })
        return {
            'report_date': str(latest_date),
            'classification': '按产品分类' if '按产品分类' in df_latest['分类类型'].values else '按行业分类',
            'items': items,
        }
    except Exception as e:
        return {'_error': str(e)[:100]}


def _normalize_financial(fin: dict, spot: dict, rev_comp: dict = None) -> dict:
    """把同花顺摘要字段映射为统一键，优先 spot（估值）再财务摘要。"""
    def g(*keys, default=None):
        for k in keys:
            if k in fin and fin[k] is not None:
                return fin[k]
        return default

    out = {
        'name': spot.get('name', ''),
        'market_cap': spot.get('market_cap') or _safe_float(g('总市值'), 0),
        'float_market_cap': spot.get('float_market_cap'),
        'pe_ratio': spot.get('pe_ratio') or _safe_float(g('市盈率-动态', '市盈率'), 0),
        'pb_ratio': spot.get('pb_ratio') or _safe_float(g('市净率'), 0),
        'dividend_yield': spot.get('dividend_yield') or _safe_float(g('股息率', '股息率(%)'), 0),
        'close': spot.get('close'),
        'roe': _safe_float(g('净资产收益率', '净资产收益率-摊薄', '加权净资产收益率')),
        'gross_margin': _safe_float(g('销售毛利率', '毛利率')),
        'net_margin': _safe_float(g('销售净利率', '净利率')),
        'revenue_yoy': _safe_float(g('营业收入同比增长率', '营业总收入同比增长率')),
        'profit_yoy': _safe_float(g('净利润同比增长率', '扣非净利润同比增长率')),
        'debt_ratio': _safe_float(g('资产负债率')),
        'eps': _safe_float(g('基本每股收益', '每股收益')),
        'report_date': g('报告期', '公告日期', default=''),
    }
    if rev_comp and not rev_comp.get('_error'):
        out['revenue_composition'] = rev_comp
    return out


def fetch_and_cache(codes: Optional[List[str]] = None) -> Dict:
    os.makedirs(CACHE_DIR, exist_ok=True)
    today = datetime.now().strftime('%Y-%m-%d')

    # 目标池：watchlist + 预测库个股 + 本次指定的额外 codes
    wl = sorted(set(_load_watchlist_codes()) | set(_load_prediction_codes()))
    if codes:
        wl = sorted(set(wl) | set(codes))
    print(f'[fundamentals] 目标标的 {len(wl)} 个')

    # 优先东财全市场快照（含全 A 股），失败再回退腾讯批量接口
    # 腾讯接口优先用全 A 股列表，不把 wl 作为限制
    spot_map = _fetch_spot_map_em()
    if not spot_map:
        print('[fundamentals] spot_em 失败，回退腾讯行情接口...')
        spot_map = _fetch_spot_map_tx(None)
    print(f'[fundamentals] spot 快照 {len(spot_map)} 条')

    result = {'date': today, 'fundamentals': {}}
    for i, code in enumerate(wl):
        code6 = str(code).zfill(6)
        if len(code6) != 6 or not code6.isdigit():
            continue
        spot = spot_map.get(code6, {})
        fin = _fetch_ths_abstract(code6)
        rev_comp = _fetch_revenue_composition(code6)
        result['fundamentals'][code6] = _normalize_financial(fin, spot, rev_comp)
        if (i + 1) % 20 == 0:
            print(f'[fundamentals] 已处理 {i+1}/{len(wl)}')

    # 补充 spot_map 中有但 wl 中没有的全 A 股数据（至少市值/PE/PB）
    for code6, spot in spot_map.items():
        if code6 not in result['fundamentals']:
            result['fundamentals'][code6] = _normalize_financial({}, spot)

    out = os.path.join(CACHE_DIR, f'{today}.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f'[fundamentals] 缓存 {len(result["fundamentals"])} 条到 {out}')
    return result


def load_latest_cache() -> Optional[Dict]:
    if not os.path.exists(CACHE_DIR):
        return None
    files = sorted([f for f in os.listdir(CACHE_DIR) if f.endswith('.json')], reverse=True)
    if not files:
        return None
    with open(os.path.join(CACHE_DIR, files[0]), 'r', encoding='utf-8') as f:
        return json.load(f)


if __name__ == '__main__':
    fetch_and_cache()
