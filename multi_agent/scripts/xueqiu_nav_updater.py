#!/usr/bin/env python3
"""自动更新雪球组合每日净值收益到 xueqiu_portfolio_returns.json

依赖: 仓库根目录 .env 或 xueqiu_config.json 中的 cookies
"""
import json
import os
import sys
from datetime import datetime
import urllib.request
import urllib.parse

REPO = '/home/liudawei/github/daily_tracker_analytics'
sys.path.insert(0, REPO)

DATA_FILE = os.path.join(REPO, 'multi_agent', 'data', 'xueqiu_portfolio_returns.json')
CONFIG_FILE = os.path.join(REPO, 'multi_agent', 'config', 'xueqiu_config.json')

# 组合代码
PORTFOLIOS = {
    'ZH3650487': {'name': '郑希框架模拟盘'},
    'ZH3650823': {'name': '高股息'},
}


def load_cookies():
    """加载雪球 cookies：环境变量 XUEQIU_COOKIES 优先，其次 .env，最后 config 文件"""
    cookies = os.environ.get('XUEQIU_COOKIES', '').strip()
    if not cookies and os.path.exists(os.path.join(REPO, '.env')):
        try:
            from dotenv import load_dotenv
            load_dotenv(os.path.join(REPO, '.env'))
            cookies = os.environ.get('XUEQIU_COOKIES', '').strip()
        except Exception:
            pass
    if not cookies and os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding='utf-8') as f:
            cfg = json.load(f)
        cookies = cfg.get('cookies', '')
        if isinstance(cookies, dict):
            cookies = '; '.join(f"{k}={v}" for k, v in cookies.items())
    return cookies or ''


XUEQIU_COOKIE_ENV_VAR = 'XUEQIU_COOKIES'


def _is_cookie_env_set():
    return bool(os.environ.get(XUEQIU_COOKIE_ENV_VAR, '').strip())


def fetch_nav(cube_symbol: str, cookies: str):
    """获取单个组合历史净值"""
    import requests
    url = f'https://xueqiu.com/cubes/nav_daily/all.json?cube_symbol={cube_symbol}'
    headers = {
        'Accept': '*/*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en-US;q=0.8,en-GB;q=0.7,en;q=0.6',
        'Connection': 'keep-alive',
        'Referer': f'https://xueqiu.com/P/{cube_symbol}',
        'User-Agent': 'Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36',
        'X-Requested-With': 'XMLHttpRequest',
        'Cookie': cookies,
    }
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list) and data:
        return data[0]
    raise ValueError(f"Unexpected response for {cube_symbol}: {str(data)[:200]}")


def update_portfolio_returns(dry_run=False):
    """更新所有组合收益数据"""
    cookies = load_cookies()
    if not cookies:
        print("❌ 未找到雪球 cookies，跳过更新")
        return {'success': False, 'error': 'no cookies'}

    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding='utf-8') as f:
            data = json.load(f)
    else:
        data = {'last_updated': None, 'portfolios': {}}

    data['last_updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    for code, info in PORTFOLIOS.items():
        try:
            nav = fetch_nav(code, cookies)
            lst = nav.get('list', [])
            if not lst:
                print(f"⚠️ {code} 无净值数据")
                continue

            latest = lst[-1]
            earliest = lst[0]
            max_val = max(lst, key=lambda x: x['value'])
            min_val = min(lst, key=lambda x: x['value'])

            portfolio = data['portfolios'].get(code, {})
            portfolio.update({
                'name': info['name'],
                'start_date': earliest['date'],
                'latest_date': latest['date'],
                'latest_nav': latest['value'],
                'total_return_pct': latest['percent'],
                'max_return_pct': max_val['percent'],
                'max_return_date': max_val['date'],
                'min_return_pct': min_val['percent'],
                'min_return_date': min_val['date'],
                'history': portfolio.get('history', []) + [{
                    'date': latest['date'],
                    'value': latest['value'],
                    'percent': latest['percent'],
                    'fetched_at': data['last_updated']
                }]
            })
            portfolio.pop('last_error', None)
            portfolio.pop('last_error_time', None)
            # 去重 history 保留最新 100 条
            seen = set()
            dedup = []
            for h in reversed(portfolio['history']):
                if h['date'] not in seen:
                    dedup.append(h)
                    seen.add(h['date'])
            portfolio['history'] = list(reversed(dedup))[-100:]
            data['portfolios'][code] = portfolio

            print(f"✅ {code} {info['name']}: 最新 {latest['date']} 净值 {latest['value']} 累计 {latest['percent']:.2f}%")
        except Exception as e:
            print(f"❌ {code} 更新失败: {e}")
            data['portfolios'][code] = data['portfolios'].get(code, {})
            data['portfolios'][code]['last_error'] = str(e)
            data['portfolios'][code]['last_error_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    if not dry_run:
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"\n💾 已保存到 {DATA_FILE}")
    else:
        print("\n🔍 DRY RUN, 未保存")
    return {'success': True, 'data': data}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true', help='仅预览，不保存')
    args = parser.parse_args()
    update_portfolio_returns(dry_run=args.dry_run)
