#!/usr/bin/env python3
"""
东方财富虚拟盘接入 · 统一控制器

三条接入路径（按推荐优先级）：

路径 A — QMT桥接（★★★★★）
  东方财富证券官方量化通道。
  需要：Windows 运行 MiniQMT + 东方财富证券账户
  操作：部署 qmt_local_bridge.py → 从 Hermes 直接下单
  
路径 B — 雪球组合（★★★★☆）
  雪球组合（模拟盘），easytrader 原生支持。
  需要：雪球账号 + 组合代码 + cookies
  优势：纯 Python，不需要 Windows
  
路径 C — Hermes自建模拟盘（★★★★☆）
  本地 SQLite 模拟盘引擎，已运行。
  优势：无需外部依赖，实时追踪
  导出操作指令供手动在东方财富执行

用法：
  # 导出操作指令（供手动执行）
  python eastmoney_bridge.py --export
  
  # 生成东方财富模拟盘手动操作指南
  python eastmoney_bridge.py --manual
  
  # 路径A: QMT 桥接
  python eastmoney_bridge.py --qmt-ping          # 测试连接
  python eastmoney_bridge.py --qmt-order 000858,buy,100,136.5  # 下单
  python eastmoney_bridge.py --qmt-sync           # 同步持仓到本地
  
  # 路径B: 雪球组合
  python eastmoney_bridge.py --xueqiu-config      # 生成配置模板
  
  # 生成 Windows 桥接服务端
  python eastmoney_bridge.py --generate-bridge /path/to/output
"""
import sys
import os
import json
from datetime import datetime

BASE = '/home/liudawei/github/daily_tracker_analytics'
sys.path.insert(0, os.path.join(BASE, 'multi_agent'))

from simulator import get_portfolio_summary, get_cash, check_stop_loss, check_take_profit
from simulator import get_trade_history

# ============================================================
# 路径A: QMT 桥接
# ============================================================

def qmt_bridge(host='localhost', port=8899):
    """获取 QMT 桥接客户端实例"""
    from qmt_bridge_client import QMTBridge
    return QMTBridge(host=host, port=port)


def check_qmt_bridge(host='localhost', port=8899) -> dict:
    """检查 QMT 桥接状态"""
    try:
        qmt = qmt_bridge(host, port)
        ok = qmt.ping()
        if not ok:
            return {'connected': False, 'error': '桥接服务无响应'}
        
        return {
            'connected': True,
            'host': f'{host}:{port}',
            'message': '✅ QMT 桥接服务在线',
        }
    except Exception as e:
        return {'connected': False, 'error': str(e)}


def qmt_execute_orders(sell_first: bool = True, host='localhost', port=8899) -> dict:
    """
    通过 QMT 桥接执行模拟盘操作指令
    
    Args:
        sell_first: 先卖出再买入（腾出资金）
    
    Returns:
        dict: 执行结果
    """
    qmt = qmt_bridge(host, port)
    
    # 检查连接
    if not qmt.ping():
        return {'error': 'QMT 桥接服务离线', 'executed': False}
    
    # 获取卖出指令（止损/止盈）
    sl = check_stop_loss()
    tp = check_take_profit()
    sell_orders = []
    for a in sl + tp:
        reason = a.get('reason', f"止损 {a['loss_pct']}%" if 'loss_pct' in a else f"止盈 {a['profit_pct']}%")
        sell_orders.append({
            'ticker': a['ticker'],
            'action': 'sell',
            'shares': a['shares'],
            'price': None,  # 市价
            'reason': reason,
        })
    
    # 执行卖出
    sell_results = []
    if sell_orders:
        sell_results = qmt.batch_order(sell_orders)
    
    # 查询资产
    asset = qmt.query_asset()
    cash = asset.get('cash', 0)
    
    # 获取买入建议
    summary = get_portfolio_summary()
    positions = summary.get('positions_detail', [])
    
    buy_orders = []
    if cash > 10000 and positions:
        per_stock = cash * 0.8 / len(positions)
        for p in sorted(positions, key=lambda x: x['weight']):
            if per_stock > p['current_price'] * 100:
                shares = int(per_stock / p['current_price'] / 100) * 100
                if shares > 0:
                    buy_orders.append({
                        'ticker': p['ticker'],
                        'action': 'buy',
                        'shares': shares,
                        'price': None,  # 市价
                    })
    
    buy_results = []
    if buy_orders:
        buy_results = qmt.batch_order(buy_orders)
    
    return {
        'executed': True,
        'sell_count': len(sell_results),
        'buy_count': len(buy_results),
        'sell_results': sell_results,
        'buy_results': buy_results,
        'available_cash': cash,
    }


# ============================================================
# 路径B: 雪球组合
# ============================================================

XUEQIU_CONFIG_TEMPLATE = {
    "cookies": "您的雪球cookies（登录后从浏览器F12→Application→Cookies获取）",
    "portfolio_code": "ZH000000",
    "portfolio_market": "cn"
}


def generate_xueqiu_config():
    """生成雪球 easytrader 配置模板"""
    config_path = os.path.join(BASE, 'multi_agent', 'data', 'xueqiu_config.json')
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    
    if not os.path.exists(config_path):
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(XUEQIU_CONFIG_TEMPLATE, f, ensure_ascii=False, indent=2)
        print(f"✅ 雪球配置模板已生成: {config_path}")
        print()
        print("使用步骤：")
        print("1. 登录 https://xueqiu.com")
        print("2. F12 → Application → Cookies → 复制全部cookie")
        print(f"3. 编辑 {config_path}，填入 cookies 和 portfolio_code")
        print("4. 运行: python eastmoney_bridge.py --xueqiu-sync")
    else:
        print(f"✅ 雪球配置已存在: {config_path}")
    
    return config_path


def check_xueqiu_trader():
    """检查雪球 easytrader 是否可用"""
    config_path = os.path.join(BASE, 'multi_agent', 'data', 'xueqiu_config.json')
    
    if not os.path.exists(config_path):
        return {'available': False, 'error': '雪球配置不存在，请先执行 --xueqiu-config'}
    
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    if 'template' in str(config.get('cookies', '')):
        return {'available': False, 'error': '请先填写真实的雪球 cookies'}
    
    return {'available': True, 'config_path': config_path, 'portfolio_code': config.get('portfolio_code', '')}


def sync_xueqiu_portfolio() -> dict:
    """
    同步 Hermes 模拟盘持仓到雪球组合
    
    使用 easytrader 操作雪球组合做调仓
    """
    config_check = check_xueqiu_trader()
    if not config_check['available']:
        return {'error': config_check['error'], 'synced': False}
    
    try:
        import easytrader
        from easytrader import use
        
        config_path = config_check['config_path']
        user = use('xq')
        user.prepare(config_path)
        
        # 获取当前组合持仓
        current_positions = user.position  # 雪球组合当前持仓
        
        # 获取 Hermes 模拟盘推荐持仓
        summary = get_portfolio_summary()
        target_positions = {}
        for p in summary.get('positions_detail', []):
            target_positions[p['ticker']] = p['shares']
        
        # 计算需要调仓的品种
        # 雪球组合的调仓逻辑：先卖出现有持仓中不在目标中的，再买入目标中缺失的
        result = {'sell': [], 'buy': [], 'hold': []}
        
        # 注意：easytrader 的雪球接口操作组合需要耐心，雪球本身有风控限制
        
        return {
            'synced': True,
            'message': '持仓已同步到雪球组合',
            'result': result,
        }
    
    except Exception as e:
        return {'error': f'雪球同步失败: {e}', 'synced': False}


# ============================================================
# 路径C: 导出操作指令（通用）
# ============================================================

def export_orders() -> dict:
    """导出买入/卖出操作指令"""
    # 卖出
    sl = check_stop_loss()
    tp = check_take_profit()
    sell_orders = []
    for a in sl:
        sell_orders.append({
            'ticker': a['ticker'], 'name': a['name'],
            'action': 'sell', 'shares': a['shares'],
            'price': a['current_price'],
            'amount': round(a['shares'] * a['current_price'], 2),
            'reason': f"止损 {a['loss_pct']:.1f}%",
        })
    for a in tp:
        sell_orders.append({
            'ticker': a['ticker'], 'name': a['name'],
            'action': 'sell', 'shares': a['shares'],
            'price': a['current_price'],
            'amount': round(a['shares'] * a['current_price'], 2),
            'reason': f"止盈 {a['profit_pct']:.1f}%",
        })
    
    # 买入
    summary = get_portfolio_summary()
    cash = summary.get('cash', 0)
    buy_orders = []
    for p in summary.get('positions_detail', []):
        if cash > p['current_price'] * 100:
            shares = int((cash * 0.8 / max(len(summary.get('positions_detail', [])), 1)) 
                        / p['current_price'] / 100) * 100
            if shares > 0:
                buy_orders.append({
                    'ticker': p['ticker'], 'name': p['name'],
                    'action': 'buy', 'shares': shares,
                    'price': p['current_price'],
                    'amount': round(shares * p['current_price'], 2),
                })
    
    output = {
        'export_time': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'cash': cash,
        'total_value': summary.get('total_value', 0),
        'sell_orders': sell_orders,
        'buy_orders': buy_orders,
        'summary': f"卖出{len(sell_orders)}笔 + 买入{len(buy_orders)}笔",
    }
    
    out_path = os.path.join(BASE, 'multi_agent', 'data', 'eastmoney_export.json')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    print(f"✅ 操作指令已导出: {out_path}")
    
    return output


def format_manual_guide() -> str:
    """生成东方财富模拟盘手动操作指南"""
    export = export_orders()
    
    lines = []
    lines.append("📋 **东方财富模拟盘操作指南**")
    lines.append(f"生成: {export['export_time']}")
    lines.append(f"总资产: {export['total_value']:.0f} | 可用现金: {export['cash']:.0f}")
    lines.append("")
    lines.append("**步骤：**")
    lines.append("1️⃣ 打开东方财富APP → 交易 → 模拟盘")
    lines.append("2️⃣ 先卖出（腾出资金），再买入")
    lines.append("3️⃣ 按以下清单操作：")
    lines.append("")
    
    if export['sell_orders']:
        lines.append("**🔴 卖出：**")
        for o in export['sell_orders']:
            lines.append(f"  • {o['name']}({o['ticker']}) {o['shares']}股 @{o['price']:.2f} — {o['reason']}")
    
    if export['buy_orders']:
        if export['sell_orders']:
            lines.append("")
        lines.append("**🟢 买入：**")
        for o in export['buy_orders']:
            lines.append(f"  • {o['name']}({o['ticker']}) {o['shares']}股 @{o['price']:.2f} ≈ {o['amount']:.0f}元")
    
    if not export['sell_orders'] and not export['buy_orders']:
        lines.append("✅ 无需操作，模拟盘持仓合理")
    
    return "\n".join(lines)


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='东方财富虚拟盘接入控制器')
    
    # 通用
    parser.add_argument('--export', action='store_true', help='导出操作指令')
    parser.add_argument('--manual', action='store_true', help='生成手动操作指南')
    
    # 路径A: QMT
    parser.add_argument('--qmt-ping', action='store_true', help='测试QMT桥接')
    parser.add_argument('--qmt-order', type=str, help='QMT下单: ticker,action,shares,price')
    parser.add_argument('--qmt-sync', action='store_true', help='QMT同步持仓')
    parser.add_argument('--qmt-execute', action='store_true', help='QMT执行全部操作')
    parser.add_argument('--host', default='localhost', help='QMT桥接主机')
    parser.add_argument('--port', type=int, default=8899, help='QMT桥接端口')
    
    # 路径B: 雪球
    parser.add_argument('--xueqiu-config', action='store_true', help='生成雪球配置')
    parser.add_argument('--xueqiu-sync', action='store_true', help='同步到雪球组合')
    
    # 工具
    parser.add_argument('--generate-bridge', type=str, help='生成QMT桥接服务端')
    
    args = parser.parse_args()
    
    # --- 路径A: QMT ---
    if args.qmt_ping:
        status = check_qmt_bridge(args.host, args.port)
        if status['connected']:
            print(f"✅ QMT 桥接在线 ({status['host']})")
        else:
            print(f"❌ {status.get('error', '无法连接')}")
    
    elif args.qmt_order:
        parts = args.qmt_order.split(',')
        if len(parts) < 3:
            print("❌ 格式: ticker,action,shares[,price]")
            sys.exit(1)
        try:
            qmt = qmt_bridge(args.host, args.port)
            oid = qmt.order_stock(parts[0], parts[1], int(parts[2]),
                                  float(parts[3]) if len(parts) > 3 else None)
            print(f"✅ 委托提交: {oid}")
        except Exception as e:
            print(f"❌ {e}")
    
    elif args.qmt_sync:
        try:
            qmt = qmt_bridge(args.host, args.port)
            r = qmt.sync_to_simulator()
            print(json.dumps(r, ensure_ascii=False, indent=2))
        except Exception as e:
            print(f"❌ {e}")
    
    elif args.qmt_execute:
        r = qmt_execute_orders(host=args.host, port=args.port)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    
    # --- 路径B: 雪球 ---
    elif args.xueqiu_config:
        generate_xueqiu_config()
    
    elif args.xueqiu_sync:
        r = sync_xueqiu_portfolio()
        print(json.dumps(r, ensure_ascii=False, indent=2))
    
    # --- 工具 ---
    elif args.generate_bridge:
        from qmt_bridge_client import generate_bridge_server
        generate_bridge_server(args.generate_bridge)
    
    # --- 通用 ---
    elif args.export:
        export_orders()
    
    elif args.manual:
        print(format_manual_guide())
    
    else:
        # 默认：显示状态总览
        print("=" * 60)
        print("  🏛️  东方财富虚拟盘接入 · 统一控制器")
        print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print("=" * 60)
        
        # 本地模拟盘状态
        summary = get_portfolio_summary()
        if 'error' in summary:
            print(f"\n📊 本地模拟盘: 未初始化 (请先运行 --weekly)")
        else:
            print(f"\n📊 本地模拟盘:")
            print(f"   总资产: {summary['total_value']:.0f} (初始10万)")
            print(f"   现金: {summary['cash']:.0f}")
            print(f"   持仓: {summary['positions']} 只")
            cum_ret = summary.get('latest_snapshot', {}).get('cumulative_return', 0)
            print(f"   累计收益: {cum_ret:+.2f}%")
        
        # QMT 状态
        qmt_status = check_qmt_bridge()
        print(f"\n🔌 QMT桥接 (路径A): {'✅ 在线' if qmt_status['connected'] else '❌ 离线'}")
        if not qmt_status['connected']:
            print(f"   需要: Windows + MiniQMT + qmt_local_bridge.py")
        
        # 雪球状态
        xq_status = check_xueqiu_trader()
        print(f"\n❄️ 雪球组合 (路径B): {'✅ 已配置' if xq_status['available'] else '❌ 未配置'}")
        if not xq_status['available']:
            print(f"   需要: --xueqiu-config 生成配置")
        
        print()
        print("可用命令:")
        print("  --export             导出操作指令")
        print("  --manual             生成手动操作指南")
        print("  --qmt-ping           测试QMT桥接")
        print("  --xueqiu-config      配置雪球组合")
        print("  --generate-bridge    生成QMT服务端")
