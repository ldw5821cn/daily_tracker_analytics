#!/usr/bin/env python3
"""
LLM 预测回测验证器

功能：
1. 每天存预测记录（信号、置信度、关键位、推理）
2. 预测到期后（1d/3d/5d/10d），取实际价格验证
3. 统计准确率：按标的、板块、置信度、周期分层

与 VectorBT 的关系：
  VectorBT → 策略信号的历史收益表现（有没有赚到钱）
  本模块 → LLM方向预测的准确率（有没有说对方向）
  两者互补，共同构成回测体系

用法：
  # 存预测
  python llm_prediction_backtest.py --save predictions.json
  
  # 到期验证
  python llm_prediction_backtest.py --validate --data /tmp/stock_cron_data.json
  
  # 查看统计
  python llm_prediction_backtest.py --stats
"""

import sys
import os
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

DB_PATH = os.path.expanduser(
    '~/github/daily_tracker_analytics/multi_agent/data/llm_predictions.db'
)


def _get_conn() -> sqlite3.Connection:
    """获取 DB 连接"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            name TEXT,
            sector TEXT,
            signal TEXT NOT NULL,
            confidence REAL,
            horizon_1d TEXT,
            horizon_3d TEXT,
            horizon_5d TEXT,
            horizon_10d TEXT,
            key_support REAL,
            key_resistance REAL,
            reasoning TEXT,
            current_price REAL,
            pred_date TEXT NOT NULL,
            pred_time TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS validation_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prediction_id INTEGER NOT NULL,
            horizon INTEGER NOT NULL,
            actual_price REAL,
            actual_return REAL,
            direction_correct INTEGER,
            confidence REAL,
            validated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(prediction_id) REFERENCES predictions(id)
        );
    """)
    return conn


def save_prediction(ticker: str, name: str, sector: str,
                    signal: str, confidence: float,
                    current_price: float,
                    horizons: Dict[str, str],
                    key_levels: Dict = None,
                    reasoning: str = "",
                    bull_case: Dict = None,
                    bear_case: Dict = None,
                    risk_assessment: Dict = None) -> int:
    """保存一条 LLM 预测记录"""
    conn = _get_conn()
    try:
        cur = conn.execute("""
            INSERT INTO predictions 
            (ticker, name, sector, signal, confidence,
             horizon_1d, horizon_3d, horizon_5d, horizon_10d,
             key_support, key_resistance, reasoning,
             current_price, pred_date, pred_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ticker, name, sector, signal, confidence,
            horizons.get('1d', ''), horizons.get('3d', ''),
            horizons.get('5d', ''), horizons.get('10d', ''),
            key_levels.get('support') if key_levels else None,
            key_levels.get('resistance') if key_levels else None,
            reasoning[:500], current_price,
            datetime.now().strftime('%Y-%m-%d'),
            datetime.now().strftime('%H:%M')
        ))
        pred_id = cur.lastrowid
        conn.commit()
        return pred_id
    finally:
        conn.close()


def validate_expired_predictions(market_data: Dict[str, float]) -> Dict:
    """
    验证所有已到期但未验证的预测
    
    Args:
        market_data: {ticker: current_price} 实时价格映射
    
    Returns:
        dict: 本次验证统计
    """
    conn = _get_conn()
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        
        # 找所有未验证的预测（pred_date < today 且不在 validation_results 里）
        rows = conn.execute("""
            SELECT p.id, p.ticker, p.name, p.sector,
                   p.signal, p.confidence, p.current_price as pred_price,
                   p.pred_date, p.horizon_1d, p.horizon_3d,
                   p.horizon_5d, p.horizon_10d
            FROM predictions p
            WHERE p.pred_date < ?
            AND p.id NOT IN (
                SELECT DISTINCT prediction_id FROM validation_results
            )
            ORDER BY p.pred_date DESC
        """, (today,)).fetchall()
        
        if not rows:
            return {"validated": 0, "message": "无到期待验证预测"}
        
        stats = {"validated": 0, "correct": 0, "wrong": 0,
                 "by_horizon": {1: {"total": 0, "correct": 0},
                                3: {"total": 0, "correct": 0},
                                5: {"total": 0, "correct": 0},
                                10: {"total": 0, "correct": 0}}}
        
        for row in rows:
            ticker = row['ticker']
            current = market_data.get(ticker)
            if current is None:
                continue
            
            pred_price = row['pred_price'] or current
            ret = (current - pred_price) / pred_price if pred_price > 0 else 0
            
            horizon_map = {1: row['horizon_1d'], 3: row['horizon_3d'],
                           5: row['horizon_5d'], 10: row['horizon_10d']}
            
            for horizon in [1, 3, 5, 10]:
                h_signal = horizon_map.get(horizon, '')
                if not h_signal:
                    continue
                
                # 判断方向是否正确
                direction_correct = _check_direction(h_signal, ret)
                
                conn.execute("""
                    INSERT INTO validation_results
                    (prediction_id, horizon, actual_price, actual_return,
                     direction_correct, confidence)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (row['id'], horizon, current, round(ret, 4),
                      int(direction_correct), row['confidence']))
                
                stats["validated"] += 1
                stats["by_horizon"][horizon]["total"] += 1
                if direction_correct:
                    stats["correct"] += 1
                    stats["by_horizon"][horizon]["correct"] += 1
                else:
                    stats["wrong"] += 1
        
        conn.commit()
        
        if stats["validated"] > 0:
            stats["accuracy"] = round(stats["correct"] / stats["validated"] * 100, 1)
        else:
            stats["accuracy"] = 0
            
        return stats
    finally:
        conn.close()


def _check_direction(signal: str, actual_return: float) -> bool:
    """判断方向预测是否正确"""
    signal = signal.lower()
    threshold = 0.005  # 0.5% 视为有效变动
    
    if signal == '看涨':
        return actual_return > threshold
    elif signal == '看跌':
        return actual_return < -threshold
    elif signal == '震荡':
        return -threshold <= actual_return <= threshold
    return False


def get_accuracy_report() -> Dict:
    """生成完整准确率统计报告"""
    conn = _get_conn()
    try:
        report = {
            "total_predictions": 0,
            "total_validations": 0,
            "overall_accuracy": 0,
            "by_horizon": {},
            "by_sector": {},
            "by_confidence": {"high": {}, "medium": {}, "low": {}},
            "by_signal": {"bullish": {}, "bearish": {}, "neutral": {}},
        }
        
        # 总统计
        row = conn.execute("""
            SELECT COUNT(*) as total,
                   SUM(direction_correct) as correct
            FROM validation_results
        """).fetchone()
        if row and row['total']:
            report['total_validations'] = row['total']
            report['overall_accuracy'] = round(
                row['correct'] / row['total'] * 100, 1) if row['total'] > 0 else 0
        
        # 按周期
        for h in [1, 3, 5, 10]:
            rows = conn.execute("""
                SELECT COUNT(*) as total, SUM(direction_correct) as correct
                FROM validation_results WHERE horizon = ?
            """, (h,)).fetchone()
            if rows and rows['total']:
                report['by_horizon'][f"{h}d"] = {
                    "total": rows['total'],
                    "correct": int(rows['correct']),
                    "accuracy": round(rows['correct'] / rows['total'] * 100, 1)
                }
        
        # 按板块
        rows = conn.execute("""
            SELECT p.sector, COUNT(*) as total, SUM(v.direction_correct) as correct
            FROM validation_results v
            JOIN predictions p ON v.prediction_id = p.id
            WHERE p.sector != ''
            GROUP BY p.sector
            ORDER BY total DESC
        """).fetchall()
        for r in rows:
            report['by_sector'][r['sector']] = {
                "total": r['total'],
                "correct": int(r['correct']),
                "accuracy": round(r['correct'] / r['total'] * 100, 1)
            }
        
        # 按置信度分层
        rows = conn.execute("""
            SELECT 
                CASE 
                    WHEN v.confidence >= 0.7 THEN 'high'
                    WHEN v.confidence >= 0.5 THEN 'medium'
                    ELSE 'low'
                END as conf_level,
                COUNT(*) as total,
                SUM(v.direction_correct) as correct,
                AVG(v.confidence) as avg_conf
            FROM validation_results v
            GROUP BY conf_level
        """).fetchall()
        for r in rows:
            report['by_confidence'][r['conf_level']] = {
                "total": r['total'],
                "correct": int(r['correct']),
                "accuracy": round(r['correct'] / r['total'] * 100, 1),
                "avg_confidence": round(r['avg_conf'], 2)
            }
        
        # 按信号类型
        rows = conn.execute("""
            SELECT p.signal, COUNT(*) as total,
                   SUM(v.direction_correct) as correct
            FROM validation_results v
            JOIN predictions p ON v.prediction_id = p.id
            GROUP BY p.signal
        """).fetchall()
        for r in rows:
            report['by_signal'][r['signal']] = {
                "total": r['total'],
                "correct": int(r['correct']),
                "accuracy": round(r['correct'] / r['total'] * 100, 1)
            }
        
        # 近期趋势（近7天准确率）
        week_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        row = conn.execute("""
            SELECT COUNT(*) as total, SUM(v.direction_correct) as correct
            FROM validation_results v
            JOIN predictions p ON v.prediction_id = p.id
            WHERE p.pred_date >= ?
        """, (week_ago,)).fetchone()
        if row and row['total']:
            report['last_7days_accuracy'] = round(
                row['correct'] / row['total'] * 100, 1)
        
        return report
    finally:
        conn.close()


def save_batch_predictions(predictions: List[Dict]) -> Dict:
    """批量保存 LLM 预测"""
    stats = {"saved": 0, "errors": 0}
    for pred in predictions:
        try:
            save_prediction(
                ticker=pred.get('ticker', ''),
                name=pred.get('name', ''),
                sector=pred.get('sector', pred.get('theme', '')),
                signal=pred.get('signal', 'neutral'),
                confidence=pred.get('confidence', 0.5),
                current_price=pred.get('current_price', 0),
                horizons={
                    '1d': pred.get('horizon_1d', ''),
                    '3d': pred.get('horizon_3d', ''),
                    '5d': pred.get('horizon_5d', ''),
                    '10d': pred.get('horizon_10d', ''),
                },
                key_levels=pred.get('key_levels', {}),
                reasoning=pred.get('reasoning', '')
            )
            stats['saved'] += 1
        except Exception as e:
            stats['errors'] += 1
            print(f"  ❌ 保存失败 {pred.get('ticker', '?')}: {e}")
    return stats


# ============================================================
# CLI 入口
# ============================================================
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='LLM 预测回测验证器')
    parser.add_argument('--save', type=str, help='保存预测结果JSON文件')
    parser.add_argument('--validate', action='store_true', help='验证到期预测')
    parser.add_argument('--data', type=str, help='当前行情数据JSON（用于验证）')
    parser.add_argument('--stats', action='store_true', help='查看准确率统计')
    
    args = parser.parse_args()
    
    if args.save:
        with open(args.save, 'r', encoding='utf-8') as f:
            predictions = json.load(f)
        stats = save_batch_predictions(predictions)
        print(f"✅ 保存 {stats['saved']} 条, 失败 {stats['errors']} 条")
    
    if args.validate:
        if args.data:
            with open(args.data, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # 构建 {ticker: current_price} 映射
            market_data = {
                d['ticker']: d['current_price']
                for d in data if 'error' not in d
            }
        else:
            # 尝试从现有数据源获取
            market_data = {}
        
        result = validate_expired_predictions(market_data)
        print(f"✅ 验证 {result.get('validated', 0)} 条预测")
        print(f"   正确: {result.get('correct', 0)} | 错误: {result.get('wrong', 0)}")
        print(f"   准确率: {result.get('accuracy', 0)}%")
    
    if args.stats:
        report = get_accuracy_report()
        print(f"\n{'='*50}")
        print(f"  LLM 预测准确率统计")
        print(f"{'='*50}")
        print(f"  总验证数:  {report['total_validations']}")
        print(f"  整体准确率: {report['overall_accuracy']}%")
        print()
        
        print(f"  --- 按周期 ---")
        for h, data in report.get('by_horizon', {}).items():
            print(f"  {h:4s}: {data['total']}次 正确{data['correct']} 准确率{data['accuracy']}%")
        
        print(f"\n  --- 按信号类型 ---")
        for sig, data in report.get('by_signal', {}).items():
            print(f"  {sig:10s}: {data['total']}次 准确率{data['accuracy']}%")
        
        print(f"\n  --- 按置信度分层 ---")
        for level, data in report.get('by_confidence', {}).items():
            print(f"  {level:8s}: {data['total']}次 准确率{data['accuracy']}% (均置信度{data['avg_confidence']})")
        
        print(f"\n  --- 按板块 (Top 5) ---")
        for sector, data in sorted(report.get('by_sector', {}).items(),
                                    key=lambda x: x[1]['total'], reverse=True)[:5]:
            print(f"  {sector:12s}: {data['total']}次 准确率{data['accuracy']}%")
        
        if 'last_7days_accuracy' in report:
            print(f"\n  📈 近7天准确率: {report['last_7days_accuracy']}%")
