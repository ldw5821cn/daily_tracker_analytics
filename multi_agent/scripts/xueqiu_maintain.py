#!/usr/bin/env python3
"""雪球组合维护脚本：按规则自动维护三个组合。

规则：
- ZH3650487（短线一隔/全A股精选）：只建 A 股多头；无多头信号时空仓。
- ZH3650823（高股息红利）：固定持仓配置，按目标权重维护。
- ZH3650824（期货模拟）：仅本地模拟，不写入雪球。

用法：
  python3 multi_agent/scripts/xueqiu_maintain.py --dry-run
  python3 multi_agent/scripts/xueqiu_maintain.py
"""
import json, os, sys, argparse
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
CFG = os.path.join(REPO, 'config', 'xueqiu_config.json')
REC = os.path.join(REPO, 'data', 'recommendations.json')


def load_config():
    if not os.path.exists(CFG):
        print(f"❌ 配置不存在: {CFG}"); sys.exit(1)
    with open(CFG) as f:
        c = json.load(f)
    if not c.get('cookies') or '粘贴' in str(c['cookies']):
        print("❌ cookies未配置"); sys.exit(1)
    return c


def load_recommendations():
    if not os.path.exists(REC):
        return {'longs': [], 'shorts_or_avoids': [], 'macro_score': 50, 'allow_long': True}
    with open(REC) as f:
        return json.load(f)


def init_xq(cfg, code):
    import easytrader
    u = easytrader.use('xq')
    p = cfg['portfolios'].get(code, {})
    u.prepare(cookies=cfg['cookies'], portfolio_code=code, portfolio_market=p.get('market', 'cn'))
    try:
        pos = u.position
        print(f"  ✅ {code} ({p.get('name', '')}) 登录成功")
        return u, pos
    except Exception as e:
        print(f"  ❌ {code} 登录失败: {e}")
        return None, None


def sync_allocator(code, cfg, dry=True):
    """ZH3650487: 只建 A 股多头；无多头信号时空仓。"""
    p = cfg['portfolios'].get(code, {})
    print(f"\n{'='*50}\n📊 {code} ({p.get('name', '')})\n{'='*50}")
    u, pos = init_xq(cfg, code)
    if not u:
        return
    recs = load_recommendations()
    longs = [x for x in recs.get('longs', []) if x.get('category') == '个股']
    max_w = p.get('max_weight', 0.15)
    max_positions = 5

    targets = []
    if recs.get('allow_long', True) and longs:
        n = min(len(longs), max_positions)
        w = min(1.0 / n, max_w)
        for x in longs[:n]:
            targets.append({'stock_code': x['ticker'], 'weight': w, 'name': x.get('name', '')})
    else:
        print(f"   ℹ️ 无多头信号或宏观禁止做多，目标空仓")

    print(f"\n📋 当前持仓:")
    holdings = pos if isinstance(pos, list) else pos.get('holdings', [])
    for h in holdings:
        print(f"   {h.get('stock_name', h.get('stock_code', '?'))}: {h.get('weight', 0)*100:.2f}%")

    print(f"\n🔍 目标持仓 ({len(targets)}只):")
    for t in targets:
        print(f"   买入 {t['name']} ({t['stock_code']}): {t['weight']*100:.1f}%")
    if not targets:
        print("   (现金)")

    if not dry:
        try:
            # easytrader adjust_weight 是单只股票接口，需要逐只设置
            # 先把所有目标设为 0（空仓逻辑）
            for h in holdings:
                code = h.get('stock_code', h.get('stock_id', ''))
                # 如果目标里没有该股票，则权重设为 0
                if not any(t['stock_code'] == code for t in targets):
                    u.adjust_weight(code, 0)
            for t in targets:
                u.adjust_weight(t['stock_code'], round(t['weight'] * 100, 2))
            print(f"  ✅ 调仓成功")
        except Exception as e:
            print(f"  ❌ 调仓失败: {e}")
    else:
        print("\n   执行加 --no-dry-run")


def sync_fixed(code, cfg, dry=True):
    """ZH3650823: 固定高股息持仓。"""
    p = cfg['portfolios'].get(code, {})
    print(f"\n{'='*50}\n📊 {code} ({p.get('name', '')})\n{'='*50}")
    u, pos = init_xq(cfg, code)
    if not u:
        return
    holdings_cfg = p.get('holdings', {})
    total = sum(holdings_cfg.values())
    targets = []
    for ticker, w in holdings_cfg.items():
        targets.append({'stock_code': ticker, 'weight': w / total if total else 0})

    print(f"\n📋 当前持仓:")
    holdings = pos if isinstance(pos, list) else pos.get('holdings', [])
    for h in holdings:
        print(f"   {h.get('stock_name', h.get('stock_code', '?'))}: {h.get('weight', 0)*100:.2f}%")

    print(f"\n🔍 目标持仓 ({len(targets)}只):")
    for t in targets:
        print(f"   买入 {t['stock_code']}: {t['weight']*100:.1f}%")

    if not dry:
        try:
            # easytrader adjust_weight 是单只股票接口，需要逐只设置
            # 先把所有目标设为 0（空仓逻辑）
            for h in holdings:
                code = h.get('stock_code', h.get('stock_id', ''))
                # 如果目标里没有该股票，则权重设为 0
                if not any(t['stock_code'] == code for t in targets):
                    u.adjust_weight(code, 0)
            for t in targets:
                u.adjust_weight(t['stock_code'], round(t['weight'] * 100, 2))
            print(f"  ✅ 调仓成功")
        except Exception as e:
            print(f"  ❌ 调仓失败: {e}")
    else:
        print("\n   执行加 --no-dry-run")


def sync_futures_simulator(code, cfg):
    p = cfg['portfolios'].get(code, {})
    print(f"\n{'='*50}\n📊 {code} ({p.get('name', '')})\n{'='*50}")
    print("   ⚠️ 期货模拟盘仅本地维护，不写入雪球")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', '-n', action='store_true', help='仅预览，不执行')
    args = parser.parse_args()
    dry = args.dry_run
    if dry:
        print("🔍 DRY RUN\n")
    cfg = load_config()
    for code, p in cfg['portfolios'].items():
        src = p.get('source', '')
        if src == 'allocator':
            sync_allocator(code, cfg, dry)
        elif src == 'fixed':
            sync_fixed(code, cfg, dry)
        elif src == 'futures_simulator':
            sync_futures_simulator(code, cfg)


if __name__ == '__main__':
    main()
