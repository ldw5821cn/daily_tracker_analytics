"""
TradingAgents 风格辩论适配层（最小接入）。
对应 TradingAgents 的 bull_researcher / bear_researcher / research_manager，
但用单次结构化 LLM 调用完成，避免多轮对话的 token/延迟成本。
"""
import json
import os
import sys
import re
from typing import Dict, Optional, Tuple

# 兼容仓库结构：core 不是包，直接以脚本方式加载
import importlib.util
LLM_CLIENT_PATH = '/home/liudawei/github/daily_tracker_analytics/multi_agent/core/llm_client.py'
_spec = importlib.util.spec_from_file_location('llm_client', LLM_CLIENT_PATH)
_llm_client = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_llm_client)
chat = _llm_client.chat


def _format_report(name: str, report: Optional[Dict]) -> str:
    if not report:
        return "无"
    if isinstance(report, dict):
        # 过滤掉过长的文本字段，只保留关键数值
        compact = {k: v for k, v in report.items() if k not in ('summary', 'text', 'verdict_text')}
        return json.dumps(compact, ensure_ascii=False, indent=2, default=str)[:1200]
    return str(report)[:1200]


def _extract_json(text: str) -> Optional[Dict]:
    """从 LLM 输出中提取 JSON 对象。"""
    if not text:
        return None
    # 先尝试整个文本作为 JSON
    try:
        return json.loads(text)
    except Exception:
        pass
    # 再尝试 ```json ... ```
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # 最后尝试提取最外层 { ... }
    m = re.search(r'(\{.*\})', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    return None


def _fallback_rule_like(technical_report, fundamental_report, news_report):
    """LLM 失败时回退到原有规则计数，保证流水线不中断。"""
    from multi_agent.core.debate_engine import DebateEngine
    bull = DebateEngine.bull_argument(technical_report, fundamental_report, news_report)
    bear = DebateEngine.bear_argument(technical_report, fundamental_report, news_report)
    return bull, bear


def ta_style_debate(
    technical_report: Optional[Dict],
    fundamental_report: Optional[Dict],
    news_report: Optional[Dict],
    ticker: str = '',
    name: str = '',
    category: str = '',
    model: Optional[str] = None,
) -> Tuple[Dict, Dict, Dict]:
    """
    用 TradingAgents 风格的结构化提示词完成多空辩论。

    返回: (bull_arg, bear_arg, verdict)
      bull_arg/bear_arg 结构与 DebateEngine.bull_argument / bear_argument 一致，
      可直接被 agentic_predictor._manager_verdict 消费。
    """
    tech_json = _format_report('技术面报告', technical_report)
    fund_json = _format_report('基本面报告', fundamental_report)
    news_json = _format_report('新闻情绪报告', news_report)

    prompt = f"""你是一位专业的 A 股投研研究经理（Research Manager），正在主持一场多空辩论。
请先对三份分析师报告的"真实意图"做一个判断，再分别模拟【看涨研究员 Bull】和【看跌研究员 Bear】的立场，最后由你给出裁决。

标的：{name or ticker} ({ticker})，类别：{category or '个股'}

--- 技术面报告 ---
{tech_json}

--- 基本面报告 ---
{fund_json}

--- 新闻情绪报告 ---
{news_json}

请严格按以下 JSON 格式输出（不要输出任何其他内容）：
{{
  "intent_analysis": {{
    "core_conflict": "技术面/基本面/新闻三者最核心的矛盾点是什么？30字以内",
    "dominant_dimension": "当前哪一维度（技术面/基本面/新闻）最有说服力？为什么？",
    "blind_spot": "看涨方最容易忽视的反向风险是什么？看跌方最容易忽视的看多依据是什么？"
  }},
  "bull_score": 0-10 的整数,
  "bull_points": ["看涨论据1", "看涨论据2", ...],
  "bear_score": 0-10 的整数,
  "bear_points": ["看跌论据1", "看跌论据2", ...],
  "net_score": bull_score - bear_score,
  "rating": "强烈看多/看多/中性/看空/强烈看空" 之一,
  "recommendation": "BUY/Overweight/Hold/Underweight/SELL" 之一,
  "reasoning": "用中文简洁说明裁决逻辑，需引用 intent_analysis，100字以内"
}}

要求：
1. 必须先输出 intent_analysis；它是后续 bull/bear 判断的前提，而不是装饰性总结。
2. 看涨/看跌论据必须具体引用报告中的数据或事实，不能泛泛而谈。
3. 如果技术面和基本面背离明显，请在 intent_analysis 中明确指出哪一方更占上风及原因。
4. 评分 0-10，5 表示中性，数字越大方向越强。
5. 只输出 JSON，不要 Markdown、不要解释、不要代码块包裹。
"""

    messages = [
        {"role": "system", "content": "你是资深 A 股投研经理，擅长多空辩论与结构化输出。"},
        {"role": "user", "content": prompt},
    ]

    raw = chat(messages, model=model, temperature=0.3, max_tokens=1200)
    parsed = _extract_json(raw) if raw else None

    if parsed is None:
        print(f"  ⚠️ TradingAgents 风格辩论 LLM 解析失败，回退规则辩论: {name or ticker}", file=sys.stderr)
        bull, bear = _fallback_rule_like(technical_report, fundamental_report, news_report)
        verdict = {
            'rating': '中性',
            'recommendation': 'Hold',
            'verdict_text': 'LLM 辩论解析失败，回退规则引擎。',
        }
        return bull, bear, verdict

    bull_arg = {
        'side': '看涨(Bull)',
        'score': float(parsed.get('bull_score', 0)),
        'points': parsed.get('bull_points', []),
        'text': '## 看涨观点（LLM）\n\n' + '\n'.join(f"{i+1}. {p}" for i, p in enumerate(parsed.get('bull_points', []))),
        'llm_raw': raw,
    }
    bear_arg = {
        'side': '看跌(Bear)',
        'score': float(parsed.get('bear_score', 0)),
        'points': parsed.get('bear_points', []),
        'text': '## 看跌观点（LLM）\n\n' + '\n'.join(f"{i+1}. {p}" for i, p in enumerate(parsed.get('bear_points', []))),
        'llm_raw': raw,
    }
    verdict = {
        'rating': parsed.get('rating', '中性'),
        'recommendation': parsed.get('recommendation', 'Hold'),
        'verdict_text': parsed.get('reasoning', ''),
        'net_score': float(parsed.get('net_score', bull_arg['score'] - bear_arg['score'])),
    }
    return bull_arg, bear_arg, verdict


if __name__ == '__main__':
    # 简单自测：无报告时 LLM 应能返回 JSON
    b, r, v = ta_style_debate(
        technical_report={'score': 55, 'rating': '偏多'},
        fundamental_report={'score': 60, 'rating': '中性'},
        news_report={'sentiment_score': 0.15},
        ticker='000001',
        name='平安银行',
        category='个股',
    )
    print(json.dumps({'bull_score': b['score'], 'bear_score': r['score'], 'verdict': v}, ensure_ascii=False, indent=2))
