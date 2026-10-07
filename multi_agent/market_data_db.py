#!/usr/bin/env python3
"""
DuckDB 本地数据库管理器
全市场历史数据落地，SQL 查询加速回测
"""

import sys
import os
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional

# 尝试导入 duckdb
try:
    import duckdb
    DUCKDB_AVAILABLE = True
except ImportError:
    DUCKDB_AVAILABLE = False

# 导入 HiThink 客户端
try:
    from hithink_client import HiThinkFinanceClient
except ImportError:
    try:
        from multi_agent.hithink_client import HiThinkFinanceClient
    except ImportError:
        raise ImportError("HiThink 客户端不可用")


class MarketDataDB:
    """市场数据本地数据库"""
    
    def __init__(self, db_path: str = None):
        """
        初始化数据库
        
        Args:
            db_path: 数据库文件路径（默认 data/market_data.duckdb）
        """
        if not DUCKDB_AVAILABLE:
            raise ImportError("DuckDB 未安装，请运行: pip install duckdb")
        
        if db_path is None:
            db_path = Path(__file__).parent.parent / 'data' / 'market_data.duckdb'
            db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self.db_path = str(db_path)
        self.conn = duckdb.connect(self.db_path)
        self.client = HiThinkFinanceClient()
        
        self._init_tables()
    
    def _init_tables(self):
        """初始化数据表"""
        # A股日线数据
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS a_share_daily (
                ticker VARCHAR,
                thscode VARCHAR,
                date DATE,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume BIGINT,
                turnover DOUBLE,
                PRIMARY KEY (ticker, date)
            )
        """)
        
        # 估值数据
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS valuation (
                ticker VARCHAR,
                thscode VARCHAR,
                date DATE,
                pe_ttm DOUBLE,
                pb_mrq DOUBLE,
                ps_ttm DOUBLE,
                pc_ttm DOUBLE,
                PRIMARY KEY (ticker, date)
            )
        """)
        
        # 情绪数据
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS sentiment (
                date DATE,
                zt_count INTEGER,
                dt_count INTEGER,
                zb_count INTEGER,
                seal_rate DOUBLE,
                max_streak INTEGER,
                prev_avg_chg DOUBLE
            )
        """)
        
        print(f"✅ 数据库初始化完成: {self.db_path}")
    
    def download_stock_data(self, symbol: str, start_date: str, end_date: str, 
                           use_hithink: bool = True) -> int:
        """
        下载单只股票历史数据
        
        Args:
            symbol: A股代码（如 000001）
            start_date: 开始日期（YYYY-MM-DD）
            end_date: 结束日期（YYYY-MM-DD）
            use_hithink: 是否使用 HiThink API
        
        Returns:
            int: 插入的记录数
        """
        thscode = HiThinkFinanceClient.to_thscode(symbol)
        
        if use_hithink:
            try:
                kline = self.client.get_kline(thscode, start_date, end_date)
            except Exception as e:
                print(f"  ⚠️ HiThink 失败: {e}")
                return 0
        else:
            # 降级：腾讯接口
            print(f"  ⚠️ 腾讯接口降级方案暂未实现")
            return 0
        
        if not kline:
            return 0
        
        # 插入数据
        count = 0
        for bar in kline:
            try:
                self.conn.execute("""
                    INSERT OR REPLACE INTO a_share_daily 
                    (ticker, thscode, date, open, high, low, close, volume, turnover)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    symbol,
                    thscode,
                    bar['date'],
                    bar.get('open_price', 0),
                    bar.get('high_price', 0),
                    bar.get('low_price', 0),
                    bar.get('close_price', 0),
                    int(bar.get('volume', 0)),
                    bar.get('turnover', 0)
                ))
                count += 1
            except Exception as e:
                print(f"  ⚠️ 插入失败 {bar.get('date')}: {e}")
        
        return count
    
    def download_portfolio(self, holdings: List[Dict], start_date: str, end_date: str) -> Dict:
        """
        下载组合持仓数据
        
        Args:
            holdings: 持仓列表 [{'symbol': '000001', 'name': '平安银行'}, ...]
            start_date: 开始日期
            end_date: 结束日期
        
        Returns:
            Dict: 下载统计 {'total': n, 'success': n, 'failed': n}
        """
        stats = {'total': len(holdings), 'success': 0, 'failed': 0}
        
        for holding in holdings:
            symbol = holding['symbol']
            name = holding.get('name', symbol)
            
            print(f"📥 下载 {name} ({symbol})...")
            try:
                count = self.download_stock_data(symbol, start_date, end_date)
                if count > 0:
                    print(f"   ✅ {count} 条记录")
                    stats['success'] += 1
                else:
                    print(f"   ⚠️ 无数据")
                    stats['failed'] += 1
            except Exception as e:
                print(f"   ❌ 失败: {e}")
                stats['failed'] += 1
        
        return stats
    
    def query_daily(self, symbol: str, start_date: str = None, end_date: str = None) -> List[Dict]:
        """
        查询日线数据
        
        Args:
            symbol: A股代码
            start_date: 开始日期（可选）
            end_date: 结束日期（可选）
        
        Returns:
            List[Dict]: K线数据列表
        """
        sql = "SELECT * FROM a_share_daily WHERE ticker = ?"
        params = [symbol]
        
        if start_date:
            sql += " AND date >= ?"
            params.append(start_date)
        if end_date:
            sql += " AND date <= ?"
            params.append(end_date)
        
        sql += " ORDER BY date"
        
        result = self.conn.execute(sql, params).fetchall()
        
        return [
            {
                'ticker': row[0],
                'thscode': row[1],
                'date': row[2],
                'open': row[3],
                'high': row[4],
                'low': row[5],
                'close': row[6],
                'volume': row[7],
                'turnover': row[8]
            }
            for row in result
        ]
    
    def query_valuation(self, symbol: str = None, date: str = None) -> List[Dict]:
        """查询估值数据"""
        sql = "SELECT * FROM valuation WHERE 1=1"
        params = []
        
        if symbol:
            sql += " AND ticker = ?"
            params.append(symbol)
        if date:
            sql += " AND date = ?"
            params.append(date)
        
        result = self.conn.execute(sql, params).fetchall()
        
        return [
            {
                'ticker': row[0],
                'thscode': row[1],
                'date': row[2],
                'pe_ttm': row[3],
                'pb_mrq': row[4],
                'ps_ttm': row[5],
                'pc_ttm': row[6]
            }
            for row in result
        ]
    
    def get_stats(self) -> Dict:
        """获取数据库统计信息"""
        stats = {}
        
        # 表记录数
        tables = ['a_share_daily', 'valuation', 'sentiment']
        for table in tables:
            try:
                count = self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                stats[table] = count
            except:
                stats[table] = 0
        
        # 股票数
        try:
            stock_count = self.conn.execute("SELECT COUNT(DISTINCT ticker) FROM a_share_daily").fetchone()[0]
            stats['unique_stocks'] = stock_count
        except:
            stats['unique_stocks'] = 0
        
        # 日期范围
        try:
            date_range = self.conn.execute("""
                SELECT MIN(date), MAX(date) FROM a_share_daily
            """).fetchone()
            stats['date_range'] = {'min': date_range[0], 'max': date_range[1]}
        except:
            stats['date_range'] = None
        
        return stats
    
    def export_to_csv(self, table: str, output_path: str):
        """导出表到 CSV"""
        self.conn.execute(f"COPY {table} TO '{output_path}' (HEADER, DELIMITER ',')")
        print(f"✅ 导出完成: {output_path}")
    
    def close(self):
        """关闭数据库连接"""
        self.conn.close()


# 测试
if __name__ == '__main__':
    print("=" * 60)
    print("🧪 DuckDB 本地数据库测试")
    print("=" * 60)
    
    # 初始化数据库
    db = MarketDataDB()
    
    # 测试下载
    print("\n1. 测试下载平安银行数据...")
    count = db.download_stock_data('000001', '2024-01-01', '2024-12-31')
    print(f"   下载 {count} 条记录")
    
    # 测试查询
    print("\n2. 测试查询...")
    data = db.query_daily('000001', '2024-09-01', '2024-09-30')
    print(f"   查询到 {len(data)} 条记录")
    if data:
        print(f"   示例: {data[0]}")
    
    # 测试统计
    print("\n3. 数据库统计...")
    stats = db.get_stats()
    for key, value in stats.items():
        print(f"   {key}: {value}")
    
    # 关闭
    db.close()
    
    print("\n" + "=" * 60)
    print("✅ 测试完成")
    print("=" * 60)
