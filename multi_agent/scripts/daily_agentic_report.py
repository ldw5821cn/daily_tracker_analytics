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
    return {'bullish': '🔥', 'bearish': '❄️', 'neutral': '➖', '看多': '🔥', '看空': '❄️', '中性': '➖'}.get(signal, '➖')


def _signal_cn(signal):
    return {'bullish': '看多', 'bearish': '看空', 'neutral': '中性', '看多': '看多', '看空': '看空', '中性': '中性'}.get(signal, signal)


def build_markdown(pred_date=None, top_n=3):
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

    bullish = [p for p in preds if p['signal'] in ('bullish', '看多')]
    bearish = [p for p in preds if p['signal'] in ('bearish', '看空')]
    neutral = [p for p in preds if p['signal'] in ('neutral', '中性', 'weak_neutral')]

    lines = [
        f"📊 **多Agent量化日报** ({pred_date})",
        f"共 {len(preds)} 只 | 看多 {len(bullish)} | 看空 {len(bearish)} | 中性 {len(neutral)}",
        "",
    ]

    # 重点推荐：按回测综合分排序（只看 top3）
    top_recs = [p for p in preds if p.get('bt_score', 0) > 0][:3]
    if top_recs:
        lines.append("🏆 重点推荐")
        for p in top_recs:
            lines.append(f"{_emoji(p['signal'])} {p['name']}({p['ticker']}) 60日{p['bt_return_60d']:+.1f}% 分{p['bt_score']:.1f}")
        lines.append("")

    # 分类 Top3：按回测综合分排序
    for cat in ['ETF', '个股', '期货']:
        if cat not in groups:
            continue
        items = sorted(groups[cat], key=lambda x: x['bt_score'], reverse=True)[:top_n]
        lines.append(f"📂 {cat} Top{top_n}")
        for p in items:
            sig = _emoji(p['signal'])
            lines.append(f"{sig} {p['name']}({p['ticker']}): {p['bt_return_60d']:+.1f}%/{p['bt_max_dd_60d']:.1f}%/{p['bt_sharpe_60d']:.2f}")
        lines.append("")

    lines.append("⚠️ 排序基于近60天回测综合分，非预测收益；期现严格止损。")

    # 加入 LLM 信号回测评估
    bt = _load_backtest_summary()
    if bt and 'by_category' in bt:
        lines.append("")
        lines.append("📈 信号回测")
        for cat in ['ETF', '个股', '期货']:
            groups = bt.get('by_category', {}).get(cat, {})
            bullish = groups.get('bullish', {})
            bearish = groups.get('bearish', {})
            if not bullish and not bearish:
                continue
            long_s = f"多{bullish.get('avg_return_60d', 0):+.1f}%/{bullish.get('win_rate_60d', 0):.0f}%" if bullish else "多无"
            short_s = f"空{bearish.get('avg_return_60d', 0):+.1f}%/{bearish.get('win_rate_60d', 0):.0f}%" if bearish else "空无"
            lines.append(f"{cat}: {long_s} | {short_s}")

    # 加入目标权重
    weights = _load_weights_summary()
    if weights and 'targets' in weights:
        lines.append("")
        lines.append(f"💼 目标权重（总敞口{weights['total_exposure']:.0%} 净敞口{weights['net_exposure']:+.0%}）")
        longs = [t for t in weights['targets'] if t['target_weight'] > 0][:1]
        shorts = [t for t in weights['targets'] if t['target_weight'] < 0][:1]
        if longs:
            long_line = " ".join([f"{t['ticker']}{t['target_weight']:+.1%}" for t in longs])
            lines.append(f"🔥 多: {long_line}")
        if shorts:
            short_line = " ".join([f"{t['ticker']}{t['target_weight']:+.1%}" for t in shorts])
            lines.append(f"❄️ 空: {short_line}")

    # 因子组合滚动回测
    try:
        import json as _json
        with open('multi_agent/data/vectorbt_portfolio_backtest.json', 'r', encoding='utf-8') as f:
            bt = _json.load(f)
        if 'error' not in bt:
            lines.append("")
            lines.append("📊 VectorBT 组合回测")
            scenarios = bt.get('scenarios', {})
            # 优先展示带成本的只做多+风控策略
            preferred = scenarios.get('weekly_long_only_risk_with_cost')
            if preferred and 'error' not in preferred:
                lines.append(f"  推荐(带成本): 年化{preferred['annualized_return']:+.1f}% 回撤{preferred['max_drawdown']:.1f}% 夏普{preferred['sharpe_ratio']}")
            for name, v in scenarios.items():
                if 'error' not in v and name != 'weekly_long_only_risk_with_cost':
                    lines.append(f"  {name}: 年化{v['annualized_return']:+.1f}% 回撤{v['max_drawdown']:.1f}% 夏普{v['sharpe_ratio']}")
    except Exception:
        pass

    # 期货模拟盘持仓
    try:
        from multi_agent.futures_simulator import get_positions_summary
        fs = get_positions_summary()
        if fs['positions']:
            futs = " ".join([f"{p['contract']}{p['direction']}{p['lots']}" for p in fs['positions']])
            lines.append(f"🌾 期货: {futs} 权益{fs['total_asset']:.0f}")
    except Exception:
        pass

    # 股票/ETF 买入清单
    try:
        import multi_agent.scripts.generate_stock_etf_list as gen
        lst = gen.generate_stock_etf_list()
        buys = [i for i in lst['items'] if i['target_amount'] > 0][:2]
        if buys:
            b = " ".join([f"{i['ticker']}{i['target_amount']:.0f}元" for i in buys])
            lines.append(f"📈 买入: {b}")
    except Exception:
        pass

    # 新闻舆情（Top 1 标的）
    try:
        import json as _json
        with open('multi_agent/data/news_sentiment.json', 'r', encoding='utf-8') as f:
            ns = _json.load(f)
        if ns.get('items'):
            it = ns['items'][0]
            emoji = '🟢' if it['sentiment'] == '积极' else '🔴' if it['sentiment'] == '消极' else '⚪'
            title = it['latest_titles'][0] if it['latest_titles'] else '暂无'
            lines.append(f"📰 {it['ticker']}{emoji}{title[:28]}")
    except Exception:
        pass

    # 因子库摘要
    try:
        import json as _json
        with open('multi_agent/data/llm_factors.json', 'r', encoding='utf-8') as f:
            data = _json.load(f)
        factors = data.get('factors', [])
        try:
            with open('multi_agent/data/llm_factors_selected.json', 'r', encoding='utf-8') as f:
                selected_data = _json.load(f)
            selected_factors = selected_data.get('filtered_factors') or selected_data.get('factors', [])
        except Exception:
            selected_factors = []
        if selected_factors:
            tops = selected_factors[:3]
            parts = [f"{f['name']}(可信{f.get('llm_credibility_score','')})" for f in tops]
            lines.append(f"🧬 因子库{len(factors)}个（可信{len(selected_factors)}个）: {' | '.join(parts)}")
        elif factors:
            tops = sorted(factors, key=lambda x: x.get('score', 0), reverse=True)[:3]
            parts = [f"{f['name']}({f['avg_sharpe']}sh/{f['avg_return']:+.0f}%)" for f in tops]
            lines.append(f"🧬 因子({len(factors)}个): {' | '.join(parts)}")
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
