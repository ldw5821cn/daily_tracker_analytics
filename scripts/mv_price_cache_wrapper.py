#!/usr/bin/env python3
"""morning_validation 降级运行包装：qt.gtimg.cn + 新浪期货构建当日收盘价缓存，monkeypatch 后跑验证。

适用场景（2026-08-13/14 实测）：盘后跑验证时 warehouse daily_bar 尚无当日收盘价
（max date = 昨日，日志「从 warehouse 预加载 0 条价格」），逐只回退 get_stock_data()
走 eastmoney 若网络不可达会 4 次重试/只 × 全部标的 → 超时挂起。

方案：
- A股/ETF：qt.gtimg.cn 批量（100只/批），前缀 6/5→sh、4/8→bj、其余→sz，字段[3]=收盘价
- 期货：hq.sinajs.cn nf_ 前缀批量，字段[7]=最新价（0-indexed）
- US：当日美股收盘未发生（北京时间次日 04:00），monkeypatch us_data 返回 None → 自动记为 no_data

用法：
  .venv/bin/python scripts/mv_price_cache_wrapper.py
"""
from __future__ import annotations

import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, 'multi_agent'))

# multi_agent 不是包，按文件路径加载模块（模块内 `from core.xxx import ...` 依赖 multi_agent 在 sys.path）
import importlib.util

def _load_module(name: str, rel_path: str):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, rel_path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

mv = _load_module('morning_validation', os.path.join('multi_agent', 'scripts', 'morning_validation.py'))
us_data = _load_module('core_us_data', os.path.join('multi_agent', 'core', 'us_data.py'))


def _fetch(url: str, referer: str = None, timeout: int = 10) -> str:
    req = urllib.request.Request(url)
    if referer:
        req.add_header('Referer', referer)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('gbk', errors='ignore')


def _qt_symbol(ticker: str) -> str:
    if ticker.startswith(('6', '5')):
        return 'sh' + ticker
    if ticker.startswith(('4', '8')):
        return 'bj' + ticker
    return 'sz' + ticker


def _validate_date(pred_date: str) -> str:
    """与 morning_validation._next_trading_date_cn 一致的验证日。"""
    dt = datetime.strptime(pred_date, '%Y-%m-%d')
    wd = dt.weekday()
    if wd == 4:       # 周五 -> 周一
        delta = 3
    elif wd >= 5:     # 周末 -> 周一
        delta = 7 - wd
    else:
        delta = 1
    return (dt + timedelta(days=delta)).strftime('%Y-%m-%d')


def build_cache(rows, pred_date: str) -> dict:
    vd = _validate_date(pred_date)
    cache: dict = {}
    stocks = [r['ticker'] for r in rows if r['category'] in ('个股', 'ETF')]
    futures = [r['ticker'] for r in rows if r['category'] == '期货']
    print(f'[wrapper] 验证日 {vd} | A股/ETF {len(stocks)} 只, 期货 {len(futures)} 只')

    # A股/ETF：qt.gtimg.cn 批量（100只/批）
    for i in range(0, len(stocks), 100):
        batch = stocks[i:i + 100]
        url = 'https://qt.gtimg.cn/q=' + ','.join(_qt_symbol(t) for t in batch)
        try:
            text = _fetch(url)
        except Exception as e:
            print(f'  qt 批量失败: {e}')
            continue
        for t in batch:
            m = re.search(r'v_' + _qt_symbol(t) + r'="([^"]*)"', text)
            if m:
                f = m.group(1).split('~')
                if len(f) > 4 and f[3]:
                    try:
                        cache[(t, vd)] = float(f[3])
                    except ValueError:
                        pass

    # 期货：hq.sinajs.cn nf_ 前缀批量
    for i in range(0, len(futures), 80):
        batch = futures[i:i + 80]
        url = 'https://hq.sinajs.cn/list=' + ','.join('nf_' + t for t in batch)
        try:
            text = _fetch(url, referer='https://finance.sina.com.cn')
        except Exception as e:
            print(f'  新浪期货批量失败: {e}')
            continue
        for t in batch:
            m = re.search(r'hq_str_nf_' + t + r'="([^"]*)"', text)
            if m:
                f = m.group(1).split(',')
                if len(f) > 7 and f[7]:
                    try:
                        cache[(t, vd)] = float(f[7])
                    except ValueError:
                        pass

    print(f'[wrapper] 缓存命中 {len(cache)}/{len(stocks) + len(futures)} 只')
    return cache


def _pick_pred_date(latest: str) -> str:
    """复算 main() 的 pred_date 选取逻辑。"""
    latest_dt = datetime.strptime(latest, '%Y-%m-%d')
    wd = latest_dt.weekday()
    if wd == 5:       # 周六: 最新价格是周五，验证周四
        pred = latest_dt - timedelta(days=2)
    elif wd == 6:     # 周日
        pred = latest_dt - timedelta(days=3)
    elif wd == 0:     # 周一
        pred = latest_dt - timedelta(days=3)
    else:
        pred = latest_dt - timedelta(days=1)
    return pred.strftime('%Y-%m-%d')


def main():
    conn = mv._get_conn()
    latest = conn.execute("SELECT MAX(pred_date) FROM agentic_predictions").fetchone()[0]
    pred_date = _pick_pred_date(latest)
    rows = conn.execute(
        "SELECT * FROM agentic_predictions WHERE pred_date=?", (pred_date,)
    ).fetchall()
    conn.close()

    # US 当日美股收盘未发生 -> 返回 None（自动记为 no_data，不计入统计）
    us_data.get_us_stock_data = lambda *a, **k: None
    us_data.get_us_price = lambda *a, **k: None

    cache = build_cache(rows, pred_date)
    mv._build_price_cache = lambda rows_, pd_: cache
    print(f'[wrapper] 开始 morning_validation（验证 {pred_date} 预测）...')
    mv.main()


if __name__ == '__main__':
    main()
