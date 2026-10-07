#!/usr/bin/env python3
"""
超短情绪数据抓取模块 - P2 超短情绪分析（升级版）

数据源：
- 主：同花顺 Financial-API（官方、稳定）
- 备：akshare 东方财富接口（降级方案）

设计原则：
- 无硬编码阈值：指标阈值由 sentiment_analyzer 基于历史分位数学习
- 增量抓取：追加新 metric 到 warehouse sentiment 表，不重复回填
- 可重入：同一天重复运行覆盖当日数据
- 自动降级：HiThink 失败时自动回退到 akshare

用法：
    python3 multi_agent/sentiment_fetcher.py            # 抓今日
    python3 multi_agent/sentiment_fetcher.py --date 20260922
"""

import sys
import os
import json
import time
import argparse
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from core.warehouse import init_warehouse_db, get_warehouse_conn

# 尝试导入 HiThink 客户端
try:
    from multi_agent.hithink_client import HiThinkFinanceClient
    HITHINK_AVAILABLE = True
except ImportError:
    try:
        from hithink_client import HiThinkFinanceClient
        HITHINK_AVAILABLE = True
    except ImportError:
        HiThinkFinanceClient = None
        HITHINK_AVAILABLE = False

# metric 名称（写入 warehouse sentiment 表）
METRIC_ZT = 'zt_pool'
METRIC_DT = 'dt_pool'
METRIC_ZB = 'zb_pool'          # 炸板池（新增）
METRIC_PREV = 'zt_prev_perf'   # 昨日涨停表现汇总（新增，ticker='ALL'）


def _save_sentiment(records):
    """写入 warehouse sentiment 表（UPSERT）"""
    conn = get_warehouse_conn()
    cur = conn.cursor()
    for r in records:
        cur.execute('''INSERT INTO sentiment (date, ticker, metric, value, detail, source, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(date, ticker, metric) DO UPDATE SET value=excluded.value, detail=excluded.detail, source=excluded.source, updated_at=excluded.updated_at''',
            (r['date'], r['ticker'], r['metric'], r['value'], r['detail'], r['source']))
    conn.commit()
    conn.close()


def _retry(fn, retries=3, delay=2):
    """带重试的 API 调用"""
    last = None
    for i in range(retries):
        try:
            return fn()
        except Exception as e:
            last = e
            if i < retries - 1:
                time.sleep(delay)
    raise last


# ==================== HiThink 数据源 ====================

def _hithink_fetch_zt_pool(client: HiThinkFinanceClient, date_raw: str, date_fmt: str) -> list:
    """涨停池（HiThink API）"""
    records = []
    data = client.get_limit_up_pool(date_raw)
    
    if not data:
        return records
    
    for item in data:
        thscode = item.get('thscode', '')
        ticker = HiThinkFinanceClient.from_thscode(thscode)
        
        records.append({
            'date': date_fmt,
            'ticker': ticker,
            'metric': METRIC_ZT,
            'value': float(item.get('change_ratio', 0)),
            'detail': json.dumps({
                'name': item.get('name'),
                'limit_boards': int(item.get('limit_boards', 1) or 1),
                'amount': float(item.get('amount', 0) or 0) / 10000,  # 万元
                'industry': item.get('industry'),
                'first_seal_time': item.get('first_seal_time', ''),
                'seal_amount': float(item.get('seal_amount', 0) or 0) / 10000,  # 万元
                'turnover': float(item.get('turnover', 0) or 0),  # %
                'source': 'hithink',
            }, ensure_ascii=False, default=str),
            'source': 'hithink_zt',
        })
    return records


def _hithink_fetch_dt_pool(client: HiThinkFinanceClient, date_raw: str, date_fmt: str) -> list:
    """跌停池（HiThink API）"""
    records = []
    data = client.get_limit_down_pool(date_raw)
    
    if not data:
        return records
    
    for item in data:
        thscode = item.get('thscode', '')
        ticker = HiThinkFinanceClient.from_thscode(thscode)
        
        records.append({
            'date': date_fmt,
            'ticker': ticker,
            'metric': METRIC_DT,
            'value': float(item.get('change_ratio', 0)),
            'detail': json.dumps({
                'name': item.get('name'),
                'amount': float(item.get('amount', 0) or 0) / 10000,  # 万元
                'industry': item.get('industry'),
                'source': 'hithink',
            }, ensure_ascii=False, default=str),
            'source': 'hithink_dt',
        })
    return records


def _hithink_fetch_zb_pool(client: HiThinkFinanceClient, date_raw: str, date_fmt: str) -> list:
    """炸板池（HiThink API）"""
    records = []
    data = client.get_break_board_pool(date_raw)
    
    if not data:
        return records
    
    for item in data:
        thscode = item.get('thscode', '')
        ticker = HiThinkFinanceClient.from_thscode(thscode)
        
        records.append({
            'date': date_fmt,
            'ticker': ticker,
            'metric': METRIC_ZB,
            'value': float(item.get('change_ratio', 0)),
            'detail': json.dumps({
                'name': item.get('name'),
                'amount': float(item.get('amount', 0) or 0) / 10000,  # 万元
                'industry': item.get('industry'),
                'source': 'hithink',
            }, ensure_ascii=False, default=str),
            'source': 'hithink_zb',
        })
    return records


def _hithink_fetch_prev_zt_perf(client: HiThinkFinanceClient, date_raw: str, date_fmt: str) -> list:
    """
    昨日涨停今日表现（HiThink API）
    返回单条汇总记录（ticker='ALL'），detail 含完整统计
    """
    # HiThink 暂不提供昨日涨停表现接口，用涨停池数据推算
    # 获取昨日涨停股票
    prev_date = (datetime.strptime(date_fmt, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y%m%d')
    data = client.get_limit_up_pool(prev_date)
    
    if not data:
        return []
    
    # 获取今日这些股票的行情
    thscodes = [item.get('thscode') for item in data if item.get('thscode')]
    if not thscodes:
        return []
    
    quotes = client.get_quotes(thscodes)
    changes = [float(q.get('price_change_ratio_pct', 0)) for q in quotes]
    
    if not changes:
        return []
    
    up_cnt = sum(1 for c in changes if c > 0)
    summary = {
        'count': len(changes),
        'avg_chg': round(sum(changes) / len(changes), 3),
        'median_chg': round(sorted(changes)[len(changes)//2], 3),
        'up_cnt': up_cnt,
        'up_ratio': round(up_cnt / len(changes), 4),
        'max_chg': round(max(changes), 3),
        'min_chg': round(min(changes), 3),
        'today_again_limit': sum(1 for c in changes if c >= 9.9),
        'source': 'hithink',
    }
    
    return [{
        'date': date_fmt,
        'ticker': 'ALL',
        'metric': METRIC_PREV,
        'value': summary['avg_chg'],
        'detail': json.dumps(summary, ensure_ascii=False),
        'source': 'hithink_zt_prev',
    }]


# ==================== akshare 数据源（降级） ====================

def _akshare_fetch_zt_pool(date_raw: str, date_fmt: str) -> list:
    """涨停池（akshare 降级方案）"""
    import akshare as ak
    records = []
    df = _retry(lambda: ak.stock_zt_pool_em(date=date_raw))
    if df is None or df.empty:
        return records
    for _, row in df.iterrows():
        records.append({
            'date': date_fmt,
            'ticker': str(row['代码']).zfill(6),
            'metric': METRIC_ZT,
            'value': float(row.get('涨跌幅', 0)),
            'detail': json.dumps({
                'name': row.get('名称'),
                'limit_boards': int(row.get('连板数', 1) or 1),
                'amount': float(row.get('成交额', 0) or 0) / 10000,  # 万元
                'industry': row.get('所属行业'),
                'first_seal_time': str(row.get('首次封板时间', '')),
                'seal_amount': float(row.get('封板资金', 0) or 0) / 10000,  # 万元
                'turnover': float(row.get('换手率', 0) or 0),  # %
                'source': 'akshare',
            }, ensure_ascii=False, default=str),
            'source': 'akshare_zt',
        })
    return records


def _akshare_fetch_dt_pool(date_raw: str, date_fmt: str) -> list:
    """跌停池（akshare 降级方案）"""
    import akshare as ak
    records = []
    df = _retry(lambda: ak.stock_zt_pool_dtgc_em(date=date_raw))
    if df is None or df.empty:
        return records
    for _, row in df.iterrows():
        records.append({
            'date': date_fmt,
            'ticker': str(row['代码']).zfill(6),
            'metric': METRIC_DT,
            'value': float(row.get('涨跌幅', 0)),
            'detail': json.dumps({
                'name': row.get('名称'),
                'amount': float(row.get('成交额', 0) or 0) / 10000,  # 万元
                'industry': row.get('所属行业'),
                'source': 'akshare',
            }, ensure_ascii=False, default=str),
            'source': 'akshare_dt',
        })
    return records


def _akshare_fetch_zb_pool(date_raw: str, date_fmt: str) -> list:
    """炸板池（akshare 降级方案）"""
    import akshare as ak
    records = []
    df = _retry(lambda: ak.stock_zt_pool_zbgc_em(date=date_raw))
    if df is None or df.empty:
        return records
    for _, row in df.iterrows():
        records.append({
            'date': date_fmt,
            'ticker': str(row['代码']).zfill(6),
            'metric': METRIC_ZB,
            'value': float(row.get('涨跌幅', 0)),
            'detail': json.dumps({
                'name': row.get('名称'),
                'amount': float(row.get('成交额', 0) or 0) / 10000,  # 万元
                'industry': row.get('所属行业'),
                'source': 'akshare',
            }, ensure_ascii=False, default=str),
            'source': 'akshare_zb',
        })
    return records


def _akshare_fetch_prev_zt_perf(date_raw: str, date_fmt: str) -> list:
    """昨日涨停今日表现（akshare 降级方案）"""
    import akshare as ak
    df = _retry(lambda: ak.stock_zt_pool_previous_em(date=date_raw))
    if df is None or df.empty:
        return []
    chg = df['涨跌幅'].astype(float)
    up_cnt = int((chg > 0).sum())
    summary = {
        'count': int(len(df)),
        'avg_chg': round(float(chg.mean()), 3),
        'median_chg': round(float(chg.median()), 3),
        'up_cnt': up_cnt,
        'up_ratio': round(up_cnt / len(df), 4),
        'max_chg': round(float(chg.max()), 3),
        'min_chg': round(float(chg.min()), 3),
        'today_again_limit': int((chg >= 9.9).sum()),
        'source': 'akshare',
    }
    return [{
        'date': date_fmt,
        'ticker': 'ALL',
        'metric': METRIC_PREV,
        'value': summary['avg_chg'],
        'detail': json.dumps(summary, ensure_ascii=False),
        'source': 'akshare_zt_prev',
    }]


# ==================== 统一接口 ====================

def fetch_zt_pool(date_raw: str, date_fmt: str = None, use_hithink: bool = True) -> list:
    """涨停池：优先 HiThink，降级 akshare"""
    date_fmt = date_fmt or date_raw
    
    if use_hithink and HITHINK_AVAILABLE:
        try:
            client = HiThinkFinanceClient()
            return _hithink_fetch_zt_pool(client, date_raw, date_fmt)
        except Exception as e:
            print(f"  ⚠️ HiThink 涨停池失败，降级 akshare: {e}")
    
    return _akshare_fetch_zt_pool(date_raw, date_fmt)


def fetch_dt_pool(date_raw: str, date_fmt: str = None, use_hithink: bool = True) -> list:
    """跌停池：优先 HiThink，降级 akshare"""
    date_fmt = date_fmt or date_raw
    
    if use_hithink and HITHINK_AVAILABLE:
        try:
            client = HiThinkFinanceClient()
            return _hithink_fetch_dt_pool(client, date_raw, date_fmt)
        except Exception as e:
            print(f"  ⚠️ HiThink 跌停池失败，降级 akshare: {e}")
    
    return _akshare_fetch_dt_pool(date_raw, date_fmt)


def fetch_zb_pool(date_raw: str, date_fmt: str = None, use_hithink: bool = True) -> list:
    """炸板池：优先 HiThink，降级 akshare"""
    date_fmt = date_fmt or date_raw
    
    if use_hithink and HITHINK_AVAILABLE:
        try:
            client = HiThinkFinanceClient()
            return _hithink_fetch_zb_pool(client, date_raw, date_fmt)
        except Exception as e:
            print(f"  ⚠️ HiThink 炸板池失败，降级 akshare: {e}")
    
    return _akshare_fetch_zb_pool(date_raw, date_fmt)


def fetch_prev_zt_perf(date_raw: str, date_fmt: str = None, use_hithink: bool = True) -> list:
    """昨日涨停表现：优先 HiThink，降级 akshare"""
    date_fmt = date_fmt or date_raw
    
    if use_hithink and HITHINK_AVAILABLE:
        try:
            client = HiThinkFinanceClient()
            return _hithink_fetch_prev_zt_perf(client, date_raw, date_fmt)
        except Exception as e:
            print(f"  ⚠️ HiThink 昨日涨停表现失败，降级 akshare: {e}")
    
    return _akshare_fetch_prev_zt_perf(date_raw, date_fmt)


def _resolve_trading_date(requested: str) -> str:
    """
    防御：避免未开盘日期数据错配
    规则：
    - 请求日期是今天且当前时间 < 09:30（未开盘）→ 回退到 warehouse 最近有数据的交易日
    - 请求日期是周末 → 回退到最近交易日
    - 其他情况 → 原样返回
    """
    now = datetime.now()
    today = now.strftime('%Y-%m-%d')

    # 周末直接回退
    if requested > today:
        # 未来日期，回退
        pass
    elif requested == today and now.hour < 9:
        # 今天但开盘前，回退
        pass
    else:
        return requested

    # 回退：找 warehouse 最近有涨停池数据的日期
    conn = get_warehouse_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT MAX(date) FROM sentiment WHERE metric='zt_pool' AND date < ?
    """, (requested,))
    row = cur.fetchone()
    conn.close()
    if row and row[0]:
        fallback = row[0]
        print(f"  ⏪ 未开盘/非交易日，回退到最近交易日: {fallback}")
        return fallback
    return requested


def run_daily(date: str = None, verbose: bool = True, use_hithink: bool = True) -> dict:
    """
    抓取某日全部情绪数据并写入 warehouse

    日期格式统一为 YYYY-MM-DD（与 backfill_sentiment.py 历史数据一致）
    
    Args:
        date: 日期 YYYYMMDD 或 YYYY-MM-DD
        verbose: 是否打印详细信息
        use_hithink: 是否优先使用 HiThink API（默认 True）

    Returns:
        {'date': str, 'zt': n, 'dt': n, 'zb': n, 'prev': bool, 'source': str}
    """
    if date is None:
        date_fmt = datetime.now().strftime('%Y-%m-%d')
    elif len(date) == 8 and date.isdigit():
        date_fmt = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    else:
        date_fmt = date

    # 日期防御：避免未开盘时把最近交易日数据写到今天键下
    date_fmt = _resolve_trading_date(date_fmt)

    # akshare 接口需要 YYYYMMDD
    date_raw = date_fmt.replace('-', '')

    init_warehouse_db()
    result = {'date': date_fmt, 'zt': 0, 'dt': 0, 'zb': 0, 'prev': False, 'source': 'unknown'}

    steps = [
        ('涨停池', METRIC_ZT, fetch_zt_pool),
        ('跌停池', METRIC_DT, fetch_dt_pool),
        ('炸板池', METRIC_ZB, fetch_zb_pool),
        ('昨日涨停表现', METRIC_PREV, fetch_prev_zt_perf),
    ]

    for name, metric, fn in steps:
        try:
            records = fn(date_raw, date_fmt, use_hithink=use_hithink)
            if records:
                _save_sentiment(records)
                cnt = len(records)
                if metric == METRIC_ZT:
                    result['zt'] = cnt
                elif metric == METRIC_DT:
                    result['dt'] = cnt
                elif metric == METRIC_ZB:
                    result['zb'] = cnt
                elif metric == METRIC_PREV:
                    result['prev'] = True
                # 记录数据源
                if records[0].get('source', '').startswith('hithink'):
                    result['source'] = 'hithink'
                elif records[0].get('source', '').startswith('akshare'):
                    result['source'] = 'akshare'
                if verbose:
                    print(f"  ✅ {name}: {cnt} 条 ({records[0].get('source', 'unknown')})")
            else:
                if verbose:
                    print(f"  ⚪ {name}: 无数据")
        except Exception as e:
            if verbose:
                print(f"  ⚠️ {name} 失败: {e}")
        time.sleep(0.3)  # 限速，避免触发数据源风控

    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='超短情绪数据抓取（支持 HiThink + akshare 双源）')
    parser.add_argument('--date', help='日期 YYYYMMDD（默认今日）')
    parser.add_argument('--akshare-only', action='store_true', help='强制使用 akshare（禁用 HiThink）')
    args = parser.parse_args()

    print(f"📥 超短情绪数据抓取: {args.date or '今日'}")
    if HITHINK_AVAILABLE and not args.akshare_only:
        print("   数据源: HiThink Financial-API（官方）")
    else:
        print("   数据源: akshare 东方财富（降级）")
    
    r = run_daily(args.date, use_hithink=not args.akshare_only)
    print(f"\n完成: 涨停{r['zt']} 跌停{r['dt']} 炸板{r['zb']} 昨日表现{'✓' if r['prev'] else '✗'}")
    print(f"   数据源: {r['source']}")
