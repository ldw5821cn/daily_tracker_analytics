#!/usr/bin/env python3
"""
📊 期货模拟盘引擎

支持多品种期货模拟交易，保证金计算，每日盈亏追踪。
与雪球组合 ZH3650824 对应。

支持的合约规格（郑商所/大商所/上期所）：
  MA甲醇: 10吨/手, 保证金~10%, 最小变动1元/吨
  可扩展添加更多品种

用法:
  # 初始化（第一次）
  python futures_simulator.py --init --capital 100000
  
  # 开多仓（买入开仓）
  python futures_simulator.py --open --contract MA --direction long --lots 2 --price 2488
  
  # 开空仓（卖出开仓）
  python futures_simulator.py --open --contract MA --direction short --lots 1 --price 2500
  
  # 平仓
  python futures_simulator.py --close --contract MA --lots 1 --price 2460
  
  # 每日更新（联网获取最新行情）
  python futures_simulator.py --daily
  
  # 查看持仓
  python futures_simulator.py --status
  
  # 交易记录
  python futures_simulator.py --history
"""

import sys, os, json, sqlite3, urllib.request, re, ssl
from datetime import datetime, date
from typing import Dict, List, Optional

BASE = '/home/liudawei/github/daily_tracker_analytics'
DB_PATH = os.path.join(BASE, 'multi_agent', 'data', 'futures_simulator.db')
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# ── 合约规格定义 ──
CONTRACT_SPECS = {
    'MA': {  # 甲醇 郑商所
        'name': '甲醇',
        'exchange': '郑商所',
        'multiplier': 10,     # 10吨/手
        'margin_rate': 0.10,  # 保证金比例 10%
        'tick_size': 1,       # 最小变动 1元/吨
        'symbol': 'MA0',      # 新浪行情代码
    },
    'RB': {  # 螺纹钢 上期所
        'name': '螺纹钢',
        'exchange': '上期所',
        'multiplier': 10,
        'margin_rate': 0.10,
        'tick_size': 1,
        'symbol': 'RB0',
    },
    'CU': {  # 沪铜 上期所
        'name': '沪铜',
        'exchange': '上期所',
        'multiplier': 5,      # 5吨/手
        'margin_rate': 0.12,  # 铜保证金稍高
        'tick_size': 10,
        'symbol': 'CU0',
    },
    'SC': {  # 原油 上期能源
        'name': '原油',
        'exchange': '上期能源',
        'multiplier': 1000,   # 1000桶/手
        'margin_rate': 0.15,  # 原油保证金高
        'tick_size': 0.1,
        'symbol': 'SC0',
    },
    'AU': {  # 沪金 上期所
        'name': '沪金',
        'exchange': '上期所',
        'multiplier': 1000,   # 1000克/手
        'margin_rate': 0.10,
        'tick_size': 0.02,
        'symbol': 'AU0',
    },
    'AG': {  # 沪银 上期所
        'name': '沪银',
        'exchange': '上期所',
        'multiplier': 15,     # 15千克/手
        'margin_rate': 0.10,
        'tick_size': 1,
        'symbol': 'AG0',
    },
    'TA': {  # PTA 郑商所
        'name': 'PTA',
        'exchange': '郑商所',
        'multiplier': 5,      # 5吨/手
        'margin_rate': 0.08,
        'tick_size': 2,
        'symbol': 'TA0',
    },
    'I': {  # 铁矿石 大商所
        'name': '铁矿石',
        'exchange': '大商所',
        'multiplier': 100,    # 100吨/手
        'margin_rate': 0.12,
        'tick_size': 0.5,
        'symbol': 'I0',
    },
    'RM': {  # 菜粕 郑商所
        'name': '菜粕',
        'exchange': '郑商所',
        'multiplier': 10,     # 10吨/手
        'margin_rate': 0.08,
        'tick_size': 1,
        'symbol': 'RM0',
    },
    'C': {  # 玉米 大商所
        'name': '玉米',
        'exchange': '大商所',
        'multiplier': 10,     # 10吨/手
        'margin_rate': 0.08,
        'tick_size': 1,
        'symbol': 'C0',
    },
    'FU': {  # 燃料油 上期所
        'name': '燃料油',
        'exchange': '上期所',
        'multiplier': 10,     # 10吨/手
        'margin_rate': 0.10,
        'tick_size': 1,
        'symbol': 'FU0',
    },
}

DEFAULT_CAPITAL = 100000.0  # 10万，与雪球组合一致


def _get_conn() -> sqlite3.Connection:
    """获取DB连接，自动建表"""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contract TEXT NOT NULL,          -- 如 'MA'
            direction TEXT NOT NULL CHECK(direction IN ('long','short')),
            lots INTEGER NOT NULL DEFAULT 0, -- 手数
            entry_price REAL NOT NULL,       -- 开仓均价
            current_price REAL NOT NULL,     -- 最新价
            margin_used REAL NOT NULL,       -- 占用保证金
            pnl_total REAL DEFAULT 0,        -- 累计盈亏（含浮动）
            open_date TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            note TEXT
        );

        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL,
            contract TEXT NOT NULL,
            direction TEXT NOT NULL,
            action TEXT NOT NULL CHECK(action IN ('open_long','open_short','close_long','close_short')),
            lots INTEGER NOT NULL,
            price REAL NOT NULL,
            margin REAL DEFAULT 0,
            pnl REAL DEFAULT 0,
            total_value REAL,                -- 交易标的总额 lots*price*multiplier
            reason TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT NOT NULL,
            total_asset REAL NOT NULL,        -- 总资产 = 现金 + 保证金 + 浮动盈亏
            cash REAL NOT NULL,
            margin_used REAL NOT NULL,
            floating_pnl REAL NOT NULL,       -- 浮动盈亏
            daily_return REAL,
            cumulative_return REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    return conn


# ── 配置读写 ──

def get_config(key: str, default=None) -> str:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        return row['value'] if row else default
    finally:
        conn.close()


def set_config(key: str, value: str):
    conn = _get_conn()
    try:
        conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
    finally:
        conn.close()


def get_cash() -> float:
    raw = get_config('cash')
    return float(raw) if raw else DEFAULT_CAPITAL


def set_cash(val: float):
    set_config('cash', str(round(val, 2)))


# ── 行情获取 ──

def fetch_futures_price(contract: str) -> Optional[Dict]:
    """从新浪获取期货主力合约最新行情"""
    spec = CONTRACT_SPECS.get(contract)
    if not spec:
        return None
    
    symbol = spec['symbol']
    url = f'https://stock.finance.sina.com.cn/futures/api/jsonp.php/var_data_/InnerFuturesNewService.getDailyKLine?symbol={symbol}'
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0',
        'Referer': 'https://finance.sina.com.cn',
    })
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            text = resp.read().decode('utf-8')
        data = json.loads(re.search(r'\[.*\]', text).group())
        d = data[-1]
        prev = data[-2] if len(data) >= 2 else data[-1]
        return {
            'date': d['d'],
            'open': float(d['o']),
            'high': float(d['h']),
            'low': float(d['l']),
            'close': float(d['c']),
            'volume': int(float(d['v'])),
            'prev_close': float(prev['c']),
            'change': float(d['c']) - float(prev['c']),
            'change_pct': round((float(d['c']) - float(prev['c'])) / float(prev['c']) * 100, 2),
        }
    except Exception as e:
        return None


# ── 合约价值计算 ──

def calc_margin(contract: str, price: float, lots: int) -> float:
    """计算开仓占用的保证金"""
    spec = CONTRACT_SPECS.get(contract)
    if not spec:
        return 0
    contract_value = price * spec['multiplier'] * lots  # 合约总价值
    return round(contract_value * spec['margin_rate'], 2)


def calc_contract_value(contract: str, price: float, lots: int) -> float:
    """计算合约总价值"""
    spec = CONTRACT_SPECS.get(contract)
    if not spec:
        return 0
    return price * spec['multiplier'] * lots


# ── 开仓 ──

def open_position(contract: str, direction: str, lots: int, price: float,
                  note: str = '') -> Dict:
    """开仓（开多/开空）"""
    spec = CONTRACT_SPECS.get(contract)
    if not spec:
        return {'success': False, 'error': f'不支持的合约: {contract}'}
    
    margin_needed = calc_margin(contract, price, lots)
    cash = get_cash()
    
    if margin_needed > cash:
        return {
            'success': False,
            'error': f'保证金不足: 需要{margin_needed:.0f}, 可用现金{cash:.0f}',
            'margin_needed': margin_needed,
            'cash': cash,
        }
    
    today = date.today().strftime('%Y-%m-%d')
    contract_value = calc_contract_value(contract, price, lots)
    
    conn = _get_conn()
    try:
        # 检查是否已有同方向持仓（合并）
        existing = conn.execute(
            "SELECT * FROM positions WHERE contract=? AND direction=? AND is_active=1",
            (contract, direction)
        ).fetchone()
        
        if existing:
            # 合并持仓（加权平均价）
            total_lots = existing['lots'] + lots
            total_cost = existing['entry_price'] * existing['lots'] + price * lots
            avg_price = round(total_cost / total_lots, 2)
            new_margin = calc_margin(contract, avg_price, total_lots)
            
            conn.execute("""
                UPDATE positions SET
                    lots=?, entry_price=?, current_price=?,
                    margin_used=?, open_date=?, note=?
                WHERE id=?
            """, (total_lots, avg_price, price, new_margin, today, note, existing['id']))
            pos_id = existing['id']
        else:
            # 新建持仓
            conn.execute("""
                INSERT INTO positions
                (contract, direction, lots, entry_price, current_price,
                 margin_used, pnl_total, open_date, note)
                VALUES (?,?,?,?,?,?,0,?,?)
            """, (contract, direction, lots, price, price, margin_needed, today, note))
            pos_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        
        # 扣现金（直接操作config表，用同一个连接）
        new_cash = cash - margin_needed
        conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('cash', ?)",
                     (str(round(new_cash, 2)),))
        
        # 交易记录
        action = 'open_long' if direction == 'long' else 'open_short'
        conn.execute("""
            INSERT INTO trades
            (trade_date, contract, direction, action, lots, price, margin,
             total_value, reason)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (today, contract, direction, action, lots, price,
              margin_needed, contract_value, note or f'{direction}开仓'))
        
        conn.commit()
        
        return {
            'success': True,
            'position_id': pos_id,
            'contract': contract,
            'direction': direction,
            'lots': lots,
            'entry_price': price,
            'margin_used': margin_needed,
            'contract_value': contract_value,
            'cash_after': get_cash(),
            'note': note,
        }
    finally:
        conn.close()


# ── 平仓 ──

def close_position(contract: str, direction: Optional[str] = None,
                   lots: Optional[int] = None, price: Optional[float] = None,
                   reason: str = '') -> Dict:
    """平仓（全部或部分）"""
    conn = _get_conn()
    try:
        # 查找持仓
        if direction:
            pos = conn.execute(
                "SELECT * FROM positions WHERE contract=? AND direction=? AND is_active=1",
                (contract, direction)
            ).fetchone()
        else:
            pos = conn.execute(
                "SELECT * FROM positions WHERE contract=? AND is_active=1 LIMIT 1",
                (contract,)
            ).fetchone()
        
        if not pos:
            return {'success': False, 'error': f'无{contract}活跃持仓'}
        
        close_lots = lots if lots and lots <= pos['lots'] else pos['lots']
        if not price:
            price = pos['current_price']
        
        spec = CONTRACT_SPECS.get(contract)
        today = date.today().strftime('%Y-%m-%d')
        
        # 计算盈亏
        multiplier = spec['multiplier']
        if pos['direction'] == 'long':
            pnl = (price - pos['entry_price']) * multiplier * close_lots
        else:  # short
            pnl = (pos['entry_price'] - price) * multiplier * close_lots
        
        # 释放保证金
        margin_release = calc_margin(contract, pos['entry_price'], close_lots)
        actual_release = calc_margin(contract, price, close_lots)
        
        # 更新持仓
        remaining = pos['lots'] - close_lots
        remaining_margin = 0
        
        if remaining <= 0:
            # 全部平仓
            conn.execute("UPDATE positions SET is_active=0, lots=0, current_price=?, pnl_total=? WHERE id=?",
                        (price, pnl, pos['id']))
        else:
            # 部分平仓
            remaining_margin = calc_margin(contract, pos['entry_price'], remaining)
            conn.execute("""
                UPDATE positions SET lots=?, current_price=?, margin_used=?,
                    pnl_total=pnl_total+? WHERE id=?
            """, (remaining, price, remaining_margin, pnl, pos['id']))
        
        # 退回现金（直接操作config表，用同一个连接）
        cash_return = actual_release + pnl
        old_cash = float(conn.execute("SELECT value FROM config WHERE key='cash'").fetchone()['value'])
        conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('cash', ?)",
                     (str(round(old_cash + cash_return, 2)),))

        # 交易记录
        action = 'close_long' if pos['direction'] == 'long' else 'close_short'
        contract_value = calc_contract_value(contract, price, close_lots)
        conn.execute("""
            INSERT INTO trades
            (trade_date, contract, direction, action, lots, price, margin, pnl,
             total_value, reason)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (today, contract, pos['direction'], action, close_lots, price,
              actual_release, round(pnl, 2), contract_value,
              reason or f'平{pos["direction"]}仓'))
        
        conn.commit()
        
        return {
            'success': True,
            'contract': contract,
            'direction': pos['direction'],
            'closed_lots': close_lots,
            'close_price': price,
            'pnl': round(pnl, 2),
            'margin_released': round(actual_release, 2),
            'cash_return': round(cash_return, 2),
            'cash_after': get_cash(),
            'remaining_lots': max(0, remaining),
            'reason': reason,
        }
    finally:
        conn.close()


# ── 每日更新 ──

def update_daily(force_update: bool = False) -> Dict:
    """联网获取最新行情并更新所有持仓"""
    import time
    conn = _get_conn()
    try:
        today = date.today().strftime('%Y-%m-%d')
        positions = conn.execute(
            "SELECT * FROM positions WHERE is_active=1"
        ).fetchall()
        
        if not positions:
            return {'status': 'no_positions', 'message': '无活跃持仓'}
        
        results = []
        total_floating_pnl = 0.0
        total_margin = 0.0
        
        for pos in positions:
            contract = pos['contract']
            quote = fetch_futures_price(contract)
            
            if not quote:
                results.append({'contract': contract, 'error': '行情获取失败'})
                continue
            
            new_price = quote['close']
            spec = CONTRACT_SPECS.get(contract)
            multiplier = spec['multiplier'] if spec else 10
            
            # 计算浮动盈亏
            if pos['direction'] == 'long':
                floating = (new_price - pos['entry_price']) * multiplier * pos['lots']
            else:
                floating = (pos['entry_price'] - new_price) * multiplier * pos['lots']
            
            # 更新持仓现价和浮动盈亏
            conn.execute("""
                UPDATE positions SET current_price=?, pnl_total=?
                WHERE id=?
            """, (new_price, round(floating, 2), pos['id']))
            
            total_floating_pnl += floating
            total_margin += pos['margin_used']
            
            results.append({
                'contract': contract,
                'direction': pos['direction'],
                'lots': pos['lots'],
                'entry': pos['entry_price'],
                'current': new_price,
                'change_pct': quote['change_pct'],
                'floating_pnl': round(floating, 2),
            })
            
            time.sleep(0.5)  # 防封
        
        # 总资产快照
        cash = get_cash()
        total_asset = cash + total_margin + total_floating_pnl
        
        # 检查今日快照是否已存在
        existing = conn.execute(
            "SELECT id FROM snapshots WHERE snapshot_date=?", (today,)
        ).fetchone()
        
        # 算收益率
        first = conn.execute("SELECT total_asset FROM snapshots ORDER BY id ASC LIMIT 1").fetchone()
        initial_asset = float(first['total_asset']) if first else DEFAULT_CAPITAL
        
        prev = conn.execute("SELECT total_asset FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
        prev_asset = float(prev['total_asset']) if prev else initial_asset
        
        daily_return = round((total_asset - prev_asset) / prev_asset * 100, 2) if prev_asset > 0 else 0
        cumulative_return = round((total_asset - initial_asset) / initial_asset * 100, 2) if initial_asset > 0 else 0
        
        if not existing:
            conn.execute("""
                INSERT INTO snapshots
                (snapshot_date, total_asset, cash, margin_used, floating_pnl,
                 daily_return, cumulative_return)
                VALUES (?,?,?,?,?,?,?)
            """, (today, round(total_asset, 2), round(cash, 2),
                  round(total_margin, 2), round(total_floating_pnl, 2),
                  daily_return, cumulative_return))
        
        conn.commit()
        
        return {
            'status': 'success',
            'date': today,
            'total_asset': round(total_asset, 2),
            'cash': round(cash, 2),
            'margin_used': round(total_margin, 2),
            'floating_pnl': round(total_floating_pnl, 2),
            'daily_return': daily_return,
            'cumulative_return': cumulative_return,
            'positions_updated': len(results),
            'details': results,
        }
    finally:
        conn.close()


# ── 查看持仓 ──

def get_positions_summary() -> Dict:
    """获取当前持仓汇总"""
    conn = _get_conn()
    try:
        positions = conn.execute(
            "SELECT * FROM positions WHERE is_active=1"
        ).fetchall()
        
        detail = []
        total_margin = 0
        total_pnl = 0
        
        for pos in positions:
            contract = pos['contract']
            spec = CONTRACT_SPECS.get(contract, {})
            multiplier = spec.get('multiplier', 10)
            
            if pos['direction'] == 'long':
                pnl_pct = round((pos['current_price'] - pos['entry_price']) / pos['entry_price'] * 100, 2)
            else:
                pnl_pct = round((pos['entry_price'] - pos['current_price']) / pos['entry_price'] * 100, 2)
            
            contract_value = pos['current_price'] * multiplier * pos['lots']
            
            detail.append({
                'contract': contract,
                'name': spec.get('name', contract),
                'direction': '多' if pos['direction'] == 'long' else '空',
                'lots': pos['lots'],
                'entry_price': pos['entry_price'],
                'current_price': pos['current_price'],
                'margin_used': pos['margin_used'],
                'contract_value': contract_value,
                'floating_pnl': pos['pnl_total'],
                'return_pct': pnl_pct,
                'open_date': pos['open_date'],
            })
            total_margin += pos['margin_used']
            total_pnl += pos['pnl_total']
        
        cash = get_cash()
        total_asset = cash + total_margin + total_pnl
        
        # 最新快照
        snapshot = conn.execute(
            "SELECT * FROM snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        
        return {
            'status': 'active' if detail else 'empty',
            'time': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'total_asset': round(total_asset, 2),
            'cash': round(cash, 2),
            'margin_used': round(total_margin, 2),
            'floating_pnl': round(total_pnl, 2),
            'positions_count': len(detail),
            'positions': detail,
            'latest_snapshot': {
                'daily_return': snapshot['daily_return'] if snapshot else 0,
                'cumulative_return': snapshot['cumulative_return'] if snapshot else 0,
            } if snapshot else None,
        }
    finally:
        conn.close()


def get_trade_history(limit: int = 20) -> List[Dict]:
    """获取交易记录"""
    conn = _get_conn()
    try:
        trades = conn.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(t) for t in trades]
    finally:
        conn.close()


# ── 初始化 ──

def initialize(capital: float = DEFAULT_CAPITAL) -> Dict:
    """初始化模拟盘"""
    conn = _get_conn()
    try:
        # 检查是否已经初始化
        existing = conn.execute("SELECT value FROM config WHERE key='initialized'").fetchone()
        if existing:
            return {'status': 'already_initialized', 'capital': get_cash()}
        
        set_config('initialized', datetime.now().isoformat())
        set_config('initial_capital', str(capital))
        set_cash(capital)
        
        today = date.today().strftime('%Y-%m-%d')
        conn.execute("""
            INSERT INTO snapshots
            (snapshot_date, total_asset, cash, margin_used, floating_pnl,
             daily_return, cumulative_return)
            VALUES (?,?,?,?,?,0,0)
        """, (today, capital, capital, 0, 0))
        conn.commit()
        
        return {
            'status': 'initialized',
            'capital': capital,
            'date': today,
            'contracts_available': list(CONTRACT_SPECS.keys()),
            'xueqiu_portfolio': 'ZH3650824',
        }
    finally:
        conn.close()


# ── CLI ──

if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='📊 期货模拟盘引擎')
    parser.add_argument('--init', action='store_true', help='初始化模拟盘')
    parser.add_argument('--capital', type=float, default=DEFAULT_CAPITAL, help='初始资金(默认10万)')
    parser.add_argument('--open', action='store_true', help='开仓')
    parser.add_argument('--close', action='store_true', help='平仓')
    parser.add_argument('--contract', type=str, default='MA', help='合约代码(默认MA)')
    parser.add_argument('--direction', type=str, choices=['long', 'short'], help='方向: long(多)/short(空)')
    parser.add_argument('--lots', type=int, default=1, help='手数(默认1)')
    parser.add_argument('--price', type=float, help='价格')
    parser.add_argument('--daily', action='store_true', help='每日更新')
    parser.add_argument('--status', action='store_true', help='查看持仓')
    parser.add_argument('--history', action='store_true', help='交易记录')
    parser.add_argument('--note', type=str, default='', help='备注')
    parser.add_argument('--reason', type=str, default='', help='平仓原因')
    
    args = parser.parse_args()
    
    if args.init:
        r = initialize(args.capital)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        print(f"\n🌐 雪球组合: ZH3650824\n")
        print("可用合约:", ", ".join(f"{k}({v['name']})" for k, v in CONTRACT_SPECS.items()))
    
    elif args.open:
        if not args.price:
            # 自动获取最新价
            q = fetch_futures_price(args.contract)
            if q:
                args.price = q['close']
                print(f"📡 获取{args.contract}最新价: {args.price}")
            else:
                print("❌ 无法获取行情，请手动指定 --price")
                sys.exit(1)
        
        r = open_position(args.contract, args.direction, args.lots, args.price, args.note)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        
        if r.get('success'):
            print(f"\n✅ {args.contract}{'多' if args.direction == 'long' else '空'}仓 {args.lots}手 @{args.price}")
    
    elif args.close:
        if not args.price:
            q = fetch_futures_price(args.contract)
            if q:
                args.price = q['close']
                print(f"📡 获取{args.contract}最新价: {args.price}")
            else:
                print("❌ 无法获取行情，请手动指定 --price")
                sys.exit(1)
        
        r = close_position(args.contract, args.direction, args.lots, args.price, args.reason)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        
        if r.get('success'):
            emoji = '🟢' if r['pnl'] >= 0 else '🔴'
            print(f"\n{emoji} 平仓 {args.contract} {r['closed_lots']}手 @{r['close_price']}  盈亏: {r['pnl']:+.0f}")
    
    elif args.daily:
        r = update_daily()
        print(json.dumps(r, ensure_ascii=False, indent=2))
    
    elif args.status:
        r = get_positions_summary()
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    
    elif args.history:
        trades = get_trade_history(20)
        if not trades:
            print("📭 暂无交易记录")
        else:
            print(f"📋 最近 {len(trades)} 笔交易:")
            print(f"{'日期':12s} {'合约':6s} {'操作':12s} {'手数':>4s} {'价格':>8s} {'盈亏':>8s} {'原因'}")
            print("-" * 70)
            for t in trades:
                action_map = {
                    'open_long': '开多', 'open_short': '开空',
                    'close_long': '平多', 'close_short': '平空',
                    'add_long': '加多', 'add_short': '加空',
                }
                a = action_map.get(t['action'], t['action'])
                pnl_str = f"{t['pnl']:+.0f}" if t['pnl'] else ''
                print(f"{t['trade_date']:12s} {t['contract']:6s} {a:12s} {t['lots']:4d} {t['price']:>8.0f} {pnl_str:>8s} {t.get('reason','')[:20]}")
    
    else:
        parser.print_help()
