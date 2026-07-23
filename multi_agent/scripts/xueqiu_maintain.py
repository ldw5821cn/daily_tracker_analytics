#!/usr/bin/env python3
"""雪球组合维护脚本 v2：统一使用 recommendations.json 数据源。

规则：
- ZH3650487（短线一隔/全A股精选）：使用 daily_predictor 生成的 recommendations.json 多头信号
- ZH3650823（高股息红利）：固定持仓配置
- ZH3650824（期货模拟）：仅本地维护

用法：
  python3 multi_agent/scripts/xueqiu_maintain_v2.py --dry-run
  python3 multi_agent/scripts/xueqiu_maintain_v2.py --no-dry-run
"""
import json
import os
import sys
import argparse
import time
from datetime import datetime

REPO = '/home/liudawei/github/daily_tracker_analytics'
sys.path.insert(0, REPO)

CFG = os.path.join(REPO, 'multi_agent', 'config', 'xueqiu_config.json')
REC = os.path.join(REPO, 'multi_agent', 'data', 'recommendations.json')
LOG_DIR = os.path.join(REPO, 'logs')
LOG_FILE = os.path.join(LOG_DIR, f'xueqiu_maintain_{datetime.now().strftime("%Y-%m-%d")}.log')
os.makedirs(LOG_DIR, exist_ok=True)


def log(msg, f):
    t = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{t}] {msg}"
    print(line)
    f.write(line + '\n')


def load_config():
    if not os.path.exists(CFG):
        raise FileNotFoundError(f"配置不存在: {CFG}")
    with open(CFG) as f:
        c = json.load(f)
    if not c.get('cookies') or '粘贴' in str(c['cookies']):
        raise ValueError("cookies未配置")
    return c


def load_recommendations():
    if not os.path.exists(REC):
        return {'longs': [], 'shorts_or_avoids': [], 'macro_score': 50, 'allow_long': True, 'generated_at': None}
    with open(REC) as f:
        return json.load(f)


def init_xq(cfg, code, log_f):
    import easytrader
    u = easytrader.use('xq')
    p = cfg['portfolios'].get(code, {})
    u.prepare(cookies=cfg['cookies'], portfolio_code=code, portfolio_market=p.get('market', 'cn'))
    try:
        pos = u.position
        log(f"  ✅ {code} ({p.get('name', '')}) 登录成功", log_f)
        return u, pos
    except Exception as e:
        log(f"  ❌ {code} 登录失败: {e}", log_f)
        return None, None


def sync_allocator(code, cfg, recs, dry=True, log_f=None):
    """ZH3650487: 使用 recommendations.json 多头信号。"""
    if log_f is None:
        log_f = open(LOG_FILE, 'a', encoding='utf-8')
    p = cfg['portfolios'].get(code, {})
    log(f"\n{'='*50}\n📊 {code} ({p.get('name', '')})\n{'='*50}", log_f)
    u, pos = init_xq(cfg, code, log_f)
    if not u:
        return {'success': False, 'error': 'login failed'}
    try:
        longs = [x for x in recs.get('longs', []) if x.get('category') == '个股']
        max_w = p.get('max_weight', 0.15)
        max_positions = p.get('max_positions', 5)
        allow_long = recs.get('allow_long', True)

        targets = []
        if allow_long and longs:
            n = min(len(longs), max_positions)
            w = min(1.0 / n, max_w)
            # 留 10% 现金
            w = min(w, 0.9 / n)
            for x in longs[:n]:
                targets.append({'stock_code': x['ticker'], 'weight': w, 'name': x.get('name', '')})
            log(f"   ℹ️ 多头信号 {len(longs)} 只，入选 {n} 只，单只权重 {w*100:.1f}%", log_f)
        else:
            log(f"   ℹ️ 无多头信号或宏观禁止做多，目标空仓", log_f)

        log(f"\n📋 当前持仓:", log_f)
        holdings = pos if isinstance(pos, list) else pos.get('holdings', [])
        for h in holdings:
            log(f"   {h.get('stock_name', h.get('stock_code', '?'))}: {h.get('weight', 0)*100:.2f}%", log_f)
        if not holdings:
            log("   (空仓)", log_f)

        log(f"\n🔍 目标持仓 ({len(targets)}只):", log_f)
        for t in targets:
            log(f"   买入 {t['name']} ({t['stock_code']}): {t['weight']*100:.1f}%", log_f)
        if not targets:
            log("   (现金)", log_f)

        result = {'success': True, 'code': code, 'targets': targets, 'dry': dry}
        if not dry:
            errors = []
            # 先清空不在目标中的持仓
            for h in holdings:
                sc = h.get('stock_code', h.get('stock_id', ''))
                if not any(t['stock_code'] == sc for t in targets):
                    try:
                        u.adjust_weight(sc, 0)
                        log(f"  ✅ 清仓 {sc}", log_f)
                        time.sleep(0.3)
                    except Exception as e:
                        errors.append(f"clear {sc}: {e}")
                        log(f"  ⚠️ 清仓 {sc} 失败: {e}", log_f)
            # 设置目标仓位
            for t in targets:
                try:
                    u.adjust_weight(t['stock_code'], round(t['weight'] * 100, 2))
                    log(f"  ✅ 买入 {t['name']} ({t['stock_code']}): {t['weight']*100:.1f}%", log_f)
                    time.sleep(0.3)
                except Exception as e:
                    errors.append(f"buy {t['stock_code']}: {e}")
                    log(f"  ⚠️ 买入 {t['stock_code']} 失败: {e}", log_f)
            result['errors'] = errors
            if errors:
                result['success'] = False
            else:
                log(f"  ✅ 调仓成功", log_f)
        else:
            log("\n   DRY RUN - 未实际执行", log_f)
        return result
    finally:
        try:
            u.exit()
        except Exception:
            pass


def sync_fixed(code, cfg, dry=True, log_f=None):
    """ZH3650823: 固定高股息持仓。"""
    if log_f is None:
        log_f = open(LOG_FILE, 'a', encoding='utf-8')
    p = cfg['portfolios'].get(code, {})
    log(f"\n{'='*50}\n📊 {code} ({p.get('name', '')})\n{'='*50}", log_f)
    u, pos = init_xq(cfg, code, log_f)
    if not u:
        return {'success': False, 'error': 'login failed'}
    try:
        holdings_cfg = p.get('holdings', {})
        total = sum(holdings_cfg.values())
        targets = []
        for ticker, w in holdings_cfg.items():
            targets.append({'stock_code': ticker, 'weight': w / total if total else 0})

        log(f"\n📋 当前持仓:", log_f)
        holdings = pos if isinstance(pos, list) else pos.get('holdings', [])
        for h in holdings:
            log(f"   {h.get('stock_name', h.get('stock_code', '?'))}: {h.get('weight', 0)*100:.2f}%", log_f)

        log(f"\n🔍 目标持仓 ({len(targets)}只):", log_f)
        for t in targets:
            log(f"   买入 {t['stock_code']}: {t['weight']*100:.1f}%", log_f)

        result = {'success': True, 'code': code, 'targets': targets, 'dry': dry}
        if not dry:
            for h in holdings:
                sc = h.get('stock_code', h.get('stock_id', ''))
                if not any(t['stock_code'] == sc for t in targets):
                    try:
                        u.adjust_weight(sc, 0)
                        log(f"  ✅ 清仓 {sc}", log_f)
                        time.sleep(0.3)
                    except Exception as e:
                        log(f"  ⚠️ 清仓 {sc} 失败: {e}", log_f)
            for t in targets:
                try:
                    u.adjust_weight(t['stock_code'], round(t['weight'] * 100, 2))
                    log(f"  ✅ 买入 {t['stock_code']}: {t['weight']*100:.1f}%", log_f)
                    time.sleep(0.3)
                except Exception as e:
                    log(f"  ⚠️ 买入 {t['stock_code']} 失败: {e}", log_f)
                    result['success'] = False
        else:
            log("\n   DRY RUN - 未实际执行", log_f)
        return result
    finally:
        try:
            u.exit()
        except Exception:
            pass


def sync_futures_simulator(code, cfg, log_f=None):
    if log_f is None:
        log_f = open(LOG_FILE, 'a', encoding='utf-8')
    p = cfg['portfolios'].get(code, {})
    log(f"\n{'='*50}\n📊 {code} ({p.get('name', '')})\n{'='*50}", log_f)
    log("   ⚠️ 期货模拟盘仅本地维护，不写入雪球", log_f)
    return {'success': True, 'code': code, 'note': 'local only'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', '-n', action='store_true', help='仅预览，不执行')
    parser.add_argument('--no-dry-run', action='store_true', help='实际执行')
    args = parser.parse_args()
    dry = not args.no_dry_run
    if dry:
        print("🔍 DRY RUN\n")
    with open(LOG_FILE, 'a', encoding='utf-8') as log_f:
        log(f"\n{'='*50}\n🚀 雪球组合维护启动 (dry={dry})\n{'='*50}", log_f)
        try:
            cfg = load_config()
            recs = load_recommendations()
            log(f"recommendations generated_at: {recs.get('generated_at', 'N/A')}", log_f)
            log(f"longs: {len(recs.get('longs', []))}, allow_long: {recs.get('allow_long', True)}", log_f)
        except Exception as e:
            log(f"❌ 初始化失败: {e}", log_f)
            sys.exit(1)
        results = []
        for code, p in cfg['portfolios'].items():
            src = p.get('source', '')
            if src == 'allocator':
                r = sync_allocator(code, cfg, recs, dry, log_f)
            elif src == 'fixed':
                r = sync_fixed(code, cfg, dry, log_f)
            elif src == 'futures_simulator':
                r = sync_futures_simulator(code, cfg, log_f)
            else:
                log(f"   ⚠️ 未知 source: {src}", log_f)
                r = {'success': False, 'error': f'unknown source {src}'}
            results.append(r)
        ok = sum(1 for r in results if r.get('success'))
        log(f"\n{'='*50}\n✅ 完成: {ok}/{len(results)} 个组合成功\n{'='*50}", log_f)


if __name__ == '__main__':
    main()
