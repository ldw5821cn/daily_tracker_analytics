#!/usr/bin/env python3
"""产业链挖掘脚本（最小接入）。

输入一个主题（如"人形机器人"），输出：
- 产业链地图（JSON）
- Top 候选 A 股标的（复用 llm_predictions.db 股票池）
- Serenity 瓶颈评分
- HTML 页面部署到 docs/supply_chain_<theme>.html

Usage:
  python3 multi_agent/scripts/supply_chain_miner.py --theme "人形机器人" --db multi_agent/data/llm_predictions.db
"""
import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

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

from analysts.serenity_scorecard import score as serenity_score


def _slugify(s: str) -> str:
    return re.sub(r'[^\w]+', '_', s).strip('_')


def load_a_share_universe(db_path: str) -> List[Dict]:
    """从本地 CSV 读取深交所全 A 股列表，并合并预测数据库中的历史标的。"""
    universe = {}
    # 1. 深交所全 A 股（含行业）
    sz_path = f"{ROOT}/multi_agent/data/a_share_universe_sz.csv"
    if os.path.exists(sz_path):
        import csv
        with open(sz_path, "r", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                code = row.get("A股代码", "").strip()
                name = row.get("A股简称", "").strip()
                sector = row.get("所属行业", "").strip()
                if code and name:
                    universe[code] = {"ticker": code, "name": name, "sector": sector}
    # 2. 预测数据库中的历史标的（补充 sector 信息）
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT ticker, name, sector
            FROM agentic_predictions
            WHERE category = '个股' AND name IS NOT NULL AND name != ''
            ORDER BY ticker
            """
        )
        for r in cur.fetchall():
            d = dict(r)
            code = d["ticker"]
            if code in universe:
                # 用更细分的 sector 覆盖较粗的申万行业
                if d.get("sector") and len(str(d["sector"])) > len(str(universe[code].get("sector", ""))):
                    universe[code]["sector"] = d["sector"]
            else:
                universe[code] = d
        conn.close()
    rows = list(universe.values())
    rows.sort(key=lambda x: x["ticker"])
    return rows


def _extract_json(text: str) -> Optional[Dict]:
    if not text:
        return None
    text = text.strip()
    # 先尝试整段
    try:
        return json.loads(text)
    except Exception:
        pass
    # 去掉 Markdown 代码块
    m = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # 找最后一个 } 结束的最外层 JSON 对象
    end = text.rfind('}')
    if end != -1:
        try:
            return json.loads(text[:end+1])
        except Exception:
            pass
    # 找第一个 { 到最后一个 }
    start = text.find('{')
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end+1])
        except Exception:
            pass
    return None


def build_value_chain(theme: str) -> Dict:
    """用 LLM 生成产业链地图与瓶颈分析。"""
    prompt = f"""你是一名产业链研究专家。请针对主题【{theme}】，构建 A 股投资视角下的产业链地图。

请严格按以下 JSON 格式输出（不要输出任何其他内容）：
{{
  "theme": "{theme}",
  "demand_story": "驱动该产业链需求爆发的核心故事，50字以内",
  "value_chain": [
    {{"layer": "下游/终端", "segments": ["..."], "key_players_global": ["..."]}},
    {{"layer": "系统集成/整机", "segments": ["..."], "key_players_global": ["..."]}},
    {{"layer": "核心零部件", "segments": ["..."], "key_players_global": ["..."]}},
    {{"layer": "芯片/器件", "segments": ["..."], "key_players_global": ["..."]}},
    {{"layer": "设备", "segments": ["..."], "key_players_global": ["..."]}},
    {{"layer": "材料", "segments": ["..."], "key_players_global": ["..."]}}
  ],
  "bottlenecks": [
    {{
      "segment": "环节名称",
      "severity": 1-5,
      "reason": "为什么是瓶颈：供应商集中、认证周期长、扩产难、国产率低等",
      "global_cr3": "全球前三市占率描述",
      "china_share": "国产份额描述"
    }}
  ],
  "kill_switches": ["证伪风险1", "证伪风险2", "证伪风险3"]
}}

要求：
1. 只输出 JSON，不要 Markdown、不要解释、不要代码块。
2. bottleneck 只选最稀缺的 3-5 个环节。
3. 所有字段用中文。
"""
    messages = [
        {"role": "system", "content": "你是资深产业链研究专家，擅长构建投资级产业链地图。"},
        {"role": "user", "content": prompt},
    ]
    raw = chat(messages, temperature=0.3, max_tokens=3200)
    # 尝试直接解析，若失败可能是 JSON 被截断：找从第一个 { 到最后一个 } 的完整段
    parsed = _extract_json(raw) if raw else None
    if parsed is None:
        # 模型可能输出不完整 JSON（max_tokens 截断），尝试补全尾部缺失的括号
        text = raw.strip() if raw else ""
        # 统计未闭合的 { 和 [，补全
        open_brace = text.count('{') - text.count('}')
        open_bracket = text.count('[') - text.count(']')
        if open_brace > 0 or open_bracket > 0:
            text += ']' * open_bracket + '}' * open_brace
            parsed = _extract_json(text)
    if parsed is None:
        # 保存调试文件
        debug_path = f"/tmp/sc_map_fail_{theme}.txt"
        with open(debug_path, 'w', encoding='utf-8') as f:
            f.write(raw or '')
        raise RuntimeError(f"产业链 LLM 解析失败，已保存到 {debug_path}\n前500字:\n{raw[:500]}")
    parsed['_llm_raw'] = raw
    return parsed


def map_candidates(theme: str, value_chain: Dict, universe: List[Dict]) -> List[Dict]:
    """用 LLM 把产业链环节映射到 A 股候选标的。"""
    # 限制股票池长度，避免 token 爆炸；同时限制瓶颈数量
    bottleneck_text = '\n'.join(
        f"- {b['segment']}（严重度{b['severity']}/5）：{b['reason']}"
        for b in value_chain.get('bottlenecks', [])[:5]
    )
    universe_text = '\n'.join(
        f"{s['ticker']} {s['name']}（sector: {s['sector']}）"
        for s in universe[:600]
    )
    prompt = f"""主题：{theme}

已识别的核心瓶颈环节：
{bottleneck_text}

当前 A 股候选股票池（仅部分）：
{universe_text}

请从股票池中为每个瓶颈环节挑选最相关的 1-3 家 A 股公司。如果某环节没有直接相关标的，可输出空数组。

严格按以下 JSON 输出，不要输出任何其他内容：
{{
  "candidates": [
    {{
      "segment": "瓶颈环节名称",
      "ticker": "股票代码",
      "name": "股票简称",
      "reason": "为什么该公司处于该瓶颈环节（30字以内）",
      "revenue_exposure": "高/中/低",
      "evidence_quality": 1-5
    }}
  ]
}}

要求：
1. ticker 必须来自上述股票池。
2. 每个瓶颈环节 1-3 个候选。
3. 只输出 JSON。
"""
    messages = [
        {"role": "system", "content": "你是 A 股产业链选股专家，擅长把产业链节点映射到具体上市公司。"},
        {"role": "user", "content": prompt},
    ]
    raw = chat(messages, temperature=0.3, max_tokens=4000)
    parsed = _extract_json(raw) if raw else None
    if parsed is None:
        text = raw.strip() if raw else ""
        last_brace = text.rfind('}')
        if last_brace > 0:
            text = text[:last_brace+1]
        open_brace = text.count('{') - text.count('}')
        open_bracket = text.count('[') - text.count(']')
        if open_brace > 0 or open_bracket > 0:
            text += ']' * open_bracket + '}' * open_brace
            parsed = _extract_json(text)
    if parsed is None:
        debug_path = f"/tmp/sc_map_fail_{_slugify(theme)}.txt"
        with open(debug_path, 'w', encoding='utf-8') as f:
            f.write(raw)
        raise RuntimeError(f"候选映射 LLM 解析失败，已保存到 {debug_path}\n前500字:\n{raw[:500]}")
    return parsed.get('candidates', [])

def load_fundamentals() -> Dict[str, dict]:
    """加载最新的 fundamentals_cache，key 为 6 位代码。"""
    cache_dir = f"{ROOT}/multi_agent/data/fundamentals_cache"
    if not os.path.exists(cache_dir):
        return {}
    # 优先找非 _revenue 后缀的 fundamentals 文件
    files = sorted([f for f in os.listdir(cache_dir) if f.endswith('.json') and not f.endswith('_revenue.json')], reverse=True)
    if not files:
        return {}
    try:
        with open(os.path.join(cache_dir, files[0]), 'r', encoding='utf-8') as f:
            data = json.load(f)
        return {str(k).zfill(6): v for k, v in data.get('fundamentals', {}).items() if v}
    except Exception:
        return {}


def load_revenue_composition() -> Dict[str, dict]:
    """加载最新的主营构成缓存，key 为 6 位代码。"""
    cache_dir = f"{ROOT}/multi_agent/data/fundamentals_cache"
    if not os.path.exists(cache_dir):
        return {}
    files = sorted([f for f in os.listdir(cache_dir) if f.endswith('_revenue.json')], reverse=True)
    if not files:
        return {}
    try:
        with open(os.path.join(cache_dir, files[0]), 'r', encoding='utf-8') as f:
            data = json.load(f)
        return {str(k).zfill(6): v for k, v in data.get('data', {}).items() if v}
    except Exception:
        return {}


DEFAULT_FACTORS = {
    "demand_inflection": 3,
    "architecture_coupling": 3,
    "chokepoint_severity": 3,
    "supplier_concentration": 3,
    "expansion_difficulty": 3,
    "evidence_quality": 3,
    "valuation_disconnect": 3,
    "catalyst_timing": 3,
}
DEFAULT_PENALTIES = {
    "dilution_financing": 2,
    "governance": 2,
    "geopolitics": 2,
    "liquidity": 2,
    "hype_risk": 3,
    "accounting_quality": 2,
    "cyclicality": 2,
    "alternative_design_risk": 2,
}


def enrich_with_fundamentals(candidates: List[Dict], fund_map: Dict[str, dict], revenue_map: Dict[str, dict]) -> List[Dict]:
    """把 PE/PB/市值/ROE 等真实财务数据与主营构成附加到候选上，用于页面展示。"""
    for c in candidates:
        code = str(c.get('ticker', '')).zfill(6)
        f = fund_map.get(code, {})
        c['fundamentals'] = {
            'market_cap_yi': round(f['market_cap'] / 1e8, 2) if f.get('market_cap') else None,
            'pe_ratio': f.get('pe_ratio'),
            'pb_ratio': f.get('pb_ratio'),
            'roe': f.get('roe'),
            'gross_margin': f.get('gross_margin'),
            'revenue_yoy': f.get('revenue_yoy'),
            'profit_yoy': f.get('profit_yoy'),
            'debt_ratio': f.get('debt_ratio'),
            'report_date': f.get('report_date', ''),
        }
        # 主营构成：取前三大产品
        rev = revenue_map.get(code, {})
        top_products = sorted(rev.items(), key=lambda x: x[1], reverse=True)[:3]
        c['revenue_composition'] = {k: round(v * 100, 2) for k, v in top_products}
    return candidates


def score_candidates(candidates: List[Dict], theme: str) -> List[Dict]:
    """对每个候选标的调用 Serenity 评分卡；跳过无 name/ticker 的候选。"""
    fund_map = load_fundamentals()
    results = []
    for c in candidates:
        if not c.get('name') or not c.get('ticker'):
            print(f"   跳过无效候选: {c}")
            continue
        code = str(c['ticker']).zfill(6)
        fund = fund_map.get(code, {})
        fund_str = ""
        if fund.get('pe_ratio'):
            fund_str += f" 市盈率(TTM/动态): {fund['pe_ratio']:.2f};"
        if fund.get('pb_ratio'):
            fund_str += f" 市净率: {fund['pb_ratio']:.2f};"
        if fund.get('market_cap'):
            fund_str += f" 总市值(亿元): {fund['market_cap']/1e8:.2f};"
        if fund.get('roe') is not None:
            fund_str += f" ROE: {fund['roe']:.2f}%;"
        if fund.get('gross_margin') is not None:
            fund_str += f" 毛利率: {fund['gross_margin']:.2f}%;"
        if fund.get('revenue_yoy') is not None:
            fund_str += f" 营收同比: {fund['revenue_yoy']:.2f}%;"
        if fund.get('profit_yoy') is not None:
            fund_str += f" 净利润同比: {fund['profit_yoy']:.2f}%;"
        if fund.get('debt_ratio') is not None:
            fund_str += f" 资产负债率: {fund['debt_ratio']:.2f}%;"

        prompt = f"""主题：{theme}
瓶颈环节：{c['segment']}
候选标的：{c['name']}（{c['ticker']}）
入选理由：{c['reason']}
已知财务/估值数据（来自公开行情/财报，可能不全）：{fund_str if fund_str else '暂无'}

请按 Serenity 供应链瓶颈评分框架，为该标的各因子打分（0-5）。

严格按以下 JSON 输出：
{{
  "factors": {{
    "demand_inflection": 0-5,
    "architecture_coupling": 0-5,
    "chokepoint_severity": 0-5,
    "supplier_concentration": 0-5,
    "expansion_difficulty": 0-5,
    "evidence_quality": 0-5,
    "valuation_disconnect": 0-5,
    "catalyst_timing": 0-5
  }},
  "penalties": {{
    "dilution_financing": 0-5,
    "governance": 0-5,
    "geopolitics": 0-5,
    "liquidity": 0-5,
    "hype_risk": 0-5,
    "accounting_quality": 0-5,
    "cyclicality": 0-5,
    "alternative_design_risk": 0-5
  }},
  "evidence": [
    {{"claim": "核心看多论据", "source": "公开信息/公司财报/行业研究", "strength": "primary/media/analysis"}}
  ],
  "what_could_weaken_view": ["证伪风险1", "证伪风险2"]
}}

要求：
1. 只输出 JSON，不要解释。
2. 当财务数据显示高估值（如 PE>100 或 PB>10）且收入暴露为"低"时，valuation_disconnect 和 hype_risk 应反映风险。
3. 财务数据缺失时不要臆造，按公开信息审慎评分。
"""
        messages = [
            {"role": "system", "content": "你是量化基本面分析师，按 Serenity 框架评估标的，严禁编造数据。"},
            {"role": "user", "content": prompt},
        ]
        raw = chat(messages, temperature=0.3, max_tokens=1600)
        if not raw:
            print(f"   评分 LLM 为空，跳过 {c['name']}")
            parsed = {}
        else:
            parsed = _extract_json(raw) or {}
        # 缺省值兜底，避免 score 报错
        factors = parsed.get('factors', {}) or {}
        penalties = parsed.get('penalties', {}) or {}
        for k, v in DEFAULT_FACTORS.items():
            factors.setdefault(k, v)
        for k, v in DEFAULT_PENALTIES.items():
            penalties.setdefault(k, v)
        scorecard_input = {
            "ticker": c['ticker'],
            "company": c['name'],
            "market": "A-share",
            "factors": factors,
            "penalties": penalties,
            "evidence": parsed.get('evidence', []),
            "what_could_weaken_view": parsed.get('what_could_weaken_view', []),
        }
        try:
            result, _ = serenity_score(scorecard_input)
        except Exception as e:
            result = {"error": str(e), "final_score": 0}
        result.update({
            "segment": c['segment'],
            "selection_reason": c.get('reason', ''),
            "revenue_exposure": c.get('revenue_exposure', '中'),
        })
        results.append(result)
    results = enrich_with_fundamentals(results, fund_map, load_revenue_composition())
    results.sort(key=lambda x: x.get('final_score', 0), reverse=True)
    return results


def render_html(theme: str, value_chain: Dict, candidates: List[Dict]) -> str:
    """生成 HTML 页面。"""
    up_color, down_color = '#e74c3c', '#2ecc71'
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{theme} 产业链挖掘</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; margin: 24px; background: #f7f9fb; color: #333; }}
h1 {{ font-size: 22px; margin-bottom: 6px; }}
.meta {{ color: #666; font-size: 13px; margin-bottom: 20px; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px; margin-bottom: 24px; }}
.card {{ background: #fff; border-radius: 12px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
.card h2 {{ font-size: 15px; margin: 0 0 10px; color: #2c3e50; }}
.card .stat {{ font-size: 24px; font-weight: 600; color: #1a252f; }}
.card .label {{ font-size: 12px; color: #7f8c8d; margin-top: 4px; }}
table {{ width: 100%; border-collapse: collapse; background: #fff; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.08); margin-bottom: 24px; }}
th, td {{ padding: 10px 12px; text-align: left; font-size: 13px; border-bottom: 1px solid #eef2f5; }}
th {{ background: #eef2f5; color: #2c3e50; font-weight: 600; }}
tr:hover {{ background: #f8fafc; }}
.score-high {{ color: {up_color}; font-weight: 600; }}
.score-mid {{ color: #f59e0b; font-weight: 600; }}
.score-low {{ color: {down_color}; }}
.layer {{ background: #fff; border-radius: 8px; padding: 12px; margin-bottom: 10px; box-shadow: 0 1px 2px rgba(0,0,0,0.05); }}
.layer-title {{ font-weight: 600; color: #1e3a5f; margin-bottom: 6px; }}
.layer-seg {{ color: #475569; font-size: 13px; }}
.desc {{ font-size: 13px; color: #555; line-height: 1.7; }}
.fund {{ color: #64748b; font-size: 12px; margin-top: 4px; }}
</style>
</head>
<body>
<h1>🧭 {theme} 产业链挖掘</h1>
<div class="meta">生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · 标的池来自深交所全 A 股 + 历史预测覆盖</div>
"""
    html += f"""
<div class="cards">
  <div class="card"><h2>候选标的数</h2><div class="stat">{len(candidates)}</div></div>
  <div class="card"><h2>平均瓶颈评分</h2><div class="stat">{round(sum(c.get('final_score',0) for c in candidates)/len(candidates),1) if candidates else 0}</div></div>
  <div class="card"><h2>瓶颈环节数</h2><div class="stat">{len(value_chain.get('bottlenecks', []))}</div></div>
</div>

<h2>需求故事</h2>
<div class="layer"><div class="layer-seg">{value_chain.get('demand_story', '无')}</div></div>

<h2>产业链地图</h2>
"""
    for layer in value_chain.get('value_chain', []):
        html += f"""
<div class="layer">
  <div class="layer-title">{layer.get('layer', '')}</div>
  <div class="layer-seg">环节：{'、'.join(layer.get('segments', []))}</div>
  <div class="layer-seg" style="margin-top:4px;color:#64748b">全球龙头：{'、'.join(layer.get('key_players_global', []))}</div>
</div>
"""

    html += """
<h2>核心瓶颈</h2>
<table>
<tr><th>环节</th><th>严重度</th><th>原因</th><th>全球 CR3</th><th>国产份额</th></tr>
"""
    for b in value_chain.get('bottlenecks', []):
        html += f"""
<tr>
  <td>{b.get('segment', '')}</td>
  <td>{b.get('severity', '')}/5</td>
  <td>{b.get('reason', '')}</td>
  <td>{b.get('global_cr3', '')}</td>
  <td>{b.get('china_share', '')}</td>
</tr>\n"""
    html += """
</table>

<h2>Top 候选标的</h2>
<table>
<tr><th>排序</th><th>标的</th><th>瓶颈环节</th><th>入选理由</th><th>收入暴露</th><th>估值/财务</th><th>瓶颈评分</th><th>评级</th></tr>
"""
    for i, c in enumerate(candidates, 1):
        score = c.get('final_score', 0)
        score_class = 'score-high' if score >= 70 else ('score-mid' if score >= 55 else 'score-low')
        f = c.get('fundamentals', {})
        fund_parts = []
        if f.get('market_cap_yi'):
            fund_parts.append(f"市值 {f['market_cap_yi']}亿")
        if f.get('pe_ratio'):
            fund_parts.append(f"PE {f['pe_ratio']:.1f}")
        if f.get('pb_ratio'):
            fund_parts.append(f"PB {f['pb_ratio']:.1f}")
        if f.get('roe') is not None:
            fund_parts.append(f"ROE {f['roe']:.1f}%")
        fund_str = ' · '.join(fund_parts) if fund_parts else '暂无'
        rev = c.get('revenue_composition', {})
        rev_str = ' · '.join([f"{k} {v}%" for k, v in rev.items()]) if rev else '暂无'
        html += f"""
<tr>
  <td>{i}</td>
  <td>{c.get('company', '')}<br><span style="color:#7f8c8d">{c.get('ticker', '')}</span></td>
  <td>{c.get('segment', '')}</td>
  <td>{c.get('selection_reason', '')}</td>
  <td>{c.get('revenue_exposure', '')}<br><span style="color:#7f8c8d;font-size:0.85em">{rev_str}</span></td>
  <td class="fund">{fund_str}</td>
  <td class="{score_class}">{score}</td>
  <td>{c.get('verdict', '')}</td>
</tr>\n"""
    html += """
</table>

<h2>证伪风险</h2>
<ul class="desc">
"""
    for ks in value_chain.get('kill_switches', []):
        html += f"<li>{ks}</li>\n"
    html += """
</ul>

<h2>说明</h2>
<p class="desc">
<b>方法论：</b>Serenity 供应链瓶颈研究 — 先找产业链稀缺环节，再映射 A 股标的，最后用 8 因子瓶颈评分卡排序。<br><br>
<b>数据来源：</b>产业链结构由 LLM 基于公开知识生成；候选标的来自深交所全 A 股 + 历史预测覆盖；估值/财务数据来自 fundamentals_cache（东财快照 + 同花顺财报摘要）。<br><br>
<b>用途：</b>本页面仅用于研究线索挖掘与 watchlist 扩展，不构成投资建议。任何标的纳入组合前需人工复核其真实业务占比与财务数据。
</p>
</body>
</html>
"""
    return html


def main():
    parser = argparse.ArgumentParser(description='产业链挖掘（最小接入）')
    parser.add_argument('--theme', required=True, help='产业链主题，如"人形机器人"')
    parser.add_argument('--db', default=f'{ROOT}/multi_agent/data/llm_predictions.db', help='A 股股票池数据库')
    parser.add_argument('--output-dir', default=f'{ROOT}/docs', help='HTML 输出目录')
    args = parser.parse_args()

    print(f"主题：{args.theme}")
    print("1) 加载 A 股股票池...")
    universe = load_a_share_universe(args.db)
    print(f"   共 {len(universe)} 只 A 股")

    print("2) 生成产业链地图...")
    value_chain = build_value_chain(args.theme)

    print("3) 映射候选标的...")
    candidates = map_candidates(args.theme, value_chain, universe)
    print(f"   候选 {len(candidates)} 只")

    print("4) Serenity 瓶颈评分...")
    scored = score_candidates(candidates, args.theme)

    print("5) 生成 HTML 页面...")
    html = render_html(args.theme, value_chain, scored)
    out_path = f"{args.output_dir}/supply_chain_{_slugify(args.theme)}.html"
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"   已生成：{out_path}")

    # 同时输出 JSON 摘要
    summary = {
        'theme': args.theme,
        'value_chain': value_chain,
        'top_candidates': scored[:10],
    }
    json_path = f"{ROOT}/multi_agent/data/supply_chain_{_slugify(args.theme)}.json"
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"   已生成 JSON 摘要：{json_path}")


if __name__ == '__main__':
    main()
