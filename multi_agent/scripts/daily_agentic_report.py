#!/usr/bin/env python3
"""
多 Agent 统一日报生成 + 微信推送脚本
读取 agentic_predictions 表，生成 Markdown 日报并 stdout 输出供 Hermes 微信推送。
"""
import sys
import os
import sqlite3
import json
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, MULTI_AGENT)

from core.backtest_utils import sort_by_backtest
from scripts.run_llm_backtest import OUTPUT_PATH as BACKTEST_JSON
from strategy.portfolio_allocator import allocate, OUTPUT_PATH as WEIGHTS_JSON

DB_PATH = os.path.join(MULTI_AGENT, 'data', 'llm_predictions.db')
PAGES_URL = 'https://ldw5821cn.github.io/daily_tracker_analytics/prediction.html'


def _load_backtest_summary():
    """加载 LLM 信号回测评估结果。"""
    if not os.path.exists(BACKTEST_JSON):
        return {}
    try:
        with open(BACKTEST_JSON, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _load_weights_summary():
    """加载目标权重结果。"""
    if not os.path.exists(WEIGHTS_JSON):
        return {}
    try:
        with open(WEIGHTS_JSON, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


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


def build_markdown(pred_date=None, top_n=5):
    if pred_date is None:
        pred_date = datetime.now().strftime('%Y-%m-%d')
    preds = load_predictions(pred_date)
    if not preds:
        return f"📊 {pred_date} 暂无预测数据，请检查 multi_agent/data/llm_predictions.db"

    # 注入回测指标并排序
    preds = sort_by_backtest(preds)

    groups = {}
    for p in preds:
        groups.setdefault(p.get('category') or '其他', []).append(p)

    bullish = [p for p in preds if p['signal'] == 'bullish']
    bearish = [p for p in preds if p['signal'] == 'bearish']
    neutral = [p for p in preds if p['signal'] == 'neutral']

    lines = [
        f"📊 多 Agent 统一预测日报 ({pred_date})",
        f"共 {len(preds)} 只 | 看多 {len(bullish)} | 看空 {len(bearish)} | 中性 {len(neutral)}",
        "",
    ]

    # 重点推荐：按回测综合分排序（只看 top5）
    top_recs = [p for p in preds if p['bt_score'] > 0][:5]
    if top_recs:
        lines.append("🏆 重点推荐（按回测综合分排序）")
        for p in top_recs:
            lines.append(f"{_emoji(p['signal'])} {p['name']}({p['ticker']}) 60日收益{p['bt_return_60d']:+.1f}% 回撤{p['bt_max_dd_60d']:.1f}% 综合分{p['bt_score']:.1f}")
        lines.append("")

    # 分类 Top5：按回测综合分排序
    for cat in ['ETF', '个股', '期货']:
        if cat not in groups:
            continue
        items = sorted(groups[cat], key=lambda x: x['bt_score'], reverse=True)[:top_n]
        lines.append(f"📂 {cat} Top{top_n}（60日收益/回撤/夏普）")
        for p in items:
            sig = _emoji(p['signal'])
            lines.append(f"{sig} {p['name']}({p['ticker']}): {p['bt_return_60d']:+.1f}% / {p['bt_max_dd_60d']:.1f}% / {p['bt_sharpe_60d']:.2f}")
        lines.append("")

    lines.append("⚠️ 提示：排序基于近60天回测综合分（收益/回撤/夏普），非预测收益；期货严格止损，个股建议结合基本面。")

    # 加入 LLM 信号回测评估
    bt = _load_backtest_summary()
    if bt and 'by_category' in bt:
        lines.append("")
        lines.append("📈 信号回测评估")
        for cat in ['ETF', '个股', '期货']:
            groups = bt.get('by_category', {}).get(cat, {})
            bullish = groups.get('bullish', {})
            bearish = groups.get('bearish', {})
            if not bullish and not bearish:
                continue
            long_s = f"看多{bullish.get('avg_return_60d', 0):+.1f}%/{bullish.get('win_rate_60d', 0):.0f}%胜" if bullish else "看多无"
            short_s = f"看空{bearish.get('avg_return_60d', 0):+.1f}%/{bearish.get('win_rate_60d', 0):.0f}%胜" if bearish else "看空无"
            lines.append(f"{cat}: {long_s} | {short_s}")

    # 加入目标权重
    weights = _load_weights_summary()
    if weights and 'targets' in weights:
        lines.append("")
        lines.append(f"💼 目标权重（总敞口{weights['total_exposure']:.0%} 净敞口{weights['net_exposure']:+.0%}）")
        longs = [t for t in weights['targets'] if t['target_weight'] > 0][:1]
        shorts = [t for t in weights['targets'] if t['target_weight'] < 0][:1]
        if longs:
            long_line = " ".join([f"{t['ticker']}({t['name']}){t['target_weight']:+.1%}" for t in longs])
            lines.append(f"🔥 多: {long_line}")
        if shorts:
            short_line = " ".join([f"{t['ticker']}({t['name']}){t['target_weight']:+.1%}" for t in shorts])
            lines.append(f"❄️ 空: {short_line}")

    # 期货模拟盘持仓
    try:
        from multi_agent.futures_simulator import get_positions_summary
        fs = get_positions_summary()
        if fs['positions']:
            futs = " ".join([f"{p['contract']}{p['direction']}{p['lots']}" for p in fs['positions']])
            lines.append(f"🌾 期货: {futs} 权益{fs['total_asset']:.0f}")
    except Exception:
        pass

    lines.append(f"📱 完整页面：{PAGES_URL}")
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
