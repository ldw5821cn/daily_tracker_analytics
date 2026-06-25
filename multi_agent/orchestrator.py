"""
多 Agent 并行分析主引擎
- 并行启动四个分析师 Agent
- 运行辩论裁决
- 输出综合报告

注：Hermes Agent 中有 delegate_task 可以实现真正的并行
这里用顺序执行+函数调用，兼容 cron 自动运行模式
"""
import sys
import os
import json
import argparse
from datetime import datetime

sys.path.insert(0, '/home/zhihu/daily_tracker_analytics/etf_tracker/multi_agent')

from analysts.technical_analyst import analyze as tech_analysis
from analysts.fundamentals_analyst import analyze as fundamental_analysis
from analysts.news_analyst import analyze as news_analysis
from core.debate_engine import DebateEngine


def analyze_stock(ticker, name="", current_date=None, output_file=None, agentic=True):
    """
    全流程多 Agent 分析
    
    Args:
        ticker: 股票代码（如 601991）
        name: 股票名称（如 大唐发电）
        current_date: 分析日期
        output_file: 输出文件路径（可选）
    
    Returns:
        dict 完整的分析结果
    """
    if current_date is None:
        current_date = datetime.now().strftime('%Y-%m-%d')
    
    print(f"\n{'='*70}")
    print(f"  🔄 多 Agent 分析启动: {name}({ticker})")
    print(f"  日期: {current_date}")
    print(f"{'='*70}")
    
    # ========== Step 1: 并行四个分析师 ==========
    
    # 这里用顺序执行但可以独立跑
    # 在生产环境中可以用 delegate_task 起四个子 Agent 并行
    
    print(f"\n  ➡️ Agent 1/4: 技术面分析师 (Technical Analyst)")
    print(f"  {'─'*50}")
    try:
        tech_report = tech_analysis(ticker, name, current_date)
        print(f"  ✅ 完成 | 评分: {tech_report['score']}/100 | 评级: {tech_report['rating']}")
    except Exception as e:
        print(f"  ❌ 技术面分析失败: {e}")
        tech_report = {'score': 50, 'rating': '中性', 'backtest_results': [], 'tech_snapshot': {}, 'current_price': 0, 'signals': [], 'reasons': []}
    
    print(f"\n  ➡️ Agent 2/4: 基本面分析师 (Fundamentals Analyst)")
    print(f"  {'─'*50}")
    try:
        fundamental_report = fundamental_analysis(ticker, name, current_date)
        print(f"  ✅ 完成 | 评分: {fundamental_report['score']}/100 | 评级: {fundamental_report['rating']}")
    except Exception as e:
        print(f"  ❌ 基本面分析失败: {e}")
        fundamental_report = None
    
    print(f"\n  ➡️ Agent 3/4: 新闻分析师 (News Analyst)")
    print(f"  {'─'*50}")
    try:
        news_report = news_analysis(ticker, name, current_date)
        print(f"  ✅ 完成 | 情绪: {news_report['sentiment_score']:+.2f} | 新闻: {news_report['news_count']}条")
    except Exception as e:
        print(f"  ❌ 新闻分析失败: {e}")
        news_report = None
    
    print(f"\n  ➡️ Agent 4/4: 辩论与裁决 (Debate Engine)")
    print(f"  {'─'*50}")
    
    # ========== Step 2: 展开辩论 ==========
    print(f"  ┌─ 看涨研究员 (Bull Researcher) 分析中...")
    bull_arg = DebateEngine.bull_argument(tech_report, fundamental_report, news_report)
    print(f"  └─ 看涨信号: {bull_arg['score']} 个")
    
    print(f"  ┌─ 看跌研究员 (Bear Researcher) 分析中...")
    bear_arg = DebateEngine.bear_argument(tech_report, fundamental_report, news_report)
    print(f"  └─ 看跌信号: {bear_arg['score']} 个")
    
    # ========== Step 3: 风险评估 ==========
    print(f"  ┌─ 风控官 (Risk Manager) 评估中...")
    risk_report = DebateEngine.risk_assessment(tech_report, fundamental_report, news_report, bull_arg, bear_arg)
    print(f"  └─ 风险: {risk_report['overall_risk']}")
    
    # ========== Step 4: 最终裁决 ==========
    print(f"  ┌─ 研究经理 (Research Manager) 综合裁决中...")
    verdict = DebateEngine.verdict(
        tech_report, fundamental_report, news_report,
        bull_arg, bear_arg, risk_report,
        tech_report.get('backtest_results', [])
    )
    print(f"  └─ 评级: {verdict['rating']} | 建议: {verdict['recommendation']}")
    
    # ========== Agentic LLM 增强裁决（可选）==========
    agentic_report = None
    if agentic:
        try:
            from agentic_report_generator import AgenticReportGenerator
            gen = AgenticReportGenerator()
            agentic_report = gen.generate(
                ticker, name,
                technical_report=tech_report,
                fundamental_report=fundamental_report,
                news_report=news_report
            )
            print(f"  ✅ Agentic 报告生成完成: {agentic_report.get('rating', 'N/A')} (置信度 {agentic_report.get('confidence', 0):.0%})")
        except Exception as e:
            print(f"  ⚠️ Agentic 报告生成失败: {e}")
    
    # ========== 组装最终报告 ==========
    full_report_lines = []
    full_report_lines.append(f"# 🏛️ 多 Agent 投资分析报告")
    full_report_lines.append(f"")
    full_report_lines.append(f"**标的**: {name} ({ticker})")
    full_report_lines.append(f"**分析日期**: {current_date}")
    full_report_lines.append(f"**当前价格**: {tech_report.get('current_price', 'N/A')} 元")
    full_report_lines.append(f"")
    full_report_lines.append(f"---")
    full_report_lines.append(f"")
    full_report_lines.append(f"## 🏆 最终裁决")
    full_report_lines.append(f"")
    full_report_lines.append(verdict['verdict_text'])
    full_report_lines.append(f"")
    
    if agentic_report:
        full_report_lines.append(f"---")
        full_report_lines.append(f"")
        full_report_lines.append(agentic_report['report_text'])
    
    full_report_lines.append(f"---")
    full_report_lines.append(f"")
    full_report_lines.append(f"## 📊 分析师报告")
    full_report_lines.append(f"")
    
    # 技术面报告
    if tech_report and tech_report.get('summary'):
        full_report_lines.append(tech_report['summary'])
    
    full_report_lines.append(f"---")
    full_report_lines.append(f"")
    
    # 基本面报告
    if fundamental_report and fundamental_report.get('summary'):
        full_report_lines.append(fundamental_report['summary'])
        full_report_lines.append(f"")
        full_report_lines.append(f"---")
        full_report_lines.append(f"")
    
    # 新闻报告
    if news_report and news_report.get('summary'):
        full_report_lines.append(news_report['summary'])
        full_report_lines.append(f"")
        full_report_lines.append(f"---")
        full_report_lines.append(f"")
    
    # 辩论详情
    full_report_lines.append(f"## 🗣️ 辩论详情")
    full_report_lines.append(f"")
    full_report_lines.append(bull_arg['text'])
    full_report_lines.append(bear_arg['text'])
    
    # 风险
    full_report_lines.append(risk_report['text'])
    
    # 免责
    full_report_lines.append(f"---")
    full_report_lines.append(f"⚠️ **免责声明**: 本报告由多 Agent 系统自动生成，基于量化模型和历史数据，仅供参考和学习研究之用，不构成任何投资建议。投资有风险，入市须谨慎。")
    full_report_lines.append(f"")
    
    report_text = "\n".join(full_report_lines)
    
    result = {
        'ticker': ticker,
        'name': name,
        'analysis_date': current_date,
        'current_price': tech_report.get('current_price', 0),
        'technical_report': {
            'score': tech_report.get('score'),
            'rating': tech_report.get('rating'),
            'summary': tech_report.get('summary'),
            'backtest_results': tech_report.get('backtest_results', []),
            'tech_snapshot': tech_report.get('tech_snapshot', {}),
            'signals': tech_report.get('signals', []),
            'prediction': tech_report.get('prediction'),
        },
        'fundamental_report': {
            'score': fundamental_report.get('score') if fundamental_report else None,
            'rating': fundamental_report.get('rating') if fundamental_report else None,
            'summary': fundamental_report.get('summary') if fundamental_report else None,
            'fundamentals': fundamental_report.get('fundamentals', {}) if fundamental_report else {},
        } if fundamental_report else None,
        'news_report': {
            'sentiment_score': news_report.get('sentiment_score') if news_report else None,
            'news_count': news_report.get('news_count') if news_report else 0,
            'keywords': news_report.get('keywords', []) if news_report else [],
            'summary': news_report.get('summary') if news_report else None,
        } if news_report else None,
        'bull_argument': bull_arg,
        'bear_argument': bear_arg,
        'risk_assessment': risk_report,
        'verdict': verdict,
        'agentic_report': agentic_report,
        'full_report': report_text,
    }
    
    # 输出到文件
    if output_file:
        os.makedirs(os.path.dirname(output_file), exist_ok=True) if os.path.dirname(output_file) else None
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(report_text)
        print(f"\n  📁 报告已保存: {output_file}")
    
    return result


def print_short_summary(result):
    """打印简要总结"""
    v = result['verdict']
    t = result['technical_report']
    news = result['news_report']
    
    lines = [
        f"🏛️ {result['name']}({result['ticker']}) 多Agent分析结果",
        f"",
        f"价格: {result['current_price']}元",
        f"评级: {v['rating']} (综合评分{v['weighted_score']})",
        f"建议: {v['recommendation']}",
        f"技术面: {t['rating']}({t['score']}/100)",
        f"基本面: {result['fundamental_report']['rating']}({result['fundamental_report']['score']}/100)" if result.get('fundamental_report') else "",
        f"新闻情绪: {news['sentiment_score']:+.2f}" if news else "",
        f"多空: Bull({v['bull_score']}) vs Bear({v['bear_score']}) | 净信号{v['net_signal']:+d}",
        f"风险: {result['risk_assessment']['overall_risk']}",
    ]
    return "\n".join(l for l in lines if l)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='多Agent股票分析')
    parser.add_argument('ticker', help='股票代码')
    parser.add_argument('--name', '-n', default='', help='股票名称')
    parser.add_argument('--date', '-d', default=None, help='分析日期')
    parser.add_argument('--output', '-o', default=None, help='输出文件路径')
    parser.add_argument('--brief', '-b', action='store_true', help='仅输出简要总结')
    
    args = parser.parse_args()
    
    result = analyze_stock(args.ticker, args.name, args.date, args.output)
    
    print(f"\n{'='*70}")
    print(print_short_summary(result))
    print(f"{'='*70}")
