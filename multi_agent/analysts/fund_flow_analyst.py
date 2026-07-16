#!/usr/bin/env python3
"""资金流分析器：个股主力资金流向 + 板块资金流向评分。"""
import json, os, time
from typing import Dict, List, Optional
import requests

_PROXY = os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy') or ''

def _s():
    s = requests.Session()
    s.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                       'Referer': 'http://quote.eastmoney.com/'})
    if _PROXY: s.proxies = {'http': _PROXY, 'https': _PROXY}
    s.trust_env = bool(_PROXY)
    return s

def get_stock_fund_flow(ticker: str, market: str = 'sh', days: int = 20) -> Optional[Dict]:
    secid = {'sh': 1, 'sz': 0, 'SH': 1, 'SZ': 0}.get(market, 1)
    params = {'lmt': days, 'klt': 101, 'secid': f'{secid}.{ticker}',
              'fields1': 'f1,f2,f3,f7',
              'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65',
              'ut': 'b2884a393a59ad64002292a3e90d46a5'}
    for _ in range(2):
        try:
            r = _s().get('http://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get',
                         params=params, timeout=10)
            if r.status_code != 200: continue
            data = r.json()
            klines = data.get('data', {}).get('klines', [])
            if not klines: continue
            rows = []
            for k in klines:
                p = k.split(',')
                if len(p) >= 13:
                    rows.append({'date': p[0], 'main_net': float(p[3]), 'main_ratio': float(p[4]),
                                 'super_large_ratio': float(p[6]), 'large_ratio': float(p[8]),
                                 'medium_ratio': float(p[10]), 'small_ratio': float(p[12])})
            if not rows: return None
            r5 = rows[-5:] if len(rows)>=5 else rows
            return {'ticker': ticker, 'date': rows[-1]['date'],
                    'main_net_flow': rows[-1]['main_net'], 'main_net_ratio': rows[-1]['main_ratio'],
                    'super_large_ratio': rows[-1]['super_large_ratio'], 'large_ratio': rows[-1]['large_ratio'],
                    'medium_ratio': rows[-1]['medium_ratio'], 'small_ratio': rows[-1]['small_ratio'],
                    'avg_5d_main_flow': sum(r['main_net'] for r in r5)/len(r5),
                    'recent_5d_flow_trend': rows[-1]['main_net']-rows[-6]['main_net'] if len(rows)>=6 else 0}
        except:
            if _ == 0: time.sleep(0.5)
    return None

def get_sector_fund_flow_rank(top_n: int = 10) -> List[Dict]:
    params = {'pn': 1, 'pz': top_n, 'po': 1, 'np': 1, 'ut': 'b2884a393a59ad64002292a3e90d46a5',
              'fltt': 2, 'invt': 2, 'fid0': 'f62', 'fs': 'm:90+t:2', 'stat': 1,
              'fields': 'f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f204,f205'}
    for _ in range(2):
        try:
            r = _s().get('http://push2.eastmoney.com/api/qt/clist/get', params=params, timeout=10)
            if r.status_code != 200: continue
            items = r.json().get('data', {}).get('diff', [])
            return [{'sector': i.get('f14',''), 'change_pct': i.get('f3',0),
                     'main_net_flow': i.get('f62',0), 'flow_3d': i.get('f204',0),
                     'flow_5d': i.get('f205',0)} for i in items] if items else []
        except:
            if _ == 0: time.sleep(0.5)
    return []

def compute_score(ff: Optional[Dict]) -> float:
    if not ff: return 50.0
    s = 50.0
    ratio = ff.get('main_net_ratio', 0)
    if ratio > 2: s += min(ratio, 15)
    elif ratio < -2: s -= min(abs(ratio), 15)
    s += min(max(ff.get('avg_5d_main_flow',0)/1000, -10), 10)
    s += min(max(ff.get('recent_5d_flow_trend',0)/2000, -10), 10)
    delta = ff.get('super_large_ratio',0) - ff.get('small_ratio',0)
    if delta > 5: s += min(delta, 15)
    elif delta < -5: s -= min(abs(delta), 15)
    return max(0, min(100, s))

if __name__ == '__main__':
    r = get_stock_fund_flow('600028', 'sh')
    if r:
        print(f'{r["ticker"]}: main_ratio={r["main_net_ratio"]:.1f}% super_large={r["super_large_ratio"]:.1f}% small={r["small_ratio"]:.1f}%')
        print(f'  score={compute_score(r):.1f}')
    else:
        print('获取失败')
