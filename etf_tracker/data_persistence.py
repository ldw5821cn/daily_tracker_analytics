#!/usr/bin/env python3
"""
数据持久化模块 - SQLite数据库
功能：
1. 保存历史数据到SQLite
2. 保存预测结果用于后续验证
3. 保存预警记录
4. 保存行业新闻
5. 数据查询与统计
"""

import os
import sqlite3
import json
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import warnings
warnings.filterwarnings('ignore')


class DataPersistence:
    """数据持久化管理器"""
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path or os.path.expanduser("~/etf_tracker/data/etf_tracker.db")
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_database()
    
    def _init_database(self):
        """初始化数据库表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 1. ETF历史数据表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS etf_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                etf_code TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume INTEGER,
                amount REAL,
                ma5 REAL,
                ma10 REAL,
                ma20 REAL,
                rsi14 REAL,
                macd REAL,
                macd_signal REAL,
                macd_hist REAL,
                bb_upper REAL,
                bb_middle REAL,
                bb_lower REAL,
                atr14 REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(etf_code, date)
            )
        ''')
        
        # 2. 预测结果表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                etf_code TEXT NOT NULL,
                predict_date TEXT NOT NULL,
                target_date TEXT NOT NULL,
                period_days INTEGER,
                predicted_price REAL,
                expected_return REAL,
                trend TEXT,
                confidence REAL,
                actual_price REAL,
                actual_return REAL,
                accuracy INTEGER,  -- 1:正确, 0:错误, NULL:未验证
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 3. 预警记录表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                etf_code TEXT,
                stock_code TEXT,
                stock_name TEXT,
                alert_type TEXT NOT NULL,
                alert_level TEXT NOT NULL,
                message TEXT,
                triggered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                resolved INTEGER DEFAULT 0
            )
        ''')
        
        # 4. 行业新闻表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS industry_news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                industry TEXT NOT NULL,
                title TEXT,
                url TEXT,
                source TEXT,
                news_date TEXT,
                sentiment TEXT,
                sentiment_score REAL,
                summary TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 5. 个股分析表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS stock_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_code TEXT NOT NULL,
                stock_name TEXT,
                analysis_date TEXT NOT NULL,
                latest_price REAL,
                total_return REAL,
                rsi REAL,
                macd_status TEXT,
                kdj_status TEXT,
                trend TEXT,
                score REAL,
                multi_factor_score REAL,
                rating TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(stock_code, analysis_date)
            )
        ''')
        
        # 6. 回测结果表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS backtest_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                etf_code TEXT NOT NULL,
                backtest_date TEXT NOT NULL,
                period_days INTEGER,
                total_return REAL,
                volatility REAL,
                max_drawdown REAL,
                sharpe_ratio REAL,
                win_rate REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(etf_code, backtest_date, period_days)
            )
        ''')
        
        conn.commit()
        conn.close()
        print(f"数据库初始化完成: {self.db_path}")
    
    def save_etf_history(self, etf_code: str, df: pd.DataFrame):
        """保存ETF历史数据"""
        conn = sqlite3.connect(self.db_path)
        
        # 准备数据
        data = []
        for _, row in df.iterrows():
            date_val = row['date']
            if hasattr(date_val, 'strftime'):
                date_str = date_val.strftime('%Y-%m-%d')
            else:
                date_str = str(date_val)
            
            data.append({
                'etf_code': etf_code,
                'date': date_str,
                'open': float(row.get('open', 0) or 0),
                'high': float(row.get('high', 0) or 0),
                'low': float(row.get('low', 0) or 0),
                'close': float(row.get('close', 0) or 0),
                'volume': int(row.get('volume', 0) or 0),
                'amount': float(row.get('amount', 0) or 0),
                'ma5': float(row.get('ma5', 0)) if 'ma5' in row and pd.notna(row['ma5']) else None,
                'ma10': float(row.get('ma10', 0)) if 'ma10' in row and pd.notna(row['ma10']) else None,
                'ma20': float(row.get('ma20', 0)) if 'ma20' in row and pd.notna(row['ma20']) else None,
                'rsi14': float(row.get('rsi14', 0)) if 'rsi14' in row and pd.notna(row['rsi14']) else None,
                'macd': float(row.get('macd', 0)) if 'macd' in row and pd.notna(row['macd']) else None,
                'macd_signal': float(row.get('macd_signal', 0)) if 'macd_signal' in row and pd.notna(row['macd_signal']) else None,
                'macd_hist': float(row.get('macd_hist', 0)) if 'macd_hist' in row and pd.notna(row['macd_hist']) else None,
                'bb_upper': float(row.get('bb_upper', 0)) if 'bb_upper' in row and pd.notna(row['bb_upper']) else None,
                'bb_middle': float(row.get('bb_middle', 0)) if 'bb_middle' in row and pd.notna(row['bb_middle']) else None,
                'bb_lower': float(row.get('bb_lower', 0)) if 'bb_lower' in row and pd.notna(row['bb_lower']) else None,
                'atr14': float(row.get('atr14', 0)) if 'atr14' in row and pd.notna(row['atr14']) else None
            })
        
        # 使用REPLACE避免重复
        cursor = conn.cursor()
        for item in data:
            cursor.execute('''
                REPLACE INTO etf_history 
                (etf_code, date, open, high, low, close, volume, amount, 
                 ma5, ma10, ma20, rsi14, macd, macd_signal, macd_hist,
                 bb_upper, bb_middle, bb_lower, atr14)
                VALUES 
                (:etf_code, :date, :open, :high, :low, :close, :volume, :amount,
                 :ma5, :ma10, :ma20, :rsi14, :macd, :macd_signal, :macd_hist,
                 :bb_upper, :bb_middle, :bb_lower, :atr14)
            ''', item)
        
        conn.commit()
        conn.close()
        print(f"  保存ETF历史数据: {len(data)} 条")
    
    def save_prediction(self, etf_code: str, prediction: Dict):
        """保存预测结果"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        predict_date = datetime.now().strftime('%Y-%m-%d')
        
        for day_key, pred in prediction.items():
            if day_key.startswith('day_'):
                period_days = int(day_key.replace('day_', ''))
                target_date = (datetime.now() + timedelta(days=period_days)).strftime('%Y-%m-%d')
                
                cursor.execute('''
                    INSERT INTO predictions 
                    (etf_code, predict_date, target_date, period_days, 
                     predicted_price, expected_return, trend, confidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    etf_code, predict_date, target_date, period_days,
                    pred.get('predicted_price', 0),
                    pred.get('expected_return', 0),
                    pred.get('trend', ''),
                    pred.get('confidence', 0)
                ))
        
        conn.commit()
        conn.close()
        print(f"  保存预测结果: {len([k for k in prediction.keys() if k.startswith('day_')])} 条")
    
    def save_alerts(self, alerts: List[Dict]):
        """保存预警记录"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        for alert in alerts:
            cursor.execute('''
                INSERT INTO alerts 
                (etf_code, stock_code, stock_name, alert_type, alert_level, message)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                alert.get('etf_code', ''),
                alert.get('stock_code', ''),
                alert.get('stock_name', ''),
                alert.get('type', ''),
                alert.get('level', ''),
                alert.get('message', '')
            ))
        
        conn.commit()
        conn.close()
        print(f"  保存预警记录: {len(alerts)} 条")
    
    def save_stock_analysis(self, stock_code: str, stock_name: str, analysis: Dict):
        """保存个股分析结果"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        analysis_date = datetime.now().strftime('%Y-%m-%d')
        
        cursor.execute('''
            REPLACE INTO stock_analysis 
            (stock_code, stock_name, analysis_date, latest_price, total_return,
             rsi, macd_status, kdj_status, trend, score, multi_factor_score, rating)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            stock_code, stock_name, analysis_date,
            analysis.get('latest_price', 0),
            analysis.get('total_return', 0),
            analysis.get('rsi', 0),
            analysis.get('macd_status', ''),
            analysis.get('kdj_status', ''),
            analysis.get('trend', ''),
            analysis.get('score', 0),
            analysis.get('multi_factor_score', {}).get('total_score', 0) if isinstance(analysis.get('multi_factor_score'), dict) else 0,
            analysis.get('multi_factor_score', {}).get('rating', '') if isinstance(analysis.get('multi_factor_score'), dict) else ''
        ))
        
        conn.commit()
        conn.close()
    
    def save_backtest_result(self, etf_code: str, result: Dict):
        """保存回测结果"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        backtest_date = datetime.now().strftime('%Y-%m-%d')
        
        cursor.execute('''
            REPLACE INTO backtest_results 
            (etf_code, backtest_date, period_days, total_return, volatility,
             max_drawdown, sharpe_ratio, win_rate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            etf_code, backtest_date, result.get('period_days', 0),
            result.get('total_return', 0),
            result.get('volatility', 0),
            result.get('max_drawdown', 0),
            result.get('sharpe_ratio', 0),
            result.get('win_rate', 0)
        ))
        
        conn.commit()
        conn.close()
    
    def get_latest_data(self, etf_code: str, days: int = 30) -> pd.DataFrame:
        """获取最新数据"""
        conn = sqlite3.connect(self.db_path)
        
        query = '''
            SELECT * FROM etf_history 
            WHERE etf_code = ? 
            ORDER BY date DESC 
            LIMIT ?
        '''
        
        df = pd.read_sql_query(query, conn, params=(etf_code, days))
        conn.close()
        
        return df
    
    def get_prediction_accuracy(self, etf_code: str, days: int = 30) -> Dict:
        """获取预测准确率统计"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT period_days, 
                   COUNT(*) as total,
                   SUM(CASE WHEN accuracy = 1 THEN 1 ELSE 0 END) as correct
            FROM predictions 
            WHERE etf_code = ? 
              AND predict_date >= date('now', '-{} days')
              AND accuracy IS NOT NULL
            GROUP BY period_days
        '''.format(days), (etf_code,))
        
        results = {}
        for row in cursor.fetchall():
            period_days, total, correct = row
            accuracy = (correct / total * 100) if total > 0 else 0
            results[f"day_{period_days}"] = {
                "total": total,
                "correct": correct,
                "accuracy": round(accuracy, 2)
            }
        
        conn.close()
        return results
    
    def update_prediction_accuracy(self):
        """更新预测准确率（验证历史预测）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 获取未验证的预测
        cursor.execute('''
            SELECT id, etf_code, target_date, predicted_price, trend
            FROM predictions 
            WHERE accuracy IS NULL 
              AND target_date <= date('now')
        ''')
        
        predictions_to_check = cursor.fetchall()
        
        for pred_id, etf_code, target_date, predicted_price, trend in predictions_to_check:
            # 获取实际价格
            cursor.execute('''
                SELECT close FROM etf_history 
                WHERE etf_code = ? AND date = ?
            ''', (etf_code, target_date))
            
            result = cursor.fetchone()
            if result:
                actual_price = result[0]
                
                # 判断预测是否正确
                pred_return = predicted_price - actual_price
                is_correct = 1 if (pred_return > 0 and '涨' in trend) or (pred_return < 0 and '跌' in trend) else 0
                
                cursor.execute('''
                    UPDATE predictions 
                    SET actual_price = ?, accuracy = ?
                    WHERE id = ?
                ''', (actual_price, is_correct, pred_id))
        
        conn.commit()
        conn.close()
        print(f"  更新预测准确率: {len(predictions_to_check)} 条")
    
    def get_alert_history(self, days: int = 7) -> pd.DataFrame:
        """获取预警历史"""
        conn = sqlite3.connect(self.db_path)
        
        query = '''
            SELECT * FROM alerts 
            WHERE triggered_at >= date('now', '-{} days')
            ORDER BY triggered_at DESC
        '''.format(days)
        
        df = pd.read_sql_query(query, conn)
        conn.close()
        
        return df
    
    def generate_statistics_report(self) -> str:
        """生成统计报告"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        report = "# 数据统计报告\n\n"
        
        # 1. 数据量统计
        cursor.execute("SELECT COUNT(*) FROM etf_history")
        etf_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM predictions")
        pred_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM alerts")
        alert_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM stock_analysis")
        stock_count = cursor.fetchone()[0]
        
        report += f"## 数据量统计\n\n"
        report += f"- ETF历史数据: {etf_count} 条\n"
        report += f"- 预测记录: {pred_count} 条\n"
        report += f"- 预警记录: {alert_count} 条\n"
        report += f"- 个股分析: {stock_count} 条\n\n"
        
        # 2. 预测准确率
        cursor.execute('''
            SELECT period_days, 
                   COUNT(*) as total,
                   SUM(CASE WHEN accuracy = 1 THEN 1 ELSE 0 END) as correct
            FROM predictions 
            WHERE accuracy IS NOT NULL
            GROUP BY period_days
        ''')
        
        report += "## 预测准确率\n\n"
        report += "| 预测周期 | 总次数 | 正确次数 | 准确率 |\n"
        report += "|----------|--------|----------|--------|\n"
        
        for row in cursor.fetchall():
            period_days, total, correct = row
            accuracy = (correct / total * 100) if total > 0 else 0
            report += f"| {period_days}日 | {total} | {correct} | {accuracy:.1f}% |\n"
        
        report += "\n"
        
        # 3. 预警统计
        cursor.execute('''
            SELECT alert_type, COUNT(*) as count
            FROM alerts 
            WHERE triggered_at >= date('now', '-7 days')
            GROUP BY alert_type
            ORDER BY count DESC
        ''')
        
        report += "## 最近7天预警统计\n\n"
        report += "| 预警类型 | 次数 |\n"
        report += "|----------|------|\n"
        
        for row in cursor.fetchall():
            alert_type, count = row
            report += f"| {alert_type} | {count} |\n"
        
        conn.close()
        
        return report


if __name__ == "__main__":
    # 测试
    db = DataPersistence()
    
    # 生成统计报告
    report = db.generate_statistics_report()
    print(report)
