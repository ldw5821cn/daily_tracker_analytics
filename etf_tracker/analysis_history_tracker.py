#!/usr/bin/env python3
"""
分析历史持久化与回测验证模块
功能：
1. 保存每日 ETF 多周期回测结果
2. 保存多模型预测结果（1日/5日）
3. 保存评分、仓位建议、市场摘要
4. 历史回测验证：对比预测与真实收益，计算准确率/方向准确率/MAE/RMSE
5. 生成回测验证报告
"""

import os
import sqlite3
import json
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import warnings
warnings.filterwarnings('ignore')


class AnalysisHistoryTracker:
    """分析历史持久化与回测验证器"""
    
    def __init__(self, db_path: str = ""):
        self.db_path = db_path or os.path.expanduser("~/etf_tracker/data/etf_tracker.db")
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_database()
    
    def _init_database(self):
        """初始化数据库表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 1. 每日 ETF 快照表（含评分、信号、仓位）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS daily_etf_snapshot (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                etf_code TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,
                current_price REAL,
                score REAL,
                signal TEXT,
                position_size REAL,
                sector TEXT,
                theme TEXT,
                rsi14 REAL,
                macd_status TEXT,
                ma_status TEXT,
                backtest_30d_return REAL,
                backtest_60d_return REAL,
                backtest_90d_return REAL,
                backtest_30d_max_dd REAL,
                backtest_60d_max_dd REAL,
                backtest_90d_max_dd REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(etf_code, snapshot_date)
            )
        ''')
        
        # 2. 多模型预测结果表（细化字段）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS model_predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                etf_code TEXT NOT NULL,
                predict_date TEXT NOT NULL,
                target_date TEXT NOT NULL,
                period_days INTEGER NOT NULL,
                ensemble_predicted_return REAL,
                ensemble_trend TEXT,
                confidence REAL,
                lightgbm_return REAL,
                xgboost_return REAL,
                random_forest_return REAL,
                arima_return REAL,
                lstm_return REAL,
                actual_return REAL,
                direction_accuracy INTEGER,  -- 1:方向正确, 0:方向错误, NULL:未验证
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(etf_code, predict_date, period_days)
            )
        ''')
        
        # 3. 回测验证汇总表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS backtest_validation (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                etf_code TEXT NOT NULL,
                validation_date TEXT NOT NULL,
                period_days INTEGER NOT NULL,
                predicted_return REAL,
                actual_return REAL,
                mae REAL,
                rmse REAL,
                direction_accuracy INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(etf_code, validation_date, period_days)
            )
        ''')
        
        # 5. 每日报告元数据表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS daily_report_meta (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_date TEXT NOT NULL UNIQUE,
                report_path TEXT,
                wechat_pushed INTEGER DEFAULT 0,
                top_etfs TEXT,
                sector_ranking TEXT,
                international_summary TEXT,
                llm_report_enabled INTEGER DEFAULT 0,
                llm_report_length INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 6. 板块轮动历史表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sector_rotation (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sector TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,
                rank INTEGER,
                score REAL,
                return_30d REAL,
                return_60d REAL,
                return_90d REAL,
                signal TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(sector, snapshot_date)
            )
        ''')
        
        conn.commit()
        conn.close()
        print(f"分析历史数据库初始化完成: {self.db_path}")
    
    # ---------------- 保存 ----------------
    
    def save_daily_snapshot(self, etf_code: str, snapshot_date: str, data: Dict):
        """保存 ETF 每日快照"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        backtests = data.get('backtest', []) or []
        bt_30 = next((b for b in backtests if b.get('period_days') == 30), {})
        bt_60 = next((b for b in backtests if b.get('period_days') == 60), {})
        bt_90 = next((b for b in backtests if b.get('period_days') == 90), {})
        
        cursor.execute('''
            REPLACE INTO daily_etf_snapshot 
            (etf_code, snapshot_date, current_price, score, signal, position_size,
             sector, theme, rsi14, macd_status, ma_status,
             backtest_30d_return, backtest_60d_return, backtest_90d_return,
             backtest_30d_max_dd, backtest_60d_max_dd, backtest_90d_max_dd)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            etf_code,
            snapshot_date,
            data.get('current_price'),
            data.get('score'),
            data.get('signal'),
            data.get('position_size'),
            data.get('sector'),
            data.get('theme'),
            data.get('rsi14'),
            data.get('macd_status'),
            data.get('ma_status'),
            bt_30.get('total_return'),
            bt_60.get('total_return'),
            bt_90.get('total_return'),
            bt_30.get('max_drawdown'),
            bt_60.get('max_drawdown'),
            bt_90.get('max_drawdown')
        ))
        
        conn.commit()
        conn.close()
    
    def save_prediction(self, etf_code: str, predict_date: str, prediction: Dict):
        """保存多模型预测结果
        prediction 格式:
        {
            'period_days': 1,
            'ensemble': {'return_1d': 0.005, 'trend': 'up', 'confidence': 0.62},
            'individual': {'lightgbm': {...}, 'xgboost': {...}, ...}
        }
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        ensemble = prediction.get('ensemble', {}) or {}
        individual = prediction.get('individual', {}) or {}
        period_days = prediction.get('period_days', 1)
        target_date = (datetime.strptime(predict_date, '%Y-%m-%d') + timedelta(days=period_days)).strftime('%Y-%m-%d')
        
        def _extract_return(model_name):
            m = individual.get(model_name, {})
            if isinstance(m, dict):
                for k in ['return_1d', 'return', 'predicted_return']:
                    if k in m and m[k] is not None:
                        return float(m[k])
            return None
        
        cursor.execute('''
            REPLACE INTO model_predictions
            (etf_code, predict_date, target_date, period_days,
             ensemble_predicted_return, ensemble_trend, confidence,
             lightgbm_return, xgboost_return, random_forest_return, arima_return, lstm_return)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            etf_code, predict_date, target_date, period_days,
            ensemble.get('return_1d'),
            ensemble.get('trend'),
            ensemble.get('confidence'),
            _extract_return('lightgbm'),
            _extract_return('xgboost'),
            _extract_return('random_forest'),
            _extract_return('arima'),
            _extract_return('lstm')
        ))
        
        conn.commit()
        conn.close()
    
    def save_daily_report_meta(self, report_date: str, report_path: str, data: Dict):
        """保存每日报告元数据"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        top_etfs = json.dumps(data.get('top_etfs', []), ensure_ascii=False) if data.get('top_etfs') else None
        sector_ranking = json.dumps(data.get('sector_ranking', []), ensure_ascii=False) if data.get('sector_ranking') else None
        
        cursor.execute('''
            REPLACE INTO daily_report_meta
            (report_date, report_path, wechat_pushed, top_etfs, sector_ranking,
             international_summary, llm_report_enabled, llm_report_length)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            report_date,
            report_path,
            1 if data.get('wechat_pushed') else 0,
            top_etfs,
            sector_ranking,
            data.get('international_summary', '')[:2000],
            1 if data.get('llm_report_enabled') else 0,
            data.get('llm_report_length')
        ))
        
        conn.commit()
        conn.close()
    
    # ---------------- 验证 ----------------
    
    def update_predictions_with_actual(self, df: pd.DataFrame, etf_code: str, predict_date: str, period_days: int):
        """根据实际价格更新预测的方向准确率
        df: 包含 predict_date 和 target_date 收盘价的 DataFrame
        """
        if df is None or len(df) < 2:
            return
        
        target_date = (datetime.strptime(predict_date, '%Y-%m-%d') + timedelta(days=period_days)).strftime('%Y-%m-%d')
        
        # 找到预测日和目标日价格
        pred_row = df[df['date'] == predict_date]
        target_row = df[df['date'] == target_date]
        
        if pred_row.empty or target_row.empty:
            return
        
        pred_close = pred_row['close'].values[0] if hasattr(pred_row['close'], 'values') else pred_row['close']
        target_close = target_row['close'].values[0] if hasattr(target_row['close'], 'values') else target_row['close']
        pred_price = float(pred_close)
        actual_price = float(target_close)
        actual_return = (actual_price - pred_price) / pred_price
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, ensemble_predicted_return, ensemble_trend
            FROM model_predictions
            WHERE etf_code = ? AND predict_date = ? AND period_days = ?
        ''', (etf_code, predict_date, period_days))
        
        row = cursor.fetchone()
        if row:
            pred_id, pred_return, trend = row
            pred_return = pred_return or 0.0
            # 方向准确率：预测方向与实际方向一致
            pred_direction = 1 if pred_return > 0 else (-1 if pred_return < 0 else 0)
            actual_direction = 1 if actual_return > 0 else (-1 if actual_return < 0 else 0)
            direction_accuracy = 1 if pred_direction == actual_direction and pred_direction != 0 else 0
            
            cursor.execute('''
                UPDATE model_predictions
                SET actual_return = ?, direction_accuracy = ?
                WHERE id = ?
            ''', (actual_return, direction_accuracy, pred_id))
            
            # 同时写入回测验证表
            mae = abs(pred_return - actual_return)
            rmse = (pred_return - actual_return) ** 2
            cursor.execute('''
                REPLACE INTO backtest_validation
                (etf_code, validation_date, period_days, predicted_return, actual_return, mae, rmse, direction_accuracy)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (etf_code, predict_date, period_days, pred_return, actual_return, mae, rmse, direction_accuracy))
        
        conn.commit()
        conn.close()
    
    def validate_all_pending_predictions(self, data_source=None):
        """验证所有待验证的预测（需要 data_source 返回 df）"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT etf_code, predict_date, period_days, target_date
            FROM model_predictions
            WHERE direction_accuracy IS NULL
            ORDER BY predict_date, etf_code, period_days
        ''')
        
        pending = cursor.fetchall()
        conn.close()
        
        validated = 0
        for etf_code, predict_date, period_days, target_date in pending:
            try:
                # 优先从 etf_history 表获取历史价格
                df = self._get_history_from_db(etf_code, predict_date, target_date)
                if df is None or len(df) < 2:
                    # 如果数据库没有，尝试用外部数据源（如果提供）
                    if data_source is not None:
                        df = data_source(etf_code)
                
                if df is not None and len(df) >= 2:
                    self.update_predictions_with_actual(df, etf_code, predict_date, period_days)
                    validated += 1
            except Exception as e:
                print(f"  验证预测失败 {etf_code} {predict_date}: {e}")
        
        print(f"  已验证 {validated}/{len(pending)} 条预测")
        return validated
    
    def _get_history_from_db(self, etf_code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
        """从数据库获取历史价格"""
        conn = sqlite3.connect(self.db_path)
        query = '''
            SELECT date, close FROM etf_history
            WHERE etf_code = ? AND date BETWEEN ? AND ?
            ORDER BY date ASC
        '''
        df = pd.read_sql_query(query, conn, params=(etf_code, start_date, end_date))
        conn.close()
        if not df.empty:
            df['date'] = df['date'].astype(str)
        return df if not df.empty else None
    
    # ---------------- 查询 ----------------
    
    def get_backtest_30_60_90(self, etf_code: str) -> Dict:
        """获取 ETF 最近 30/60/90 天回测结果"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT backtest_30d_return, backtest_60d_return, backtest_90d_return,
                   backtest_30d_max_dd, backtest_60d_max_dd, backtest_90d_max_dd
            FROM daily_etf_snapshot
            WHERE etf_code = ?
            ORDER BY snapshot_date DESC
            LIMIT 1
        ''', (etf_code,))
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return {
                "return_30d": row[0], "return_60d": row[1], "return_90d": row[2],
                "max_dd_30d": row[3], "max_dd_60d": row[4], "max_dd_90d": row[5]
            }
        return {}
    
    def get_prediction_accuracy_summary(self, etf_code: str = None, days: int = 90) -> Dict:
        """获取预测准确率汇总"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        params = []
        where_clause = "WHERE direction_accuracy IS NOT NULL"
        if etf_code:
            where_clause += " AND etf_code = ?"
            params.append(etf_code)
        if days:
            where_clause += " AND predict_date >= date('now', '-{} days')".format(days)
        
        sql = '''
            SELECT period_days,
                   COUNT(*) as total,
                   SUM(CASE WHEN direction_accuracy = 1 THEN 1 ELSE 0 END) as correct,
                   AVG(ABS(ensemble_predicted_return - actual_return)) as mae,
                   AVG((ensemble_predicted_return - actual_return) * (ensemble_predicted_return - actual_return)) as mse
            FROM model_predictions
            {where_clause}
            GROUP BY period_days
        '''.format(where_clause=where_clause)
        
        cursor.execute(sql, tuple(params))
        
        results = {}
        for row in cursor.fetchall():
            period_days, total, correct, mae, mse = row
            results[f"day_{period_days}"] = {
                "total": total,
                "correct": correct,
                "direction_accuracy": round(correct / total * 100, 1) if total else 0,
                "mae": round(mae * 100, 2) if mae else None,
                "rmse": round((mse ** 0.5) * 100, 2) if mse else None
            }
        
        conn.close()
        return results
    
    def generate_validation_report(self, etf_code: str = "", days: int = 90) -> str:
        """生成回测验证报告"""
        summary = self.get_prediction_accuracy_summary(etf_code, days)
        
        report = "# 多模型预测回测验证报告\n\n"
        report += f"> **验证日期**: {datetime.now().strftime('%Y-%m-%d')}\n"
        report += f"> **ETF筛选**: {etf_code or '全部'}\n"
        report += f"> **统计窗口**: 最近 {days} 天\n\n"
        
        if not summary:
            report += "暂无可验证的预测记录。\n"
            return report
        
        report += "| 预测周期 | 样本数 | 方向准确率 | MAE | RMSE |\n"
        report += "|----------|--------|------------|-----|------|\n"
        
        for period, stats in sorted(summary.items(), key=lambda x: int(x[0].replace('day_', ''))):
            report += f"| {period.replace('day_', '')}日 | {stats['total']} | {stats['direction_accuracy']}% | {stats['mae']}% | {stats['rmse']}% |\n"
        
        report += "\n"
        report += "## 说明\n\n"
        report += "- **方向准确率**: 预测涨跌方向与实际方向一致的比例\n"
        report += "- **MAE**: 平均绝对误差（预测收益率 vs 实际收益率）\n"
        report += "- **RMSE**: 均方根误差，反映预测偏离幅度\n"
        report += "- 预测基于 LightGBM/XGBoost/RandomForest/ARIMA/LSTM 五模型融合\n"
        
        return report
    
    # ---------------- 行业轮动 ----------------
    
    def save_sector_ranking(self, sector_ranking, snapshot_date: str):
        """保存每日板块排名"""
        if sector_ranking is None or sector_ranking.empty:
            return
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        for idx, row in sector_ranking.iterrows():
            cursor.execute('''
                REPLACE INTO sector_rotation
                (sector, snapshot_date, rank, score, return_30d, return_60d, return_90d, signal)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                row.get('板块', f'sector_{idx}'),
                snapshot_date,
                int(idx) + 1,
                float(row.get('综合评分', 0) or 0),
                float(row.get('30天收益', 0) or 0) if '30天收益' in row else None,
                float(row.get('60天收益', 0) or 0),
                float(row.get('90天收益', 0) or 0) if '90天收益' in row else None,
                str(row.get('信号', ''))
            ))
        conn.commit()
        conn.close()
    
    def get_sector_rotation_analysis(self, days_lookback: int = 14) -> Dict:
        """分析板块轮动速度
        返回每个板块的排名变化趋势
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 获取最近 N 天的所有板块排名历史
        cursor.execute('''
            SELECT sector, snapshot_date, rank, score, return_60d
            FROM sector_rotation
            WHERE snapshot_date >= date('now', '-{} days')
            ORDER BY snapshot_date, rank
        '''.format(days_lookback))
        
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return {}
        
        df = pd.DataFrame(rows, columns=['sector', 'date', 'rank', 'score', 'return_60d'])
        
        results = {}
        for sector in df['sector'].unique():
            sdf = df[df['sector'] == sector].sort_values('date')
            if len(sdf) < 2:
                continue
            
            first = sdf.iloc[0]
            last = sdf.iloc[-1]
            rank_change = first['rank'] - last['rank']  # 负=排名上升，正=排名下降
            score_change = (last['score'] or 0) - (first['score'] or 0)
            
            # 轮动速度 = 排名变化 / 天数
            days_span = max((pd.to_datetime(last['date']) - pd.to_datetime(first['date'])).days, 1)
            rotation_speed = rank_change / days_span
            
            results[sector] = {
                "current_rank": int(last['rank']),
                "previous_rank": int(first['rank']),
                "rank_change": int(rank_change),
                "score_change": round(score_change, 1),
                "rotation_speed": round(rotation_speed, 2),
                "direction": "上升" if rank_change > 0 else "下降" if rank_change < 0 else "持平",
                "return_60d": round(last['return_60d'] * 100, 2) if last['return_60d'] else 0,
                "data_points": len(sdf)
            }
        
        return results


if __name__ == "__main__":
    # 测试
    tracker = AnalysisHistoryTracker()
    
    # 保存测试数据
    tracker.save_daily_snapshot("516150", datetime.now().strftime('%Y-%m-%d'), {
        "current_price": 2.046,
        "score": 65.0,
        "signal": "bullish",
        "position_size": 0.3,
        "sector": "稀土永磁",
        "theme": "有色",
        "rsi14": 62.5,
        "macd_status": "金叉",
        "ma_status": "多头排列",
        "backtest": [
            {"period_days": 30, "total_return": 0.052, "max_drawdown": -0.035},
            {"period_days": 60, "total_return": -0.028, "max_drawdown": -0.085},
            {"period_days": 90, "total_return": 0.125, "max_drawdown": -0.092}
        ]
    })
    
    tracker.save_prediction("516150", datetime.now().strftime('%Y-%m-%d'), {
        "period_days": 1,
        "ensemble": {"return_1d": 0.005, "trend": "up", "confidence": 0.62},
        "individual": {
            "lightgbm": {"return_1d": 0.004},
            "xgboost": {"return_1d": 0.006},
            "random_forest": {"return_1d": 0.003},
            "arima": {"return_1d": 0.005},
            "lstm": {"return_1d": 0.007}
        }
    })
    
    report = tracker.generate_validation_report()
    print(report)
