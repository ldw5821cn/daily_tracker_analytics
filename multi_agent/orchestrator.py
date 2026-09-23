"""
多 Agent 并行分析主引擎
- 并行启动四个分析师 Agent
- 运行辩论裁决
- 输出综合报告
- 【P0 新增】集成证据链，为 AI 结论添加可追溯的证据支持

注：Hermes Agent 中有 delegate_task 可以实现真正的并行
这里用顺序执行+函数调用，兼容 cron 自动运行模式
"""
import sys
import os
import json
import argparse
from datetime import datetime

sys.path.insert(0, '/home/zhihu/daily_tracker_analytics/etf_tracker/multi_agent')
sys.path.insert(0, '/home/liudawei/github/daily_tracker_analytics/multi_agent')

from analysts.technical_analyst import analyze as tech_analysis
from analysts.fundamentals_analyst import analyze as fundamental_analysis
from analysts.news_analyst import analyze as news_analysis
from core.debate_engine import DebateEngine

# 证据链模块（P0 新增）
try:
    from evidence import (
        Evidence, EvidenceType, EvidenceLevel,
        AnalysisResultWithEvidence, Condition, Scenario,
        convert_prediction_to_evidence_result
    )
    EVIDENCE_CHAIN_AVAILABLE = True
except ImportError:
    EVIDENCE_CHAIN_AVAILABLE = False


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
        'evidence_chain': build_evidence_chain(
            ticker, name, tech_report, fundamental_report, news_report,
            bull_arg, bear_arg, verdict, current_date
        ) if EVIDENCE_CHAIN_AVAILABLE else None,
    }
    
    # 输出到文件
    if output_file:
        os.makedirs(os.path.dirname(output_file), exist_ok=True) if os.path.dirname(output_file) else None
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(report_text)
        print(f"\n  📁 报告已保存: {output_file}")
        
        # 【P0 新增】保存证据链 JSON
        if result['evidence_chain']:
            evidence_file = output_file.replace('.md', '_evidence.json')
            with open(evidence_file, 'w', encoding='utf-8') as f:
                f.write(result['evidence_chain'].to_json())
            print(f"  📁 证据链已保存: {evidence_file}")
    
    return result


# ============================================================
# 证据链构建（P0 新增）
# ============================================================

def build_evidence_chain(
    ticker: str,
    name: str,
    tech_report: dict,
    fundamental_report: dict,
    news_report: dict,
    bull_arg: dict,
    bear_arg: dict,
    verdict: dict,
    current_date: str
) -> AnalysisResultWithEvidence:
    """
    从多 Agent 分析结果构建证据链
    
    将各分析师的输出、辩论结果、最终裁决整合为带证据链的分析结果
    """
    # 确定证据充分度
    evidence_level = EvidenceLevel.LIMITED.value
    if tech_report and fundamental_report and news_report:
        evidence_level = EvidenceLevel.SUFFICIENT.value
    elif not tech_report and not fundamental_report and not news_report:
        evidence_level = EvidenceLevel.INSUFFICIENT.value
    
    # 创建基础结果
    result = AnalysisResultWithEvidence(
        headline=f"{name}({ticker}) - {verdict.get('rating', 'N/A')}",
        thesis=verdict.get('verdict_text', '')[:200],
        evidence_level=evidence_level,
        ticker=ticker,
        name=name
    )
    
    # 添加技术面证据
    if tech_report:
        # 支持证据
        if tech_report.get('score', 0) >= 60:
            result.add_support(Evidence(
                source_id="tech-score-001",
                source_type=EvidenceType.FACT.value,
                content=f"技术面评分 {tech_report['score']}/100，评级 {tech_report.get('rating', 'N/A')}",
                timestamp=current_date
            ))
        
        # 技术指标快照
        tech_snapshot = tech_report.get('tech_snapshot', {})
        if tech_snapshot:
            indicators = []
            if tech_snapshot.get('ma_trend'):
                indicators.append(f"均线趋势: {tech_snapshot['ma_trend']}")
            if tech_snapshot.get('rsi_14'):
                indicators.append(f"RSI14: {tech_snapshot['rsi_14']}")
            if tech_snapshot.get('macd_signal'):
                indicators.append(f"MACD: {tech_snapshot['macd_signal']}")
            
            if indicators:
                result.add_support(Evidence(
                    source_id="tech-indicators-001",
                    source_type=EvidenceType.FACT.value,
                    content=" | ".join(indicators),
                    timestamp=current_date
                ))
        
        # 信号列表
        signals = tech_report.get('signals', [])
        if signals:
            signal_text = f"技术信号: {', '.join(signals[:3])}"
            result.add_support(Evidence(
                source_id="tech-signals-001",
                source_type=EvidenceType.INFERENCE.value,
                content=signal_text,
                timestamp=current_date
            ))
    
    # 添加基本面证据
    if fundamental_report:
        score = fundamental_report.get('score', 0)
        if score >= 60:
            result.add_support(Evidence(
                source_id="fund-score-001",
                source_type=EvidenceType.FACT.value,
                content=f"基本面评分 {score}/100，评级 {fundamental_report.get('rating', 'N/A')}",
                timestamp=current_date
            ))
        else:
            result.add_counter(Evidence(
                source_id="fund-score-001",
                source_type=EvidenceType.FACT.value,
                content=f"基本面评分较低 {score}/100",
                timestamp=current_date
            ))
        
        # 关键财务指标
        fundamentals = fundamental_report.get('fundamentals', {})
        if fundamentals:
            key_metrics = []
            if fundamentals.get('pe_ratio'):
                key_metrics.append(f"PE: {fundamentals['pe_ratio']}")
            if fundamentals.get('pb_ratio'):
                key_metrics.append(f"PB: {fundamentals['pb_ratio']}")
            if fundamentals.get('roe'):
                key_metrics.append(f"ROE: {fundamentals['roe']}%")
            
            if key_metrics:
                result.add_support(Evidence(
                    source_id="fund-metrics-001",
                    source_type=EvidenceType.FACT.value,
                    content=" | ".join(key_metrics),
                    timestamp=current_date
                ))
    
    # 添加新闻情绪证据
    if news_report:
        sentiment = news_report.get('sentiment_score', 0)
        news_count = news_report.get('news_count', 0)
        
        if sentiment > 0.2:
            result.add_support(Evidence(
                source_id="news-sentiment-001",
                source_type=EvidenceType.OPINION.value,
                content=f"新闻情绪积极 ({sentiment:+.2f})，相关新闻 {news_count} 条",
                timestamp=current_date
            ))
        elif sentiment < -0.2:
            result.add_counter(Evidence(
                source_id="news-sentiment-001",
                source_type=EvidenceType.OPINION.value,
                content=f"新闻情绪消极 ({sentiment:+.2f})，相关新闻 {news_count} 条",
                timestamp=current_date
            ))
        else:
            result.add_alternative(Evidence(
                source_id="news-sentiment-001",
                source_type=EvidenceType.OPINION.value,
                content=f"新闻情绪中性 ({sentiment:+.2f})",
                timestamp=current_date
            ))
    
    # 添加辩论证据
    if bull_arg and bull_arg.get('score', 0) > 0:
        result.add_support(Evidence(
            source_id="debate-bull-001",
            source_type=EvidenceType.INFERENCE.value,
            content=f"看涨论证: {bull_arg.get('text', '')[:100]}...",
            timestamp=current_date
        ))
    
    if bear_arg and bear_arg.get('score', 0) > 0:
        result.add_counter(Evidence(
            source_id="debate-bear-001",
            source_type=EvidenceType.INFERENCE.value,
            content=f"看跌论证: {bear_arg.get('text', '')[:100]}...",
            timestamp=current_date
        ))
    
    # 设置基线关系
    net_signal = verdict.get('net_signal', 0)
    if net_signal > 2:
        result.set_baseline_relation("agree", f"AI 与量化基线一致看多 (净信号 {net_signal:+d})")
    elif net_signal < -2:
        result.set_baseline_relation("disagree", f"AI 与量化基线不一致 (净信号 {net_signal:+d})")
    else:
        result.set_baseline_relation("insufficient", f"信号不明确 (净信号 {net_signal:+d})")
    
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
