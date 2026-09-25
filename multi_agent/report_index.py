#!/usr/bin/env python3
"""
报告索引生成器 - 整合所有报告入口

功能：
1. 扫描 docs/ 目录，发现所有报告文件
2. 按类型分组（盘前报告/情绪/题材/游资/ETF/其他）
3. 生成统一的 index.html 入口页面

用法：
    python3 multi_agent/report_index.py
"""

import os
import json
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO / 'docs'


def discover_reports():
    """发现所有报告文件"""
    reports = {
        'daily': [],       # 盘前报告
        'sentiment': [],   # 情绪分析
        'theme': [],       # 题材雷达
        'wisdom': [],      # 游资心法
        'narrative': [],   # 大V观点
        'evidence': [],    # 证据链
        'etf': [],         # ETF 报告
        'other': [],       # 其他
    }

    for f in sorted(DOCS_DIR.glob('*'), reverse=True):
        if not f.is_file():
            continue
        name = f.name
        stat = f.stat()
        size_kb = stat.st_size / 1024
        mtime = datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M')

        entry = {
            'name': name,
            'path': f'docs/{name}',
            'size_kb': round(size_kb, 1),
            'mtime': mtime,
        }

        # 分类
        if name.startswith('daily_report_'):
            reports['daily'].append(entry)
        elif name.startswith('sentiment_report') or 'sentiment_data' in name:
            reports['sentiment'].append(entry)
        elif name.startswith('theme_report'):
            reports['theme'].append(entry)
        elif name.startswith('trader_wisdom'):
            reports['wisdom'].append(entry)
        elif name.startswith('narrative_report'):
            reports['narrative'].append(entry)
        elif name.startswith('evidence_report'):
            reports['evidence'].append(entry)
        elif 'etf' in name.lower() or 'multi_etf' in name:
            reports['etf'].append(entry)
        elif f.suffix in ['.html', '.md']:
            reports['other'].append(entry)

    return reports


def generate_index(reports):
    """生成 index.html"""

    def section(title, icon, items, color='#60a5fa'):
        if not items:
            return ''
        html = f'''
  <div class="section">
    <div class="section-title" style="color: {color}">{icon} {title}（{len(items)}）</div>
    <div class="report-list">
'''
        for item in items[:10]:  # 最多10条
            html += f'''
      <a href="{item['path']}" class="report-item" target="_blank">
        <div class="report-name">{item['name']}</div>
        <div class="report-meta">{item['size_kb']} KB | {item['mtime']}</div>
      </a>
'''
        html += '''    </div>
  </div>
'''
        return html

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>量化系统报告中心</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #0f172a; color: #e2e8f0; line-height: 1.6; padding: 20px;
  }}
  .container {{ max-width: 960px; margin: 0 auto; }}
  h1 {{ color: #60a5fa; font-size: 24px; margin-bottom: 6px; }}
  .subtitle {{ color: #94a3b8; font-size: 13px; margin-bottom: 24px; }}

  .section {{ margin-bottom: 24px; }}
  .section-title {{
    font-size: 16px; font-weight: 600; margin-bottom: 12px;
    display: flex; align-items: center; gap: 6px;
  }}

  .report-list {{
    display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 10px;
  }}
  .report-item {{
    display: block; background: #1e293b; border-radius: 10px; padding: 12px;
    text-decoration: none; color: #e2e8f0; transition: all 0.2s;
    border-left: 3px solid #334155;
  }}
  .report-item:hover {{
    background: #334155; border-left-color: #60a5fa;
    transform: translateX(4px);
  }}
  .report-name {{ font-size: 14px; font-weight: 600; margin-bottom: 4px; }}
  .report-meta {{ font-size: 12px; color: #64748b; }}

  .stats-bar {{
    display: flex; gap: 16px; margin-bottom: 24px; padding: 16px;
    background: #1e293b; border-radius: 12px;
  }}
  .stat {{ text-align: center; flex: 1; }}
  .stat-num {{ font-size: 24px; font-weight: 700; color: #60a5fa; }}
  .stat-label {{ font-size: 12px; color: #94a3b8; }}

  .footer {{
    text-align: center; color: #64748b; font-size: 12px;
    margin-top: 32px; padding-top: 16px; border-top: 1px solid #334155;
  }}
</style>
</head>
<body>
<div class="container">
  <h1>📊 量化系统报告中心</h1>
  <div class="subtitle">LLM-native 量化系统 | 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>

  <div class="stats-bar">
    <div class="stat">
      <div class="stat-num">{len(reports['daily'])}</div>
      <div class="stat-label">盘前报告</div>
    </div>
    <div class="stat">
      <div class="stat-num">{len(reports['sentiment'])}</div>
      <div class="stat-label">情绪分析</div>
    </div>
    <div class="stat">
      <div class="stat-num">{len(reports['theme'])}</div>
      <div class="stat-label">题材雷达</div>
    </div>
    <div class="stat">
      <div class="stat-num">{len(reports['wisdom'])}</div>
      <div class="stat-label">游资心法</div>
    </div>
  </div>
'''

    html += section('📋 盘前报告（P0-P4 整合）', '📋', reports['daily'], '#60a5fa')
    html += section('📡 情绪分析（P2）', '📡', reports['sentiment'], '#f87171')
    html += section('🎯 题材雷达（P3）', '🎯', reports['theme'], '#fbbf24')
    html += section('📚 游资心法（P4）', '📚', reports['wisdom'], '#4ade80')
    html += section('📰 大V观点（P1）', '📰', reports['narrative'], '#a78bfa')
    html += section('🔗 证据链（P0）', '🔗', reports['evidence'], '#38bdf8')
    html += section('📈 ETF 报告', '📈', reports['etf'], '#f472b6')
    html += section('📄 其他报告', '📄', reports['other'], '#94a3b8')

    html += '''
  <div class="footer">
    数据源: akshare 东财/新浪 | 分析: LLM-native 量化系统 v1.0<br>
    模块: P0证据链 | P1大V复盘 | P2超短情绪 | P3题材雷达 | P4游资心法库
  </div>
</div>
</body>
</html>
'''

    return html


if __name__ == '__main__':
    print("=" * 60)
    print("📊 报告索引生成器")
    print("=" * 60)

    reports = discover_reports()
    total = sum(len(v) for v in reports.values())
    print(f"\n发现 {total} 个报告文件:")
    for k, v in reports.items():
        if v:
            print(f"  {k}: {len(v)} 个")

    html = generate_index(reports)
    output_path = DOCS_DIR / 'index.html'
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)

    print(f"\n✅ 索引已生成: {output_path}")
    print("=" * 60)
