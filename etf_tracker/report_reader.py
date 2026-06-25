#!/usr/bin/env python3
"""
研报解析模块 - 提取机构观点并生成投资信号
支持：
1. PDF 研报解析（本地文件）
2. 文本/网页研报解析
3. 多家机构观点聚合
4. 生成"机构观点"报告章节
"""

import os
import re
import json
import pandas as pd
from datetime import datetime
from typing import List, Dict, Optional, Union
from dataclasses import dataclass
import warnings
warnings.filterwarnings('ignore')


@dataclass
class ResearchReport:
    """研报数据类"""
    title: str = ""
    institution: str = ""
    author: str = ""
    date: str = ""
    target_code: str = ""
    target_name: str = ""
    rating: str = ""  # 买入/增持/中性/减持
    target_price: float = 0.0
    current_price: float = 0.0
    summary: str = ""
    key_points: List[str] = None
    risks: List[str] = None
    source: str = ""
    
    def __post_init__(self):
        if self.key_points is None:
            self.key_points = []
        if self.risks is None:
            self.risks = []
    
    def to_dict(self) -> Dict:
        return {
            'title': self.title,
            'institution': self.institution,
            'author': self.author,
            'date': self.date,
            'target_code': self.target_code,
            'target_name': self.target_name,
            'rating': self.rating,
            'target_price': self.target_price,
            'current_price': self.current_price,
            'upside': self._calculate_upside(),
            'summary': self.summary,
            'key_points': self.key_points,
            'risks': self.risks,
            'source': self.source
        }
    
    def _calculate_upside(self) -> float:
        if self.current_price > 0 and self.target_price > 0:
            return round((self.target_price - self.current_price) / self.current_price * 100, 2)
        return 0.0


class PDFReportParser:
    """PDF 研报解析器"""
    
    def __init__(self):
        self.text = ""
    
    def parse(self, file_path: str) -> ResearchReport:
        """解析 PDF 研报"""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"文件不存在: {file_path}")
        
        # 尝试使用 PyPDF2
        try:
            text = self._parse_with_pypdf2(file_path)
            if not text or len(text) < 50:
                text = self._parse_with_pymupdf(file_path)
        except Exception:
            # 回退到 pymupdf
            try:
                text = self._parse_with_pymupdf(file_path)
            except Exception as e:
                raise RuntimeError(f"PDF 解析失败: {e}")
        
        self.text = text
        return self._extract_info(text, file_path)
    
    def _parse_with_pypdf2(self, file_path: str) -> str:
        """使用 PyPDF2 解析"""
        try:
            import PyPDF2
            text = ""
            with open(file_path, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
            return text
        except ImportError:
            return ""
    
    def _parse_with_pymupdf(self, file_path: str) -> str:
        """使用 PyMuPDF 解析"""
        try:
            import fitz
            text = ""
            doc = fitz.open(file_path)
            for page in doc:
                text += page.get_text() + "\n"
            doc.close()
            return text
        except ImportError:
            return ""
    
    def _extract_info(self, text: str, file_path: str) -> ResearchReport:
        """从文本中提取关键信息"""
        report = ResearchReport()
        report.source = file_path
        
        # 提取标题（前 200 字符中较长的那行）
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        if lines:
            # 标题通常是前几句中较长的
            for line in lines[:10]:
                if len(line) > 10 and not any(k in line for k in ['证券研究报告', '请仔细阅读', '免责声明']):
                    report.title = line
                    break
        
        # 提取机构名称
        institution_patterns = [
            r'([\u4e00-\u9fa5]{2,8}证券)',
            r'([\u4e00-\u9fa5]{2,8}基金)',
            r'([\u4e00-\u9fa5]{2,8}资管)',
            r'([\u4e00-\u9fa5]{2,8}研究所)'
        ]
        for pattern in institution_patterns:
            match = re.search(pattern, text)
            if match:
                report.institution = match.group(1)
                break
        
        # 提取评级（中英文兼容，放宽匹配）
        rating_patterns = {
            '买入': r'(买入|BUY|Buy)',
            '增持': r'(增持|Overweight|OVERWEIGHT)',
            '中性': r'(中性|Neutral|NEUTRAL|HOLD|Hold)',
            '减持': r'(减持|Underweight|UNDERWEIGHT|Sell|SELL)'
        }
        for rating, pattern in rating_patterns.items():
            if re.search(pattern, text):
                report.rating = rating
                break
        
        # 提取目标价（中英文兼容，支持 RMB/Yuan/元）
        price_patterns = [
            r'目标价[：:\s]*(\d+\.?\d*)',
            r'目标价格[：:\s]*(\d+\.?\d*)',
            r'目标价\s*(\d+\.?\d*)\s*元',
            r'目标价.*?([\d\.]+)\s*元',
            r'Target Price[：:\s]*([\d\.]+)',
            r'Target[：:\s]*([\d\.]+)',
            r'Target Price:\s*([\d\.]+)\s*(RMB|Yuan|元)?'
        ]
        for pattern in price_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                report.target_price = float(match.group(1))
                break
        
        # 提取当前价（中英文兼容）
        current_patterns = [
            r'当前价[：:\s]*(\d+\.?\d*)',
            r'现价[：:\s]*(\d+\.?\d*)',
            r'收盘价[：:\s]*(\d+\.?\d*)',
            r'Current Price[：:\s]*([\d\.]+)',
            r'Price[：:\s]*([\d\.]+)'
        ]
        for pattern in current_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                report.current_price = float(match.group(1))
                break
        
        # 提取日期
        date_patterns = [
            r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日',
            r'(\d{4}-\d{2}-\d{2})',
            r'(\d{4}/\d{2}/\d{2})'
        ]
        for pattern in date_patterns:
            match = re.search(pattern, text)
            if match:
                if len(match.groups()) == 3:
                    report.date = f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
                else:
                    report.date = match.group(1).replace('/', '-')
                break
        
        # 提取核心观点（"投资要点"、"核心观点"之后的内容）
        report.summary = self._extract_summary(text)
        report.key_points = self._extract_key_points(text)
        report.risks = self._extract_risks(text)
        
        return report
    
    def _extract_summary(self, text: str) -> str:
        """提取摘要（中英文兼容）"""
        summary_patterns = [
            r'投资要点\s*[:：]?\s*(.*?)(?=\n\s*\n|核心观点|盈利预测)',
            r'核心观点\s*[:：]?\s*(.*?)(?=\n\s*\n|投资要点|盈利预测)',
            r'投资评级[:：]\s*\S+\s*(.*?)(?=\n\s*\n|风险提示)',
            r'Investment Highlights\s*[:：]?\s*(.*?)(?=\n\s*\n|Core Views|Risks)',
            r'Core Views\s*[:：]?\s*(.*?)(?=\n\s*\n|Investment Highlights|Risks)',
            r'Executive Summary\s*[:：]?\s*(.*?)(?=\n\s*\n|Risks)'
        ]
        for pattern in summary_patterns:
            match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
            if match:
                summary = match.group(1).strip()
                # 清理多余空白
                summary = re.sub(r'\s+', ' ', summary)
                if len(summary) > 20:
                    return summary[:500]
        return ""
    
    def _extract_key_points(self, text: str) -> List[str]:
        """提取关键要点（中英文兼容）"""
        points = []
        # 查找 "投资要点" 或 "Investment Highlights" 后的列表项
        match = re.search(r'(投资要点|Investment Highlights)\s*[:：]?\s*(.*?)(?=风险提示|盈利预测|投资评级|Risks|Core Views)', text, re.DOTALL | re.IGNORECASE)
        if match:
            section = match.group(2)
            # 提取以数字、项目符号开头的段落
            items = re.findall(r'[\u25cf\u2022\u25cb\u2605]?\s*([\d一二三四五六七八九十]+[\.、])?\s*([^\n]{10,200})', section)
            for _, item in items[:5]:
                clean = re.sub(r'\s+', ' ', item).strip()
                if clean and len(clean) > 10:
                    points.append(clean)
        
        # 如果没有提取到，尝试提取前几个关键句子
        if not points:
            sentences = re.split(r'(?<=[。！？\.\!\?])\s+', text[:3000])
            for sent in sentences[:5]:
                if len(sent) > 20:
                    points.append(sent.strip())
        
        return points[:5]
    
    def _extract_risks(self, text: str) -> List[str]:
        """提取风险提示（中英文兼容）"""
        risks = []
        match = re.search(r'(风险提示|Risks|Risk Factors)\s*[:：]?\s*(.*?)(?=\n\s*\n|免责声明|投资评级|Disclaimer)', text, re.DOTALL | re.IGNORECASE)
        if match:
            section = match.group(2)
            items = re.findall(r'[\u25cf\u2022\u25cb]?\s*([^\n]{10,200})', section)
            for item in items[:5]:
                clean = re.sub(r'\s+', ' ', item).strip()
                if clean and len(clean) > 10:
                    risks.append(clean)
        return risks


class ReportAggregator:
    """研报观点聚合器"""
    
    def __init__(self):
        self.reports: List[ResearchReport] = []
    
    def add_report(self, report: ResearchReport):
        """添加研报"""
        self.reports.append(report)
    
    def add_from_pdf(self, file_path: str):
        """从 PDF 添加研报"""
        parser = PDFReportParser()
        report = parser.parse(file_path)
        self.reports.append(report)
    
    def add_from_text(self, text: str, metadata: Dict = None):
        """从文本添加研报"""
        parser = PDFReportParser()
        report = parser._extract_info(text, metadata.get('source', 'text') if metadata else 'text')
        if metadata:
            for key, value in metadata.items():
                if hasattr(report, key) and value:
                    setattr(report, key, value)
        self.reports.append(report)
    
    def get_by_code(self, code: str) -> List[ResearchReport]:
        """按代码获取研报"""
        return [r for r in self.reports if r.target_code == code]
    
    def aggregate_view(self, code: str) -> Dict:
        """聚合某只标的的机构观点"""
        reports = self.get_by_code(code)
        if not reports:
            return {}
        
        # 评级统计
        rating_count = {}
        target_prices = []
        upside_list = []
        summaries = []
        key_points = []
        risks = []
        
        for r in reports:
            if r.rating:
                rating_count[r.rating] = rating_count.get(r.rating, 0) + 1
            if r.target_price > 0:
                target_prices.append(r.target_price)
            upside = r._calculate_upside()
            if upside != 0:
                upside_list.append(upside)
            if r.summary:
                summaries.append(r.summary)
            key_points.extend(r.key_points)
            risks.extend(r.risks)
        
        # 计算一致目标价和平均上行空间
        avg_target = sum(target_prices) / len(target_prices) if target_prices else 0
        avg_upside = sum(upside_list) / len(upside_list) if upside_list else 0
        
        # 多数评级
        majority_rating = ""
        if rating_count:
            majority_rating = max(rating_count, key=rating_count.get)
        
        # 买入/增持比例
        bullish_count = rating_count.get('买入', 0) + rating_count.get('增持', 0)
        total_rating = sum(rating_count.values())
        bullish_ratio = bullish_count / total_rating if total_rating > 0 else 0
        
        return {
            'code': code,
            'report_count': len(reports),
            'rating_distribution': rating_count,
            'majority_rating': majority_rating,
            'bullish_ratio': round(bullish_ratio, 2),
            'avg_target_price': round(avg_target, 2),
            'avg_upside': round(avg_upside, 2),
            'latest_report_date': max([r.date for r in reports if r.date], default=""),
            'institutions': list(set([r.institution for r in reports if r.institution])),
            'summary_points': list(set(summaries))[:3],
            'key_points': list(set(key_points))[:5],
            'risk_points': list(set(risks))[:5]
        }
    
    def generate_markdown_section(self, code: str, name: str = "") -> str:
        """生成 Markdown 格式的机构观点章节"""
        view = self.aggregate_view(code)
        if not view:
            return f"\n## 机构观点：{name or code}\n\n暂无相关研报。\n"
        
        md = f"\n## 机构观点：{name or code}\n\n"
        md += f"**研报数量**: {view['report_count']} 篇 | "
        md += f"**多数评级**: {view['majority_rating'] or '-'} | "
        md += f"**看多比例**: {view['bullish_ratio']*100:.0f}% | "
        md += f"**平均目标价**: {view['avg_target_price']:.2f} 元 | "
        md += f"**平均上行空间**: {view['avg_upside']:+.2f}%\n\n"
        
        md += "**评级分布**:\n\n"
        for rating, count in view['rating_distribution'].items():
            md += f"- {rating}: {count} 篇\n"
        
        if view['summary_points']:
            md += "\n**核心观点**:\n\n"
            for i, point in enumerate(view['summary_points'], 1):
                md += f"{i}. {point}\n"
        
        if view['key_points']:
            md += "\n**关键要点**:\n\n"
            for point in view['key_points']:
                md += f"- {point}\n"
        
        if view['risk_points']:
            md += "\n**风险提示**:\n\n"
            for point in view['risk_points']:
                md += f"- {point}\n"
        
        md += "\n"
        return md


if __name__ == "__main__":
    # 测试示例
    aggregator = ReportAggregator()
    
    # 示例：手动添加一篇研报文本
    sample_text = """
    中信证券研究报告
    贵州茅台（600519）深度报告
    日期：2026-06-30
    
    投资评级：买入
    目标价：1800元
    当前价：1185元
    
    投资要点：
    1. 白酒行业龙头地位稳固，飞天茅台需求刚性
    2. 直销渠道占比提升，吨价上行趋势明确
    3. 分红率提升，股东回报持续改善
    
    风险提示：
    1. 宏观经济下行影响高端白酒需求
    2. 渠道库存波动风险
    """
    
    aggregator.add_from_text(sample_text, {
        'source': 'sample',
        'target_code': '600519',
        'target_name': '贵州茅台'
    })
    
    section = aggregator.generate_markdown_section('600519', '贵州茅台')
    print(section)
