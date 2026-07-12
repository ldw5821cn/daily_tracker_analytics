"""LLM 因子解释与可信度评分。

读取精选因子库，调用 LLM 为每个因子生成投资逻辑解释，并给出可信度评分。
可信度低的因子会被过滤，生成新的精选因子文件。
"""
from __future__ import annotations

import json
import os
import sys
from typing import Dict, List

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'multi_agent'))

from core.llm_client import chat

SELECTED_FACTORS_PATH = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'llm_factors_selected.json')
OUTPUT_PATH = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'llm_factors_selected.json')


def _build_prompt(factors: List[Dict]) -> str:
    lines = []
    for i, f in enumerate(factors, 1):
        lines.append(f"{i}. 名称: {f['name']}")
        lines.append(f"   描述: {f.get('description', '无')}")
        lines.append(f"   来源: {f.get('source', 'unknown')}")
        lines.append(f"   样本外收益: {f.get('avg_test_return', 0):.2f}%")
        lines.append(f"   样本外回撤: {f.get('avg_test_drawdown', 0):.2f}%")
        lines.append(f"   Rank IC: {f.get('avg_rank_ic', 0):.4f}")
        lines.append(f"   代码: {f.get('code', '')[:200]}")
        lines.append("")
    prompt = f"""你是量化投资专家。请对以下每个因子从投资逻辑和经济直觉角度进行解释，并给出可信度评分（0-100）。

评分标准：
- 90-100: 有清晰、稳定、可解释的经济/行为金融学逻辑，且在不同市场环境下表现稳健
- 70-89: 有合理逻辑，但可能在某些市场环境下失效
- 50-69: 逻辑较弱，可能是数据挖掘或过度拟合
- 0-49: 没有合理逻辑，应被淘汰

要求：
1. 为每个因子输出一段简短的投资逻辑解释（不超过50字）
2. 给出可信度评分（整数）
3. 如果评分为0-49，请给出淘汰理由

请严格按以下 JSON 格式输出，不要包含其他内容：
{{
  "evaluations": [
    {{
      "name": "因子名称",
      "interpretation": "投资逻辑解释",
      "credibility_score": 85,
      "concern": "如果有风险或不足，请说明"
    }}
  ]
}}

因子列表：
{chr(10).join(lines)}
"""
    return prompt


def _parse_llm_output(text: str) -> List[Dict]:
    """从 LLM 输出中提取 JSON。"""
    if not text:
        return []
    text = text.strip()
    # 尝试直接解析
    try:
        data = json.loads(text)
        return data.get('evaluations', [])
    except json.JSONDecodeError:
        pass
    # 尝试提取 ```json 代码块
    if '```json' in text:
        start = text.find('```json') + 7
        end = text.find('```', start)
        block = text[start:end].strip()
        try:
            data = json.loads(block)
            return data.get('evaluations', [])
        except Exception:
            pass
    return []


def _merge_evaluations(factors: List[Dict], evaluations: List[Dict]) -> List[Dict]:
    """将 LLM 评价合并到因子数据中。"""
    eval_map = {e['name']: e for e in evaluations if 'name' in e}
    merged = []
    for f in factors:
        e = eval_map.get(f['name'], {})
        f['llm_interpretation'] = e.get('interpretation', '暂无')
        f['llm_credibility_score'] = int(e.get('credibility_score', 50))
        f['llm_concern'] = e.get('concern', '')
        merged.append(f)
    return merged


def run_factor_interpreter(min_credibility: int = 50, model: str = 'deepseek-v4-flash') -> Dict:
    """用 LLM 解释因子并过滤低可信度因子。"""
    if not os.path.exists(SELECTED_FACTORS_PATH):
        return {'error': 'selected factors not found'}

    with open(SELECTED_FACTORS_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
    factors = data.get('factors', [])
    if not factors:
        return {'error': 'no factors to interpret'}

    print(f"[factor_interpreter] 正在让 LLM 解释 {len(factors)} 个因子...")
    prompt = _build_prompt(factors)
    response = chat([{'role': 'user', 'content': prompt}], model=model, max_tokens=4000)
    if response is None:
        return {'error': 'LLM call failed or no API key'}

    evaluations = _parse_llm_output(response)
    if not evaluations:
        return {'error': 'LLM output parsing failed', 'raw': response[:500]}

    merged = _merge_evaluations(factors, evaluations)
    filtered = [f for f in merged if f.get('llm_credibility_score', 0) >= min_credibility]

    data['factors'] = merged
    data['filtered_factors'] = filtered
    data['min_credibility'] = min_credibility
    data['llm_model'] = model

    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[factor_interpreter] 原始因子: {len(merged)}，可信因子（≥{min_credibility}）: {len(filtered)}")
    print("[factor_interpreter] 可信度评分：")
    for f in merged:
        print(f"  {f['name']}: {f['llm_credibility_score']} | {f['llm_interpretation'][:50]}")

    return {
        'total': len(merged),
        'filtered': len(filtered),
        'min_credibility': min_credibility,
        'model': model,
    }


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--min-credibility', type=int, default=50)
    parser.add_argument('--model', type=str, default='deepseek-v4-flash')
    args = parser.parse_args()

    result = run_factor_interpreter(min_credibility=args.min_credibility, model=args.model)
    print(f"\n✅ LLM 因子解释完成: {result}")
