#!/usr/bin/env python3
"""夜间闲时多 LLM 辩论精修。

逻辑：
1. 读取当日已生成的 agentic_predictions
2. 按 weighted_score 偏离中性最远 / 仓位最高 / 重点板块 排序，取 Top N
3. 对这些标的单独启用 AGENTIC_USE_LLM_DEBATE=true 重新 predict_one
4. 更新 llm_predictions.db 的对应记录（保留 llm_debate_detail）

设计为夜间闲时（23:00-02:00）运行，不影响盘后常规预测时效。
"""
import os
import sys
import json
import sqlite3
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, MULTI_AGENT)

os.environ['AGENTIC_USE_LLM_DEBATE'] = 'true'

from analysts.agentic_predictor import predict_one
from core.db import get_predictions_conn

DB_PATH = os.path.join(MULTI_AGENT, 'data', 'llm_predictions.db')

# 夜间精修标的数量上限
NIGHTLY_LIMIT = int(os.environ.get('NIGHTLY_DEBATE_LIMIT', '30'))
# 重点板块（可配置）
FOCUS_SECTORS = os.environ.get('NIGHTLY_FOCUS_SECTORS', '半导体,通信设备,银行,电力,煤炭开采加工,油气开采及服务').split(',')


def get_today_predictions(pred_date: str):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, ticker, name, sector, category, weighted_score, signal, confidence, position_pct, "
        "component_scores, reasoning "
        "FROM agentic_predictions WHERE pred_date=? ORDER BY abs(weighted_score - 50) DESC",
        (pred_date,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def select_targets(predictions: list, limit: int = NIGHTLY_LIMIT) -> list:
    """选择需要精修的目标：
    - 优先选信号极端的（偏离 50 最远）
    - 优先选重点板块的
    - 优先选仓位高的
    """
    scored = []
    for p in predictions:
        sector_bonus = 5 if any(s in (p.get('sector') or '') for s in FOCUS_SECTORS) else 0
        score = abs((p.get('weighted_score') or 50) - 50)
        score += sector_bonus
        score += (p.get('position_pct') or 0) * 100
        scored.append((score, p))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [x[1] for x in scored[:limit]]


def update_prediction(pred_date: str, ticker: str, new_result: dict):
    """用多 LLM 辩论结果更新数据库中的单条记录。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """UPDATE agentic_predictions SET
                signal=?, confidence=?, weighted_score=?, position_pct=?,
                target_price=?, stop_loss=?, key_support=?, key_resistance=?,
                reasoning=?, bull_points=?, bear_points=?, component_scores=?
            WHERE pred_date=? AND ticker=?""",
            (
                new_result.get('signal'),
                new_result.get('confidence'),
                new_result.get('weighted_score'),
                new_result.get('position_pct'),
                new_result.get('target_price'),
                new_result.get('stop_loss'),
                new_result.get('key_support'),
                new_result.get('key_resistance'),
                new_result.get('reasoning'),
                json.dumps(new_result.get('bull_points', []), ensure_ascii=False),
                json.dumps(new_result.get('bear_points', []), ensure_ascii=False),
                json.dumps(new_result.get('component_scores', {}), ensure_ascii=False),
                pred_date,
                ticker,
            )
        )
        conn.commit()
    finally:
        conn.close()


def main():
    pred_date = os.environ.get('NIGHTLY_DEBATE_DATE') or (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    print(f"🌙 夜间多 LLM 辩论精修: {pred_date}, limit={NIGHTLY_LIMIT}")

    predictions = get_today_predictions(pred_date)
    if not predictions:
        print("  无当日预测，退出")
        return
    print(f"  当日共 {len(predictions)} 条预测")

    targets = select_targets(predictions)
    print(f"  精选 {len(targets)} 个标的重新辩论")

    updated = 0
    skipped = 0
    for item in targets:
        ticker = item['ticker']
        name = item.get('name', '')
        sector = item.get('sector', '')
        category = item.get('category', '个股')
        print(f"\n  🔄 {ticker} {name} 启用多 LLM 辩论...")
        try:
            new_result = predict_one(
                ticker=ticker,
                name=name,
                sector=sector,
                category=category,
                fast=False,
                ultra=False,
            )
            if 'error' in new_result:
                print(f"    ❌ {ticker}: {new_result['error']}")
                skipped += 1
                continue
            update_prediction(pred_date, ticker, new_result)
            print(f"    ✅ {ticker}: {new_result.get('signal')} 评分{new_result.get('weighted_score')} "
                  f"(原{item.get('weighted_score')})")
            updated += 1
        except Exception as e:
            print(f"    ❌ {ticker}: 异常 {e}")
            skipped += 1

    print(f"\n🌙 完成: 更新 {updated} 条, 跳过 {skipped} 条")


if __name__ == '__main__':
    main()
