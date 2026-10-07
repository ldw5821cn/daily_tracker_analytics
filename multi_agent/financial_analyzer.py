#!/usr/bin/env python3
"""
财务分析器 - 基于 HiThink Financial-API
提供财务报表分析、财务指标计算、基本面评分
"""

import sys
import os
import json
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from pathlib import Path
import math

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

# 导入 HiThink 客户端
try:
    from hithink_client import HiThinkFinanceClient
except ImportError:
    try:
        from multi_agent.hithink_client import HiThinkFinanceClient
    except ImportError:
        raise ImportError("HiThink 客户端不可用")


@dataclass
class FinancialStatement:
    """财务报表数据类"""
    ticker: str
    name: str
    thscode: str
    report_date: str           # 报告期
    report_type: str           # 报表类型：income/balance/cashflow
    
    # 利润表关键字段
    revenue: Optional[float] = None              # 营业收入
    net_profit: Optional[float] = None           # 净利润
    gross_profit: Optional[float] = None         # 毛利润
    operating_profit: Optional[float] = None     # 营业利润
    total_profit: Optional[float] = None         # 利润总额
    
    # 资产负债表关键字段
    total_assets: Optional[float] = None         # 总资产
    total_liabilities: Optional[float] = None    # 总负债
    total_equity: Optional[float] = None         # 股东权益
    current_assets: Optional[float] = None       # 流动资产
    current_liabilities: Optional[float] = None  # 流动负债
    monetary_funds: Optional[float] = None       # 货币资金
    
    # 现金流量表关键字段
    operating_cashflow: Optional[float] = None   # 经营现金流
    investing_cashflow: Optional[float] = None   # 投资现金流
    financing_cashflow: Optional[float] = None   # 筹资现金流
    net_cashflow: Optional[float] = None         # 现金净增加额
    
    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class FinancialIndicators:
    """财务指标数据类"""
    ticker: str
    name: str
    report_date: str
    
    # 盈利能力
    roe: Optional[float] = None                  # 净资产收益率
    roa: Optional[float] = None                  # 总资产收益率
    gross_margin: Optional[float] = None         # 毛利率
    net_margin: Optional[float] = None           # 净利率
    operating_margin: Optional[float] = None     # 营业利润率
    
    # 成长能力
    revenue_growth: Optional[float] = None       # 营收增长率
    profit_growth: Optional[float] = None        # 利润增长率
    asset_growth: Optional[float] = None         # 资产增长率
    
    # 偿债能力
    current_ratio: Optional[float] = None        # 流动比率
    quick_ratio: Optional[float] = None          # 速动比率
    debt_ratio: Optional[float] = None           # 资产负债率
    interest_coverage: Optional[float] = None    # 利息保障倍数
    
    # 营运能力
    asset_turnover: Optional[float] = None       # 总资产周转率
    inventory_turnover: Optional[float] = None   # 存货周转率
    receivable_turnover: Optional[float] = None  # 应收账款周转率
    
    # 现金流
    ocf_to_profit: Optional[float] = None        # 经营现金流/净利润
    ocf_to_revenue: Optional[float] = None       # 经营现金流/营业收入
    free_cashflow: Optional[float] = None        # 自由现金流
    
    # 综合评分
    profitability_score: Optional[float] = None  # 盈利能力评分 (0-100)
    growth_score: Optional[float] = None         # 成长能力评分 (0-100)
    solvency_score: Optional[float] = None       # 偿债能力评分 (0-100)
    efficiency_score: Optional[float] = None     # 营运能力评分 (0-100)
    overall_score: Optional[float] = None        # 综合评分 (0-100)
    
    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


class FinancialAnalyzer:
    """财务分析器"""
    
    def __init__(self):
        self.client = HiThinkFinanceClient()
        self._cache = {}
    
    def get_income_statement(self, symbol: str, periods: int = 4) -> List[FinancialStatement]:
        """
        获取利润表
        
        Args:
            symbol: A股代码
            periods: 期数
        
        Returns:
            List[FinancialStatement]: 利润表列表
        """
        thscode = HiThinkFinanceClient.to_thscode(symbol)
        data = self.client.get_income_statement(thscode, periods)
        
        statements = []
        for item in data:
            stmt = FinancialStatement(
                ticker=symbol,
                name=symbol,  # 稍后填充
                thscode=thscode,
                report_date=item.get('report_date', ''),
                report_type='income',
                revenue=item.get('total_revenue'),
                net_profit=item.get('net_profit'),
                gross_profit=item.get('gross_profit'),
                operating_profit=item.get('operating_profit'),
                total_profit=item.get('total_profit'),
            )
            statements.append(stmt)
        
        return statements
    
    def get_balance_sheet(self, symbol: str, periods: int = 4) -> List[FinancialStatement]:
        """获取资产负债表"""
        thscode = HiThinkFinanceClient.to_thscode(symbol)
        data = self.client.get_balance_sheet(thscode, periods)
        
        statements = []
        for item in data:
            stmt = FinancialStatement(
                ticker=symbol,
                name=symbol,
                thscode=thscode,
                report_date=item.get('report_date', ''),
                report_type='balance',
                total_assets=item.get('total_assets'),
                total_liabilities=item.get('total_liabilities'),
                total_equity=item.get('total_equity'),
                current_assets=item.get('current_assets'),
                current_liabilities=item.get('current_liabilities'),
                monetary_funds=item.get('monetary_funds'),
            )
            statements.append(stmt)
        
        return statements
    
    def get_cash_flow(self, symbol: str, periods: int = 4) -> List[FinancialStatement]:
        """获取现金流量表"""
        thscode = HiThinkFinanceClient.to_thscode(symbol)
        data = self.client.get_cash_flow(thscode, periods)
        
        statements = []
        for item in data:
            stmt = FinancialStatement(
                ticker=symbol,
                name=symbol,
                thscode=thscode,
                report_date=item.get('report_date', ''),
                report_type='cashflow',
                operating_cashflow=item.get('net_operating_cashflow'),
                investing_cashflow=item.get('net_investing_cashflow'),
                financing_cashflow=item.get('net_financing_cashflow'),
                net_cashflow=item.get('net_cash_increase'),
            )
            statements.append(stmt)
        
        return statements
    
    def calculate_indicators(self, symbol: str, name: str = None, report: str = None) -> FinancialIndicators:
        """
        计算财务指标
        
        Args:
            symbol: A股代码
            name: 股票名称
            report: 报告期（如 2024-4，默认为最新年报 2024-4）
        
        Returns:
            FinancialIndicators: 财务指标
        """
        thscode = HiThinkFinanceClient.to_thscode(symbol)
        
        # 默认使用最新年报
        if report is None:
            report = '2024-4'
        
        # 获取财务指标（HiThink 已计算好）
        indicators_data = self.client.get_financial_indicators(thscode, report)
        
        if not indicators_data:
            raise ValueError(f"财务数据不足: {symbol}")
        
        # 提取各能力维度指标
        metrics = {
            'growth': {},
            'profitability': {},
            'solvency': {},
            'operation': {},
            'cash-flow': {}
        }
        
        for item in indicators_data:
            ability = item.get('ability')
            index_id = item.get('index_id')
            value = item.get('value')
            
            if ability in metrics:
                metrics[ability][index_id] = value
        
        # 创建指标对象
        indicators = FinancialIndicators(
            ticker=symbol,
            name=name or symbol,
            report_date=indicators_data[0].get('report', '') if indicators_data else '',
        )
        
        # ========== 盈利能力 ==========
        profitability = metrics['profitability']
        indicators.roe = profitability.get('index_weighted_avg_roe')
        indicators.net_margin = profitability.get('sale_net_interest_ratio')  # 银行用净息差
        indicators.gross_margin = profitability.get('sale_gross_margin')
        indicators.roa = profitability.get('total_assets_net_ratio')
        
        # ========== 成长能力 ==========
        growth = metrics['growth']
        indicators.revenue_growth = growth.get('calculate_operating_income_yoy_growth_ratio')
        indicators.profit_growth = growth.get('calculate_parent_holder_net_profit_yoy_growth_ratio')
        indicators.asset_growth = growth.get('total_assets_growth_ratio')
        
        # ========== 偿债能力 ==========
        solvency = metrics['solvency']
        indicators.current_ratio = solvency.get('current_ratio')
        indicators.quick_ratio = solvency.get('quick_ratio')
        indicators.debt_ratio = solvency.get('assets_debt_ratio')
        indicators.interest_coverage = solvency.get('earned_interest_multiple')
        
        # ========== 营运能力 ==========
        operation = metrics['operation']
        indicators.asset_turnover = operation.get('total_assets_turnover_ratio')
        indicators.inventory_turnover = operation.get('inventory_turnover_ratio')
        indicators.receivable_turnover = operation.get('receive_account_turnover_ratio')
        
        # ========== 现金流 ==========
        cashflow = metrics['cash-flow']
        indicators.ocf_to_profit = cashflow.get('net_profit_cash_content')
        indicators.ocf_to_revenue = cashflow.get('operating_cash_flow_net_divide_income')
        
        # ========== 综合评分 ==========
        indicators = self._calculate_scores(indicators)
        
        return indicators
    
    def _calculate_scores(self, indicators: FinancialIndicators) -> FinancialIndicators:
        """计算综合评分"""
        
        # 盈利能力评分 (0-100)
        profitability_scores = []
        if indicators.roe is not None:
            # ROE: <5%=0分, >20%=100分
            roe_score = max(0, min(100, (indicators.roe - 5) / 15 * 100))
            profitability_scores.append(roe_score)
        
        if indicators.net_margin is not None:
            # 净利率: <5%=0分, >30%=100分
            margin_score = max(0, min(100, (indicators.net_margin - 5) / 25 * 100))
            profitability_scores.append(margin_score)
        
        if indicators.gross_margin is not None:
            # 毛利率: <20%=0分, >60%=100分
            gross_score = max(0, min(100, (indicators.gross_margin - 20) / 40 * 100))
            profitability_scores.append(gross_score)
        
        if profitability_scores:
            indicators.profitability_score = sum(profitability_scores) / len(profitability_scores)
        
        # 成长能力评分 (0-100)
        growth_scores = []
        if indicators.revenue_growth is not None:
            # 营收增长: <0%=0分, >30%=100分
            growth_score = max(0, min(100, (indicators.revenue_growth / 30) * 100))
            growth_scores.append(growth_score)
        
        if indicators.profit_growth is not None:
            # 利润增长: <0%=0分, >30%=100分
            profit_score = max(0, min(100, (indicators.profit_growth / 30) * 100))
            growth_scores.append(profit_score)
        
        if growth_scores:
            indicators.growth_score = sum(growth_scores) / len(growth_scores)
        
        # 偿债能力评分 (0-100)
        solvency_scores = []
        if indicators.current_ratio is not None:
            # 流动比率: <1=0分, >2=100分
            current_score = max(0, min(100, (indicators.current_ratio - 1) * 100))
            solvency_scores.append(current_score)
        
        if indicators.debt_ratio is not None:
            # 资产负债率: >80%=0分, <30%=100分
            debt_score = max(0, min(100, (80 - indicators.debt_ratio) / 50 * 100))
            solvency_scores.append(debt_score)
        
        if solvency_scores:
            indicators.solvency_score = sum(solvency_scores) / len(solvency_scores)
        
        # 营运能力评分 (0-100)
        efficiency_scores = []
        if indicators.asset_turnover is not None:
            # 总资产周转率: <0.5=0分, >2=100分
            turnover_score = max(0, min(100, (indicators.asset_turnover - 0.5) / 1.5 * 100))
            efficiency_scores.append(turnover_score)
        
        if indicators.ocf_to_revenue is not None:
            # 现金流/营收: <5%=0分, >20%=100分
            ocf_score = max(0, min(100, (indicators.ocf_to_revenue - 5) / 15 * 100))
            efficiency_scores.append(ocf_score)
        
        if efficiency_scores:
            indicators.efficiency_score = sum(efficiency_scores) / len(efficiency_scores)
        
        # 综合评分
        all_scores = [
            indicators.profitability_score,
            indicators.growth_score,
            indicators.solvency_score,
            indicators.efficiency_score,
        ]
        valid_scores = [s for s in all_scores if s is not None]
        if valid_scores:
            indicators.overall_score = sum(valid_scores) / len(valid_scores)
        
        return indicators
    
    def analyze_portfolio(self, holdings: List[Dict]) -> List[FinancialIndicators]:
        """
        分析组合财务指标
        
        Args:
            holdings: 持仓列表 [{'symbol': '000001', 'name': '平安银行', 'weight': 0.2}, ...]
        
        Returns:
            List[FinancialIndicators]: 财务指标列表
        """
        results = []
        
        for holding in holdings:
            symbol = holding['symbol']
            name = holding.get('name', symbol)
            
            try:
                indicators = self.calculate_indicators(symbol, name)
                results.append(indicators)
            except Exception as e:
                print(f"  ⚠️ {name} ({symbol}) 财务分析失败: {e}")
        
        return results
    
    def generate_report(self, holdings: List[Dict]) -> str:
        """
        生成财务分析报告
        
        Args:
            holdings: 持仓列表
        
        Returns:
            str: Markdown 报告
        """
        indicators_list = self.analyze_portfolio(holdings)
        
        # 生成报告
        report = "# 📊 财务分析报告\n\n"
        report += f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        report += "---\n\n"
        
        # 汇总表
        report += "## 一、财务指标概览\n\n"
        report += "| 标的 | ROE | 净利率 | 营收增长 | 资产负债率 | 综合评分 |\n"
        report += "|------|-----|--------|----------|------------|----------|\n"
        
        for ind in indicators_list:
            roe_str = f"{ind.roe:.1f}%" if ind.roe is not None else "N/A"
            margin_str = f"{ind.net_margin:.1f}%" if ind.net_margin is not None else "N/A"
            growth_str = f"{ind.revenue_growth:+.1f}%" if ind.revenue_growth is not None else "N/A"
            debt_str = f"{ind.debt_ratio:.1f}%" if ind.debt_ratio is not None else "N/A"
            score_str = f"{ind.overall_score:.0f}" if ind.overall_score is not None else "N/A"
            
            # 评分颜色
            if ind.overall_score and ind.overall_score >= 70:
                score_str = f"🟢 {score_str}"
            elif ind.overall_score and ind.overall_score >= 50:
                score_str = f"🟡 {score_str}"
            else:
                score_str = f"🔴 {score_str}"
            
            report += f"| {ind.name} | {roe_str} | {margin_str} | {growth_str} | {debt_str} | {score_str} |\n"
        
        report += "\n---\n\n"
        
        # 详细分析
        report += "## 二、详细财务分析\n\n"
        
        for ind in indicators_list:
            report += f"### {ind.name} ({ind.ticker})\n\n"
            report += f"**报告期**: {ind.report_date}\n\n"
            
            # 盈利能力
            report += "#### 盈利能力\n\n"
            if ind.roe is not None:
                report += f"- **ROE**: {ind.roe:.2f}%\n"
            if ind.net_margin is not None:
                report += f"- **净利率**: {ind.net_margin:.2f}%\n"
            if ind.gross_margin is not None:
                report += f"- **毛利率**: {ind.gross_margin:.2f}%\n"
            if ind.profitability_score is not None:
                report += f"- **盈利评分**: {ind.profitability_score:.0f}/100\n"
            report += "\n"
            
            # 成长能力
            report += "#### 成长能力\n\n"
            if ind.revenue_growth is not None:
                report += f"- **营收增长**: {ind.revenue_growth:+.2f}%\n"
            if ind.profit_growth is not None:
                report += f"- **利润增长**: {ind.profit_growth:+.2f}%\n"
            if ind.growth_score is not None:
                report += f"- **成长评分**: {ind.growth_score:.0f}/100\n"
            report += "\n"
            
            # 偿债能力
            report += "#### 偿债能力\n\n"
            if ind.debt_ratio is not None:
                report += f"- **资产负债率**: {ind.debt_ratio:.2f}%\n"
            if ind.current_ratio is not None:
                report += f"- **流动比率**: {ind.current_ratio:.2f}\n"
            if ind.solvency_score is not None:
                report += f"- **偿债评分**: {ind.solvency_score:.0f}/100\n"
            report += "\n"
            
            # 现金流
            if ind.ocf_to_revenue is not None or ind.free_cashflow is not None:
                report += "#### 现金流\n\n"
                if ind.ocf_to_revenue is not None:
                    report += f"- **经营现金流/营收**: {ind.ocf_to_revenue:.2f}%\n"
                if ind.free_cashflow is not None:
                    report += f"- **自由现金流**: {ind.free_cashflow:,.0f}\n"
                report += "\n"
            
            report += "---\n\n"
        
        # 综合建议
        report += "## 三、综合建议\n\n"
        
        # 按综合评分排序
        scored = [ind for ind in indicators_list if ind.overall_score is not None]
        scored.sort(key=lambda x: x.overall_score, reverse=True)
        
        if scored:
            report += "### 财务质量排名\n\n"
            for i, ind in enumerate(scored[:5], 1):
                report += f"{i}. **{ind.name}**: {ind.overall_score:.0f}分"
                if ind.roe:
                    report += f" (ROE {ind.roe:.1f}%)"
                report += "\n"
            report += "\n"
        
        # 风险提示
        weak = [ind for ind in indicators_list if ind.overall_score is not None and ind.overall_score < 50]
        if weak:
            report += "### 财务风险关注\n\n"
            for ind in weak:
                report += f"- **{ind.name}**: 综合评分 {ind.overall_score:.0f}分"
                if ind.debt_ratio and ind.debt_ratio > 70:
                    report += f"，资产负债率较高 ({ind.debt_ratio:.1f}%)"
                if ind.roe and ind.roe < 5:
                    report += f"，ROE 较低 ({ind.roe:.1f}%)"
                report += "\n"
            report += "\n"
        
        report += "---\n\n"
        report += "*报告生成: LLM-native 量化系统 | HiThink Financial-API*\n"
        
        return report


# 测试
if __name__ == '__main__':
    print("=" * 60)
    print("🧪 财务分析器测试")
    print("=" * 60)
    
    analyzer = FinancialAnalyzer()
    
    # 测试持仓
    holdings = [
        {'symbol': '000001', 'name': '平安银行', 'weight': 0.2},
        {'symbol': '000598', 'name': '兴蓉环境', 'weight': 0.15},
        {'symbol': '000027', 'name': '深圳能源', 'weight': 0.12},
        {'symbol': '600011', 'name': '华能国际', 'weight': 0.13},
        {'symbol': '600027', 'name': '华电国际', 'weight': 0.12},
        {'symbol': '600023', 'name': '浙能电力', 'weight': 0.12},
        {'symbol': '600642', 'name': '申能股份', 'weight': 0.11},
        {'symbol': '601398', 'name': '工商银行', 'weight': 0.05},
    ]
    
    print("\n1. 分析持仓财务指标...")
    indicators_list = analyzer.analyze_portfolio(holdings)
    for ind in indicators_list:
        print(f"   {ind.name}: ROE={ind.roe:.2f}%, 净利率={ind.net_margin:.2f}%, 综合评分={ind.overall_score:.0f}")
    
    print("\n2. 生成财务报告...")
    report = analyzer.generate_report(holdings)
    
    # 保存报告
    output_path = Path(__file__).parent.parent / 'docs' / 'financial_report.md'
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"   ✅ 报告已保存: {output_path}")
    
    print("\n" + "=" * 60)
    print("✅ 测试完成")
    print("=" * 60)
