#!/usr/bin/env python3
"""
#1 个股LLM报告 — 为每只个股生成结构化分析报告
从watchlist读取个股，用数据层获取行情+技术分析，输出 Markdown 报告到 docs/reports/
"""
import sys, os, json
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'multi_agent'))

REPO_ROOT = os.path.join(os.path.dirname(__file__), '..')
WATCHLIST_PATH = os.path.join(REPO_ROOT, 'multi_agent', 'watchlist.json')
DOCS_DIR = os.path.join(REPO_ROOT, 'docs')
REPORTS_DIR = os.path.join(DOCS_DIR, 'reports')

from core.data_layer import get_stock_data, calc_technical_indicators, multi_period_backtest, get_realtime_price


def analyze_single_stock(ticker, name):
    """对单只股票做轻量级技术分析（不跑完整多Agent）"""
    try:
        df, _ = get_stock_data(ticker)
        if df is None or len(df) < 20:
            return {'ticker': ticker, 'name': name, 'error': '数据不足'}

        df = calc_technical_indicators(df)
        backtest = multi_period_backtest(df)
        rt = get_realtime_price(ticker)
        latest = df.iloc[-1]

        # 信号判定
        rsi = float(latest.get('rsi_14', 50))
        macd_hist = float(latest.get('macd_hist', 0))
        ma5 = float(latest.get('ma5', 0))
        ma20 = float(latest.get('ma20', 0))
        ma60 = float(latest.get('ma60', 0)) if len(df) >= 60 else 0

        if ma5 > ma20 > ma60: ma_trend = '多头排列'
        elif ma5 < ma20 < ma60: ma_trend = '空头排列'
        else: ma_trend = '震荡整理'

        score = 0
        signals = []
        if rsi < 30: score += 20; signals.append('RSI超卖')
        elif rsi > 70: score -= 20; signals.append('RSI超买')
        else: score += 10; signals.append('RSI中性')

        if macd_hist > 0: score += 15; signals.append('MACD多头')
        else: score -= 10; signals.append('MACD空头')

        vol_ratio = float(latest.get('vol_ratio', 1))
        if vol_ratio > 1.5: score += 10; signals.append('放量')
        elif vol_ratio < 0.5: score -= 5; signals.append('缩量')

        if '多头排列' in ma_trend: score += 15
        elif '空头排列' in ma_trend: score -= 15

        return {
            'ticker': ticker, 'name': name,
            'price': round(float(latest['close']), 3),
            'change_pct': round(((float(latest['close']) / float(df.iloc[-2]['close']) - 1) * 100), 2) if len(df) > 1 else 0,
            'rsi': round(rsi, 1),
            'macd': '多头' if macd_hist > 0 else '空头',
            'ma_trend': ma_trend,
            'vol_ratio': round(vol_ratio, 2),
            'score': min(100, max(0, score + 50)),
            'signals': signals,
            'backtest': backtest,
            'high_52w': round(float(df['high'].max()), 2) if len(df) > 60 else 0,
            'low_52w': round(float(df['low'].min()), 2) if len(df) > 60 else 0,
        }
    except Exception as e:
        return {'ticker': ticker, 'name': name, 'error': str(e)}


def generate_stock_report(results, date_str):
    """生成个股综合报告"""
    valid = [r for r in results if 'error' not in r]
    if not valid:
        return "# 📊 个股分析报告\n\n> 暂无数据\n"

    report = f"""# 📊 个股投资分析报告

> **报告日期**: {date_str}  
> **覆盖标的**: {len(valid)} 只个股  
> **数据源**: Tushare + AkShare + 新浪财经  
> **分析框架**: RSI / MACD / 均线排列 / 量价关系 / 多周期回测  

---

## 一、综合排名

| 排名 | 名称 | 代码 | 最新价 | 涨跌幅 | 综合评分 | 技术信号 | RSI | MACD | 均线 |
|------|------|------|--------|--------|----------|----------|-----|------|------|
"""
    sorted_valid = sorted(valid, key=lambda r: r.get('score', 0), reverse=True)
    for idx, r in enumerate(sorted_valid, 1):
        sig = r.get('score', 0)
        sig_icon = '🟢' if sig >= 70 else '🟡' if sig >= 40 else '🔴'
        chg = r.get('change_pct', 0)
        chg_str = f'<span style="color:{"#f85149" if chg<0 else "#3fb950"}">{chg:+.2f}%</span>'
        report += f"| {idx} | {r['name']} | {r['ticker']} | {r.get('price', '?')} | {chg_str} | {sig_icon} {sig}/100 | {' '.join(r.get('signals', []))} | {r.get('rsi', '?')} | {r.get('macd', '?')} | {r.get('ma_trend', '?')} |\n"

    report += "\n---\n\n## 二、各股详细分析\n\n"

    for r in sorted_valid[:15]:  # 前15只详细展示
        bt = r.get('backtest', [])
        bt_str = "| 周期 | 收益 | 最大回撤 | 夏普比率 |\n|------|------|----------|----------|\n"
        for p in bt:
            if p['days'] in [30, 60, 90, 365]:
                bt_str += f"| {p['period_name']} | {p['total_return']:+.2f}% | {p['max_drawdown']:.2f}% | {p['sharpe']:.2f} |\n"

        report += f"""### {r['name']} ({r['ticker']})

| 指标 | 数值 |
|------|------|
| 最新价 | {r.get('price', '?')} |
| 涨跌幅 | {r.get('change_pct', 0):+.2f}% |
| 综合评分 | {r.get('score', 0)}/100 |
| RSI(14) | {r.get('rsi', '?')} |
| MACD | {r.get('macd', '?')} |
| 均线趋势 | {r.get('ma_trend', '?')} |
| 量比 | {r.get('vol_ratio', '?')} |

{bt_str}

"""

    report += f"""

---

*报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*  
*数据来源: Tushare / AkShare / 新浪财经*  
*⚠️ 仅供参考，不构成投资建议*
"""
    return report


def main():
    with open(WATCHLIST_PATH) as f:
        watchlist = json.load(f)

    stocks = [w for w in watchlist if w.get('category') == '个股']
    date_str = datetime.now().strftime('%Y-%m-%d')

    print(f"📊 分析 {len(stocks)} 只个股...")
    results = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        fut_map = {ex.submit(analyze_single_stock, w['ticker'], w['name']): w for w in stocks}
        for fut in as_completed(fut_map):
            r = fut.result()
            results.append(r)
            name = r.get('name', r.get('ticker', '?'))
            if 'error' in r:
                print(f"  ❌ {name}: {r['error']}")
            else:
                print(f"  ✅ {name}: {r.get('score',0)}/100 RSI={r.get('rsi','?')}")

    # 生成报告
    report = generate_stock_report(results, date_str)
    day_dir = os.path.join(REPORTS_DIR, date_str)
    os.makedirs(day_dir, exist_ok=True)
    path = os.path.join(day_dir, f'stock_report_1.md')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\n✅ 个股报告已生成: {path}")
    print(f"   成功: {len([r for r in results if 'error' not in r])}/{len(stocks)}")


if __name__ == '__main__':
    main()
