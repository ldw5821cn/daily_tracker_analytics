#!/usr/bin/env python3
"""为产业链挖掘收集主题级新闻舆情摘要。

聚合来源：
- 财联社财经快讯（全市场）
- 东方财富 A 股热榜
- Jina AI 搜索主题关键词
- 东方财富主题搜索（如果 eastmoney_bridge 可用）

输出：
- docs/supply_chain_<theme>_news.html  主题舆情页面
- multi_agent/data/supply_chain_<theme>_news.json  结构化摘要
- 同时在 market_context_cache/<theme>_news_context.txt 写入纯文本摘要，
  供 supply_chain_miner.py Serenity 评分 prompt 读取。
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List

ROOT = '/home/liudawei/github/daily_tracker_analytics'
for _p in [ROOT, f'{ROOT}/multi_agent']:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import importlib.util
LLM_CLIENT_PATH = f'{ROOT}/multi_agent/core/llm_client.py'
_spec = importlib.util.spec_from_file_location('llm_client', LLM_CLIENT_PATH)
_llm_client = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_llm_client)
chat = _llm_client.chat


def _slugify(s: str) -> str:
    return re.sub(r'[^\w]+', '_', s).strip('_')


def _load_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _fetch_cx_news(limit: int = 30) -> List[Dict]:
    try:
        from analysts.news_analyst import _fetch_akshare_cx_news
        rows = _fetch_akshare_cx_news() or []
        out = []
        for r in rows[:limit]:
            title = r.get('title', '') or r.get('summary', '')
            if title:
                out.append({'title': title, 'source': '财联社', 'url': r.get('url', ''), 'tag': r.get('tag', '')})
        return out
    except Exception as e:
        print(f'   财联社快讯失败: {e}')
        return []


def _fetch_hot_rank(limit: int = 20) -> List[Dict]:
    try:
        from analysts.news_analyst import _fetch_akshare_hot_rank
        rows = _fetch_akshare_hot_rank() or []
        out = []
        for r in rows[:limit]:
            title = r.get('title', '')
            if title:
                out.append({'title': title, 'source': '东财热榜', 'url': ''})
        return out
    except Exception as e:
        print(f'   东财热榜失败: {e}')
        return []


def _fetch_jina(theme: str, limit: int = 10) -> List[Dict]:
    """用 Jina AI 免费搜索主题相关网页。"""
    try:
        from core.social_search_engine import search_jina
        query = f"{theme} A股 产业链 投资机会 2026"
        items = search_jina(query, n=limit)
        for it in items:
            it['source'] = it.get('source', 'Jina Search')
        return items
    except Exception as e:
        print(f'   Jina 搜索失败: {e}')
        return []


def _fetch_theme_eastmoney(theme: str, limit: int = 10) -> List[Dict]:
    """用 news_analyst._fetch_eastmoney_news 搜索主题相关新闻。"""
    try:
        from analysts.news_analyst import _fetch_eastmoney_news
        results = _fetch_eastmoney_news(theme) or []
        out = []
        for r in results[:limit]:
            title = r.get('title', '')
            # 去掉高亮标签
            title = re.sub(r'</?em>', '', title)
            out.append({
                'title': title,
                'source': r.get('source', '东方财富'),
                'url': r.get('url', ''),
                'date': r.get('date', ''),
                'summary': re.sub(r'</?em>', '', r.get('desc', '')),
            })
        return out
    except Exception as e:
        print(f'   东方财富主题搜索失败: {e}')
        return []


def _filter_by_theme(items: List[Dict], theme: str) -> List[Dict]:
    """只保留标题或摘要里含主题关键词的条目。"""
    keywords = list(set(re.findall(r'[\u4e00-\u9fa5a-zA-Z0-9]+', theme)))
    # 允许一些常见简称
    alias_map = {
        '人形机器人': ['机器人', '人形', '具身智能'],
        '固态电池': ['固态电池', '固态', '硫化物', '全固态'],
        'CPO光模块': ['CPO', '光模块', '共封装光学'],
        'AI半导体': ['AI', '半导体', '芯片', '算力'],
        '低空经济': ['低空', '飞行汽车', 'eVTOL', '无人机'],
    }
    extra = alias_map.get(theme, [theme])
    keywords = list(set(keywords + extra))
    out = []
    for it in items:
        text = f"{it.get('title', '')} {it.get('summary', '')} {it.get('tag', '')}".lower()
        if any(kw.lower() in text for kw in keywords if len(kw) >= 2):
            out.append(it)
    return out


def _llm_summarize(theme: str, items: List[Dict]) -> Dict:
    """用 LLM 对主题新闻做情绪判定、关键论点、风险信号提取。"""
    if not items:
        return {
            'sentiment': 'neutral',
            'score': 0.0,
            'summary': '暂无相关舆情数据。',
            'key_points': [],
            'risk_signals': [],
            'hot_tickers': [],
        }
    lines = []
    for i, it in enumerate(items[:25], 1):
        lines.append(f"{i}. [{it.get('source', '')}] {it.get('title', '')}")
    prompt = f"""你是 A 股舆情分析师。请基于以下关于【{theme}】的新闻标题，给出结构化的舆情摘要。

新闻标题：
{chr(10).join(lines)}

请严格按以下 JSON 输出：
{{
  "sentiment": "positive|neutral|negative",
  "score": -1.0 到 1.0 之间的数字,
  "summary": "50字以内的整体判断",
  "key_points": ["关键论点1", "关键论点2", "关键论点3"],
  "risk_signals": ["风险信号1", "风险信号2"],
  "hot_tickers": ["代码 名称", "代码 名称"]
}}

要求：
1. 只输出 JSON，不要解释。
2. score 需综合利好/利空密度、来源权威性、情绪强度。
3. hot_tickers 只列出标题中明确提到的 A 股代码或公司名称，最多5个。
"""
    messages = [
        {'role': 'system', 'content': '你是 A 股舆情分析师，擅长从新闻标题中提取市场情绪与关键信号。'},
        {'role': 'user', 'content': prompt},
    ]
    raw = chat(messages, temperature=0.3, max_tokens=1200)
    parsed = {}
    if raw:
        try:
            import json as _json
            # 尝试去掉 markdown 代码块
            text = raw.strip()
            if text.startswith('```'):
                text = re.sub(r'```(?:json)?\s*|\s*```', '', text).strip()
            parsed = _json.loads(text)
        except Exception:
            print(f'   LLM 摘要解析失败，使用 fallback')
    if not parsed:
        parsed = {
            'sentiment': 'neutral',
            'score': 0.0,
            'summary': f'收集到 {len(items)} 条相关舆情，但 LLM 摘要解析失败。',
            'key_points': [it.get('title', '') for it in items[:5]],
            'risk_signals': [],
            'hot_tickers': [],
        }
    return parsed


def build_news_context(theme: str) -> Dict:
    print(f'[{theme}] 收集舆情数据...')
    cx = _fetch_cx_news(30)
    hot = _fetch_hot_rank(20)
    jina = _fetch_jina(theme, 10)
    em = _fetch_theme_eastmoney(theme, 10)

    all_items = cx + hot + jina + em
    filtered = _filter_by_theme(all_items, theme)
    filtered.sort(key=lambda x: x.get('title', ''), reverse=False)
    # 去重
    seen = set()
    unique = []
    for it in filtered:
        k = it.get('title', '').strip()
        if k and k not in seen:
            seen.add(k)
            unique.append(it)

    print(f'   原始 {len(all_items)} 条 -> 主题相关 {len(unique)} 条')
    summary = _llm_summarize(theme, unique)
    summary['items'] = unique[:30]
    summary['date'] = datetime.now().strftime('%Y-%m-%d')
    summary['theme'] = theme
    return summary


def render_html(theme: str, data: Dict) -> str:
    up_color, down_color = '#e74c3c', '#2ecc71'
    sentiment = data.get('sentiment', 'neutral')
    score = data.get('score', 0.0)
    if sentiment == 'positive' or score > 0.2:
        sentiment_color = up_color
    elif sentiment == 'negative' or score < -0.2:
        sentiment_color = down_color
    else:
        sentiment_color = '#f59e0b'
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{theme} 舆情监控</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; margin: 24px; background: #f7f9fb; color: #333; }}
h1 {{ font-size: 22px; margin-bottom: 6px; }}
.meta {{ color: #666; font-size: 13px; margin-bottom: 20px; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 16px; margin-bottom: 24px; }}
.card {{ background: #fff; border-radius: 12px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
.card h2 {{ font-size: 15px; margin: 0 0 10px; color: #2c3e50; }}
.card .stat {{ font-size: 24px; font-weight: 600; color: #1a252f; }}
.card .label {{ font-size: 12px; color: #7f8c8d; margin-top: 4px; }}
.sentiment {{ color: {sentiment_color}; font-weight: 600; }}
table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.08); margin-bottom: 24px; }}
th, td {{ padding: 10px 12px; text-align: left; font-size: 13px; border-bottom: 1px solid #eef2f5; }}
th {{ background: #eef2f5; color: #2c3e50; font-weight: 600; }}
tr:hover {{ background: #f8fafc; }}
ul {{ line-height: 1.8; }}
li {{ margin-bottom: 6px; }}
a {{ color: #2563eb; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<h1>📰 {theme} 舆情监控</h1>
<div class="meta">生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · 来源：东方财富主题搜索</div>

<div class="cards">
  <div class="card"><h2>舆情情绪</h2><div class="stat sentiment">{sentiment.upper()}</div><div class="label">score {score:+.2f}</div></div>
  <div class="card"><h2>相关新闻数</h2><div class="stat">{len(data.get('items', []))}</div></div>
  <div class="card"><h2>热股提及</h2><div class="stat">{len(data.get('hot_tickers', []))}</div></div>
</div>

<h2>整体判断</h2>
<p>{data.get('summary', '暂无')}</p>

<h2>关键论点</h2>
<ul>
"""
    for p in data.get('key_points', []):
        html += f"<li>{p}</li>\n"
    html += """
</ul>

<h2>风险信号</h2>
<ul>
"""
    for r in data.get('risk_signals', []):
        html += f"<li>{r}</li>\n"
    html += """
</ul>

<h2>热股提及</h2>
<ul>
"""
    for t in data.get('hot_tickers', []):
        html += f"<li>{t}</li>\n"
    html += """
</ul>

<h2>相关新闻</h2>
<table>
<tr><th>#</th><th>来源</th><th>标题</th></tr>
"""
    for i, it in enumerate(data.get('items', [])[:30], 1):
        title = it.get('title', '')
        url = it.get('url', '')
        if url:
            title_cell = f'<a href="{url}" target="_blank">{title}</a>'
        else:
            title_cell = title
        html += f"<tr><td>{i}</td><td>{it.get('source', '')}</td><td>{title_cell}</td></tr>\n"
    html += """
</table>

<h2>说明</h2>
<p style="font-size:13px;color:#64748b;line-height:1.7">
数据来源为公开新闻标题与搜索摘要，情绪打分由 LLM 基于标题密度与关键词综合判定，仅供参考，不构成投资建议。
</p>
</body>
</html>
"""
    return html


def save_context_text(theme: str, data: Dict) -> str:
    """生成纯文本摘要，供 supply_chain_miner.py 读取注入 prompt。"""
    lines = [
        f"【{theme} 舆情摘要（{data.get('date', '')}）】",
        f"- 整体情绪: {data.get('sentiment', 'neutral')} (score {data.get('score', 0):+.2f})",
        f"- 整体判断: {data.get('summary', '')}",
        "- 关键论点:",
    ]
    for p in data.get('key_points', []):
        lines.append(f"  · {p}")
    if data.get('risk_signals'):
        lines.append("- 风险信号:")
        for r in data.get('risk_signals', []):
            lines.append(f"  · {r}")
    if data.get('hot_tickers'):
        lines.append("- 新闻热股提及: " + "; ".join(data.get('hot_tickers', [])))
    lines.append(f"- 相关新闻数: {len(data.get('items', []))} 条")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description='产业链主题舆情监控')
    parser.add_argument('--theme', required=True, help='主题，如"人形机器人"')
    parser.add_argument('--output-dir', default=f'{ROOT}/docs', help='HTML 输出目录')
    args = parser.parse_args()

    data = build_news_context(args.theme)

    # HTML
    html = render_html(args.theme, data)
    html_path = f"{args.output_dir}/supply_chain_{_slugify(args.theme)}_news.html"
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'   HTML: {html_path}')

    # JSON
    json_path = f"{ROOT}/multi_agent/data/supply_chain_{_slugify(args.theme)}_news.json"
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f'   JSON: {json_path}')

    # 纯文本上下文：供 supply_chain_miner.py prompt 读取
    ctx_dir = Path(f"{ROOT}/multi_agent/data/market_context_cache")
    ctx_dir.mkdir(parents=True, exist_ok=True)
    ctx_path = ctx_dir / f"{_slugify(args.theme)}_news_context.txt"
    ctx_path.write_text(save_context_text(args.theme, data), encoding='utf-8')
    print(f'   CONTEXT: {ctx_path}')


if __name__ == '__main__':
    main()
