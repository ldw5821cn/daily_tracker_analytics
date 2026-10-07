#!/usr/bin/env python3
"""
估值分析器 - 基于 HiThink Financial-API
提供 PE/PB/PS/PC 估值分析、行业对比、历史分位数计算
"""

import sys
import os
import json
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from dataclasses import dataclass
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

# 导入 HiThink 客户端
try:
    from hithink_client import HiThinkFinanceClient
except ImportError:
    try:
        from multi_agent.hithink_client import HiThinkFinanceClient
    except ImportError:
        raise ImportError("HiThink 客户端不可用，请先安装 hithink_client.py")


@dataclass
class ValuationMetrics:
    """估值指标数据类"""
    ticker: str
    name: str
    thscode: str
    
    # 估值指标
    pe_ttm: Optional[float] = None      # 市盈率 TTM
    pe_mrq: Optional[float] = None      # 市盈率 MRQ
    pb_mrq: Optional[float] = None      # 市净率 MRQ
    ps_ttm: Optional[float] = None      # 市销率 TTM
    pc_ttm: Optional[float] = None      # 市现率 TTM
    
    # 衍生指标
    pb_percentile: Optional[float] = None   # PB 历史分位数
    pe_percentile: Optional[float] = None   # PE 历史分位数
    valuation_status: str = "unknown"       # 估值状态：undervalued/fair/overvalued
    
    # 行业对比
    industry: Optional[str] = None
    industry_pe_median: Optional[float] = None
    industry_pb_median: Optional[float] = None
    pe_vs_industry: Optional[float] = None  # 相对行业溢价/折价
    pb_vs_industry: Optional[float] = None
    
    # 股息率（如可获取）
    dividend_yield: Optional[float] = None
    
    def to_dict(self) -> dict:
        return {
            'ticker': self.ticker,
            'name': self.name,
            'thscode': self.thscode,
            'pe_ttm': self.pe_ttm,
            'pe_mrq': self.pe_mrq,
            'pb_mrq': self.pb_mrq,
            'ps_ttm': self.ps_ttm,
            'pc_ttm': self.pc_ttm,
            'pb_percentile': self.pb_percentile,
            'pe_percentile': self.pe_percentile,
            'valuation_status': self.valuation_status,
            'industry': self.industry,
            'industry_pe_median': self.industry_pe_median,
            'industry_pb_median': self.industry_pb_median,
            'pe_vs_industry': self.pe_vs_industry,
            'pb_vs_industry': self.pb_vs_industry,
            'dividend_yield': self.dividend_yield,
        }


class ValuationAnalyzer:
    """估值分析器"""
    
    def __init__(self):
        self.client = HiThinkFinanceClient()
        self._cache = {}
    
    def analyze_stock(self, symbol: str, name: str = None) -> ValuationMetrics:
        """
        分析单只股票估值
        
        Args:
            symbol: A股代码（如 000001）
            name: 股票名称（可选）
        
        Returns:
            ValuationMetrics: 估值指标
        """
        thscode = HiThinkFinanceClient.to_thscode(symbol)
        
        # 获取估值数据
        valuations = self.client.get_valuation([thscode])
        if not valuations:
            raise ValueError(f"无法获取估值数据: {symbol}")
        
        v = valuations[0]
        
        metrics = ValuationMetrics(
            ticker=symbol,
            name=name or symbol,
            thscode=thscode,
            pe_ttm=v.get('pe_ttm'),
            pe_mrq=v.get('pe_mrq'),
            pb_mrq=v.get('pb_mrq'),
            ps_ttm=v.get('ps_ttm'),
            pc_ttm=v.get('pc_ttm'),
        )
        
        # 计算估值状态
        metrics.valuation_status = self._classify_valuation(metrics)
        
        return metrics
    
    def analyze_portfolio(self, holdings: List[Dict]) -> List[ValuationMetrics]:
        """
        分析组合估值
        
        Args:
            holdings: 持仓列表 [{'symbol': '000001', 'name': '平安银行', 'weight': 0.2}, ...]
        
        Returns:
            List[ValuationMetrics]: 估值指标列表
        """
        results = []
        
        for holding in holdings:
            symbol = holding['symbol']
            name = holding.get('name', symbol)
            
            try:
                metrics = self.analyze_stock(symbol, name)
                results.append(metrics)
            except Exception as e:
                print(f"  ⚠️ {name} ({symbol}) 估值分析失败: {e}")
        
        return results
    
    def compare_with_industry(self, metrics: ValuationMetrics) -> ValuationMetrics:
        """
        与行业对比
        
        Args:
            metrics: 估值指标
        
        Returns:
            ValuationMetrics: 更新后的估值指标（含行业对比）
        """
        # 获取行业数据（简化版，实际应通过 HiThink 行业接口）
        # 这里使用模拟数据演示
        industry_data = self._get_industry_benchmarks(metrics.ticker)
        
        if industry_data:
            metrics.industry = industry_data.get('industry')
            metrics.industry_pe_median = industry_data.get('pe_median')
            metrics.industry_pb_median = industry_data.get('pb_median')
            
            # 计算相对行业溢价/折价
            if metrics.pe_ttm and metrics.industry_pe_median:
                metrics.pe_vs_industry = (metrics.pe_ttm / metrics.industry_pe_median - 1) * 100
            
            if metrics.pb_mrq and metrics.industry_pb_median:
                metrics.pb_vs_industry = (metrics.pb_mrq / metrics.industry_pb_median - 1) * 100
        
        return metrics
    
    def calculate_percentile(self, symbol: str, years: int = 5) -> Dict[str, float]:
        """
        计算历史估值分位数（需要历史数据，简化版）
        
        Args:
            symbol: A股代码
            years: 历史年数
        
        Returns:
            Dict: {'pb_percentile': 0.3, 'pe_percentile': 0.5}
        """
        # 注意：HiThink API 暂不提供历史估值数据
        # 这里返回模拟数据，实际应通过其他数据源计算
        return {
            'pb_percentile': 0.5,  # 50% 分位
            'pe_percentile': 0.5,
            'note': '历史分位数需要额外数据源，当前为模拟值'
        }
    
    def screen_undervalued(self, criteria: Dict = None) -> List[Dict]:
        """
        筛选低估股票（简化版）
        
        Args:
            criteria: 筛选条件 {'pb_max': 1.0, 'pe_max': 15, 'min_market_cap': 50e8}
        
        Returns:
            List[Dict]: 低估股票列表
        """
        # 注意：HiThink API 暂不提供全市场扫描
        # 这里返回示例数据，实际应通过全市场数据接口实现
        print("⚠️ 全市场扫描需要额外数据接口，当前返回示例数据")
        
        examples = [
            {'symbol': '000001', 'name': '平安银行', 'pb': 0.48, 'pe': 5.17, 'reason': '破净+低PE'},
            {'symbol': '601398', 'name': '工商银行', 'pb': 0.74, 'pe': 7.89, 'reason': '破净+高股息'},
        ]
        
        return examples
    
    def _classify_valuation(self, metrics: ValuationMetrics) -> str:
        """估值分类"""
        pb = metrics.pb_mrq
        pe = metrics.pe_ttm
        
        # 简单规则（实际应使用历史分位数）
        if pb and pb < 0.8:
            return "undervalued"  # 破净，低估
        elif pb and pb > 2.0:
            return "overvalued"   # 高 PB，高估
        elif pe and pe < 10:
            return "undervalued"  # 低 PE，低估
        elif pe and pe > 30:
            return "overvalued"   # 高 PE，高估
        else:
            return "fair"         # 合理
    
    def _get_industry_benchmarks(self, symbol: str) -> Optional[Dict]:
        """获取行业基准数据（模拟）"""
        # 模拟数据，实际应通过 HiThink 行业接口获取
        industry_map = {
            '000001': {'industry': '银行', 'pe_median': 6.5, 'pb_median': 0.65},
            '601398': {'industry': '银行', 'pe_median': 6.5, 'pb_median': 0.65},
            '000598': {'industry': '公用事业', 'pe_median': 15.0, 'pb_median': 1.5},
        }
        return industry_map.get(symbol)
    
    def generate_report(self, holdings: List[Dict]) -> str:
        """
        生成估值分析报告（Markdown）
        
        Args:
            holdings: 持仓列表
        
        Returns:
            str: Markdown 报告
        """
        metrics_list = self.analyze_portfolio(holdings)
        
        # 添加行业对比
        for m in metrics_list:
            self.compare_with_industry(m)
        
        # 生成报告
        report = "# 📊 估值分析报告\n\n"
        report += f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        report += "---\n\n"
        
        # 汇总表
        report += "## 一、持仓估值概览\n\n"
        report += "| 标的 | PE(TTM) | PB(MRQ) | 估值状态 | 行业 | PE vs 行业 | PB vs 行业 |\n"
        report += "|------|---------|---------|----------|------|------------|------------|\n"
        
        for m in metrics_list:
            pe_str = f"{m.pe_ttm:.2f}" if m.pe_ttm else "N/A"
            pb_str = f"{m.pb_mrq:.2f}" if m.pb_mrq else "N/A"
            status_icon = {"undervalued": "🟢 低估", "fair": "🟡 合理", "overvalued": "🔴 高估"}.get(m.valuation_status, "⚪ 未知")
            industry = m.industry or "-"
            pe_vs = f"{m.pe_vs_industry:+.1f}%" if m.pe_vs_industry else "-"
            pb_vs = f"{m.pb_vs_industry:+.1f}%" if m.pb_vs_industry else "-"
            
            report += f"| {m.name} | {pe_str} | {pb_str} | {status_icon} | {industry} | {pe_vs} | {pb_vs} |\n"
        
        report += "\n---\n\n"
        
        # 低估标的
        undervalued = [m for m in metrics_list if m.valuation_status == "undervalued"]
        if undervalued:
            report += "## 二、低估标的\n\n"
            for m in undervalued:
                report += f"### {m.name} ({m.ticker})\n\n"
                report += f"- **PB**: {m.pb_mrq:.2f}"
                if m.pb_vs_industry:
                    report += f"（较行业 {m.pb_vs_industry:+.1f}%）"
                report += "\n"
                report += f"- **PE**: {m.pe_ttm:.2f}"
                if m.pe_vs_industry:
                    report += f"（较行业 {m.pe_vs_industry:+.1f}%）"
                report += "\n"
                if m.industry:
                    report += f"- **行业**: {m.industry}\n"
                report += "\n"
        
        # 高估标的
        overvalued = [m for m in metrics_list if m.valuation_status == "overvalued"]
        if overvalued:
            report += "## 三、高估标的（注意风险）\n\n"
            for m in overvalued:
                report += f"- **{m.name}**: PB {m.pb_mrq:.2f}, PE {m.pe_ttm:.2f}\n"
            report += "\n"
        
        # 建议
        report += "---\n\n## 四、估值建议\n\n"
        
        if undervalued:
            names = [m.name for m in undervalued]
            report += f"1. **低估标的**: {', '.join(names)}"
            report += "（安全边际较高，可关注）\n"
        
        if overvalued:
            names = [m.name for m in overvalued]
            report += f"2. **高估标的**: {', '.join(names)}"
            report += "（估值偏高，注意风险）\n"
        
        report += "\n---\n\n"
        report += "*报告生成: LLM-native 量化系统 | HiThink Financial-API*\n"
        
        return report


# 测试
if __name__ == '__main__':
    print("=" * 60)
    print("🧪 估值分析器测试")
    print("=" * 60)
    
    analyzer = ValuationAnalyzer()
    
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
    
    print("\n1. 分析持仓估值...")
    metrics_list = analyzer.analyze_portfolio(holdings)
    for m in metrics_list:
        print(f"   {m.name}: PE={m.pe_ttm:.2f}, PB={m.pb_mrq:.2f}, 状态={m.valuation_status}")
    
    print("\n2. 生成估值报告...")
    report = analyzer.generate_report(holdings)
    
    # 保存报告
    output_path = Path(__file__).parent.parent / 'docs' / 'valuation_report.md'
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"   ✅ 报告已保存: {output_path}")
    
    print("\n" + "=" * 60)
    print("✅ 测试完成")
    print("=" * 60)
