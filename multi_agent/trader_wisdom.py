#!/usr/bin/env python3
"""
游资心法知识库 - P4 游资心法库

功能：
1. 加载本地 manifest.json + 42篇 markdown 文档
2. 构建可搜索索引（按游资/标签/关键词）
3. 支持查询：按游资、按标签、按关键词
4. 与 P2 情绪周期联动推荐相关心法

用法：
    python3 multi_agent/trader_wisdom.py                          # 打印统计
    python3 multi_agent/trader_wisdom.py --trader 92科比          # 按游资查询
    python3 multi_agent/trader_wisdom.py --tag 情绪周期           # 按标签查询
    python3 multi_agent/trader_wisdom.py --search "打板"          # 关键词搜索
    python3 multi_agent/trader_wisdom.py --recommend 退潮期       # 联动情绪周期推荐
"""

import sys
import os
import json
import re
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass, field

BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / 'data' / 'trader_wisdom'


@dataclass
class WisdomDoc:
    """单篇游资心法文档"""
    id: str
    trader_id: str
    trader_name: str
    title: str
    kind: str          # deep_report / study_notes
    tags: List[str]
    quote: str
    file_path: str
    content: str = ''  # 延迟加载

    def to_dict(self):
        return {
            'id': self.id,
            'trader_name': self.trader_name,
            'title': self.title,
            'kind': self.kind,
            'tags': self.tags,
            'quote': self.quote,
            'char_count': len(self.content),
        }


class TraderWisdomLibrary:
    """游资心法知识库"""

    def __init__(self, data_dir: str = None):
        self.data_dir = data_dir or str(DATA_DIR)
        self.manifest = self._load_manifest()
        self.docs: List[WisdomDoc] = self._build_index()

    def _load_manifest(self) -> dict:
        path = os.path.join(self.data_dir, 'manifest.json')
        if not os.path.exists(path):
            return {}
        with open(path, encoding='utf-8') as f:
            return json.load(f)

    def _build_index(self) -> List[WisdomDoc]:
        docs = []
        for doc_info in self.manifest.get('documents', []):
            file_id = doc_info.get('cache_file', '').replace('documents/', '').replace('.md', '')
            file_path = os.path.join(self.data_dir, f'{file_id}.md')
            docs.append(WisdomDoc(
                id=doc_info.get('id', ''),
                trader_id=doc_info.get('trader_id', ''),
                trader_name=doc_info.get('trader_name', '未知'),
                title=doc_info.get('title', ''),
                kind=doc_info.get('kind', ''),
                tags=doc_info.get('tags', []),
                quote=doc_info.get('quote', ''),
                file_path=file_path,
            ))
        return docs

    def _load_content(self, doc: WisdomDoc) -> str:
        """延迟加载文档内容"""
        if doc.content:
            return doc.content
        try:
            with open(doc.file_path, encoding='utf-8') as f:
                doc.content = f.read()
        except FileNotFoundError:
            doc.content = ''
        return doc.content

    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------
    def list_traders(self) -> List[str]:
        """列出全部游资"""
        return sorted(set(d.trader_name for d in self.docs))

    def list_tags(self) -> List[str]:
        """列出全部标签"""
        return sorted(set(t for d in self.docs for t in d.tags))

    def get_by_trader(self, trader_name: str) -> List[WisdomDoc]:
        """按游资查询"""
        return [d for d in self.docs if d.trader_name == trader_name]

    def get_by_tag(self, tag: str) -> List[WisdomDoc]:
        """按标签查询"""
        return [d for d in self.docs if tag in d.tags]

    def search(self, keyword: str, max_results: int = 10) -> List[Dict]:
        """
        全文关键词搜索
        返回: [{doc, context}] 含上下文片段
        """
        results = []
        keyword_lower = keyword.lower()

        for doc in self.docs:
            content = self._load_content(doc)
            if not content:
                continue

            # 搜索关键词出现位置
            positions = []
            for match in re.finditer(re.escape(keyword), content, re.IGNORECASE):
                start = max(0, match.start() - 50)
                end = min(len(content), match.end() + 50)
                context = content[start:end].replace('\n', ' ')
                positions.append(context)

            if positions:
                results.append({
                    'doc': doc.to_dict(),
                    'matches': len(positions),
                    'contexts': positions[:3],  # 最多3个片段
                })

        # 按匹配次数排序
        results.sort(key=lambda x: x['matches'], reverse=True)
        return results[:max_results]

    def recommend_by_phase(self, phase: str) -> List[Dict]:
        """
        根据 P2 情绪周期推荐相关心法
        无硬编码：基于标签匹配 + 关键词权重
        """
        # 情绪周期与标签/关键词映射
        phase_mapping = {
            '冰点期':     {'tags': ['弱市', '风险控制', '心态'], 'keywords': ['冰点', '止损', '空仓', '等待']},
            '修复期':     {'tags': ['首板', '低吸', '情绪周期'], 'keywords': ['修复', '反弹', '试错', '企稳']},
            '启动期':     {'tags': ['龙头战法', '打板', '题材'], 'keywords': ['龙头', '主升', '启动', '连板']},
            '高潮期':     {'tags': ['仓位管理', '风险控制'], 'keywords': ['高潮', '兑现', '减仓', '拥挤']},
            '退潮期':     {'tags': ['风险控制', '弱市', '心态'], 'keywords': ['退潮', '亏钱', '防守', '回撤']},
        }

        mapping = phase_mapping.get(phase, {'tags': [], 'keywords': []})
        recommended = []

        # 按标签匹配
        for tag in mapping['tags']:
            for doc in self.get_by_tag(tag):
                entry = {
                    'doc': doc.to_dict(),
                    'match_reason': f"标签匹配: {tag}",
                    'relevance': 2,  # 标签匹配权重高
                }
                if not any(r['doc']['id'] == entry['doc']['id'] for r in recommended):
                    recommended.append(entry)

        # 按关键词匹配（补充）
        for kw in mapping['keywords']:
            for doc in self.docs:
                content = self._load_content(doc)
                if kw in content:
                    entry = {
                        'doc': doc.to_dict(),
                        'match_reason': f"关键词: {kw}",
                        'relevance': 1,
                    }
                    if not any(r['doc']['id'] == entry['doc']['id'] for r in recommended):
                        recommended.append(entry)

        # 按相关度排序
        recommended.sort(key=lambda x: x['relevance'], reverse=True)
        return recommended[:8]  # 最多推荐8篇

    def get_summary(self) -> Dict:
        """知识库统计"""
        traders = self.list_traders()
        tags = self.list_tags()
        total_chars = sum(
            len(self._load_content(d)) for d in self.docs
        )
        return {
            'total_docs': len(self.docs),
            'total_traders': len(traders),
            'total_tags': len(tags),
            'total_chars': total_chars,
            'traders': traders,
            'tags': tags,
        }


# ----------------------------------------------------------------------
# 报告生成
# ----------------------------------------------------------------------
def generate_report(library: TraderWisdomLibrary, output_dir: str = None) -> str:
    """生成游资心法库 HTML 报告"""
    if output_dir is None:
        output_dir = str(BASE.parent / 'docs')

    summary = library.get_summary()

    # 按游资分组
    by_trader = {}
    for doc in library.docs:
        trader = doc.trader_name
        if trader not in by_trader:
            by_trader[trader] = []
        by_trader[trader].append(doc)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>游资心法库 - {summary['total_docs']}篇 / {summary['total_traders']}位</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #0f172a; color: #e2e8f0; line-height: 1.6; padding: 20px;
  }}
  .container {{ max-width: 960px; margin: 0 auto; }}
  h1 {{ color: #60a5fa; font-size: 24px; margin-bottom: 6px; }}
  .subtitle {{ color: #94a3b8; font-size: 13px; margin-bottom: 20px; }}

  .stats-grid {{
    display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 24px;
  }}
  .stat-card {{
    background: #1e293b; border-radius: 12px; padding: 16px; text-align: center;
  }}
  .stat-num {{ font-size: 28px; font-weight: 700; color: #60a5fa; }}
  .stat-label {{ font-size: 12px; color: #94a3b8; margin-top: 4px; }}

  .trader-grid {{
    display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px;
  }}
  .trader-card {{
    background: #1e293b; border-radius: 12px; padding: 14px;
    border-left: 3px solid #60a5fa;
  }}
  .trader-name {{ font-size: 15px; font-weight: 700; color: #e2e8f0; margin-bottom: 6px; }}
  .trader-docs {{ font-size: 12px; color: #94a3b8; margin-bottom: 8px; }}
  .trader-quote {{
    font-size: 12px; color: #64748b; font-style: italic;
    border-left: 2px solid #334155; padding-left: 8px;
    max-height: 60px; overflow: hidden;
  }}
  .tag-list {{ display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }}
  .tag {{
    font-size: 11px; padding: 2px 6px; border-radius: 4px;
    background: rgba(96,165,250,0.15); color: #60a5fa;
  }}

  .footer {{
    text-align: center; color: #64748b; font-size: 12px;
    margin-top: 24px; padding-top: 14px; border-top: 1px solid #334155;
  }}
</style>
</head>
<body>
<div class="container">
  <h1>📚 游资心法库</h1>
  <div class="subtitle">数据来源: easy-stock (trading-mastery) | {summary['total_chars']:,} 字</div>

  <div class="stats-grid">
    <div class="stat-card">
      <div class="stat-num">{summary['total_traders']}</div>
      <div class="stat-label">游资</div>
    </div>
    <div class="stat-card">
      <div class="stat-num">{summary['total_docs']}</div>
      <div class="stat-label">篇文档</div>
    </div>
    <div class="stat-card">
      <div class="stat-num">{summary['total_tags']}</div>
      <div class="stat-label">标签</div>
    </div>
    <div class="stat-card">
      <div class="stat-num">{summary['total_chars']//1000}k</div>
      <div class="stat-label">字数</div>
    </div>
  </div>

  <div class="trader-grid">
"""

    for trader in sorted(by_trader.keys()):
        docs = by_trader[trader]
        quote = docs[0].quote if docs else ''
        tags = sorted(set(t for d in docs for t in d.tags))
        html += f"""
    <div class="trader-card">
      <div class="trader-name">{trader}</div>
      <div class="trader-docs">{len(docs)}篇 | {docs[0].title}</div>
      <div class="trader-quote">"{quote[:80]}{'...' if len(quote) > 80 else ''}"</div>
      <div class="tag-list">
"""
        for tag in tags[:5]:
            html += f'        <span class="tag">{tag}</span>\n'
        html += """      </div>
    </div>
"""

    html += f"""
  </div>

  <div class="footer">
    生成时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}<br>
    用法: trader_wisdom.py --trader [游资名] | --tag [标签] | --search [关键词] | --recommend [情绪周期]
  </div>
</div>
</body>
</html>
"""

    output_path = os.path.join(output_dir, 'trader_wisdom.html')
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    return output_path


# ----------------------------------------------------------------------
# CLI 测试
# ----------------------------------------------------------------------
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='游资心法库')
    parser.add_argument('--trader', help='按游资查询')
    parser.add_argument('--tag', help='按标签查询')
    parser.add_argument('--search', help='关键词搜索')
    parser.add_argument('--recommend', help='按情绪周期推荐')
    parser.add_argument('--report', action='store_true', help='生成HTML报告')
    args = parser.parse_args()

    print("=" * 60)
    print("📚 游资心法库")
    print("=" * 60)

    lib = TraderWisdomLibrary()
    summary = lib.get_summary()
    print(f"\n📊 统计: {summary['total_traders']}位游资 | {summary['total_docs']}篇 | {summary['total_tags']}标签 | {summary['total_chars']:,}字")

    if args.trader:
        docs = lib.get_by_trader(args.trader)
        print(f"\n📖 {args.trader} 的文档（{len(docs)}篇）:")
        for d in docs:
            print(f"  - {d.title} | 标签: {', '.join(d.tags)}")
            print(f"    名言: {d.quote[:60]}...")

    elif args.tag:
        docs = lib.get_by_tag(args.tag)
        print(f"\n🏷️ 标签「{args.tag}」相关文档（{len(docs)}篇）:")
        for d in docs:
            print(f"  - {d.trader_name}: {d.title}")

    elif args.search:
        results = lib.search(args.search)
        print(f"\n🔍 搜索「{args.search}」: {len(results)}条结果")
        for r in results[:5]:
            print(f"  [{r['matches']}次] {r['doc']['trader_name']} - {r['doc']['title']}")
            for ctx in r['contexts'][:2]:
                print(f"    ...{ctx}...")

    elif args.recommend:
        recs = lib.recommend_by_phase(args.recommend)
        print(f"\n💡 {args.recommend} 推荐心法（{len(recs)}篇）:")
        for r in recs:
            print(f"  [{r['relevance']}] {r['doc']['trader_name']} - {r['doc']['title']}")
            print(f"    原因: {r['match_reason']}")

    elif args.report:
        path = generate_report(lib)
        print(f"\n✅ 报告已生成: {path}")

    else:
        # 默认打印统计
        print(f"\n👥 游资名单: {', '.join(summary['traders'])}")
        print(f"\n🏷️ 标签: {', '.join(summary['tags'])}")
        print("\n💡 用法:")
        print("  --trader 92科比     # 按游资查询")
        print("  --tag 情绪周期      # 按标签查询")
        print("  --search 打板       # 关键词搜索")
        print("  --recommend 退潮期  # 情绪周期推荐")
        print("  --report            # 生成HTML报告")

    print("\n" + "=" * 60)
