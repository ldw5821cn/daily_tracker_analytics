#!/usr/bin/env python3
"""调用 last30days-cn 生成盘前主题简报，并保存为 HTML。

Usage:
  . etf_tracker/.venv/bin/activate
  python3 multi_agent/scripts/last30days_morning_brief.py \
      --topics "A股,人形机器人,低空经济,固态电池" \
      --output-dir docs/morning_briefs

要求 last30days-cn skill 已克隆到 /tmp/last30days-skill-cn
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from html import escape
from typing import List

ROOT = '/home/liudawei/github/daily_tracker_analytics'
LAST30DAYS_SCRIPT = '/tmp/last30days-skill-cn/skills/last30days/scripts/last30days.py'
DEFAULT_TOPICS = ["A股", "人形机器人", "低空经济", "固态电池", "AI半导体", "CPO光模块"]


def _slugify(text: str) -> str:
    """生成文件系统安全的 slug。"""
    text = text.strip().lower()
    text = re.sub(r'[^\u4e00-\u9fa5a-z0-9]+', '-', text)
    return text.strip('-') or 'topic'


def run_last30days(topic: str, output_dir: str, sources: str = "weibo,baidu") -> dict:
    """调用 last30days-cn CLI，返回结果字典。"""
    save_dir = os.path.join(output_dir, '_raw', _slugify(topic))
    os.makedirs(save_dir, exist_ok=True)
    env = os.environ.copy()
    env['LAST30DAYS_OUTPUT_DIR'] = save_dir
    cmd = [
        sys.executable, LAST30DAYS_SCRIPT,
        topic,
        '--search', sources,
        '--emit', 'json',
        '--quick',
        '--save-dir', save_dir,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=180,
            cwd='/tmp/last30days-skill-cn',
            env=env,
        )
        report_path = os.path.join(save_dir, 'report.json')
        if os.path.exists(report_path):
            try:
                with open(report_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                data['topic'] = topic
                return data
            except Exception:
                pass
        full_text = result.stdout
        end = full_text.rfind('}')
        if end > 0:
            start = full_text.rfind('{', 0, end)
            if start >= 0:
                try:
                    return json.loads(full_text[start:end+1])
                except json.JSONDecodeError:
                    pass
        lines = result.stdout.strip().splitlines()
        return {"topic": topic, "error": "no json found", "stdout_tail": '\n'.join(lines[-20:])}
    except subprocess.TimeoutExpired:
        return {"topic": topic, "error": "timeout"}
    except Exception as e:
        return {"topic": topic, "error": str(e)}


def render_html(items: List[dict], output_path: str):
    """渲染盘前简报 HTML。"""
    date_str = datetime.now().strftime('%Y-%m-%d')
    rows = []
    for item in items:
        topic = item.get('topic', '')
        error = item.get('error')
        if error:
            rows.append(f'<div class="topic"><h3>{topic}</h3><p class="muted">数据获取失败: {error}</p></div>')
            continue
        entries = []
        for source in ['weibo', 'xiaohongshu', 'bilibili', 'zhihu', 'douyin', 'wechat', 'baidu', 'toutiao']:
            for e in item.get(source, []) or []:
                e['source'] = source
                entries.append(e)
        entries.sort(key=lambda x: x.get('score', 0), reverse=True)
        if not entries:
            rows.append(f'<div class="topic"><h3>{topic}</h3><p class="muted">暂无相关讨论</p></div>')
            continue
        entry_html = []
        for e in entries[:5]:
            title = e.get('title', '') or e.get('text', '')[:100]
            source = e.get('source', 'unknown')
            date = e.get('date', '') or '日期未知'
            url = e.get('url', '')
            score = e.get('score', 0)
            link = f'<a href="{url}" target="_blank">{escape(title)}</a>' if url else escape(title)
            entry_html.append(
                f'<li><span class="badge">{source}</span> <span class="date">{date}</span> '
                f'{link} <span class="score">{score}</span></li>'
            )
        rows.append(
            f'<div class="topic"><h3>{topic}</h3><ul>{"".join(entry_html)}</ul></div>'
        )

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>盘前简报 {date_str}</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif;
            background: #0f172a; color: #f8fafc; padding: 40px 20px; }}
    .container {{ max-width: 900px; margin: 0 auto; }}
    h1 {{ font-size: 2.5rem; margin-bottom: 8px; }}
    .date {{ color: #94a3b8; margin-bottom: 30px; }}
    .topic {{ background: #1e293b; border-radius: 16px; padding: 24px; margin-bottom: 20px; }}
    h3 {{ font-size: 1.4rem; margin-bottom: 12px; color: #38bdf8; }}
    ul {{ list-style: none; }}
    li {{ padding: 10px 0; border-bottom: 1px solid #334155; line-height: 1.5; }}
    li:last-child {{ border-bottom: none; }}
    .badge {{ background: #334155; color: #e2e8f0; padding: 2px 8px; border-radius: 4px; font-size: 0.8rem; }}
    .date {{ color: #94a3b8; font-size: 0.85rem; margin-left: 6px; }}
    .score {{ color: #22c55e; font-size: 0.85rem; margin-left: 6px; }}
    .muted {{ color: #94a3b8; }}
    a {{ color: #38bdf8; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    footer {{ margin-top: 40px; text-align: center; color: #64748b; }}
  </style>
</head>
<body>
  <div class="container">
    <h1>盘前主题简报</h1>
    <p class="date">{date_str} · 数据来源：微博/百度</p>
    {''.join(rows)}
    <footer>由 last30days-cn 自动生成 · 仅供研究参考</footer>
  </div>
</body>
</html>"""
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)


def main():
    parser = argparse.ArgumentParser(description='盘前主题简报生成器')
    parser.add_argument('--topics', default=','.join(DEFAULT_TOPICS), help='逗号分隔主题')
    parser.add_argument('--output-dir', default=f'{ROOT}/docs/morning_briefs', help='输出目录')
    parser.add_argument('--sources', default='weibo,baidu', help='last30days 数据源')
    args = parser.parse_args()

    if not os.path.exists(LAST30DAYS_SCRIPT):
        print(f"错误: 找不到 last30days-cn 脚本 {LAST30DAYS_SCRIPT}")
        print("请先克隆: git clone --depth 1 https://github.com/Jesseovo/last30days-skill-cn.git /tmp/last30days-skill-cn")
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)
    topics = [t.strip() for t in args.topics.split(',') if t.strip()]
    results = []
    for topic in topics:
        print(f'正在收集: {topic}')
        res = run_last30days(topic, args.output_dir, args.sources)
        res['topic'] = topic
        results.append(res)

    date_str = datetime.now().strftime('%Y-%m-%d')
    html_path = os.path.join(args.output_dir, f'{date_str}.html')
    render_html(results, html_path)
    print(f'已生成: {html_path}')


if __name__ == '__main__':
    main()
