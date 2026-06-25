#!/usr/bin/env python3
"""
 · 模拟盘引擎

模拟真实投资组合操作，追踪推荐股票的实际收益表现。

功能：
1. 初始化：每周Top10等额买入
2. 每日更新：持仓市值、盈亏、净值曲线
3. 止损检查：-8%自动止损信号
4. 调仓：每周根据新推荐调整
5. 导出：生成东方财富可导入的操作指令

数据表：
  simulator_positions    — 当前持仓
  simulator_snapshots    — 每日净值快照
  simulator_trades       — 交易记录
  simulator_weekly_picks — 每周推荐

用法：
  # 初始化模拟盘（每周一盘前）
  python simulator.py --init --top10 top10_list.json
  
  # 每日更新（盘后）
  python simulator.py --daily --data /tmp/stock_cron_data.json
  
  # 查看持仓
  python simulator.py --status
  
  # 导出操作指令
  python simulator.py --export
  
  # 止损检查
  python simulator.py --check-stop-loss
"""
import sys
import os
import json
import sqlite3
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple

BASE = '/home/liudawei/github/daily_tracker_analytics'
sys.path.insert(0, f'{BASE}/multi_agent')

DB_PATH = os.path.join(BASE, 'multi_agent', 'data', 'simulator.db')
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# 默认模拟初始资金
DEFAULT_CAPITAL = 100000.0  # 10万
STOP_LOSS_THRESHOLD = -0.08  # -8%止损
TAKE_PROFIT_THRESHOLD = 0.20  # +20%止盈


def _get_conn() -> sqlite3.Connection:
    """获取 DB 连接，建表"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS simulator_config (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        
        CREATE TABLE IF NOT EXISTS simulator_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL UNIQUE,
            name TEXT,
            shares REAL NOT NULL DEFAULT 0,
            entry_price REAL NOT NULL DEFAULT 0,
            current_price REAL NOT NULL DEFAULT 0,
            cost_basis REAL NOT NULL DEFAULT 0,
            entry_date TEXT NOT NULL,
            sector TEXT,
            weekly_rank INTEGER,
            is_active INTEGER DEFAULT 1
        );
        
        CREATE TABLE IF NOT EXISTS simulator_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT NOT NULL,
            total_value REAL NOT NULL,
            cash REAL NOT NULL,
            stocks_value REAL NOT NULL,
            daily_return REAL,
            cumulative_return REAL,
            benchmark_close REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_snap_date ON simulator_snapshots(snapshot_date);
        
        CREATE TABLE IF NOT EXISTS simulator_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            name TEXT,
            action TEXT NOT NULL CHECK(action IN ('buy','sell')),
            shares REAL NOT NULL,
            price REAL NOT NULL,
            amount REAL NOT NULL,
            reason TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_trade_date ON simulator_trades(trade_date);
        
        CREATE TABLE IF NOT EXISTS simulator_weekly_picks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_start TEXT NOT NULL,
            ticker TEXT NOT NULL,
            name TEXT,
            rank INTEGER,
            composite_score REAL,
            entry_price REAL,
            exit_price REAL,
            return_pct REAL,
            is_selected INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_weekly ON simulator_weekly_picks(week_start);
    """)
    return conn


# ============================================================
# 配置读写
# ============================================================

def get_config(key: str, default=None, conn: sqlite3.Connection = None) -> str:
    own_conn = False
    if conn is None:
        conn = _get_conn()
        own_conn = True
    try:
        row = conn.execute("SELECT value FROM simulator_config WHERE key=?", (key,)).fetchone()
        if row:
            return row['value']
        return default
    finally:
        if own_conn:
            conn.close()


def set_config(key: str, value: str, conn: sqlite3.Connection = None):
    own_conn = False
    if conn is None:
        conn = _get_conn()
        own_conn = True
    try:
        conn.execute("INSERT OR REPLACE INTO simulator_config (key, value) VALUES (?, ?)", (key, value))
        if own_conn:
            conn.commit()
    finally:
        if own_conn:
            conn.close()


def get_cash(conn: sqlite3.Connection = None) -> float:
    """获取现金余额"""
    own_conn = False
    if conn is None:
        conn = _get_conn()
        own_conn = True
    try:
        raw = conn.execute("SELECT value FROM simulator_config WHERE key='cash'").fetchone()
        if raw:
            return float(raw['value'])
        return DEFAULT_CAPITAL
    finally:
        if own_conn:
            conn.close()


def set_cash(val: float, conn: sqlite3.Connection = None):
    """设置现金余额"""
    own_conn = False
    if conn is None:
        conn = _get_conn()
        own_conn = True
    try:
        conn.execute("INSERT OR REPLACE INTO simulator_config (key, value) VALUES ('cash', ?)",
                     (str(round(val, 2)),))
        if own_conn:
            conn.commit()
    finally:
        if own_conn:
            conn.close()


# ============================================================
# 每周初始化
# ============================================================

def init_weekly_portfolio(top10_list: List[Dict], week_start: str = None,
                          capital: float = DEFAULT_CAPITAL):
    """
    初始化/更新模拟盘
    
    Args:
        top10_list: [{ticker, name, price, composite_score, sector}]
        week_start: 周开始日期 YYYY-MM-DD
        capital: 可用资金
    
    注：A股最小买入100股，分配时会自动跳过买不起100股的高价股，
    剩余资金分配给其他可买的标的。
    """
    conn = _get_conn()
    try:
        if week_start is None:
            today = date.today()
            week_start = (today - timedelta(days=today.weekday())).strftime('%Y-%m-%d')
        
        # 清空旧持仓（标记非活跃）
        conn.execute("UPDATE simulator_positions SET is_active=0 WHERE is_active=1")
        
        # 计算每只可买的股数
        # 贪婪算法：从最低价开始，逐个加入清单，确保每只都能买至少100股
        sorted_top10 = sorted(top10_list, key=lambda s: float(s.get('price', s.get('current_price', 99999))))
        
        # 预留10%现金
        budget = capital * 0.9
        
        # 贪婪：从最便宜的股票开始逐个加入affordable
        affordable = []
        for s in sorted_top10:
            test_set = affordable + [s]
            per_stock = budget / len(test_set)
            # 检查当前test_set里的每一只是否能买至少100股
            all_can_buy = all(
                float(x.get('price', x.get('current_price', 0))) * 100 <= per_stock
                for x in test_set
            )
            if all_can_buy:
                affordable.append(s)
            else:
                break
        
        if len(affordable) < 1:
            # 极端情况：最便宜的也买不起，只买1-2只
            affordable = sorted_top10[:min(2, len(sorted_top10))]
        
        per_stock = budget / len(affordable)
        print(f"  可买 {len(affordable)} 只（等额{per_stock:.0f}元/只，其余因价格高或分散会<100股）")
        
        total_spent = 0
        
        # 保存每周推荐
        for i, s in enumerate(top10_list):
            ticker = s.get('ticker', s.get('code', ''))
            name = s.get('name', '')
            price = float(s.get('price', s.get('current_price', 0)))
            score = s.get('composite_score', 0)
            sector = s.get('sector', s.get('theme', ''))
            
            if price <= 0:
                continue
            
            # 计算可买数量（100股取整）
            if s in affordable:
                budget_for_this = per_stock
                shares = int(budget_for_this / price / 100) * 100
            else:
                shares = 0
            
            actual_cost = shares * price
            total_spent += actual_cost
            
            if shares > 0:
                # upsert 持仓
                existing = conn.execute(
                    "SELECT id FROM simulator_positions WHERE ticker=?",
                    (ticker,)).fetchone()
                
                if existing:
                    conn.execute("""
                        UPDATE simulator_positions SET
                            name=?, shares=?, entry_price=?, current_price=?,
                            cost_basis=?, entry_date=?, sector=?,
                            weekly_rank=?, is_active=1
                        WHERE ticker=?
                    """, (name, shares, price, price, actual_cost,
                          week_start, sector, i + 1, ticker))
                else:
                    conn.execute("""
                        INSERT INTO simulator_positions
                        (ticker, name, shares, entry_price, current_price,
                         cost_basis, entry_date, sector, weekly_rank, is_active)
                        VALUES (?,?,?,?,?,?,?,?,?,1)
                    """, (ticker, name, shares, price, price,
                          actual_cost, week_start, sector, i + 1))
                
                # 存 weekly_picks
                conn.execute("""
                    INSERT INTO simulator_weekly_picks
                    (week_start, ticker, name, rank, composite_score, entry_price, is_selected)
                    VALUES (?,?,?,?,?,?,1)
                """, (week_start, ticker, name, i + 1, score, price))
                
                # 交易记录
                conn.execute("""
                    INSERT INTO simulator_trades
                    (trade_date, ticker, name, action, shares, price, amount, reason)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (week_start, ticker, name, 'buy', shares, price,
                      actual_cost, f'周度推荐 #{i+1} 评分{score}'))
                print(f"  ✅ 买入 {name}({ticker}) {shares}股 @{price} = {actual_cost:.0f}元")
            else:
                print(f"  ⏭️ 跳过 {name}({ticker}) @{price:.2f} 买不起100股（需{price*100:.0f}元/预算{per_stock:.0f}元）")
        
        cash = capital - total_spent
        print(f"  💰 买入总额: {total_spent:.0f} | 现金: {cash:.0f}")
        set_cash(cash, conn)
        
        # 初始快照
        stocks_value = sum(
            r['cost_basis'] for r in conn.execute(
                "SELECT cost_basis FROM simulator_positions WHERE is_active=1").fetchall()
        )
        total = cash + stocks_value
        conn.execute("""
            INSERT INTO simulator_snapshots
            (snapshot_date, total_value, cash, stocks_value, daily_return, cumulative_return)
            VALUES (?,?,?,?,0,?)
        """, (week_start, total, cash, stocks_value,
              round((total - capital) / capital * 100, 2)))
        
        conn.commit()
        
        # 统计实际已买入数量
        active_count = conn.execute(
            "SELECT COUNT(*) as c FROM simulator_positions WHERE is_active=1 AND shares > 0"
        ).fetchone()['c']
        
        print(f"✅ 模拟盘初始化完成")
        print(f"   初始资金: {capital:.2f}")
        print(f"   持仓: {active_count} 只")
        print(f"   现金: {cash:.2f}")
        print(f"   总资产: {total:.2f}")
        
        return {
            'capital': capital,
            'positions': active_count,
            'cash': cash,
            'total': total,
        }
    finally:
        conn.close()


# ============================================================
# 每日更新
# ============================================================

def update_daily(market_data: List[Dict] = None,
                 current_prices: Dict[str, float] = None) -> Dict:
    """
    每日更新持仓市值和净值
    
    Args:
        market_data: 从 predictor 采集的市场数据
        current_prices: {ticker: price} 直接的价格映射
    
    Returns:
        dict: 更新统计
    """
    # 构建价格映射
    prices = {}
    if market_data:
        for d in market_data:
            if 'error' not in d and 'current_price' in d:
                prices[d.get('ticker', '')] = float(d['current_price'])
    if current_prices:
        prices.update(current_prices)
    
    conn = _get_conn()
    try:
        today = date.today().strftime('%Y-%m-%d')
        positions = conn.execute(
            "SELECT * FROM simulator_positions WHERE is_active=1"
        ).fetchall()
        
        if not positions:
            return {'error': '无活跃持仓'}
        
        # 更新持仓现价和市值
        total_stocks_value = 0
        update_count = 0
        for pos in positions:
            ticker = pos['ticker']
            price = prices.get(ticker)
            if price and price > 0:
                conn.execute(
                    "UPDATE simulator_positions SET current_price=? WHERE id=?",
                    (price, pos['id']))
                update_count += 1
            # 用 current_price 算市值（可能是更新后的或旧的）
            current = price or pos['current_price']
            total_stocks_value += pos['shares'] * current
        
        cash = get_cash(conn)
        total_value = cash + total_stocks_value
        
        # 获取初始资本
        initial_capital = float(get_config('initial_capital', str(DEFAULT_CAPITAL)))
        if initial_capital == DEFAULT_CAPITAL:
            # 尝试从第一笔快照推算
            first = conn.execute(
                "SELECT total_value FROM simulator_snapshots ORDER BY id ASC LIMIT 1"
            ).fetchone()
            if first:
                initial_capital = first['total_value']
                set_config('initial_capital', str(initial_capital), conn)
        
        # 计算昨日净值（无则用初始）
        yesterday = conn.execute(
            "SELECT total_value FROM simulator_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        prev_value = yesterday['total_value'] if yesterday else initial_capital
        
        daily_return = round((total_value - prev_value) / prev_value * 100, 2) if prev_value > 0 else 0
        cumulative_return = round((total_value - initial_capital) / initial_capital * 100, 2) if initial_capital > 0 else 0
        
        # 检查是否已录入今日快照
        existing = conn.execute(
            "SELECT id FROM simulator_snapshots WHERE snapshot_date=?",
            (today,)).fetchone()
        
        if not existing:
            conn.execute("""
                INSERT INTO simulator_snapshots
                (snapshot_date, total_value, cash, stocks_value,
                 daily_return, cumulative_return)
                VALUES (?,?,?,?,?,?)
            """, (today, total_value, cash, total_stocks_value,
                  daily_return, cumulative_return))
        
        conn.commit()
        
        # 止损检查
        stop_loss_hits = [] if update_count > 0 else None
        
        return {
            'update_count': update_count,
            'total_stocks_value': round(total_stocks_value, 2),
            'cash': round(cash, 2),
            'total_value': round(total_value, 2),
            'daily_return': daily_return,
            'cumulative_return': cumulative_return,
            'initial_capital': round(initial_capital, 2),
        }
    finally:
        conn.close()


# ============================================================
# 止损检查
# ============================================================

def check_stop_loss(threshold: float = STOP_LOSS_THRESHOLD) -> List[Dict]:
    """检查需要止损的持仓"""
    conn = _get_conn()
    try:
        positions = conn.execute(
            "SELECT * FROM simulator_positions WHERE is_active=1"
        ).fetchall()
        
        alerts = []
        for pos in positions:
            entry = pos['entry_price']
            current = pos['current_price']
            if entry > 0:
                loss_pct = (current - entry) / entry
                if loss_pct <= threshold:
                    alerts.append({
                        'ticker': pos['ticker'],
                        'name': pos['name'],
                        'loss_pct': round(loss_pct * 100, 2),
                        'entry_price': entry,
                        'current_price': current,
                        'shares': pos['shares'],
                        'loss_amount': round(pos['shares'] * (current - entry), 2),
                    })
        
        return alerts
    finally:
        conn.close()


def check_take_profit(threshold: float = TAKE_PROFIT_THRESHOLD) -> List[Dict]:
    """检查达到止盈的持仓"""
    conn = _get_conn()
    try:
        positions = conn.execute(
            "SELECT * FROM simulator_positions WHERE is_active=1"
        ).fetchall()
        
        alerts = []
        for pos in positions:
            entry = pos['entry_price']
            current = pos['current_price']
            if entry > 0:
                profit_pct = (current - entry) / entry
                if profit_pct >= threshold:
                    alerts.append({
                        'ticker': pos['ticker'],
                        'name': pos['name'],
                        'profit_pct': round(profit_pct * 100, 2),
                        'entry_price': entry,
                        'current_price': current,
                        'shares': pos['shares'],
                        'profit_amount': round(pos['shares'] * (current - entry), 2),
                    })
        return alerts
    finally:
        conn.close()


# ============================================================
# 调仓
# ============================================================

def rebalance(new_top10: List[Dict], week_start: str = None) -> Dict:
    """
    每周调仓：卖出不在推荐中的持仓，买入新推荐
    
    Args:
        new_top10: [{ticker, name, price, composite_score}]
        week_start: 周标签
    """
    if week_start is None:
        today = date.today()
        week_start = (today - timedelta(days=today.weekday())).strftime('%Y-%m-%d')
    
    conn = _get_conn()
    try:
        current_positions = conn.execute(
            "SELECT * FROM simulator_positions WHERE is_active=1"
        ).fetchall()
        
        new_tickers = {s.get('ticker', s.get('code', '')) for s in new_top10}
        old_tickers = {p['ticker'] for p in current_positions}
        
        results = {'sell': [], 'buy': [], 'hold': [], 'cash': get_cash(conn)}
        
        # 卖出不在新推荐中的
        to_sell = [p for p in current_positions if p['ticker'] not in new_tickers]
        for pos in to_sell:
            proceeds = pos['shares'] * pos['current_price']
            conn.execute("""
                INSERT INTO simulator_trades
                (trade_date, ticker, name, action, shares, price, amount, reason)
                VALUES (?,?,?,?,?,?,?,?)
            """, (week_start, pos['ticker'], pos['name'], 'sell',
                  pos['shares'], pos['current_price'], proceeds,
                  '调仓剔除'))
            conn.execute("UPDATE simulator_positions SET is_active=0, current_price=? WHERE id=?",
                        (pos['current_price'], pos['id']))
            results['sell'].append({
                'ticker': pos['ticker'],
                'name': pos['name'],
                'proceeds': round(proceeds, 2),
            })
            new_cash = get_cash(conn) + proceeds
            set_cash(new_cash, conn)
        
        cash = get_cash(conn)
        
        # 买入新推荐中不持有的
        hold_count = len(new_tickers - {p['ticker'] for p in to_sell})
        to_buy = [s for s in new_top10
                  if s.get('ticker', s.get('code', '')) not in old_tickers
                  and s.get('ticker', s.get('code', '')) in new_tickers]
        
        # 加已有的持仓
        hold_tickers = set()
        for p in current_positions:
            if p['ticker'] not in {s['ticker'] for s in to_sell}:
                hold_tickers.add(p['ticker'])
        
        buy_count = len(to_buy) + len(hold_tickers)
        if buy_count > 0:
            per_stock = cash * 0.9 / buy_count
            set_cash(cash * 0.1, conn)
        else:
            per_stock = 0
        
        for s in to_buy:
            ticker = s.get('ticker', s.get('code', ''))
            name = s.get('name', '')
            price = float(s.get('price', s.get('current_price', 0)))
            score = s.get('composite_score', 0)
            sector = s.get('sector', '')
            
            if price <= 0:
                continue
            
            shares = int((per_stock / price) / 100) * 100
            actual_cost = shares * price
            
            conn.execute("""
                INSERT INTO simulator_positions
                (ticker, name, shares, entry_price, current_price,
                 cost_basis, entry_date, sector, weekly_rank, is_active)
                VALUES (?,?,?,?,?,?,?,?,?,1)
            """, (ticker, name, shares, price, price,
                  actual_cost, week_start, sector, 1))
            
            conn.execute("""
                INSERT INTO simulator_trades
                (trade_date, ticker, name, action, shares, price, amount, reason)
                VALUES (?,?,?,?,?,?,?,?)
            """, (week_start, ticker, name, 'buy', shares, price,
                  actual_cost, f'调仓新增 评分{score}'))
            
            cash_left = get_cash(conn) - actual_cost
            set_cash(cash_left, conn)
            
            results['buy'].append({
                'ticker': ticker,
                'name': name,
                'cost': round(actual_cost, 2),
            })
        
        results['hold'] = [{'ticker': p['ticker'], 'name': p['name']}
                          for p in current_positions if p['ticker'] not in to_sell]
        results['cash'] = get_cash(conn)
        
        conn.commit()
        
        print(f"✅ 调仓完成:")
        print(f"   卖出: {len(results['sell'])} 只")
        print(f"   买入: {len(results['buy'])} 只")
        print(f"   持有: {len(results['hold'])} 只")
        print(f"   现金: {results['cash']:.2f}")
        
        return results
    finally:
        conn.close()


# ============================================================
# 报告
# ============================================================

def get_portfolio_summary() -> Dict:
    """生成当前持仓汇总"""
    conn = _get_conn()
    try:
        positions = conn.execute(
            "SELECT * FROM simulator_positions WHERE is_active=1 "
            "ORDER BY weekly_rank"
        ).fetchall()
        
        if not positions:
            return {'error': '无活跃持仓'}
        
        total_cost = 0
        total_market = 0
        positions_detail = []
        
        for pos in positions:
            cost = pos['cost_basis']
            market = pos['shares'] * pos['current_price']
            total_cost += cost
            total_market += market
            pnl = market - cost
            pnl_pct = round((pos['current_price'] - pos['entry_price']) / pos['entry_price'] * 100, 2) if pos['entry_price'] > 0 else 0
            
            positions_detail.append({
                'ticker': pos['ticker'],
                'name': pos['name'],
                'shares': pos['shares'],
                'entry_price': pos['entry_price'],
                'current_price': pos['current_price'],
                'cost': round(cost, 2),
                'market_value': round(market, 2),
                'pnl': round(pnl, 2),
                'pnl_pct': pnl_pct,
                'entry_date': pos['entry_date'],
                'sector': pos['sector'],
                'weight': 0,  # 后面计算
            })
        
        cash = get_cash(conn)
        total = total_market + cash
        
        # 计算权重
        for p in positions_detail:
            p['weight'] = round(p['market_value'] / total * 100, 1) if total > 0 else 0
        
        # 最新快照
        snapshot = conn.execute(
            "SELECT * FROM simulator_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        
        return {
            'date': date.today().strftime('%Y-%m-%d'),
            'cash': round(cash, 2),
            'stocks_value': round(total_market, 2),
            'total_value': round(total, 2),
            'positions': len(positions_detail),
            'positions_detail': positions_detail,
            'weighted_pnl': round((total - DEFAULT_CAPITAL) / DEFAULT_CAPITAL * 100, 2),
            'latest_snapshot': {
                'daily_return': snapshot['daily_return'] if snapshot else 0,
                'cumulative_return': snapshot['cumulative_return'] if snapshot else 0,
            } if snapshot else None,
        }
    finally:
        conn.close()


def get_performance_curve() -> List[Dict]:
    """获取收益率曲线数据（用于Pages图表）"""
    conn = _get_conn()
    try:
        snapshots = conn.execute(
            "SELECT * FROM simulator_snapshots ORDER BY snapshot_date"
        ).fetchall()
        return [dict(s) for s in snapshots]
    finally:
        conn.close()


def get_trade_history(limit: int = 20) -> List[Dict]:
    """获取交易历史"""
    conn = _get_conn()
    try:
        trades = conn.execute(
            "SELECT * FROM simulator_trades ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall()
        return [dict(t) for t in trades]
    finally:
        conn.close()


# ============================================================
# 导出东方财富操作指令
# ============================================================

def export_for_eastmoney() -> Dict:
    """
    导出东方财富模拟盘操作指令
    
    东方财富模拟炒股支持：
    - 买入：代码、价格、数量
    - 卖出：代码、价格、数量
    
    输出 JSON 格式操作指令，可通过浏览器自动化执行
    """
    conn = _get_conn()
    try:
        positions = conn.execute(
            "SELECT * FROM simulator_positions WHERE is_active=1"
        ).fetchall()
        
        cash = get_cash(conn)
        
        # 需要卖出的（止损/止盈）
        stop_loss = check_stop_loss()
        take_profit = check_take_profit()
        
        sell_orders = []
        for s in stop_loss:
            sell_orders.append({
                'ticker': s['ticker'],
                'name': s['name'],
                'action': 'sell',
                'price': s['current_price'],
                'shares': s['shares'],
                'reason': f'止损 {s["loss_pct"]}%',
            })
        for t in take_profit:
            sell_orders.append({
                'ticker': t['ticker'],
                'name': t['name'],
                'action': 'sell',
                'price': t['current_price'],
                'shares': t['shares'],
                'reason': f'止盈 {t["profit_pct"]}%',
            })
        
        # 买入指令（如果有卖出回笼资金）
        buy_amount = sum(s['price'] * s['shares'] for s in sell_orders)
        available = cash + buy_amount * 0.99  # 扣除摩擦
        
        return {
            'export_time': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'cash': round(cash, 2),
            'sell_orders': sell_orders,
            'buy_orders': [],
            'total_available': round(available, 2),
            'summary': f"卖出{len(sell_orders)}笔, 可用{available:.0f}元",
        }
    finally:
        conn.close()


# ============================================================
# 微信推送格式化
# ============================================================

def format_wechat_summary(summary: Dict = None) -> str:
    """格式化持仓日报"""
    if summary is None:
        summary = get_portfolio_summary()
    
    if 'error' in summary:
        return f"⚠️ 模拟盘: {summary['error']}"
    
    lines = []
    lines.append(f"💰 **模拟盘日报**")
    lines.append(f"📅 {summary['date']}")
    lines.append("")
    
    sd = summary.get('latest_snapshot', {}) or {}
    cum_ret = sd.get('cumulative_return', 0)
    daily_ret = sd.get('daily_return', 0)
    
    # 总体情况
    emoji = '🟢' if cum_ret >= 0 else '🔴'
    lines.append(f"{emoji} **总资产: {summary['total_value']:.2f}** ({cum_ret:+.2f}%)")
    lines.append(f"> 💵 现金: {summary['cash']:.2f} | 📊 股票: {summary['stocks_value']:.2f}")
    lines.append(f"> 📈 今日: {daily_ret:+.2f}% | 累计: {cum_ret:+.2f}%")
    lines.append(f"> 持仓: {summary['positions']} 只")
    lines.append("")
    
    # 持仓明细
    lines.append("**📋 持仓明细：**")
    for p in summary['positions_detail'][:10]:
        pnl_e = '🟢' if p['pnl_pct'] >= 0 else '🔴'
        lines.append(
            f"{pnl_e} **{p['name']}**({p['ticker']}) "
            f"@{p['current_price']:.2f} "
            f"{p['pnl_pct']:+.2f}% "
            f"权重{p['weight']:.0f}%"
        )
        lines.append(f"> 仓位{p['shares']:.0f}股 成本{p['entry_price']:.2f} 市值{p['market_value']:.0f}")
    
    # 止损预警
    sl_alerts = check_stop_loss()
    if sl_alerts:
        lines.append("")
        lines.append("⚠️ **止损预警：**")
        for a in sl_alerts:
            lines.append(f"> 🔴 {a['name']}({a['ticker']}) 亏损{a['loss_pct']:.1f}% "
                        f"@{a['current_price']:.2f} 建议卖出")
    
    tp_alerts = check_take_profit()
    if tp_alerts:
        if not sl_alerts:
            lines.append("")
        lines.append("🎯 **止盈提醒：**")
        for a in tp_alerts:
            lines.append(f"> 🟢 {a['name']}({a['ticker']}) 盈利{a['profit_pct']:.1f}% "
                        f"@{a['current_price']:.2f}")
    
    lines.append("")
    lines.append("*初始资金10万 · 研究辅助非投资建议*")
    
    return "\n".join(lines)


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description=' · 模拟盘引擎')
    parser.add_argument('--init', action='store_true', help='初始化模拟盘')
    parser.add_argument('--top10', type=str, help='Top10推荐JSON路径（配合--init）')
    parser.add_argument('--capital', type=float, default=DEFAULT_CAPITAL, help='初始资金')
    parser.add_argument('--daily', action='store_true', help='每日更新')
    parser.add_argument('--data', type=str, default='/tmp/stock_cron_data.json', help='行情数据JSON')
    parser.add_argument('--status', action='store_true', help='查看持仓')
    parser.add_argument('--check-stop-loss', action='store_true', help='止损检查')
    parser.add_argument('--export', action='store_true', help='导出东方财富操作指令')
    parser.add_argument('--rebalance', type=str, help='调仓（新Top10 JSON路径）')
    parser.add_argument('--wechat', action='store_true', help='生成微信推送文本')
    
    args = parser.parse_args()
    
    if args.init:
        if not args.top10:
            print("❌ --init 需要 --top10 <JSON_PATH>")
            sys.exit(1)
        with open(args.top10, 'r') as f:
            top10 = json.load(f)
        result = init_weekly_portfolio(top10, capital=args.capital)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    
    elif args.daily:
        if os.path.exists(args.data):
            with open(args.data, 'r') as f:
                data = json.load(f)
        else:
            data = None
        result = update_daily(data)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    
    elif args.status:
        summary = get_portfolio_summary()
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    
    elif args.check_stop_loss:
        alerts = check_stop_loss()
        if alerts:
            print(f"⚠️ 发现 {len(alerts)} 个止损信号:")
            for a in alerts:
                print(f"  🔴 {a['name']}({a['ticker']}) 亏损{a['loss_pct']:.1f}%")
        else:
            print("✅ 无止损信号")
    
    elif args.export:
        result = export_for_eastmoney()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    
    elif args.rebalance:
        with open(args.rebalance, 'r') as f:
            new_top10 = json.load(f)
        result = rebalance(new_top10)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    
    elif args.wechat:
        print(format_wechat_summary())
    
    else:
        parser.print_help()
