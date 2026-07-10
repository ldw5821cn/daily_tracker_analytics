#!/usr/bin/env python3
"""
多 Agent 统一日报生成 + 微信推送脚本
读取 agentic_predictions 表，生成 Markdown 日报并 stdout 输出供 Hermes 微信推送。
"""
import sys
import os
import json
import sqlite3
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
DB_PATH = os.path.join(MULTI_AGENT, 'data', 'llm_predictions.db')


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def load_predictions(pred_date=None):
    if pred_date is None:
        pred_date = datetime.now().strftime('%Y-%m-%d')
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM agentic_predictions WHERE pred_date=? ORDER BY weighted_score DESC",
            (pred_date,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _emoji(signal):
    return {'bullish': '🔥', 'bearish': '❄️', 'neutral': '➖'}.get(signal, '➖')


def _signal_cn(signal):
    return {'bullish': '看多', 'bearish': '看空', 'neutral': '中性'}.get(signal, signal)


def build_markdown(pred_date=None, top_n=10):
    if pred_date is None:
        pred_date = datetime.now().strftime('%Y-%m-%d')
    preds = load_predictions(pred_date)
    if not preds:
        return f"📊 {pred_date} 暂无预测数据，请检查 multi_agent/data/llm_predictions.db"

    # 按 category 分组
    groups = {}
    for p in preds:
        groups.setdefault(p.get('category') or '其他', []).append(p)

    lines = [f"📊 多 Agent 统一预测日报 ({pred_date})", "", f"共 {len(preds)} 只标的 | 技术面+多空辩论综合评分 | 低风险优先", ""]

    # 总体统计
    bullish = [p for p in preds if p['signal'] == 'bullish']
    bearish = [p for p in preds if p['signal'] == 'bearish']
    neutral = [p for p in preds if p['signal'] == 'neutral']
    lines.append(f"📈 看多 {len(bullish)} | 📉 看空 {len(bearish)} | ➖ 中性 {len(neutral)}")
    lines.append("")

    # Top 推荐（看多且评分最高）
    top_bull = sorted([p for p in preds if p['signal'] == 'bullish'], key=lambda x: x['weighted_score'], reverse=True)[:5]
    if top_bull:
        lines.append("🏆 今日重点推荐（看多）")
        for p in top_bull:
            lines.append(f"{_emoji(p['signal'])} **{p['name']} ({p['ticker']})** 评分{p['weighted_score']} 置信度{p['confidence']:.0%} 仓位{p['position_pct']*100:.1f}%")
            lines.append(f"   目标价 {p['target_price']} | 止损 {p['stop_loss']} | 1日/3日/5日 {p['horizon_1d']}/{p['horizon_3d']}/{p['horizon_5d']}")
        lines.append("")

    # 分类明细
    for cat in ['ETF', '个股', '期货']:
        if cat not in groups:
            continue
        items = sorted(groups[cat], key=lambda x: x['weighted_score'], reverse=True)
        lines.append(f"### {cat} ({len(items)}只)")
        lines.append("| 名称 | 代码 | 信号 | 评分 | 置信 | 1日 | 3日 | 5日 | 目标/止损 | 仓位 |")
        lines.append("|------|------|------|------|------|-----|-----|-----|-----------|------|")
        for p in items[:top_n]:
            name = (p['name'] or p['ticker'])[:6]
            lines.append(
                f"| {name} | {p['ticker']} | {_signal_cn(p['signal'])} | {p['weighted_score']} | "
                f"{p['confidence']:.0%} | {p['horizon_1d']} | {p['horizon_3d']} | {p['horizon_5d']} | "
                f"{p['target_price']}/{p['stop_loss']} | {p['position_pct']*100:.1f}% |"
            )
        lines.append("")

    # 风险提示
    lines.append("⚠️ 风险提示")
    lines.append("- 以上预测基于技术面与多空辩论，fast 模式已跳过基本面/新闻；个股建议结合基本面二次确认。")
    lines.append("- 仓位建议为单标的占总资金比例，实际需结合整体仓位与板块分散。")
    lines.append("- 期货自带杠杆，请严格按止损执行。")

    return "\n".join(lines)


def main():
    # 支持 --date YYYY-MM-DD
    pred_date = None
    if len(sys.argv) >= 3 and sys.argv[1] == '--date':
        pred_date = sys.argv[2]
    elif len(sys.argv) >= 2 and sys.argv[1].startswith('20'):
        pred_date = sys.argv[1]

    # 如果没有今天数据，取最新日期
    conn = _get_conn()
    latest = conn.execute("SELECT MAX(pred_date) as d FROM agentic_predictions").fetchone()[0]
    conn.close()
    if pred_date is None:
        pred_date = latest or datetime.now().strftime('%Y-%m-%d')

    md = build_markdown(pred_date)
    print(md)


if __name__ == '__main__':
    main()
