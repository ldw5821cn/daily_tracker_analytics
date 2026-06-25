#!/usr/bin/env python3
"""
雪球模拟盘短线一割 自动操作器
通过Playwright + 已保存的登录态自动调仓

架构：
  首次：打开浏览器 -> 你扫码/手机号登录 -> 保存session到本地
  后续：直接复用session -> 全自动调仓 -> cron运行
  
用法：
  python3 xueqiu_sim_auto.py --login    # 首次：登录+保存session
  python3 xueqiu_sim_auto.py --check    # 查持仓
  python3 xueqiu_sim_auto.py --trade    # 执行交易计划
  python3 xueqiu_sim_auto.py --auto     # 一键：查持仓+调仓（默认）
  
交易计划格式（trade_plan.json）：
  [{"symbol": "SH600000", "type": 1, "price": 12.5, "shares": 200}, ...]
  type: 1=买入, 2=卖出
"""
import asyncio
import json
import os
import sys
from datetime import datetime
import json

# ── 雪球组合GID（必须通过环境变量设置）──
# 获取方式：组合页面URL中的 gid 参数
# 设置：export XUEQIU_GID=xxx 或 写入 .env
_GID_ENV = os.environ.get('XUEQIU_GID')
if not _GID_ENV:
    raise ValueError("必须设置环境变量 XUEQIU_GID（雪球组合GID）")
GID = int(_GID_ENV)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, 'data', 'xueqiu_sim_state.json')
PLAN_FILE = os.path.join(BASE_DIR, 'data', 'trade_plan.json')
LOG_FILE = os.path.join(BASE_DIR, 'data', 'sim_trade_log.json')

# 最小化依赖 - 条件导入playwright
try:
    from playwright.async_api import async_playwright
    HAS_PW = True
except ImportError:
    HAS_PW = False
    print("⚠️  playwright 未安装，运行: pip install playwright && python -m playwright install chromium")


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return None


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)


def load_plan():
    if os.path.exists(PLAN_FILE):
        with open(PLAN_FILE) as f:
            return json.load(f)
    return None


async def login_and_save(page, context):
    """登录并保存session"""
    print("🌐 打开浏览器，请在窗口中登录雪球...")
    await page.goto('https://xueqiu.com')
    await page.wait_for_timeout(3000)
    
    # 等待用户手动登录
    max_wait = 120  # 最多等2分钟
    waited = 0
    while waited < max_wait:
        cookies = await context.cookies()
        has_token = any(c['name'] == 'xq_a_token' and c['value'] for c in cookies)
        if has_token:
            break
        await asyncio.sleep(2)
        waited += 2
        if waited % 20 == 0:
            print(f"  ⏳ 等待登录... ({waited}s)")
    
    if waited >= max_wait:
        print("❌ 登录超时")
        return False
    
    # 保存完整session（含所有反爬token）
    state = await context.storage_state()
    save_state(state)
    print(f"✅ 登录成功！session已保存")
    return True


async def call_api(page, endpoint, method='GET', params=None, data=None):
    """通过浏览器调用模拟盘API（利用浏览器已有的cookie+反爬token）"""
    from urllib.parse import urlencode
    
    url = f'https://tc.xueqiu.com/tc/snowx/MONI/{endpoint}'
    if params:
        url += '?' + urlencode(params)
    
    if method == 'GET':
        js = f"""
(async () => {{
    const r = await fetch('{url}', {{
        headers: {{'Accept': 'application/json, text/plain, */*', 'Referer': 'https://xueqiu.com/performance'}}
    }});
    const text = await r.text();
    try {{ return JSON.parse(text); }} catch(e) {{ return {{_raw: text.substring(0,300)}}; }}
}})()
"""
    else:
        form = urlencode(data) if data else ''
        js = f"""
(async () => {{
    const r = await fetch('{url}', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json', 'Referer': 'https://xueqiu.com/performance'}},
        body: '{form}'
    }});
    const text = await r.text();
    try {{ return JSON.parse(text); }} catch(e) {{ return {{_raw: text.substring(0,300)}}; }}
}})()
"""
    
    result = await page.evaluate(js)
    return result


async def query_holdings(page):
    """查询当前持仓（通过交易记录汇总）"""
    result = await call_api(page, 'transaction/list.json', params={'gid': GID, 'row': 200})
    
    if not isinstance(result, dict) or not result.get('success'):
        print(f"⚠️  查询交易记录失败: {str(result)[:200]}")
        return {}
    
    txns = result.get('result_data', {}).get('transactions', [])
    
    # 汇总：买入(+) - 卖出(-)
    holdings = {}
    for t in txns:
        sym = t['symbol']
        qty = int(t['shares']) * (1 if t['type'] == 1 else -1)
        price = float(t['price'])
        
        if sym not in holdings:
            holdings[sym] = {'symbol': sym, 'name': t['name'], 'shares': 0, 'total_cost': 0.0}
        
        holdings[sym]['shares'] += qty
        if qty > 0:
            holdings[sym]['total_cost'] += qty * price
    
    # 筛出当前持仓（shares>0）
    active = {k: v for k, v in holdings.items() if v['shares'] > 0}
    
    # 查最新行情
    if active:
        codes = ','.join(active.keys())
        try:
            import urllib.request
            url2 = f'http://qt.gtimg.cn/q={codes}'
            resp = urllib.request.urlopen(url2, timeout=5).read().decode('gbk', errors='ignore')
            for line in resp.split(';'):
                if '~' in line:
                    parts = line.split('~')
                    if len(parts) > 31:
                        sym = parts[2].replace('SH', 'SH').replace('SZ', 'SZ')
                        price = float(parts[3]) if parts[3] else 0
                        pct = parts[32] if len(parts) > 32 else '0'
                        if sym in active:
                            active[sym]['current'] = price
                            active[sym]['change_pct'] = pct
                            cost = active[sym]['total_cost'] / active[sym]['shares']
                            active[sym]['return_pct'] = round((price - cost) / cost * 100, 2)
        except:
            pass
    
    return active


async def place_order(page, symbol, trade_type, price, shares, stock_name=''):
    """下单"""
    data = {
        'type': str(trade_type),
        'date': datetime.now().strftime('%Y-%m-%d'),
        'gid': str(GID),
        'symbol': symbol,
        'price': str(price),
        'shares': str(int(shares)),
    }
    
    result = await call_api(page, 'transaction/add.json', method='POST', data=data)
    action = '买入' if trade_type == 1 else '卖出'
    
    success = isinstance(result, dict) and result.get('success')
    if success:
        print(f"  ✅ {action} {stock_name or symbol} {int(shares)}股 @ {price}")
    else:
        print(f"  ❌ {action} {symbol} 失败: {str(result)[:150]}")
    
    # 记录日志
    log_entry = {
        'time': datetime.now().isoformat(),
        'action': action,
        'symbol': symbol,
        'name': stock_name,
        'price': price,
        'shares': int(shares),
        'success': success,
        'response': str(result)[:300],
    }
    _append_log(log_entry)
    
    return success


def _append_log(entry):
    logs = []
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE) as f:
            try: logs = json.load(f)
            except: pass
    logs.append(entry)
    if len(logs) > 1000:
        logs = logs[-1000:]
    with open(LOG_FILE, 'w') as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)


async def auto_trade(page, keep_browser=False):
    """全自动：查持仓 -> 检查交易计划 -> 执行"""
    
    # 先打开performance页面初始化session
    print("🔑 初始化session...")
    await page.goto('https://xueqiu.com/performance')
    await page.wait_for_timeout(5000)  # 等页面加载完（含反爬JS执行）
    print(f"   URL: {page.url}")
    
    # 检查是否登录
    title = await page.title()
    if '登录' in title:
        print("❌ Session已过期，需要重新登录")
        return False
    
    # 查持仓
    print("\n📋 查询当前持仓...")
    holdings = await query_holdings(page)
    
    total_value = 0
    if holdings:
        print(f"\n  当前持仓 ({len(holdings)} 只):")
        for sym, info in sorted(holdings.items()):
            cur = info.get('current', 0)
            ret = info.get('return_pct', 0)
            ret_str = f"{ret:+.2f}%" if ret else ''
            print(f"    {info['name']:10s} {sym:10s} {info['shares']:>6.0f}股  {cur:>8.2f}  {ret_str}")
            total_value += cur * info['shares']
        print(f"\n  持仓市值: ~{total_value:.0f}")
    else:
        print("  📭 空仓")
    
    # 检查是否有交易计划
    plan = load_plan()
    if plan and isinstance(plan, list):
        print(f"\n📝 执行交易计划 ({len(plan)} 笔)...")
        for trade in plan:
            await asyncio.sleep(1)  # 防风控
            await place_order(
                page,
                trade['symbol'],
                trade.get('type', 1),
                trade['price'],
                trade['shares'],
                trade.get('name', '')
            )
    else:
        print("\n  📭 无待执行交易计划")
    
    print(f"\n✅ 操作完成 ({(datetime.now()).strftime('%H:%M:%S')})")
    return True


async def main():
    import argparse
    parser = argparse.ArgumentParser(description='雪球模拟盘自动操作')
    parser.add_argument('--login', action='store_true', help='首次登录并保存session')
    parser.add_argument('--check', action='store_true', help='查询持仓')
    parser.add_argument('--trade', action='store_true', help='执行交易计划')
    parser.add_argument('--auto', action='store_true', default=True, help='全自动（默认）')
    args = parser.parse_args()
    
    if not HAS_PW:
        print("请先安装: pip install playwright && python -m playwright install chromium")
        sys.exit(1)
    
    state = load_state()
    
    # 全自动模式
    if args.auto and not args.login:
        if not state:
            print("❌ 未找到保存的登录session，请先运行: python3 xueqiu_sim_auto.py --login")
            return
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                storage_state=state,
                user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                viewport={'width': 1280, 'height': 800}
            )
            page = await context.new_page()
            
            ok = await auto_trade(page)
            
            # 如果session过期，重新保存
            if not ok:
                new_state = await context.storage_state()
                save_state(new_state)
            
            await browser.close()
        return
    
    # 首次登录模式
    if args.login:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )
            page = await context.new_page()
            ok = await login_and_save(page, context)
            if ok:
                await auto_trade(page)
            await browser.close()
        return


if __name__ == '__main__':
    asyncio.run(main())
