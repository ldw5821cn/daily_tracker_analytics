"""
辩论与裁决引擎 - 多 Agent 交叉验证
对应 TradingAgents-CN 的 bull_researcher / bear_researcher / research_manager / risk_manager
"""
import sys
sys.path.insert(0, '/home/zhihu/daily_tracker_analytics/etf_tracker/multi_agent')

import json
from datetime import datetime


class DebateEngine:
    """
    辩论引擎 - 让多方观点展开辩论并做出裁决
    
    流程：
    1. 技术面分析师 → 产出技术报告
    2. 基本面分析师 → 产出基本面报告
    3. 新闻分析师 → 产出舆情报告
    4. 看涨研究员 (Bull) → 基于三份报告，找出支持上涨的证据
    5. 看跌研究员 (Bear) → 基于三份报告，找出支持下跌的证据
    6. 研究经理 (Research Manager) → 综合双方论点做出裁决
    7. 风控官 → 评估风险，给出最终建议
    """
    
    @staticmethod
    def bull_argument(technical_report, fundamental_report, news_report):
        """
        看涨研究员 - 找出支持上涨的证据
        
        基于三份报告的数据，用量化逻辑找出看涨信号
        """
        lines = []
        lines.append("## 看涨观点")
        lines.append("")
        
        bull_points = []
        bull_score = 0
        
        # 从技术面找看涨证据
        if technical_report:
            t = technical_report
            tr = t.get('backtest_results', [])
            
            # 多周期趋势
            if tr:
                for p in tr:
                    if p['total_return'] > 20 and '60' not in p['period_name']:
                        continue  # 中长线大涨不算短线看涨
                    if p['total_return'] > 5:
                        bull_score += 1
                        bull_points.append(f"{p['period_name']}涨幅{p['total_return']:+.1f}%")
                
                # 夏普比
                high_sharpe = sum(1 for p in tr if p['sharpe'] > 1)
                if high_sharpe >= 3:
                    bull_points.append(f"多周期夏普比优秀({high_sharpe}/6个周期>1)")
            
            # 技术指标
            s = t.get('tech_snapshot', {})
            if s.get('rsi_14', 50) < 40:
                bull_points.append(f"RSI({s.get('rsi_14', 0):.0f})接近超卖区，可能存在反弹机会")
            
            # 均线支撑
            cp = t.get('current_price', 0)
            for ma_key in ['ma60', 'ma120']:
                mv = s.get(ma_key)
                if mv and mv < cp < mv * 1.15:
                    bull_points.append(f"价格({cp})在{ma_key}上方({(cp/mv-1)*100:.1f}%)，均线有支撑")
            
            # 布林下轨支撑
            if s.get('boll_down') and cp <= s['boll_down'] * 1.05:
                bull_points.append(f"价格接近布林下轨({s['boll_down']})，技术性支撑")
        
        # 从新闻找看涨证据
        if news_report:
            ns = news_report.get('sentiment_score', 0)
            if ns > 0.2:
                bull_points.append(f"新闻情绪偏积极({ns:+.2f})")
            
            kw = news_report.get('keywords', [])
            positive_kw = [k for k in kw if k in ['涨停', '突破', '利好', '政策支持', '增持', '回购', '业绩']]
            if positive_kw:
                bull_points.append(f"新闻关键词含利好: {', '.join(positive_kw)}")
        
        # 从基本面找看涨证据
        if fundamental_report:
            f = fundamental_report.get('fundamentals', {})
            if f.get('revenue_growth', 0) > 5:
                bull_points.append(f"营收增长{f['revenue_growth']:+.1f}%")
            if f.get('dividend_yield', 0) > 2:
                bull_points.append(f"股息率{f['dividend_yield']:.1f}%")
            if f.get('profit_margins', 0) > 10:
                bull_points.append(f"净利率{f['profit_margins']:.1f}%较好")
            if f.get('pe_ratio') and 0 < f['pe_ratio'] < 15:
                bull_points.append(f"PE({f['pe_ratio']})偏低，估值合理")
        
        # 与技术面评分对齐：避免独立机械计数导致与综合评分背离
        tech_score = technical_report.get('score', 50) if technical_report else 50
        if tech_score > 50:
            bull_score = max(bull_score, (tech_score - 50) / 10)
        if tech_score <= 40:
            bull_score = 0.0
        # 技术面明显偏多时，至少拉平多空强度，防止均线压制类噪音过度主导
        if tech_score > 55:
            bull_score = max(bull_score, 1.0)

        lines.append(f"看涨强度: {bull_score} 个积极信号")
        if bull_points:
            lines.append(f"")
            lines.append("**看涨论据:**")
            for i, point in enumerate(bull_points[:8], 1):
                lines.append(f"{i}. {point}")
        else:
            lines.append("当前无明显看涨信号。")
        
        lines.append(f"")
        
        return {
            'side': '看涨(Bull)',
            'score': bull_score,
            'points': bull_points,
            'text': "\n".join(lines),
        }
    
    @staticmethod
    def bear_argument(technical_report, fundamental_report, news_report):
        """
        看跌研究员 - 找出支持下跌的证据
        """
        lines = []
        lines.append("## 看跌观点")
        lines.append("")
        
        bear_points = []
        bear_score = 0
        
        # 从技术面找看跌证据
        if technical_report:
            t = technical_report
            tr = t.get('backtest_results', [])
            s = t.get('tech_snapshot', {})
            
            # MACD空头
            macd_hist = s.get('macd_hist', 0)
            if macd_hist is not None and macd_hist < 0:
                bear_score += 1
                bear_points.append(f"MACD柱状为负({macd_hist:.2f})，动能偏空")
            
            # 均线压制
            cp = t.get('current_price', 0)
            for ma_key in ['ma5', 'ma10', 'ma20']:
                mv = s.get(ma_key)
                if mv and cp < mv:
                    bear_score += 1
                    bear_points.append(f"价格在{ma_key}({mv})下方，短期均线压制")
                    break
            
            # 布林压制
            if s.get('boll_mid') and cp < s['boll_mid']:
                bear_points.append(f"价格在布林中轨({s['boll_mid']})下方")
            
            # RSI
            rsi = s.get('rsi_14', 50)
            if rsi is not None and rsi > 70:
                bear_points.append(f"RSI({rsi:.0f})偏高")
            
            # 近1月回撤
            if tr:
                r30 = next((p for p in tr if p['days'] == 30), None)
                if r30 and r30['max_drawdown'] < -15:
                    bear_score += 1
                    bear_points.append(f"近1月最大回撤{r30['max_drawdown']:.1f}%，波动剧烈")
            
            # 高波动
            vol = s.get('annual_vol', 0)
            if vol > 50:
                bear_points.append(f"年化波动率{vol:.0f}%，风险较高")
        
        # 从新闻找看跌证据
        if news_report:
            ns = news_report.get('sentiment_score', 0)
            if ns < -0.2:
                bear_points.append(f"新闻情绪偏消极({ns:+.2f})")
            kw = news_report.get('keywords', [])
            negative_kw = [k for k in kw if k in ['跌停', '利空', '减持', '回调', '风险', '处罚', '调查']]
            if negative_kw:
                bear_points.append(f"新闻含利空关键词: {', '.join(negative_kw)}")
        
        # 从基本面找看跌证据
        if fundamental_report:
            f = fundamental_report.get('fundamentals', {})
            if f.get('debt_to_equity', 0) > 100:
                bear_points.append(f"负债权益比{f['debt_to_equity']:.0f}%偏高")
            if f.get('profit_margins', 0) < 5 and 'profit_margins' in f:
                bear_points.append(f"净利率{f['profit_margins']:.1f}%偏低")
            if f.get('revenue_growth', 0) < -5:
                bear_points.append(f"营收负增长{f['revenue_growth']:.1f}%")
            if f.get('pe_ratio') and f['pe_ratio'] > 50:
                bear_points.append(f"PE({f['pe_ratio']:.0f})过高")
        
        # 与技术面评分对齐
        tech_score = technical_report.get('score', 50) if technical_report else 50
        if tech_score < 50:
            bear_score = max(bear_score, (50 - tech_score) / 10)
        if tech_score >= 60:
            bear_score = 0.0
        if tech_score < 45:
            bear_score = max(bear_score, 1.0)

        lines.append(f"看跌强度: {bear_score} 个消极信号")
        if bear_points:
            lines.append(f"")
            lines.append("**看跌论据:**")
            for i, point in enumerate(bear_points[:8], 1):
                lines.append(f"{i}. {point}")
        else:
            lines.append("当前无明显看跌信号。")
        
        lines.append(f"")
        
        return {
            'side': '看跌(Bear)',
            'score': bear_score,
            'points': bear_points,
            'text': "\n".join(lines),
        }
    
    @staticmethod
    def risk_assessment(technical_report, fundamental_report, news_report, bull_arg, bear_arg):
        """
        风险评估（对应 Risk Manager 三风控官）
        """
        lines = []
        lines.append("# 风险评估报告")
        lines.append("")
        
        risks = []
        
        # 技术面风险
        if technical_report:
            tr = technical_report.get('backtest_results', [])
            if tr:
                # 最大回撤
                min_dd = min(p['max_drawdown'] for p in tr)
                if min_dd < -20:
                    risks.append(("高风险", f"多周期最大回撤达{min_dd:.1f}%"))
                elif min_dd < -10:
                    risks.append(("中风险", f"多周期最大回撤达{min_dd:.1f}%"))
                else:
                    risks.append(("低风险", f"多周期最大回撤仅{min_dd:.1f}%"))
                
                # 波动率
                avg_vol = sum(p['volatility'] for p in tr) / len(tr)
                if avg_vol > 50:
                    risks.append(("高风险", f"平均年化波动率{avg_vol:.0f}%，波动剧烈"))
                elif avg_vol > 30:
                    risks.append(("中风险", f"平均年化波动率{avg_vol:.0f}%，波动适中"))
                else:
                    risks.append(("低风险", f"平均年化波动率{avg_vol:.0f}%，波动温和"))
        
        # 基本面风险
        if fundamental_report:
            f = fundamental_report.get('fundamentals', {})
            if f.get('debt_to_equity', 0) > 150:
                risks.append(("高风险", f"负债权益比{f['debt_to_equity']:.0f}%，财务杠杆高"))
            if not f.get('profit_margins') or f['profit_margins'] < 3:
                risks.append(("中风险", "盈利利润率偏低"))
        
        # 多空对比风险
        bull_s = bull_arg.get('score', 0)
        bear_s = bear_arg.get('score', 0)
        net = bull_s - bear_s
        if bear_s > bull_s:
            risks.append(("中风险", f"看跌信号({bear_s})多于看涨信号({bull_s})"))
        
        lines.append(f"### 风险等级评估")
        high = sum(1 for r in risks if r[0] == "高风险")
        mid = sum(1 for r in risks if r[0] == "中风险")
        
        if high >= 2:
            overall_risk = "高风险 ⚠️"
        elif high >= 1:
            overall_risk = "中高风险 ⚠️"
        elif mid >= 2:
            overall_risk = "中风险 🟡"
        elif mid >= 1:
            overall_risk = "低风险 🟢"
        else:
            overall_risk = "较低风险 🟢"
        
        lines.append(f"**综合风险评估**: {overall_risk}")
        lines.append(f"")
        lines.append(f"**具体风险项:**")
        for level, desc in risks:
            icon = "🔴" if "高" in level else "🟡" if "中" in level else "🟢"
            lines.append(f"- {icon} {level}: {desc}")
        lines.append(f"")
        
        return {
            'overall_risk': overall_risk,
            'risks': risks,
            'text': "\n".join(lines),
        }
    
    @staticmethod
    def verdict(technical_report, fundamental_report, news_report, 
                bull_arg, bear_arg, risk_report, backtest_results):
        """
        最终裁决（对应 Research Manager）
        综合所有观点输出最终结论
        """
        lines = []
        lines.append("# 最终投资裁决")
        lines.append("")
        
        # 计算总分
        tech_score = technical_report.get('score', 50) if technical_report else 50
        fundamental_score = fundamental_report.get('score', 50) if fundamental_report else 50
        news_sentiment = news_report.get('sentiment_score', 0) if news_report else 0
        
        bull_score = bull_arg.get('score', 0)
        bear_score = bear_arg.get('score', 0)
        
        # 综合评分（加权）
        weighted = (tech_score * 0.35 + fundamental_score * 0.25 
                    + (news_sentiment * 50 + 50) * 0.15  # 新闻情绪归一化
                    + (bull_score / max(bear_score + bull_score, 1)) * 50 * 0.25)
        
        net_signal = bull_score - bear_score
        
        # 评级
        if net_signal >= 3 and weighted >= 60:
            rating = "偏多"
            recommendation = "可逢低关注"
        elif net_signal >= 1 and weighted >= 50:
            rating = "中性偏多"
            recommendation = "关注为主"
        elif net_signal <= -3 and weighted < 50:
            rating = "偏空"
            recommendation = "建议规避"
        elif net_signal <= -1:
            rating = "中性偏空"
            recommendation = "谨慎观望"
        else:
            rating = "中性"
            recommendation = "持仓观望"
        
        # 判断风险调整
        if risk_report:
            if "高" in risk_report.get('overall_risk', ''):
                if rating in ['偏多', '中性偏多']:
                    recommendation += "（但注意风险控制）"
                    rating = "中性偏多（高风险）"
        
        lines.append(f"**最终评级**: {rating}")
        lines.append(f"**综合评分**: {weighted:.0f}/100")
        lines.append(f"**多空对比**: 看涨{bull_score} vs 看跌{bear_score}（净信号{net_signal:+.0f}）")
        lines.append(f"**操作建议**: {recommendation}")
        lines.append(f"")
        
        # 关键位置
        if technical_report:
            s = technical_report.get('tech_snapshot', {})
            cp = technical_report.get('current_price', 0)
            supports = []
            pressures = []
            for k in ['ma60', 'ma30', 'ma20', 'boll_down']:
                v = s.get(k)
                if v and v < cp:
                    supports.append(f"{k.replace('ma','MA')}({v:.2f})")
            for k in ['ma5', 'ma10', 'boll_mid', 'ma20']:
                v = s.get(k)
                if v and v > cp:
                    pressures.append(f"{k.replace('ma','MA')}({v:.2f})")
            
            lines.append(f"**支撑**: {' → '.join(supports[:3])}" if supports else "")
            lines.append(f"**压力**: {' → '.join(pressures[:3])}" if pressures else "")
            lines.append(f"")
        
        # 回测摘要
        if backtest_results:
            lines.append(f"### 回测参考")
            for p in backtest_results[:3]:
                lines.append(f"- {p['period_name']}: {p['total_return']:+.1f}%  最大回撤{p['max_drawdown']:.1f}%  夏普{p['sharpe']:.2f}")
            lines.append(f"")
        
        # 核心理由
        lines.append(f"### 核心理由")
        all_points = [b for b in bull_arg.get('points', [])] + [r for r in bear_arg.get('points', [])]
        for i, point in enumerate(all_points[:6], 1):
            lines.append(f"{i}. {point}")
        lines.append(f"")
        
        # 风险提示
        if risk_report:
            lines.append(f"### 风险提醒")
            lines.append(risk_report.get('text', ''))
        
        lines.append(f"---")
        lines.append(f"⚠️ **免责声明**: 以上分析基于多因子模型和历史数据，仅供参考，不构成投资建议。")
        lines.append(f"")
        
        return {
            'rating': rating,
            'weighted_score': round(weighted, 1),
            'net_signal': net_signal,
            'bull_score': bull_score,
            'bear_score': bear_score,
            'recommendation': recommendation,
            'verdict_text': "\n".join(lines),
        }
