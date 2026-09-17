#!/usr/bin/env python3
"""雪球 cookie 每日自检（防过期报警）。

工作目录约定: 仓库根目录 daily_tracker_analytics。
读取顺序与 xueqiu_nav_updater.load_cookies 完全一致:
    环境变量 XUEQIU_COOKIES > 仓库 .env > config/xueqiu_config.json

流程:
    1. 用现行 cookie 调一次真实接口 (第一个组合 NAV)
       HTTP 200 且拿到净值序列 -> 健康, 静默退出 (exit 0)
    2. 失败 -> 先尝试「带 cookie 访问主页」刷新会话, 再测一次
       二次仍失败 -> 判定 cookie 已过期, 输出报警 (exit 1)

输出约定: 健康时不产生任何输出, 供 cron 静默模式使用。
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, 'multi_agent'))

from scripts import xueqiu_nav_updater as xu  # noqa: E402

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
      'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36')


def ping_with_cookie(cookies: str) -> bool:
    """带着现行 cookie 访问雪球主页一次, 刷新会话。返回 True 表示访问成功。"""
    import requests
    try:
        r = requests.get('https://xueqiu.com', headers={
            'User-Agent': UA, 'Cookie': cookies, 'Referer': 'https://xueqiu.com/'},
            timeout=25, allow_redirects=True)
        return r.status_code == 200
    except Exception:
        return False


def check_health(cookies: str):
    """对第一个组合实测 NAV, 返回 (ok, symbol, 详情)。"""
    if not xu.PORTFOLIOS:
        return True, '', '(未配置组合, 跳过实测)'
    symbol = next(iter(xu.PORTFOLIOS))
    try:
        xu.fetch_nav(symbol, cookies)
        return True, symbol, f'{symbol} NAV 实测 HTTP 200'
    except Exception as e:
        return False, symbol, f'{symbol} NAV 实测失败: {e}'


def main() -> int:
    cookies = xu.load_cookies()
    if not cookies:
        print('[SILENT]')  # 未配置时静默, 避免误导
        return 0

    ok, symbol, detail = check_health(cookies)
    if ok:
        return 0

    # 第一次失败: 尝试带 cookie 刷新会话后再测一次
    if ping_with_cookie(cookies):
        ok, symbol, detail = check_health(cookies)
        if ok:
            return 0

    # 仍失败: cookie 已过期
    print('⚠️ 雪球 cookie 已过期 (NAV 实测失败)')
    print(f'   {detail}')
    print('   请打开 https://xueqiu.com 登录后, 按 F12 → Network → 任意请求 → '
          '复制 Request Headers 中的 Cookie 整段发给我')
    return 1


if __name__ == '__main__':
    sys.exit(main())
