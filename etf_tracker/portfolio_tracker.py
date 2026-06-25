#!/usr/bin/env python3
"""
投资组合绩效分析模块
功能：
1. 虚拟组合管理（初始资金 + 按信号建仓/清仓）
2. 每日 NAV 记录与收益率计算
3. 基于 quantstats 的 HTML 绩效报告
4. 交易历史管理

用法：
    tracker = PortfolioTracker(initial_cash=100000)
    tracker.update(etf_results, report_date)  # 按信号更新持仓
    html_path = tracker.generate_performance_report()  # 生成HTML报告
"""

import os
import json
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import warnings
warnings.filterwarnings('ignore')

DB_PATH = os.path.expanduser("~/etf_tracker/data/etf_tracker.db")


class PortfolioTracker:
    """虚拟组合管理器"""

    # 每只 ETF 的仓位配置权重（按评分分配）
    MAX_POSITIONS = 5        # 最多持有 ETF 数量
    CASH_RESERVE = 0.20      # 最低现金比例

    # Kelly 公式参数
    KELLY_FRACTION = 0.25    # 半凯利系数（风险控制，默认1/4凯利）
    MAX_SINGLE_POSITION = 0.35  # 单只标的最大仓位上限

    def __init__(self, initial_cash: float = 100000.0, db_path: str = ""):
        self.db_path = db_path or DB_PATH
        self.initial_cash = initial_cash
        self._init_database()

    def _calc_kelly_params(self, result: Dict) -> tuple:
        """从分析结果计算Kelly参数"""
        win_rate = 0.55  # 默认
        avg_win = 1.0    # 默认盈亏比
        avg_loss = 1.0

        backtest = result.get('backtest', [])
        # 用60天回测的胜率和夏普估算
        bt_60 = next((b for b in backtest if b.get('period_days') == 60), {})
        if bt_60:
            wr = bt_60.get('win_rate', 0)
            sharpe = bt_60.get('sharpe', 0)
            ret = bt_60.get('total_return', 0)
            if wr > 0:
                win_rate = wr / 100  # 转小数
            # 从收益和胜率估算盈亏比
            if win_rate > 0 and win_rate < 1:
                avg_win = max(0.1, abs(ret / 100) / win_rate) if ret > 0 else 0.1
                avg_loss = max(0.1, abs(ret / 100) / (1 - win_rate)) if ret < 0 else 0.1
                if avg_loss == 0: avg_loss = 0.1
        return win_rate, avg_win, avg_loss

    def _kelly_formula(self, win_rate: float, avg_win: float, avg_loss: float) -> float:
        """Kelly公式计算最优仓位比例"""
        if avg_loss <= 0 or win_rate <= 0 or win_rate >= 1:
            return self.CASH_RESERVE
        # f* = p/a - q/b (其中 a=avg_loss, b=avg_win, p=win_rate, q=1-p)
        # 标准Kelly: f* = (p/a - q/b) — 适用于非对称盈亏
        # 简化Kelly: f* = p - q/(avg_win/avg_loss)
        r = avg_win / avg_loss if avg_loss > 0 else 1
        kelly = win_rate - (1 - win_rate) / r if r > 0 else 0
        # 半Kelly/四分之一Kelly控制风险
        kelly = max(0, kelly * self.KELLY_FRACTION)
        # 保留现金储备
        kelly = min(kelly, 1 - self.CASH_RESERVE)
        return kelly

    def _init_database(self):
        """初始化组合相关表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # 每日组合净值
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS portfolio_nav (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE,
                total_value REAL,
                cash REAL,
                invested REAL,
                daily_return REAL,
                cumulative_return REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 交易记录
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS portfolio_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                etf_code TEXT NOT NULL,
                etf_name TEXT,
                trade_date TEXT NOT NULL,
                trade_type TEXT NOT NULL,   -- buy / sell
                price REAL,
                shares REAL,
                amount REAL,
                profit_loss REAL,
                signal TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # 持仓快照
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS portfolio_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                etf_code TEXT NOT NULL,
                etf_name TEXT,
                entry_date TEXT,
                entry_price REAL,
                shares REAL,
                current_price REAL,
                market_value REAL,
                profit_loss REAL,
                return_pct REAL,
                signal TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(etf_code, updated_at)
            )
        ''')

        conn.commit()
        conn.close()

    def get_current_positions(self, date: str = None) -> List[Dict]:
        """获取当前持仓"""
        date = date or datetime.now().strftime('%Y-%m-%d')
        conn = sqlite3.connect(self.db_path)
        query = '''
            SELECT etf_code, etf_name, entry_date, entry_price, shares,
                   current_price, market_value, profit_loss, return_pct, signal
            FROM portfolio_positions
            WHERE updated_at = ?
              AND shares > 0
        '''
        df = pd.read_sql_query(query, conn, params=(date,))
        conn.close()
        return df.to_dict('records') if not df.empty else []

    def get_trade_history(self, days: int = 90) -> pd.DataFrame:
        """获取交易历史"""
        conn = sqlite3.connect(self.db_path)
        query = '''
            SELECT etf_code, etf_name, trade_date, trade_type, price, shares, amount, profit_loss, signal
            FROM portfolio_trades
            WHERE trade_date >= date('now', '-{} days')
            ORDER BY trade_date DESC
        '''.format(days)
        df = pd.read_sql_query(query, conn)
        conn.close()
        return df

    def get_nav_history(self, days: int = 365) -> pd.DataFrame:
        """获取净值历史"""
        conn = sqlite3.connect(self.db_path)
        query = '''
            SELECT date, total_value, cash, invested, daily_return, cumulative_return
            FROM portfolio_nav
            WHERE date >= date('now', '-{} days')
            ORDER BY date ASC
        '''.format(days)
        df = pd.read_sql_query(query, conn)
        conn.close()
        return df

    def update(self, quick_results: List[Dict], report_date: str, 
               sector_ranking=None, initial_cash: float = None) -> Dict:
        """
        根据信号更新组合持仓
        返回：组合状态摘要
        """
        if initial_cash:
            self.initial_cash = initial_cash

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # 1. 获取昨日持仓
        yesterday = (datetime.strptime(report_date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
        yesterday_positions = {}
        cursor.execute('''
            SELECT etf_code, etf_name, entry_price, shares FROM portfolio_positions
            WHERE updated_at = ? AND shares > 0
        ''', (yesterday,))
        for row in cursor.fetchall():
            yesterday_positions[row[0]] = {
                'name': row[1], 'entry_price': row[2], 'shares': row[3]
            }

        # 2. 获取昨日现金
        cursor.execute('SELECT cash FROM portfolio_nav WHERE date = ?', (yesterday,))
        cash_row = cursor.fetchone()
        cash = cash_row[0] if cash_row else self.initial_cash

        # 3. 处理所有 ETF
        new_positions = {}
        total_invested = 0.0
        trades = []

        # 按综合评分排序，取前 N 名
        sorted_etfs = sorted(quick_results, key=lambda x: x['signals']['score'], reverse=True)
        top_etfs = sorted_etfs[:self.MAX_POSITIONS]

        for r in top_etfs:
            code = r['code']
            name = r['name']
            price = float(r['current_price'])
            score = r['signals']['score']
            signal = r['signals'].get('overall_signal', 'neutral')

            # 处理已经持有的
            if code in yesterday_positions:
                pos = yesterday_positions[code]
                shares = pos['shares']
                entry_price = pos['entry_price']
                market_value = shares * price
                profit_loss = market_value - (shares * entry_price)
                return_pct = (price - entry_price) / entry_price * 100

                # 看空则卖出
                if signal == 'bearish' or score < -20:
                    trades.append({
                        'code': code, 'name': name,
                        'type': 'sell', 'price': price,
                        'shares': shares, 'amount': market_value,
                        'profit_loss': profit_loss,
                        'signal': signal
                    })
                    cash += market_value
                    continue  # 不加入新持仓

                new_positions[code] = {
                    'name': name, 'entry_date': pos.get('entry_date', report_date),
                    'entry_price': entry_price, 'shares': shares,
                    'current_price': price, 'market_value': market_value,
                    'profit_loss': profit_loss, 'return_pct': return_pct,
                    'signal': signal
                }
                total_invested += market_value
            else:
                # 看多则买入 — 使用Kelly公式计算仓位
                if signal == 'bullish' and score > 20:
                    # 从回测结果计算Kelly比例
                    win_rate, avg_win, avg_loss = self._calc_kelly_params(r)
                    kelly_pct = self._kelly_formula(win_rate, avg_win, avg_loss)
                    # 限制单只仓位上限
                    alloc_ratio = min(kelly_pct, self.MAX_SINGLE_POSITION)
                    alloc = cash * alloc_ratio
                    shares = int(alloc / price / 100) * 100  # 按 100 股取整
                    if shares > 0 and alloc <= cash:
                        cost = shares * price
                        cash -= cost
                        total_invested += cost
                        new_positions[code] = {
                            'name': name, 'entry_date': report_date,
                            'entry_price': price, 'shares': shares,
                            'current_price': price, 'market_value': cost,
                            'profit_loss': 0, 'return_pct': 0,
                            'signal': signal
                        }
                        trades.append({
                            'code': code, 'name': name,
                            'type': 'buy', 'price': price,
                            'shares': shares, 'amount': cost,
                            'profit_loss': 0, 'signal': signal
                        })

        # 4. 保存持仓快照
        cursor.execute('DELETE FROM portfolio_positions WHERE updated_at = ?', (report_date,))
        for code, pos in new_positions.items():
            cursor.execute('''
                INSERT INTO portfolio_positions
                (etf_code, etf_name, entry_date, entry_price, shares,
                 current_price, market_value, profit_loss, return_pct, signal, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (code, pos['name'], pos['entry_date'], pos['entry_price'],
                  pos['shares'], pos['current_price'], pos['market_value'],
                  pos['profit_loss'], pos['return_pct'], pos['signal'], report_date))

        # 5. 保存交易记录
        for t in trades:
            cursor.execute('''
                INSERT INTO portfolio_trades
                (etf_code, etf_name, trade_date, trade_type, price, shares, amount, profit_loss, signal)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (t['code'], t['name'], report_date, t['type'],
                  t['price'], t['shares'], t['amount'], t['profit_loss'], t['signal']))

        # 6. 计算净值
        total_value = cash + total_invested
        prev_nav = None
        cursor.execute('SELECT total_value FROM portfolio_nav ORDER BY date DESC LIMIT 1')
        prev_row = cursor.fetchone()
        if prev_row:
            prev_nav = prev_row[0]

        daily_return = (total_value - prev_nav) / prev_nav if prev_nav and prev_nav > 0 else 0
        cumulative_return = (total_value - self.initial_cash) / self.initial_cash

        cursor.execute('''
            REPLACE INTO portfolio_nav
            (date, total_value, cash, invested, daily_return, cumulative_return)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (report_date, total_value, cash, total_invested, daily_return, cumulative_return))

        conn.commit()
        conn.close()

        return {
            'date': report_date,
            'total_value': round(total_value, 2),
            'cash': round(cash, 2),
            'invested': round(total_invested, 2),
            'daily_return': round(daily_return * 100, 2),
            'cumulative_return': round(cumulative_return * 100, 2),
            'positions': len(new_positions),
            'trades': len(trades)
        }

    def generate_performance_report(self, output_dir: str = None) -> str:
        """
        使用 quantstats 生成 HTML 绩效报告
        返回 HTML 文件路径
        """
        try:
            import quantstats as qs
        except ImportError:
            print("  quantstats 未安装，请执行: pip install quantstats")
            return ""

        output_dir = output_dir or "/home/zhihu/daily-_tracker_analytics/reports"
        os.makedirs(output_dir, exist_ok=True)

        # 获取净值序列
        df_nav = self.get_nav_history(days=365)
        if df_nav.empty or len(df_nav) < 5:
            print("  净值数据不足（<5天），无法生成绩效报告")
            return ""

        # 计算组合日收益率
        df_nav['daily_ret'] = df_nav['total_value'].pct_change()
        returns = df_nav['daily_ret'].dropna()

        # 沪深300作为基准
        try:
            import yfinance as yf
            bench = yf.download('000300.SS', period='1y', progress=False)['Close']
            bench_ret = bench.pct_change().dropna()
            # 对齐日期
            common_dates = returns.index.intersection(bench_ret.index)
            returns = returns[common_dates]
            bench_ret = bench_ret[common_dates]
        except Exception:
            bench_ret = None
            print("  基准数据获取失败，仅输出组合表现")

        report_path = f"{output_dir}/portfolio_report_{datetime.now().strftime('%Y-%m-%d')}.html"

        try:
            # 使用 quantstats 生成全量报告
            kwargs = dict(
                output=report_path,
                title=f"多板块ETF组合 — 起始资金 ¥{self.initial_cash:,.0f}"
            )
            if bench_ret is not None:
                qs.reports.html(returns, benchmark=bench_ret, **kwargs)
            else:
                qs.reports.html(returns, **kwargs)

            print(f"  📊 绩效报告已生成: {report_path}")
            return report_path

        except Exception as e:
            print(f"  quantstats 报告生成失败: {e}")
            # 降级：输出统计摘要
            stats = qs.stats(returns)
            stats_path = report_path.replace('.html', '.md')
            with open(stats_path, 'w', encoding='utf-8') as f:
                f.write(f"# 组合绩效统计\n\n日期: {datetime.now().strftime('%Y-%m-%d')}\n\n")
                for k, v in stats.items():
                    f.write(f"- {k}: {v}\n")
            return stats_path

    def generate_summary_section(self) -> str:
        """生成 Markdown 格式的组合概况"""
        nav = self.get_nav_history(days=5)
        positions = self.get_current_positions()

        if nav.empty:
            return ""

        latest = nav.iloc[-1]
        section = "\n\n---\n\n# 虚拟组合表现\n\n"

        section += f"| 指标 | 数值 |\n|------|------|\n"
        section += f"| 起始资金 | ¥{self.initial_cash:,.0f} |\n"
        section += f"| 当前总值 | ¥{latest['total_value']:,.2f} |\n"
        section += f"| 累计收益 | {latest['cumulative_return']:+.2f}% |\n"
        section += f"| 持仓数量 | {len(positions)} 只 |\n"

        if len(nav) >= 2:
            # 最近几天表现
            recent = nav.tail(min(5, len(nav)))
            section += "\n**最近收益**:\n\n"
            for _, row in recent.iterrows():
                emoji = "📈" if row['daily_return'] >= 0 else "📉"
                section += f"- {row['date']}: {emoji} {row['daily_return']:+.2f}%\n"

        if positions:
            section += "\n**当前持仓**:\n\n"
            section += "| ETF | 买入价 | 现价 | 盈亏 | 信号 |\n"
            section += "|-----|--------|------|------|------|\n"
            for p in positions:
                emoji = "📈" if p.get('return_pct', 0) >= 0 else "📉"
                section += f"| {p['etf_name']} | {p['entry_price']:.3f} | {p['current_price']:.3f} | {emoji} {p['return_pct']:+.2f}% | {p['signal']} |\n"

        return section


if __name__ == "__main__":
    # 测试
    tracker = PortfolioTracker(initial_cash=50000)
    print("组合数据库已初始化")
    report = tracker.generate_performance_report()
    print(report)
