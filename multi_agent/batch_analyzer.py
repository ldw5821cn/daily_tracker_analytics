"""
批量标的分析器 - 一次分析多个股票/ETF，生成对比报告
"""
import sys
import os
import json
from datetime import datetime

sys.path.insert(0, '/home/zhihu/daily_tracker_analytics/etf_tracker/multi_agent')

from orchestrator import analyze_stock, print_short_summary


def batch_analyze(stocks, current_date=None, output_dir=None, agentic=False):
    """
    批量分析多个标的
    
    Args:
        stocks: list of (ticker, name) 元组
        current_date: 分析日期
        output_dir: 报告输出目录
        agentic: 是否启用 LLM Agentic 增强（默认关闭，日报场景提速）
    
    Returns:
        list of results
    """
    if current_date is None:
        current_date = datetime.now().strftime('%Y-%m-%d')
    
    date_str = current_date.replace('-', '')
    results = []
    
    print(f"\n{'='*70}")
    print(f"  📊 批量多 Agent 分析启动")
    print(f"  共 {len(stocks)} 个标的 | 日期: {current_date}")
    print(f"{'='*70}")
    
    for ticker, name in stocks:
        print(f"\n{'─'*70}")
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            output_file = os.path.join(output_dir, f"multi_agent_{ticker}_{date_str}.md")
        else:
            output_file = None
        
        result = analyze_stock(ticker, name, current_date, output_file, agentic=agentic)
        results.append(result)
        
        print(f"\n  📋 快照:")
        print(print_short_summary(result))
    
    # ========== 生成对比汇总报告 ==========
    summary_lines = []
    summary_lines.append(f"# 📊 批量多 Agent 分析对比报告")
    summary_lines.append(f"")
    summary_lines.append(f"**分析日期**: {current_date}")
    summary_lines.append(f"**标的数量**: {len(results)}")
    summary_lines.append(f"")
    summary_lines.append(f"---")
    summary_lines.append(f"")
    
    # 对比表格
    summary_lines.append(f"## 综合对比")
    summary_lines.append(f"")
    summary_lines.append(f"| 标的 | 价格 | 最终评级 | 综合评分 | Bull | Bear | 净信号 | 风险 | 买入区 | 开仓区 | 卖出区 | 建议 |")
    summary_lines.append(f"|------|------|---------|:-------:|:----:|:----:|:-----:|:----:|--------|--------|--------|------|")
    for r in results:
        v = r['verdict']
        risk = r['risk_assessment']['overall_risk'].split()[0]
        pred = r['technical_report'].get('prediction') or {}
        zones = pred.get('trading_zones', {})
        buy_zone = zones.get('buy', '-') if zones else '-'
        open_zone = zones.get('open', '-') if zones else '-'
        sell_zone = zones.get('sell', '-') if zones else '-'
        summary_lines.append(
            f"| {r['name']}({r['ticker']}) | {r['current_price']} | {v['rating']} | {v['weighted_score']} | "
            f"{v['bull_score']} | {v['bear_score']} | {v['net_signal']:+d} | {risk} | "
            f"{buy_zone} | {open_zone} | {sell_zone} | {v['recommendation']} |"
        )
    summary_lines.append(f"")
    
    # 详细对比
    summary_lines.append(f"## 详细分析")
    summary_lines.append(f"")
    for r in results:
        v = r['verdict']
        tech = r['technical_report']
        fund = r.get('fundamental_report')
        news = r.get('news_report')
        
        summary_lines.append(f"### {r['name']}({r['ticker']})")
        summary_lines.append(f"")
        summary_lines.append(f"- **价格**: {r['current_price']}元")
        summary_lines.append(f"- **最终评级**: {v['rating']} (综合评分{v['weighted_score']}/100)")
        summary_lines.append(f"- **建议**: {v['recommendation']}")
        summary_lines.append(f"- **技术面**: {tech['rating']}({tech['score']}/100)")
        if fund:
            summary_lines.append(f"- **基本面**: {fund['rating']}({fund['score']}/100)")
        if news:
            summary_lines.append(f"- **新闻情绪**: {news['sentiment_score']:+.2f}")
        summary_lines.append(f"- **多空**: Bull({v['bull_score']}) vs Bear({v['bear_score']}) | 净信号{v['net_signal']:+d}")
        summary_lines.append(f"- **风险**: {r['risk_assessment']['overall_risk']}")
        pred = r['technical_report'].get('prediction') or {}
        zones = pred.get('trading_zones', {})
        if zones and zones.get('buy'):
            summary_lines.append(f"- **交易区间**: 买入 {zones.get('buy', '-')} | 开仓 {zones.get('open', '-')} | 卖出 {zones.get('sell', '-')}")
        summary_lines.append(f"")
        
        # 回测速览
        bt = tech.get('backtest_results', [])
        if bt:
            summary_lines.append(f"**多周期回测**:")
            summary_lines.append(f"| 周期 | 收益 | 回撤 | 夏普 |")
            summary_lines.append(f"|------|------|------|------|")
            for p in bt[:4]:
                summary_lines.append(f"| {p['period_name']} | {p['total_return']:+.1f}% | {p['max_drawdown']:.1f}% | {p['sharpe']:.2f} |")
            summary_lines.append(f"")
        
        # 核心信号
        signals = tech.get('signals', [])
        if signals:
            summary_lines.append(f"**技术信号**:")
            for icon, title, desc in signals[:5]:
                summary_lines.append(f"- {icon} {title}: {desc}")
            summary_lines.append(f"")
        
        summary_lines.append(f"---")
        summary_lines.append(f"")
    
    summary_lines.append(f"⚠️ **免责声明**: 本报告由多 Agent 系统自动生成，仅供参考，不构成投资建议。")
    summary_lines.append(f"")
    
    summary_text = "\n".join(summary_lines)
    
    # 保存对比报告
    if output_dir:
        summary_file = os.path.join(output_dir, f"comparison_{date_str}.md")
        with open(summary_file, 'w', encoding='utf-8') as f:
            f.write(summary_text)
        print(f"\n{'='*70}")
        print(f"  📁 对比报告已保存: {summary_file}")
        print(f"{'='*70}")
    
    return results, summary_text


if __name__ == "__main__":
    # 默认分析三个标的
    stocks = [
        ("601991", "大唐发电"),
        ("515880", "通信ETF"),
        ("516150", "稀土ETF"),
    ]
    
    import argparse
    parser = argparse.ArgumentParser(description='批量多Agent分析')
    parser.add_argument('--stocks', '-s', nargs='+', help='股票代码列表（如 601991 515880 516150）')
    parser.add_argument('--names', '-n', nargs='+', help='股票名称列表（可选）')
    parser.add_argument('--date', '-d', default=None)
    parser.add_argument('--output', '-o', default='reports/')
    
    args = parser.parse_args()
    
    if args.stocks:
        names = args.names or ['' for _ in args.stocks]
        stocks = list(zip(args.stocks, names))
    
    results, summary = batch_analyze(stocks, args.date, args.output)
    
    print(f"\n{'='*70}")
    print(summary)
    print(f"{'='*70}")
