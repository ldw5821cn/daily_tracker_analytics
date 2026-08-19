#!/usr/bin/env python3
"""A/B 测试：原规则辩论 vs TradingAgents 风格 LLM 辩论（仅测 debate 引擎）。
输出对比 HTML 到 docs/ab_debate_test.html
"""
import json
import os
import sys
import time
from datetime import datetime
from collections import Counter

ROOT = '/home/liudawei/github/daily_tracker_analytics'
for _p in [ROOT, f'{ROOT}/multi_agent']:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.debate_engine import DebateEngine
from core.debate_engine_ta import ta_style_debate

# 模拟 7 个不同偏态的标的输入
TEST_CASES = [
    {
        'ticker': '000001', 'name': '平安银行', 'category': '个股',
        'technical': {'score': 52, 'rating': '中性偏多'},
        'fundamental': {'score': 58, 'rating': '中性偏多', 'fundamentals': {'debt_to_equity': 120, 'profit_margins': 25}},
        'news': {'sentiment_score': 0.12},
    },
    {
        'ticker': '600519', 'name': '贵州茅台', 'category': '个股',
        'technical': {'score': 42, 'rating': '中性偏空'},
        'fundamental': {'score': 72, 'rating': '偏多', 'fundamentals': {'debt_to_equity': 15, 'profit_margins': 52}},
        'news': {'sentiment_score': -0.05},
    },
    {
        'ticker': '000333', 'name': '美的集团', 'category': '个股',
        'technical': {'score': 38, 'rating': '偏空'},
        'fundamental': {'score': 55, 'rating': '中性'},
        'news': {'sentiment_score': -0.18},
    },
    {
        'ticker': '601398', 'name': '工商银行', 'category': '个股',
        'technical': {'score': 60, 'rating': '偏多'},
        'fundamental': {'score': 64, 'rating': '偏多', 'fundamentals': {'debt_to_equity': 90, 'profit_margins': 42}},
        'news': {'sentiment_score': 0.08},
    },
    {
        'ticker': '510300', 'name': '沪深300ETF', 'category': 'ETF',
        'technical': {'score': 49, 'rating': '中性'},
        'fundamental': {'score': 50, 'rating': '中性'},
        'news': {'sentiment_score': 0.0},
    },
    {
        'ticker': '159915', 'name': '创业板ETF', 'category': 'ETF',
        'technical': {'score': 35, 'rating': '偏空'},
        'fundamental': {'score': 45, 'rating': '中性偏空'},
        'news': {'sentiment_score': -0.22},
    },
    {
        'ticker': '512100', 'name': '中证1000ETF', 'category': 'ETF',
        'technical': {'score': 63, 'rating': '偏多'},
        'fundamental': {'score': 54, 'rating': '中性偏多'},
        'news': {'sentiment_score': 0.15},
    },
]


def signal_from_net(net: float) -> str:
    if net >= 4:
        return '强烈看多'
    if net > 0:
        return '看多'
    if net <= -4:
        return '强烈看空'
    if net < 0:
        return '看空'
    return '中性'


def run_rule(case: dict) -> dict:
    t0 = time.time()
    bull = DebateEngine.bull_argument(case['technical'], case['fundamental'], case['news'])
    bear = DebateEngine.bear_argument(case['technical'], case['fundamental'], case['news'])
    net = bull['score'] - bear['score']
    return {
        'ticker': case['ticker'], 'name': case['name'], 'category': case['category'],
        'bull_score': bull['score'], 'bear_score': bear['score'],
        'net': round(net, 2), 'signal': signal_from_net(net),
        'points': len(bull['points']) + len(bear['points']),
        'time': round(time.time() - t0, 3),
    }


def run_ta(case: dict) -> dict:
    t0 = time.time()
    bull, bear, verdict = ta_style_debate(
        case['technical'], case['fundamental'], case['news'],
        ticker=case['ticker'], name=case['name'], category=case['category'],
    )
    net = verdict.get('net_score', bull['score'] - bear['score'])
    return {
        'ticker': case['ticker'], 'name': case['name'], 'category': case['category'],
        'bull_score': bull['score'], 'bear_score': bear['score'],
        'net': round(net, 2), 'signal': signal_from_net(net),
        'rating': verdict.get('rating', '中性'),
        'points': len(bull.get('points', [])) + len(bear.get('points', [])),
        'time': round(time.time() - t0, 3),
        'llm_raw': bull.get('llm_raw', '')[:200],
    }


def summarize(results: list) -> dict:
    signals = [r['signal'] for r in results]
    c = Counter(signals)
    nets = [r['net'] for r in results]
    return {
        'count': len(results),
        'signals': dict(c),
        'avg_net': round(sum(nets) / len(nets), 2),
        'std_net': round((sum((x - sum(nets) / len(nets)) ** 2 for x in nets) / len(nets)) ** 0.5, 2),
        'avg_time': round(sum(r['time'] for r in results) / len(results), 3),
    }


def html_report(rule_results, ta_results) -> str:
    rule_sum = summarize(rule_results)
    ta_sum = summarize(ta_results)
    up_color, down_color = '#e74c3c', '#2ecc71'

    rows = []
    for r, t in zip(rule_results, ta_results):
        changed = '是' if r['signal'] != t['signal'] else '否'
        rows.append({**r, **{f'ta_{k}': v for k, v in t.items()}})
        rows[-1]['changed'] = changed

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>A/B 测试：规则辩论 vs TradingAgents LLM 辩论</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; margin: 24px; background: #f7f9fb; color: #333; }}
h1 {{ font-size: 22px; margin-bottom: 6px; }}
.meta {{ color: #666; font-size: 13px; margin-bottom: 20px; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 24px; }}
.card {{ background: #fff; border-radius: 12px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
.card h2 {{ font-size: 15px; margin: 0 0 10px; color: #2c3e50; }}
.card .stat {{ font-size: 22px; font-weight: 600; color: #1a252f; }}
.card .label {{ font-size: 12px; color: #7f8c8d; margin-top: 4px; }}
table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.08); margin-bottom: 24px; }}
th, td {{ padding: 10px 12px; text-align: left; font-size: 13px; border-bottom: 1px solid #eef2f5; }}
th {{ background: #eef2f5; color: #2c3e50; font-weight: 600; }}
.bull {{ color: {up_color}; font-weight: 600; }}
.bear {{ color: {down_color}; font-weight: 600; }}
.neutral {{ color: #7f8c8d; }}
tr:hover {{ background: #f8fafc; }}
pre {{ background: #f4f6f8; padding: 12px; border-radius: 8px; font-size: 12px; overflow-x: auto; }}
.desc {{ font-size: 13px; color: #555; line-height: 1.7; }}
</style>
</head>
<body>
<h1>A/B 测试：规则辩论 vs TradingAgents LLM 辩论</h1>
<div class="meta">生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · 样本数：{len(rows)} · 仅测试 debate 引擎，未跑完整预测流水线</div>
<div class="cards">
  <div class="card"><h2>规则辩论</h2><div class="stat">{rule_sum['avg_net']}</div><div class="label">平均净得分 (bull-bear)</div></div>
  <div class="card"><h2>TradingAgents LLM</h2><div class="stat">{ta_sum['avg_net']}</div><div class="label">平均净得分</div></div>
  <div class="card"><h2>规则信号离散度</h2><div class="stat">{rule_sum['std_net']}</div><div class="label">净得分标准差</div></div>
  <div class="card"><h2>LLM 信号离散度</h2><div class="stat">{ta_sum['std_net']}</div><div class="label">净得分标准差</div></div>
  <div class="card"><h2>规则耗时</h2><div class="stat">{rule_sum['avg_time']}s</div><div class="label">平均每个标的</div></div>
  <div class="card"><h2>LLM 耗时</h2><div class="stat">{ta_sum['avg_time']}s</div><div class="label">平均每个标的</div></div>
</div>
<table>
<tr><th>标的</th><th>规则 bull</th><th>规则 bear</th><th>规则信号</th><th>规则净得分</th><th>LLM bull</th><th>LLM bear</th><th>LLM信号</th><th>LLM净得分</th><th>方向变化</th></tr>
"""

    def sig_class(s):
        if s in ('强烈看多', '看多'): return 'bull'
        if s in ('强烈看空', '看空'): return 'bear'
        return 'neutral'

    for row in rows:
        html += f"""<tr>
  <td>{row['name']}<br><span style="color:#7f8c8d">{row['ticker']}</span></td>
  <td>{row['bull_score']}</td>
  <td>{row['bear_score']}</td>
  <td class="{sig_class(row['signal'])}">{row['signal']}</td>
  <td>{row['net']}</td>
  <td>{row['ta_bull_score']}</td>
  <td>{row['ta_bear_score']}</td>
  <td class="{sig_class(row['ta_signal'])}">{row['ta_signal']}</td>
  <td>{row['ta_net']}</td>
  <td>{'是' if row['signal'] != row['ta_signal'] else '否'}</td>
</tr>\n"""
    html += """</table>
<h2>信号分布对比</h2>
<div class="cards">
"""
    html += f"""<div class="card"><h2>规则辩论</h2><pre>{json.dumps(rule_sum['signals'], ensure_ascii=False, indent=2)}</pre></div>"""
    html += f"""<div class="card"><h2>TradingAgents LLM</h2><pre>{json.dumps(ta_sum['signals'], ensure_ascii=False, indent=2)}</pre></div>"""
    html += """</div>
<h2>说明</h2>
<p class="desc">
<b>为什么做 A/B：</b> 原规则辩论依赖硬编码阈值，容易出现单边信号压制；TradingAgents 风格让 LLM 在单次结构化调用里同时生成多空论据与净得分，期望信号分布更真实。<br><br>
<b>测试方法：</b> 用 7 组不同偏态的模拟报告输入，分别跑规则辩论与 LLM 辩论，对比 bull/bear 得分、净得分与信号分布。<br><br>
<b>fallback 机制：</b> 当 LLM 输出无法解析或 API 失败时，自动回退到规则辩论，确保流水线不中断。<br><br>
<b>注意：</b> 本页面仅用于引擎级 A/B 验证，未接入真实行情与未来收益标签，不构成投资建议。
</p>
</body>
</html>"""
    return html


def main():
    print("A/B 测试开始（仅 debate 引擎）...")
    rule_results = [run_rule(c) for c in TEST_CASES]
    print("规则辩论完成，开始 LLM 辩论...")
    ta_results = [run_ta(c) for c in TEST_CASES]

    out_path = f"{ROOT}/docs/ab_debate_test.html"
    html = html_report(rule_results, ta_results)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"已生成 A/B 报告: {out_path}")
    print(json.dumps({'rule': summarize(rule_results), 'ta': summarize(ta_results)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
