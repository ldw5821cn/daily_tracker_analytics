#!/usr/bin/env python3
"""
雪球组合 · 全自动调仓器

每周/每日全A股扫描 → 推荐Top10 → 雪球组合一键调仓

用法：
  # 每周全量扫描 + 雪球调仓
  python3 xueqiu_rebalancer.py --weekly
  
  # 仅调仓（使用已有的 weekly_top10.json）
  python3 xueqiu_rebalancer.py --rebalance
  
  # 查询组合状态
  python3 xueqiu_rebalancer.py --status
"""
import sys
import os
import json
import time
from datetime import datetime

BASE = '/home/liudawei/github/daily_tracker_analytics'
sys.path.insert(0, os.path.join(BASE, 'multi_agent'))

DATA_DIR = os.path.join(BASE, 'multi_agent', 'data')
CONFIG_PATH = os.path.join(DATA_DIR, 'xueqiu_config.json')
TOP10_PATH = os.path.join(DATA_DIR, 'weekly_top10.json')
os.makedirs(DATA_DIR, exist_ok=True)

# 雪球组合初始资金
INITIAL_CAPITAL = 1000000  # 雪球默认100万，但我们映射到10万


def get_user():
    """获取雪球 easytrader 用户实例"""
    from easytrader import use
    
    if not os.path.exists(CONFIG_PATH):
        print(f"❌ 雪球配置不存在: {CONFIG_PATH}")
        print("   请先运行: python eastmoney_bridge.py --xueqiu-config")
        sys.exit(1)
    
    user = use('xq', initial_assets=INITIAL_CAPITAL)
    user.prepare(CONFIG_PATH)
    user.autologin()
    return user


def query_status(user=None):
    """查询雪球组合当前状态"""
    close = user is None
    if user is None:
        user = get_user()
    
    with open(CONFIG_PATH) as f:
        config = json.load(f)
    code = config['portfolio_code']
    
    # 查组合信息
    url = f"https://xueqiu.com/cubes/rebalancing/history.json?cube_symbol={code}&count=1"
    resp = user.s.get(url)
    data = resp.json()
    
    info = data['list'][0] if data.get('list') else {}
    
    # 查当前持仓
    try:
        pos = user.position
    except:
        pos = []
    
    result = {
        'cube_code': code,
        'cash_pct': info.get('cash', 100),
        'cube_id': info.get('cube_id'),
        'holdings': info.get('holdings'),
        'positions': pos if isinstance(pos, list) else [],
        'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
    }
    
    if close:
        user.exit()
    
    return result


def xueqiu_rebalance(top10_list: list, user=None) -> dict:
    """
    将 Top10 推荐同步到雪球组合
    
    Args:
        top10_list: [{ticker, name, price, composite_score}]
        user: 已有的 easytrader 实例
    
    Returns:
        dict: 调仓结果
    """
    close = user is None
    if user is None:
        user = get_user()
    
    # 构建权重 {股票代码: 权重百分比}
    # 等权分配，留10%现金
    weights = {}
    n = len(top10_list[:10])
    if n == 0:
        return {'error': 'Top10 列表为空', 'rebalanced': False}
    
    weight_per_stock = 90.0 / n  # 等权，留10%现金
    
    for i, s in enumerate(top10_list[:10]):
        ticker = s.get('ticker', s.get('code', ''))
        name = s.get('name', '')
        
        # 雪球代码格式：SH或SZ前缀
        if ticker.startswith(('6', '5')):
            xq_code = f"SH{ticker}"
        else:
            xq_code = f"SZ{ticker}"
        
        # 特殊处理：第一个权重稍高（分数四舍五入的余量）
        w = weight_per_stock
        if i == 0:
            w = round(weight_per_stock + (90.0 - weight_per_stock * n), 2)
        
        weights[xq_code] = round(w, 2)
        print(f"  📊 {name}({xq_code}): {weights[xq_code]}%")
    
    print(f"\n🚀 调仓到雪球组合...")
    print(f"   组合: ZH3650487")
    print(f"   标的: {len(weights)} 只")
    print(f"   总仓位: {sum(weights.values())}% (预留 {100-sum(weights.values())}% 现金)")
    
    # 获取当前持仓
    try:
        current_pos = user.position or []
    except:
        current_pos = []
    
    existing_codes = {p['stock_code'] for p in current_pos}
    to_remove = existing_codes - set(weights.keys())
    
    if to_remove:
        print(f"\n🧹 清理 {len(to_remove)} 只旧持仓...")
        for code in to_remove:
            name = next((p['stock_name'] for p in current_pos if p['stock_code'] == code), code)
            try:
                user.adjust_weight(code, 0.0)
                print(f"  ✅ {name}({code}) 清仓")
                time.sleep(0.3)
            except Exception as e:
                print(f"  ⚠️ {name}({code}) 无法清仓(可能T+1): {e}")
    
    # 设置新仓位
    print(f"\n📊 设置目标仓位...")
    results = []
    errors = []
    for xq_code, weight in weights.items():
        try:
            user.adjust_weight(xq_code, weight)
            results.append(f"{xq_code}={weight}%")
            print(f"  ✅ {xq_code}: {weight}%")
            time.sleep(0.3)
        except Exception as e:
            errors.append(f"{xq_code}: {e}")
            print(f"  ⚠️ {xq_code}: {e}")
    
    # 验证
    time.sleep(2)
    try:
        final_pos = user.position
        print(f"\n📊 调仓统计: 成功{len(results)} 失败{len(errors)} 最终持仓{len(final_pos)}只")
    except:
        pass
    
    result = {
        'rebalanced': len(results) > 0,
        'success_count': len(results),
        'error_count': len(errors),
        'errors': errors,
        'weights': weights,
    }
    
    if close:
        user.exit()
    
    return result


def weekly_scan_and_rebalance():
    """每周全量扫描 → 雪球组合调仓"""
    print(f"\n{'='*60}")
    print(f"  🏆  · 每周全量扫描 → 雪球组合")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")
    start = time.time()
    
    # Step 1: 全A股扫描
    print(f"\n📡 Step 1: 全A股扫描...")
    from run_pipeline import scan_all_a_shares, scan_and_score
    
    stocks = scan_all_a_shares()
    all_scores = scan_and_score(stocks)
    top10 = all_scores[:10]
    
    # 保存
    with open(TOP10_PATH, 'w', encoding='utf-8') as f:
        # 转为模拟盘格式
        fmt = [{
            'ticker': s['code'],
            'name': s['name'],
            'price': s['price'],
            'composite_score': s['composite_score'],
            'sector': s.get('market', ''),
        } for s in top10]
        json.dump(fmt, f, ensure_ascii=False, indent=2)
    
    print(f"\n  📋 Top10:")
    for i, s in enumerate(top10, 1):
        print(f"  {i:2d}. {s['name']:8s}({s['code']}) 评分{s['composite_score']:3d} {s['changepercent']:+.2f}% @{s['price']:.2f}")
    
    # Step 2: 初始化 Hermes 本地模拟盘
    print(f"\n💰 Step 2: 本地模拟盘初始化...")
    from simulator import init_weekly_portfolio, get_portfolio_summary
    init_result = init_weekly_portfolio(
        [{'ticker': s['code'], 'name': s['name'], 'price': s['price'],
          'composite_score': s['composite_score'], 'sector': s.get('market', '')}
         for s in top10],
        capital=100000.0
    )
    print(f"   持仓: {init_result['positions']} 只 | 总资产: {init_result['total']:.0f}")
    
    # Step 3: 雪球组合调仓
    print(f"\n❄️ Step 3: 雪球组合调仓...")
    user = get_user()
    r = xueqiu_rebalance(
        [{'ticker': s['code'], 'name': s['name'], 'price': s['price'],
          'composite_score': s['composite_score']}
         for s in top10],
        user
    )
    
    if r.get('rebalanced'):
        print(f"\n✅ 雪球组合调仓成功!")
    else:
        print(f"\n⚠️ 雪球调仓异常: {r.get('error', '未知')}")
    
    # Step 4: Pages更新
    print(f"\n📊 Step 4: Pages更新...")
    from daily_tracker import update_pages
    summary = get_portfolio_summary()
    update_pages(top10, summary)
    
    # Step 5: 部署
    import subprocess
    print(f"\n🚀 Step 5: Pages部署...")
    subprocess.run(['bash', 'scripts/deploy_reports.sh'], 
                  cwd=BASE, capture_output=True, timeout=60)
    
    user.exit()
    
    elapsed = time.time() - start
    print(f"\n{'='*60}")
    print(f"  ✅ 全链路完成! 耗时: {elapsed:.0f}s")
    print(f"  🌐 雪球组合: https://xueqiu.com/P/ZH3650487")
    print(f"{'='*60}")


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='雪球组合全自动调仓器')
    parser.add_argument('--weekly', action='store_true', help='每周全量扫描+雪球调仓')
    parser.add_argument('--rebalance', type=str, help='从JSON文件调仓')
    parser.add_argument('--status', action='store_true', help='查询组合状态')
    
    args = parser.parse_args()
    
    if args.weekly:
        weekly_scan_and_rebalance()
    
    elif args.rebalance:
        with open(args.rebalance, 'r', encoding='utf-8') as f:
            top10 = json.load(f)
        r = xueqiu_rebalance(top10)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    
    elif args.status:
        user = get_user()
        status = query_status(user)
        print(f"\n❄️ 雪球组合状态")
        print(f"   代码: {status['cube_code']}")
        print(f"   现金: {status['cash_pct']}%")
        print(f"   持仓: {len(status['positions'])} 只")
        for p in status['positions'][:10]:
            print(f"     {p}")
        user.exit()
    
    else:
        parser.print_help()
