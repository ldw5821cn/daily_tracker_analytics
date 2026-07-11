#!/usr/bin/env python3
"""
东方财富研报下载器
自动下载指定股票/ETF 的最新券商研报 PDF
"""

import os
import re
import json
import time
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from urllib.parse import quote
import warnings
warnings.filterwarnings('ignore')

REPORT_DIR = os.path.expanduser("~/etf_tracker/research_reports")
BASE_API = "https://reportapi.eastmoney.com/report/list"
DETAIL_URL = "https://data.eastmoney.com/report/zw_stock.jshtml"


class EastmoneyReportDownloader:
    """东方财富研报下载器"""
    
    def __init__(self, output_dir: str = REPORT_DIR):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
    
    def _fetch_report_list(self, stock_code: str, days: int = 180, page_size: int = 20) -> List[Dict]:
        """获取研报列表"""
        end_date = datetime.now().strftime('%Y-%m-%d')
        begin_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        
        params = {
            'industryCode': '*',
            'pageNo': 1,
            'pageSize': page_size,
            'code': stock_code,
            'beginTime': begin_date,
            'endTime': end_date,
            'qType': 0,
        }
        
        try:
            resp = self.session.get(BASE_API, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            return data.get('data', [])
        except Exception as e:
            print(f"  获取 {stock_code} 研报列表失败: {e}")
            return []
    
    def _extract_pdf_url(self, info_code: str) -> Optional[str]:
        """从研报详情页提取 PDF 链接"""
        try:
            url = f"{DETAIL_URL}?infocode={info_code}"
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
            
            # 匹配 pdf 链接
            matches = re.findall(r'https?://[^"\s<>]+\.pdf[^"\s<>]*', resp.text)
            for match in matches:
                # 清理多余参数，保留基础 URL
                clean_url = match.split('?')[0] if '?' in match else match
                if clean_url.endswith('.pdf'):
                    return clean_url
            return matches[0] if matches else None
        except Exception as e:
            print(f"  提取 PDF 链接失败 ({info_code}): {e}")
            return None

    def _get_stock_list_from_config(self) -> List[str]:
        """从 config.json 读取 Top10 成分股代码"""
        try:
            import json
            config_path = os.path.join(os.path.dirname(__file__), 'config.json')
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            codes = set()
            for etf in config.get('etfs', []):
                for stock in etf.get('top_stocks', []):
                    code = stock.get('code', '')
                    if code and code.isdigit() and len(code) == 6:
                        codes.add(code)
            return sorted(list(codes))
        except Exception as e:
            print(f"  读取配置失败: {e}")
            return []
    
    def _download_pdf(self, url: str, save_path: str) -> bool:
        """下载 PDF 文件"""
        try:
            resp = self.session.get(url, timeout=60)
            resp.raise_for_status()
            
            # 验证是 PDF
            content_type = resp.headers.get('content-type', '')
            if not content_type.startswith('application/pdf') or len(resp.content) < 1000:
                print(f"  下载内容不是有效 PDF: {content_type}, {len(resp.content)} bytes")
                return False
            
            with open(save_path, 'wb') as f:
                f.write(resp.content)
            
            print(f"  ✅ 已下载: {save_path} ({len(resp.content)/1024:.1f} KB)")
            return True
        except Exception as e:
            print(f"  下载 PDF 失败: {e}")
            return False
    
    def _generate_filename(self, report: Dict) -> str:
        """生成文件名"""
        stock_code = report.get('stockCode', '')
        stock_name = report.get('stockName', '')
        org_name = report.get('orgSName', '') or report.get('orgName', '')
        publish_date = report.get('publishDate', '')[:10].replace('-', '')
        title = report.get('title', '')[:30]
        
        # 清理非法字符
        title = re.sub(r'[\\/:*?"<>|]', '_', title)
        org_name = re.sub(r'[\\/:*?"<>|]', '_', org_name)
        
        parts = [p for p in [stock_code, stock_name, org_name, publish_date] if p]
        filename = '_'.join(parts) + '.pdf'
        return filename
    
    def download_by_stock(self, stock_code: str, max_reports: int = 3, days: int = 180) -> List[str]:
        """
        下载某只股票的最新研报
        
        Args:
            stock_code: 股票代码，如 600519
            max_reports: 最多下载最近几篇
            days: 查询最近多少天的研报
        
        Returns:
            已下载文件路径列表
        """
        print(f"\n下载 {stock_code} 研报...")
        reports = self._fetch_report_list(stock_code, days=days, page_size=max_reports)
        
        if not reports:
            print(f"  未找到 {stock_code} 的研报")
            return []
        
        downloaded = []
        for report in reports[:max_reports]:
            info_code = report.get('infoCode')
            if not info_code:
                continue
            
            filename = self._generate_filename(report)
            save_path = os.path.join(self.output_dir, filename)
            
            # 如果已存在，跳过
            if os.path.exists(save_path):
                print(f"  ⏭️ 已存在: {filename}")
                downloaded.append(save_path)
                continue
            
            pdf_url = self._extract_pdf_url(info_code)
            if not pdf_url:
                print(f"  未找到 PDF 链接: {info_code}")
                continue
            
            if self._download_pdf(pdf_url, save_path):
                downloaded.append(save_path)
            
            time.sleep(0.5)  # 礼貌性延迟
        
        return downloaded
    
    def download_by_stock_list(self, stock_codes: List[str], max_reports: int = 3, 
                                days: int = 180, max_total_downloads: int = 0,
                                time_budget: float = 0) -> Dict[str, List[str]]:
        """
        批量下载多只股票研报（含限速保护）
        
        Args:
            stock_codes: 股票代码列表
            max_reports: 每只股票最多下载最近几篇
            days: 查询天数
            max_total_downloads: 本轮最多下载新PDF数（0=不限制）
            time_budget: 最大耗时秒数（0=不限制）
        """
        results = {code: [] for code in stock_codes}
        new_downloads = 0
        start_time = time.time()
        
        for code in stock_codes:
            # 限速检测
            if max_total_downloads > 0 and new_downloads >= max_total_downloads:
                print(f"  已达到本轮下载上限 ({max_total_downloads} 份)，停止")
                break
            if time_budget > 0 and (time.time() - start_time) > time_budget:
                print(f"  已达到时间预算 ({time_budget:.0f}s)，停止")
                break
            
            try:
                files = self.download_by_stock(code, max_reports=max_reports, days=days)
                results[code] = files
                new_downloads += len(files)
            except Exception as e:
                print(f"  {code} 下载异常: {e}")
                results[code] = []
        
        return results


def main():
    """主函数：下载 config.json 中 Top10 成分股的研报（限速版本）"""
    import time
    t0 = time.time()
    print(f"东方财富研报下载器启动: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    downloader = EastmoneyReportDownloader()
    stock_codes = downloader._get_stock_list_from_config()
    
    if not stock_codes:
        print("  未从配置读取到股票代码，使用默认列表")
        stock_codes = [
            '600519', '600111', '000858', '002371',
            '688981', '300750', '601012', '603290'
        ]
    
    print(f"  将从配置读取 {len(stock_codes)} 只成分股的研报")
    
    # 限速策略：最多下载 20 份新 PDF，总耗时不超过 100 秒
    results = downloader.download_by_stock_list(
        stock_codes, max_reports=2, days=90,
        max_total_downloads=20, time_budget=100
    )
    
    total = sum(len(v) for v in results.values())
    elapsed = time.time() - t0
    print(f"\n下载完成: 共 {total} 份研报 (耗时 {elapsed:.1f}s)")
    for code, files in results.items():
        if files:
            print(f"  {code}: {len(files)} 份")


if __name__ == '__main__':
    main()
