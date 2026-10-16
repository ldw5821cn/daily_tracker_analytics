#!/usr/bin/env python3
"""雪球组合自动调仓引擎。
用法:
  python3 multi_agent/scripts/xueqiu_sync.py --dry-run
  python3 multi_agent/scripts/xueqiu_sync.py
"""
import json, os, sys, time
from datetime import datetime
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
CFG = os.path.join(REPO, 'config', 'xueqiu_config.json')
TW = os.path.join(REPO, 'data', 'target_weights.json')
FS = os.path.join(REPO, 'data', 'futures_simulator.db')

def load_config():
    if not os.path.exists(CFG):
        print(f"❌ 配置不存在: {CFG}"); sys.exit(1)
    with open(CFG) as f:
        c = json.load(f)
    
    # 从 .env 加载 cookies
    if not c.get('cookies') or '粘贴' in str(c.get('cookies', '')):
        try:
            from dotenv import load_dotenv
            load_dotenv(os.path.join(REPO, '.env'))
            cookies = os.environ.get('XUEQIU_COOKIES', '').strip()
            if cookies:
                c['cookies'] = cookies
        except Exception as e:
            print(f"⚠️ 加载 .env 失败: {e}")
    
    if not c.get('cookies') or '粘贴' in str(c.get('cookies', '')):
        print("❌ cookies未配置\n   浏览器登录雪球→F12→Application→Cookies→复制cookie字符串"); sys.exit(1)
    return c

def get_targets():
    if not os.path.exists(TW): return []
    with open(TW) as f:
        tw = json.load(f)
    return [{'ticker':t['ticker'],'name':t.get('name',''),'weight':t['target_weight'],
             'signal':t.get('signal','neutral'),'cat':t.get('category','')}
            for t in tw.get('targets',[]) if abs(t.get('target_weight',0))>0.001]

def get_futures():
    try:
        import sqlite3
        conn = sqlite3.connect(FS)
        cur = conn.execute("SELECT contract,direction,lots,entry_price,current_price FROM positions WHERE active=1")
        rows = [dict(zip(['contract','dir','lots','entry','curr'],r)) for r in cur]
        conn.close(); return rows
    except: return []

def init_xq(cfg, code):
    import easytrader
    u = easytrader.use('xq')
    p = cfg['portfolios'].get(code,{})
    u.prepare(cookies=cfg['cookies'], portfolio_code=code, portfolio_market=p.get('market','cn'))
    try:
        # 延迟获取持仓，避免休市时出错
        import time
        time.sleep(1)
        pos = u.position
        print(f"  ✅ {code} ({p.get('name','')}) 登录成功")
        return u, pos
    except Exception as e:
        print(f"  ⚠️ {code} 获取持仓失败 (可能是休市): {e}")
        # 仍然返回 u，允许调仓
        return u, []

def sync(code, targets, dry=True):
    cfg = load_config()
    p = cfg['portfolios'].get(code,{})
    print(f"\n{'='*50}\n📊 {code} ({p.get('name','')})\n{'='*50}")
    u, pos = init_xq(cfg, code)
    if not u: return
    print(f"\n📋 当前持仓:")
    holdings = pos if isinstance(pos, list) else pos.get('holdings', [])
    for h in holdings:
        print(f"   {h.get('stock_name',h.get('stock_code','?'))}: {h.get('weight',0)*100:.2f}%")
    if dry:
        print(f"\n🔍 预览调仓 ({len(targets)}只):")
        for t in targets:
            a = '买入' if t['weight']>0 else '卖出'
            print(f"   {a} {t.get('name',t['ticker'])} ({t['ticker']}): {t['weight']*100:.1f}%")
        print(f"\n   执行加 --no-dry-run")
        return
    try:
        # 使用正确的 adjust_weight 参数格式
        for t in targets:
            stock_code = t['ticker']
            weight = t['weight']
            print(f"  调仓: {stock_code} -> {weight*100:.1f}%")
            u.adjust_weight(stock_code, weight)
        print(f"  ✅ 调仓成功")
    except Exception as e:
        print(f"  ❌ 调仓失败: {e}")

def sync_fixed(code, dry=True):
    """同步固定持仓组合"""
    cfg = load_config()
    p = cfg['portfolios'].get(code, {})
    holdings = p.get('holdings', {})
    
    if not holdings:
        print(f"⚠️ {code} 无固定持仓配置")
        return
    
    print(f"\n{'='*50}")
    print(f"📊 {code} ({p.get('name', '')}) - 固定持仓")
    print(f"{'='*50}")
    
    # 转换为 targets 格式
    targets = []
    for stock_code, weight in holdings.items():
        # 转换代码格式
        if stock_code.startswith('SH'):
            ticker = stock_code[2:] + '.SH'
        elif stock_code.startswith('SZ'):
            ticker = stock_code[2:] + '.SZ'
        elif stock_code.startswith('6'):
            ticker = stock_code + '.SH'
        elif stock_code.startswith('0') or stock_code.startswith('3'):
            ticker = stock_code + '.SZ'
        else:
            ticker = stock_code
        
        targets.append({
            'ticker': ticker,
            'name': '',
            'weight': weight,
        })
    
    # 调用 sync
    sync(code, targets, dry)

def main():
    dry = '--dry-run' in sys.argv or '-n' in sys.argv
    if dry: print("🔍 DRY RUN\n")
    cfg = load_config()
    for code, p in cfg['portfolios'].items():
        src = p.get('source','')
        if src == 'fixed':
            sync_fixed(code, dry)
        elif src == 'allocator':
            t = get_targets()
            mw = p.get('max_weight',0.15)
            for x in t: x['weight'] = min(x['weight'],mw) if x['weight']>0 else max(x['weight'],-mw)
            sync(code, t, dry)
        elif src == 'futures_simulator':
            fp = get_futures()
            print(f"\n📊 期货模拟 {code}: {len(fp)} 持仓")
            for x in fp: print(f"   {x['contract']} {'多' if x['dir']=='long' else '空'} {x['lots']}手")
            print("   ⚠️ 期货仅本地模拟")

if __name__ == '__main__':
    main()
