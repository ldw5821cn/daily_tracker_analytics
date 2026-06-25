#!/usr/bin/env python3
"""
QMT 桥接客户端 — 从 WSL/Linux 远程控制 Windows 上的 MiniQMT

架构：
  WSL (Hermes Agent)          Windows (MiniQMT)
  ┌─────────────────┐         ┌──────────────────────┐
  │ qmt_bridge_client │──HTTP→│ qmt_local_bridge.py   │
  │ (本文件)          │←JSON──│ (运行在 Windows)       │
  │                   │         │    ↓ xtquant          │
  │ simulator │         │ MiniQMT → 模拟盘/实盘 │
  └─────────────────┘         └──────────────────────┘

两种桥接模式：
  1. qmt-bridge（推荐）: https://github.com/atompilot/qmt-bridge
     开源的 HTTP/WebSocket 服务，已在生产验证
  
  2. 自建桥接: qmt_local_bridge.py（本仓库配套）
     轻量级，直接暴露 REST API

用法（在 WSL 端）：
  from qmt_bridge_client import QMTBridge
  
  # 连接已有桥接服务
  qmt = QMTBridge(host='192.168.1.100', port=8899)
  
  # 查询资产
  assets = qmt.query_asset()
  
  # 下单
  order_id = qmt.order_stock('600000.SH', 'buy', 100, 10.5)
  
  # 同步持仓到本地模拟盘
  qmt.sync_to_simulator()
"""
import json
import os
import sys
import urllib.request
import urllib.error
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

DEFAULT_HOST = 'localhost'
DEFAULT_PORT = 8899
DEFAULT_TIMEOUT = 15


class QMTBridgeError(Exception):
    """QMT 桥接通信异常"""
    pass


class QMTBridge:
    """
    QMT 桥接客户端
    
    通过 HTTP API 与 Windows 上的 qmt_local_bridge.py 或 qmt-bridge 通信。
    
    Args:
        host: Windows 机器 IP（WSL 中用 localhost 即可访问宿主机）
        port: 桥接服务端口
        timeout: HTTP 超时秒数
    """
    
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 timeout: int = DEFAULT_TIMEOUT):
        self.base_url = f"http://{host}:{port}"
        self.timeout = timeout
        self._connected = False
    
    def _request(self, endpoint: str, data: dict = None,
                 method: str = 'GET') -> dict:
        """发送 HTTP 请求到桥接服务"""
        url = f"{self.base_url}{endpoint}"
        
        if method == 'GET' and data:
            params = '&'.join(f"{k}={urllib.request.quote(str(v))}" 
                              for k, v in data.items())
            url = f"{url}?{params}"
        
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode('utf-8') if data and method != 'GET' else None,
            headers={
                'Content-Type': 'application/json',
                'User-Agent': 'Hermes-QMT-Bridge/1.0',
            },
            method=method,
        )
        
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                result = json.loads(resp.read().decode('utf-8'))
                if result.get('error'):
                    raise QMTBridgeError(result['error'])
                return result.get('data', result)
        except urllib.error.URLError as e:
            raise QMTBridgeError(f"无法连接 QMT 桥接服务 ({self.base_url}): {e}")
        except json.JSONDecodeError as e:
            raise QMTBridgeError(f"桥接返回非JSON数据: {e}")
    
    # ============================================================
    # 连接测试
    # ============================================================
    
    def ping(self) -> bool:
        """测试桥接服务是否在线"""
        try:
            result = self._request('/ping')
            self._connected = True
            return True
        except QMTBridgeError:
            self._connected = False
            return False
    
    # ============================================================
    # 账户查询
    # ============================================================
    
    def query_asset(self, account_id: str = None) -> dict:
        """
        查询账户资产
        
        Returns:
            {
                'total_assets': float,    # 总资产
                'cash': float,            # 可用资金
                'market_value': float,    # 持仓市值
                'profit': float,          # 总盈亏
                'profit_ratio': float,    # 盈亏比例
            }
        """
        data = {'account_id': account_id} if account_id else None
        return self._request('/asset', data, 'POST')
    
    def query_positions(self) -> List[dict]:
        """
        查询当前持仓
        
        Returns:
            [{
                'ticker': str,           # 代码 (如 '600000.SH')
                'name': str,             # 名称
                'shares': int,           # 持仓数量
                'cost_price': float,     # 成本价
                'current_price': float,  # 现价
                'market_value': float,   # 市值
                'profit_ratio': float,   # 盈亏比例
            }]
        """
        return self._request('/positions')
    
    def query_orders(self, status: str = None, limit: int = 20) -> List[dict]:
        """
        查询委托记录
        
        Args:
            status: 'all', 'pending', 'filled', 'cancelled'
            limit: 最大返回条数
        """
        data = {'status': status or 'all', 'limit': limit}
        return self._request('/orders', data, 'POST')
    
    # ============================================================
    # 下单
    # ============================================================
    
    def order_stock(self, ticker: str, action: str, shares: int,
                    price: float = None, account_id: str = None) -> str:
        """
        A股买卖委托
        
        Args:
            ticker: 股票代码 (如 '600000.SH' 或 '000858')
            action: 'buy' 或 'sell'
            shares: 股数 (必须100的整数倍)
            price: 指定价格，None=市价
            account_id: 资金账号
        
        Returns:
            order_id: 委托编号
        """
        # 自动补全交易所后缀
        if '.' not in ticker:
            if ticker.startswith(('6', '5')):
                ticker = f"{ticker}.SH"
            else:
                ticker = f"{ticker}.SZ"
        
        data = {
            'ticker': ticker,
            'action': action,
            'shares': shares,
            'price': price,
            'price_type': 2 if price is None else 1,  # 2=市价, 1=限价
        }
        if account_id:
            data['account_id'] = account_id
        
        result = self._request('/order', data, 'POST')
        return result.get('order_id', '')
    
    def cancel_order(self, order_id: str) -> bool:
        """撤单"""
        result = self._request('/cancel', {'order_id': order_id}, 'POST')
        return result.get('success', False)
    
    def batch_order(self, orders: List[dict]) -> List[dict]:
        """
        批量下单
        
        Args:
            orders: [{'ticker', 'action', 'shares', 'price'}]
        
        Returns:
            [{'order_id', 'ticker', 'status'}]
        """
        return self._request('/batch_order', {'orders': orders}, 'POST')
    
    # ============================================================
    # 同步到 Hermes 本地模拟盘
    # ============================================================
    
    def sync_to_simulator(self) -> dict:
        """
        将 QMT 模拟盘的实际持仓同步到 Hermes 本地模拟盘
        
        这样我们的本地模拟盘就能反映东方财富模拟盘里的真实状态。
        """
        try:
            positions = self.query_positions()
            asset = self.query_asset()
        except QMTBridgeError as e:
            return {'error': f"同步失败: {e}", 'synced': False}
        
        # 导入本地模拟盘
        import sys
        sys.path.insert(0, os.path.join(
            os.path.dirname(__file__), '..', 'multi_agent'))
        
        # 构建 top10 格式的数据
        synced_positions = []
        for p in positions:
            ticker = p['ticker'].replace('.SH', '').replace('.SZ', '')
            synced_positions.append({
                'ticker': ticker,
                'name': p.get('name', ''),
                'price': p.get('current_price', 0),
                'shares': p.get('shares', 0),
                'entry_price': p.get('cost_price', 0),
                'composite_score': 50,  # 占位
                'sector': '',
            })
        
        return {
            'synced': True,
            'total_assets': asset.get('total_assets', 0),
            'positions': len(synced_positions),
            'positions_list': synced_positions,
        }


# ============================================================
# qmt_local_bridge.py — 在 Windows 上运行的服务端
# ============================================================

QMT_BRIDGE_SERVER_CODE = r'''#!/usr/bin/env python3
"""
qmt_local_bridge.py — QMT 本地桥接服务（在 Windows 上运行）

通过 HTTP API 暴露 xtquant 功能，供 WSL/Linux 端调用。

启动：
  python qmt_local_bridge.py --port 8899

依赖：
  pip install xtquant flask flask-cors
  （需在 Windows Python 环境中安装）

API 端点：
  GET  /ping          — 健康检查
  POST /asset         — 查询资产
  GET  /positions     — 查询持仓
  POST /order         — 下单
  POST /cancel        — 撤单
  POST /batch_order   — 批量下单
  GET  /orders        — 查询委托

环境变量：
  QMT_PATH: MiniQMT 安装路径（默认 C:\\qmt\\userdata_mini）
  QMT_ACCOUNT: 资金账号
"""
import sys
import os
import json
import threading
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ========== xtquant 初始化 ==========
QMT_PATH = os.environ.get('QMT_PATH', r'C:\qmt\userdata_mini')
QMT_ACCOUNT = os.environ.get('QMT_ACCOUNT', '')

trader = None
acc = None


def init_qmt():
    """初始化 QMT 连接"""
    global trader, acc
    from xtquant import xttrader as xt
    from xtquant import xttype
    from xtquant import xtconstant
    
    trader = xt.XtQuantTrader(QMT_PATH, int(datetime.now().timestamp() * 1000) % 1000000)
    trader.start()
    trader.connect()
    
    if QMT_ACCOUNT:
        acc = xttype.XtAccountInfo(
            account_id=QMT_ACCOUNT,
            account_type=xtconstant.STOCK_ACCOUNT
        )
        trader.subscribe(acc)


# ========== API 端点 ==========

@app.route('/ping')
def ping():
    return jsonify({'data': {'status': 'ok', 'time': datetime.now().isoformat()}})


@app.route('/asset', methods=['POST'])
def query_asset():
    if trader is None or acc is None:
        return jsonify({'error': 'QMT 未初始化', 'data': {}})
    
    try:
        asset = trader.query_asset(acc)
        return jsonify({'data': {
            'total_assets': float(asset.get('total_assets', 0)),
            'cash': float(asset.get('cash', 0)),
            'market_value': float(asset.get('market_value', 0)),
            'profit': float(asset.get('profit', 0)),
            'profit_ratio': float(asset.get('profit_ratio', 0)),
        }})
    except Exception as e:
        return jsonify({'error': str(e), 'data': {}})


@app.route('/positions')
def query_positions():
    if trader is None or acc is None:
        return jsonify({'error': 'QMT 未初始化', 'data': []})
    
    try:
        positions = trader.query_stock_positions(acc)
        result = []
        for p in positions:
            result.append({
                'ticker': p.get('stock_code', ''),
                'name': p.get('stock_name', ''),
                'shares': int(p.get('amount', 0)),
                'cost_price': float(p.get('cost_price', 0)),
                'current_price': float(p.get('last_price', 0)),
                'market_value': float(p.get('market_value', 0)),
                'profit_ratio': float(p.get('profit_ratio', 0)),
            })
        return jsonify({'data': result})
    except Exception as e:
        return jsonify({'error': str(e), 'data': []})


@app.route('/order', methods=['POST'])
def order_stock():
    if trader is None or acc is None:
        return jsonify({'error': 'QMT 未初始化'})
    
    data = request.get_json()
    ticker = data.get('ticker', '')
    action = data.get('action', 'buy')
    shares = int(data.get('shares', 0))
    price = data.get('price')
    price_type = data.get('price_type', 1)
    
    from xtquant import xtconstant
    
    try:
        order_id = trader.order_stock(
            acc,
            ticker,
            xtconstant.STOCK_BUY if action == 'buy' else xtconstant.STOCK_SELL,
            shares,
            xtconstant.FIX_PRICE if price_type == 1 else xtconstant.LATEST_PRICE,
            price or -1,
        )
        return jsonify({'data': {'order_id': str(order_id), 'success': True}})
    except Exception as e:
        return jsonify({'error': str(e), 'order_id': ''})


@app.route('/cancel', methods=['POST'])
def cancel_order():
    if trader is None:
        return jsonify({'error': 'QMT 未初始化'})
    
    data = request.get_json()
    order_id = data.get('order_id', '')
    
    try:
        trader.cancel_order(order_id)
        return jsonify({'data': {'success': True}})
    except Exception as e:
        return jsonify({'error': str(e), 'success': False})


@app.route('/batch_order', methods=['POST'])
def batch_order():
    """批量下单"""
    data = request.get_json()
    orders = data.get('orders', [])
    results = []
    
    for o in orders:
        try:
            oid = trader.order_stock(
                acc, o['ticker'],
                xtconstant.STOCK_BUY if o['action'] == 'buy' else xtconstant.STOCK_SELL,
                int(o['shares']),
                xtconstant.FIX_PRICE if o.get('price') else xtconstant.LATEST_PRICE,
                o.get('price', -1),
            )
            results.append({'ticker': o['ticker'], 'order_id': str(oid), 'status': 'submitted'})
        except Exception as e:
            results.append({'ticker': o['ticker'], 'order_id': '', 'status': f'failed: {e}'})
    
    return jsonify({'data': results})


@app.route('/orders', methods=['POST'])
def query_orders():
    if trader is None or acc is None:
        return jsonify({'error': 'QMT 未初始化', 'data': []})
    
    data = request.get_json()
    status = data.get('status', 'all')
    limit = data.get('limit', 20)
    
    try:
        orders = trader.query_stock_orders(acc)
        result = []
        for o in orders[:limit]:
            result.append({
                'order_id': o.get('order_id', ''),
                'ticker': o.get('stock_code', ''),
                'action': 'buy' if o.get('order_type') == 1 else 'sell',
                'shares': int(o.get('order_amount', 0)),
                'price': float(o.get('price', 0)),
                'status': o.get('order_status', ''),
                'time': str(o.get('order_time', '')),
            })
        return jsonify({'data': result})
    except Exception as e:
        return jsonify({'error': str(e), 'data': []})


# ========== 启动 ==========
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='QMT 本地桥接服务')
    parser.add_argument('--port', type=int, default=8899, help='监听端口')
    parser.add_argument('--qmt-path', help='MiniQMT 安装路径')
    parser.add_argument('--account', help='资金账号')
    args = parser.parse_args()
    
    if args.qmt_path:
        QMT_PATH = args.qmt_path
    if args.account:
        QMT_ACCOUNT = args.account
    
    # 初始化 QMT
    try:
        init_qmt()
        print(f"✅ QMT 已连接 (路径: {QMT_PATH})")
    except Exception as e:
        print(f"⚠️  QMT 初始化失败: {e}")
        print("   桥接服务仍会启动，但交易接口不可用")
    
    print(f"🚀 QMT 桥接服务启动于 http://localhost:{args.port}")
    app.run(host='0.0.0.0', port=args.port, debug=False)
'''


def generate_bridge_server(dest_dir: str = None):
    """
    生成 qmt_local_bridge.py（用于在 Windows 上运行的桥接服务端）
    
    Args:
        dest_dir: 输出目录（默认打印到 stdout）
    """
    if dest_dir:
        os.makedirs(dest_dir, exist_ok=True)
        path = os.path.join(dest_dir, 'qmt_local_bridge.py')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(QMT_BRIDGE_SERVER_CODE)
        print(f"✅ 已生成: {path}")
        print(f"   复制到 Windows 机器后运行:")
        print(f"   python qmt_local_bridge.py --port 8899")
        return path
    
    print(QMT_BRIDGE_SERVER_CODE)
    return None


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='QMT 桥接客户端')
    parser.add_argument('--host', default=DEFAULT_HOST, help='桥接服务主机')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT, help='桥接服务端口')
    parser.add_argument('--ping', action='store_true', help='测试连接')
    parser.add_argument('--asset', action='store_true', help='查询资产')
    parser.add_argument('--positions', action='store_true', help='查询持仓')
    parser.add_argument('--orders', action='store_true', help='查询委托')
    parser.add_argument('--sync', action='store_true', help='同步持仓到本地模拟盘')
    parser.add_argument('--order', type=str, help='下单: ticker,action,shares,price')
    parser.add_argument('--generate-bridge', type=str, help='生成桥接服务端到指定目录')
    
    args = parser.parse_args()
    
    if args.generate_bridge:
        generate_bridge_server(args.generate_bridge)
        sys.exit(0)
    
    qmt = QMTBridge(host=args.host, port=args.port)
    
    if args.ping:
        ok = qmt.ping()
        print(f"{'✅' if ok else '❌'} 桥接服务: {'在线' if ok else '离线'}")
    
    elif args.asset:
        try:
            asset = qmt.query_asset()
            print(json.dumps(asset, ensure_ascii=False, indent=2))
        except QMTBridgeError as e:
            print(f"❌ {e}")
    
    elif args.positions:
        try:
            positions = qmt.query_positions()
            print(json.dumps(positions, ensure_ascii=False, indent=2))
        except QMTBridgeError as e:
            print(f"❌ {e}")
    
    elif args.orders:
        try:
            orders = qmt.query_orders()
            print(json.dumps(orders, ensure_ascii=False, indent=2))
        except QMTBridgeError as e:
            print(f"❌ {e}")
    
    elif args.sync:
        try:
            r = qmt.sync_to_simulator()
            print(json.dumps(r, ensure_ascii=False, indent=2))
        except QMTBridgeError as e:
            print(f"❌ {e}")
    
    elif args.order:
        parts = args.order.split(',')
        if len(parts) < 3:
            print("❌ 格式: ticker,action,shares[,price]")
            sys.exit(1)
        ticker = parts[0]
        action = parts[1]
        shares = int(parts[2])
        price = float(parts[3]) if len(parts) > 3 else None
        try:
            oid = qmt.order_stock(ticker, action, shares, price)
            print(f"✅ 委托已提交: {oid}")
        except QMTBridgeError as e:
            print(f"❌ {e}")
    
    else:
        parser.print_help()
        print()
        print("=== 快速使用 ===")
        print(f"  # 测试连接")
        print(f"  python {__file__} --ping")
        print(f"  # 查询")
        print(f"  python {__file__} --asset")
        print(f"  python {__file__} --positions")
        print(f"  # 生成Windows桥接服务")
        print(f"  python {__file__} --generate-bridge /tmp/qmt")
