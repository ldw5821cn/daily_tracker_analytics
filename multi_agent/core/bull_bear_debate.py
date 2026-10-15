#!/usr/bin/env python3
"""Bull vs Bear 辩论引擎

基于 TradingAgents 架构，实现多空双方辩论机制：
- Bull Researcher (看多研究员)
- Bear Researcher (看空研究员)
- Debate Judge (辩论裁判)

整合到现有 orchestrator.py 分析流程中。
"""
from __future__ import annotations

import json
import os
import sys
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, MULTI_AGENT)


@dataclass
class DebateArgument:
    """辩论论点"""
    side: str  # 'bull' or 'bear'
    argument: str
    evidence: List[str]
    confidence: float  # 0-1
    weight: int  # 1-10


@dataclass
class DebateResult:
    """辩论结果"""
    ticker: str
    date: str
    bull_score: float
    bear_score: float
    winner: str  # 'bull', 'bear', 'neutral'
    confidence: float
    key_arguments: List[DebateArgument]
    recommendation: str
    risk_factors: List[str]


class BullBearDebate:
    """多空辩论引擎"""
    
    def __init__(self):
        self.max_rounds = 3  # 最多辩论轮数
    
    def run_debate(self, ticker: str, analyses: Dict) -> DebateResult:
        """运行多空辩论
        
        Args:
            ticker: 股票代码
            analyses: 各分析师的分析结果 {'technical': {...}, 'fundamental': {...}, ...}
        
        Returns:
            DebateResult 辩论结果
        """
        # 1. 生成 Bull 论点
        bull_args = self._generate_bull_arguments(analyses)
        
        # 2. 生成 Bear 论点
        bear_args = self._generate_bear_arguments(analyses)
        
        # 3. 模拟辩论过程
        debate_history = self._simulate_debate(bull_args, bear_args)
        
        # 4. 裁判评分
        result = self._judge_debate(ticker, bull_args, bear_args, debate_history)
        
        return result
    
    def _generate_bull_arguments(self, analyses: Dict) -> List[DebateArgument]:
        """生成看多论点"""
        args = []
        
        # 技术面看多
        tech = analyses.get('technical', {})
        tech_score = tech.get('score', 50)
        if tech_score >= 60:
            args.append(DebateArgument(
                side='bull',
                argument=f"技术面强势 (评分 {tech_score}/100)",
                evidence=[f"技术指标: {tech.get('signals', [])}"],
                confidence=tech_score / 100,
                weight=8
            ))
        
        # 基本面看多
        fund = analyses.get('fundamental', {})
        fund_score = fund.get('score', 50)
        if fund_score >= 60:
            args.append(DebateArgument(
                side='bull',
                argument=f"基本面优秀 (评分 {fund_score}/100)",
                evidence=[f"财务指标: {fund.get('reasons', [])}"],
                confidence=fund_score / 100,
                weight=9
            ))
        
        # 资金流向看多
        fund_flow = analyses.get('fund_flow', {})
        if fund_flow.get('net_inflow', 0) > 50000000:
            args.append(DebateArgument(
                side='bull',
                argument="资金大幅净流入",
                evidence=[f"净流入: {fund_flow.get('net_inflow', 0) / 100000000:.2f}亿"],
                confidence=0.7,
                weight=7
            ))
        
        # 情绪面看多
        sentiment = analyses.get('sentiment', {})
        if sentiment.get('score', 50) >= 60:
            args.append(DebateArgument(
                side='bull',
                argument="市场情绪积极",
                evidence=[f"情绪评分: {sentiment.get('score', 50)}/100"],
                confidence=sentiment.get('score', 50) / 100,
                weight=6
            ))
        
        return args
    
    def _generate_bear_arguments(self, analyses: Dict) -> List[DebateArgument]:
        """生成看空论点"""
        args = []
        
        # 技术面看空
        tech = analyses.get('technical', {})
        tech_score = tech.get('score', 50)
        if tech_score <= 40:
            args.append(DebateArgument(
                side='bear',
                argument=f"技术面弱势 (评分 {tech_score}/100)",
                evidence=[f"风险信号: {tech.get('signals', [])}"],
                confidence=(100 - tech_score) / 100,
                weight=8
            ))
        
        # 基本面看空
        fund = analyses.get('fundamental', {})
        fund_score = fund.get('score', 50)
        if fund_score <= 40:
            args.append(DebateArgument(
                side='bear',
                argument=f"基本面恶化 (评分 {fund_score}/100)",
                evidence=[f"风险因素: {fund.get('reasons', [])}"],
                confidence=(100 - fund_score) / 100,
                weight=9
            ))
        
        # 资金流向看空
        fund_flow = analyses.get('fund_flow', {})
        if fund_flow.get('net_inflow', 0) < -50000000:
            args.append(DebateArgument(
                side='bear',
                argument="资金大幅净流出",
                evidence=[f"净流出: {abs(fund_flow.get('net_inflow', 0)) / 100000000:.2f}亿"],
                confidence=0.7,
                weight=7
            ))
        
        # 情绪面看空
        sentiment = analyses.get('sentiment', {})
        if sentiment.get('score', 50) <= 40:
            args.append(DebateArgument(
                side='bear',
                argument="市场情绪悲观",
                evidence=[f"情绪评分: {sentiment.get('score', 50)}/100"],
                confidence=(100 - sentiment.get('score', 50)) / 100,
                weight=6
            ))
        
        return args
    
    def _simulate_debate(self, bull_args: List[DebateArgument], 
                        bear_args: List[DebateArgument]) -> List[Dict]:
        """模拟辩论过程"""
        history = []
        
        # 简单模拟：每方提出最强论点
        for round_num in range(self.max_rounds):
            round_record = {'round': round_num + 1, 'bull': [], 'bear': []}
            
            # Bull 发言
            if round_num < len(bull_args):
                round_record['bull'].append(asdict(bull_args[round_num]))
            
            # Bear 反驳
            if round_num < len(bear_args):
                round_record['bear'].append(asdict(bear_args[round_num]))
            
            history.append(round_record)
        
        return history
    
    def _judge_debate(self, ticker: str, bull_args: List[DebateArgument],
                     bear_args: List[DebateArgument], 
                     history: List[Dict]) -> DebateResult:
        """裁判评分"""
        # 计算总分
        bull_score = sum(arg.confidence * arg.weight for arg in bull_args)
        bear_score = sum(arg.confidence * arg.weight for arg in bear_args)
        
        # 归一化到 0-100
        total = bull_score + bear_score
        if total > 0:
            bull_score = (bull_score / total) * 100
            bear_score = (bear_score / total) * 100
        else:
            bull_score = bear_score = 50
        
        # 判断胜负
        if bull_score > bear_score + 10:
            winner = 'bull'
            confidence = (bull_score - bear_score) / 100
            recommendation = '买入'
        elif bear_score > bull_score + 10:
            winner = 'bear'
            confidence = (bear_score - bull_score) / 100
            recommendation = '卖出'
        else:
            winner = 'neutral'
            confidence = 0.5
            recommendation = '持有'
        
        # 收集风险因素
        risk_factors = [arg.argument for arg in bear_args]
        
        # 合并所有论点
        all_args = bull_args + bear_args
        
        return DebateResult(
            ticker=ticker,
            date=datetime.now().strftime('%Y-%m-%d'),
            bull_score=round(bull_score, 1),
            bear_score=round(bear_score, 1),
            winner=winner,
            confidence=round(confidence, 2),
            key_arguments=all_args,
            recommendation=recommendation,
            risk_factors=risk_factors
        )


def run_bull_bear_debate(ticker: str, analyses: Dict) -> Dict:
    """运行多空辩论 - 便捷函数"""
    debate = BullBearDebate()
    result = debate.run_debate(ticker, analyses)
    
    # 转换为字典
    result_dict = asdict(result)
    result_dict['key_arguments'] = [asdict(arg) for arg in result.key_arguments]
    
    return result_dict


if __name__ == '__main__':
    # 测试
    print("=== 测试 Bull vs Bear 辩论 ===")
    
    # 模拟分析数据
    test_analyses = {
        'technical': {'score': 65, 'signals': ['均线多头排列', 'MACD金叉']},
        'fundamental': {'score': 70, 'reasons': ['ROE>15%', '营收增长20%']},
        'fund_flow': {'net_inflow': 80000000},
        'sentiment': {'score': 60}
    }
    
    result = run_bull_bear_debate('000001.SZ', test_analyses)
    print(json.dumps(result, ensure_ascii=False, indent=2))
