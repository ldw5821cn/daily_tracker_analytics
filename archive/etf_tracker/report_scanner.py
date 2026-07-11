#!/usr/bin/env python3
"""
自动研报扫描器
扫描 ~/etf_tracker/research_reports/ 目录下的 PDF 研报
提取机构观点并生成聚合报告
"""

import os
import re
import glob
import json
from datetime import datetime
from typing import List, Dict
import warnings
warnings.filterwarnings('ignore')

from report_reader import ReportAggregator, PDFReportParser, ResearchReport

REPORT_DIR = os.path.expanduser("~/etf_tracker/research_reports")
OUTPUT_DIR = os.path.expanduser("~/etf_tracker/reports")


def extract_code_from_filename(filename: str) -> str:
    """从文件名提取股票/ETF 代码"""
    # 支持格式：600519_贵州茅台.pdf、贵州茅台_600519.pdf、516150.pdf 等
    patterns = [
        r'(\d{6})',      # A股 6 位代码
        r'(\d{5,6}\.[A-Za-z]+)',  # 港股/美股 如 00700.HK、GRAB
        r'([A-Za-z]{2,6})'         # 纯英文代码
    ]
    base = os.path.basename(filename)
    for pattern in patterns:
        match = re.search(pattern, base)
        if match:
            return match.group(1)
    return ""


def extract_name_from_filename(filename: str) -> str:
    """从文件名提取名称"""
    base = os.path.splitext(os.path.basename(filename))[0]
    # 去掉代码，保留中文/英文名称
    name = re.sub(r'\d{5,6}(\.[A-Za-z]+)?', '', base)
    name = re.sub(r'[_\-]+', ' ', name).strip()
    return name


def scan_reports(report_dir: str = REPORT_DIR) -> ReportAggregator:
    """扫描目录并解析所有 PDF 研报"""
    aggregator = ReportAggregator()
    
    if not os.path.exists(report_dir):
        os.makedirs(report_dir, exist_ok=True)
        print(f"研报目录已创建: {report_dir}")
        return aggregator
    
    pdf_files = glob.glob(os.path.join(report_dir, "*.pdf"))
    if not pdf_files:
        print(f"未在 {report_dir} 发现 PDF 研报")
        return aggregator
    
    print(f"发现 {len(pdf_files)} 份 PDF 研报，开始解析...")
    
    for pdf_path in sorted(pdf_files):
        try:
            parser = PDFReportParser()
            report = parser.parse(pdf_path)
            
            # 从文件名补充代码/名称
            if not report.target_code:
                report.target_code = extract_code_from_filename(pdf_path)
            if not report.target_name:
                report.target_name = extract_name_from_filename(pdf_path)
            
            aggregator.add_report(report)
            print(f"  ✅ {os.path.basename(pdf_path)} -> {report.target_code} {report.target_name}")
            
        except Exception as e:
            print(f"  ❌ {os.path.basename(pdf_path)} 解析失败: {e}")
    
    return aggregator


def generate_research_summary(aggregator: ReportAggregator, codes: List[str] = None) -> str:
    """生成研报汇总 Markdown"""
    codes = codes or []
    md = f"""# 机构研报观点汇总

*扫描时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*  
*扫描目录: {REPORT_DIR}*

---

"""
    
    if not aggregator.reports:
        md += "暂无研报数据。\n"
        return md
    
    # 如果没有指定代码，扫描所有
    if not codes:
        codes = sorted(list(set(r.target_code for r in aggregator.reports if r.target_code)))
    
    # 过滤空字符串
    codes = [c for c in codes if c]    
    for code in codes:
        reports = aggregator.get_by_code(code)
        if not reports:
            continue
        
        name = reports[0].target_name or code
        md += aggregator.generate_markdown_section(code, name)
        md += "---\n\n"
    
    return md


def main():
    """主函数：扫描研报并输出汇总"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    aggregator = scan_reports()
    
    # 生成汇总报告
    md = generate_research_summary(aggregator)
    
    output_path = os.path.join(OUTPUT_DIR, f"research_summary_{datetime.now().strftime('%Y%m%d')}.md")
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(md)
    
    print(f"\n研报汇总报告已保存: {output_path}")
    
    # 保存 JSON 数据供其他模块调用
    json_data = [r.to_dict() for r in aggregator.reports]
    json_path = os.path.join(OUTPUT_DIR, f"research_data_{datetime.now().strftime('%Y%m%d')}.json")
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)
    print(f"研报 JSON 数据已保存: {json_path}")


if __name__ == '__main__':
    main()
