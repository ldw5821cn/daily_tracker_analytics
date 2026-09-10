#!/usr/bin/env python3
"""
真正的多 LLM 独立 Agent 辩论引擎（Vibe-Trading 借鉴）。

架构：
- Bull Agent: 独立 LLM，只收集看涨证据、目标价、thesis breakers
- Bear Agent: 独立 LLM，只收集看跌证据、目标价、thesis breakers
- Judge Agent: 独立 LLM，综合双方观点，输出最终信号/置信度/仓位

每个 Agent 独立调用 chat()，避免单模型角色扮演的自我妥协。
"""
import os
import sys
import json
from typing import Dict, Optional

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, MULTI_AGENT)
sys.path.insert(0, PROJECT_ROOT)

from core.llm_client import chat


def _safe_json(obj) -> str:
    """安全地序列化报告对象为文本。"""
    if obj is None:
        return "无"
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2, default=str)[:4000]
    except Exception:
        return str(obj)[:4000]


def _build_context(ticker: str, name: str, sector: str, category: str,
                   technical: Optional[Dict], fundamental: Optional[Dict],
                   news: Optional[Dict], macro_report: Optional[Dict]) -> str:
    """构建所有 Agent 共享的轻量上下文（避免过长 prompt 拖慢推理）。"""
    ts = technical.get('tech_snapshot', {}) if technical else {}
    tech_summary = f"""当前价{ts.get('current_price', 'N/A')}, MA20={ts.get('ma20', 'N/A')}, RSI14={ts.get('rsi_14', 'N/A')}, MACD柱={ts.get('macd_hist', 'N/A')}, 布林带={ts.get('boll_down', 'N/A')}/{ts.get('boll_mid', 'N/A')}/{ts.get('boll_up', 'N/A')}"""
    
    fs = fundamental.get('fundamentals', {}) if fundamental else {}
    fund_summary = f"PE={fs.get('pe_ratio', 'N/A')}, 营收增长={fs.get('revenue_growth', 'N/A')}%, 净利率={fs.get('profit_margins', 'N/A')}%, 负债权益比={fs.get('debt_to_equity', 'N/A')}%, Z-score={fs.get('altman_z_score', 'N/A')}"
    
    news_summary = f"情绪={news.get('sentiment_score', 'N/A')}, 关键词={news.get('keywords', [])[:5]}" if news else "无"
    macro_summary = f"宏观信号={macro_report.get('macro_signal', 'N/A')}, 分数={macro_report.get('macro_score', 'N/A')}" if macro_report else "无"
    
    return f"""标的: {name}({ticker}) 类别: {category} 板块: {sector or '未知'}
技术: {tech_summary}
基本面: {fund_summary}
新闻: {news_summary}
宏观: {macro_summary}
"""


def bull_agent(ticker: str, name: str, sector: str, category: str,
               technical: Optional[Dict], fundamental: Optional[Dict],
               news: Optional[Dict], macro_report: Optional[Dict],
               temperature: float = 0.3) -> Dict:
    """独立 Bull Agent：只找看涨证据。"""
    ctx = _build_context(ticker, name, sector, category, technical, fundamental, news, macro_report)
    prompt = f"""你是独立的看涨分析师 Agent。你的唯一任务是为以下标的找出最有力的看涨证据。

{ctx}

请严格从看多角度输出（不要故意平衡观点）：
1. 核心看涨逻辑（2-3条，每条必须有数据支撑）
2. 目标价或目标区间（基于技术面/基本面推导）
3. Thesis Breakers: 列出2-3个会推翻你看涨结论的具体触发器（价格/指标/事件阈值）
4. 看涨强度评分（0-100）
5. 置信度（0-100）

输出格式必须是 JSON:
{{
    "core_arguments": ["...", "..."],
    "target_price": float,
    "target_price_reason": "...",
    "thesis_breakers": [{{"trigger": "...", "threshold": "..."}}],
    "bull_score": int,
    "confidence": int,
    "summary": "50字以内总结"
}}
"""
    response = chat([
        {'role': 'system', 'content': '你是顶级买方分析师，擅长发现被低估的投资机会。你只输出 JSON，不输出任何解释性文字。'},
        {'role': 'user', 'content': prompt},
    ], temperature=temperature, max_tokens=800, retries=1, retry_delay=2.0)
    return _parse_agent_response(response, 'bull')


def bear_agent(ticker: str, name: str, sector: str, category: str,
               technical: Optional[Dict], fundamental: Optional[Dict],
               news: Optional[Dict], macro_report: Optional[Dict],
               temperature: float = 0.3) -> Dict:
    """独立 Bear Agent：只找看跌证据。"""
    ctx = _build_context(ticker, name, sector, category, technical, fundamental, news, macro_report)
    prompt = f"""你是独立的看跌分析师 Agent。你的唯一任务是为以下标的找出最有力的看跌证据。

{ctx}

请严格从看空角度输出（不要故意平衡观点）：
1. 核心看跌逻辑（2-3条，每条必须有数据支撑）
2. 下行目标价或目标区间
3. Thesis Breakers: 列出2-3个会推翻你看跌结论的具体触发器（价格/指标/事件阈值）
4. 看跌强度评分（0-100）
5. 置信度（0-100）

输出格式必须是 JSON:
{{
    "core_arguments": ["...", "..."],
    "target_price": float,
    "target_price_reason": "...",
    "thesis_breakers": [{{"trigger": "...", "threshold": "..."}}],
    "bear_score": int,
    "confidence": int,
    "summary": "50字以内总结"
}}
"""
    response = chat([
        {'role': 'system', 'content': '你是顶级风险分析师，擅长发现投资标的的下行风险。你只输出 JSON，不输出任何解释性文字。'},
        {'role': 'user', 'content': prompt},
    ], temperature=temperature, max_tokens=800, retries=1, retry_delay=2.0)
    return _parse_agent_response(response, 'bear')


def judge_agent(ticker: str, name: str, sector: str, category: str,
                bull_report: Dict, bear_report: Dict,
                technical: Optional[Dict], fundamental: Optional[Dict],
                news: Optional[Dict], macro_report: Optional[Dict],
                temperature: float = 0.2) -> Dict:
    """独立 Judge Agent：综合 Bull/Bear 观点，输出最终裁决。"""
    ctx = _build_context(ticker, name, sector, category, technical, fundamental, news, macro_report)
    prompt = f"""你是独立的首席投资官 Agent。请综合看涨/看跌双方观点，做出最终裁决。

{ctx}

===== 看涨分析师观点 =====
{_safe_json(bull_report)}

===== 看跌分析师观点 =====
{_safe_json(bear_report)}

请输出最终裁决：
1. 最终信号："看多" / "看空" / "中性" / "观望"
2. 加权得分（0-100，50为中性，越高越看多）
3. 置信度（0-100）
4. 建议仓位比例（0-25%，按信号强度）
5. 目标价
6. 止损价
7. 关键支撑/阻力位
8. 核心理由（50字以内）
9. 风险提示（50字以内）
10. 最终裁决必须明确回应双方观点：哪一方证据更强，为什么？

输出格式必须是 JSON:
{{
    "signal": "看多|看空|中性|观望",
    "weighted_score": float,
    "confidence": int,
    "position_pct": float,
    "target_price": float,
    "stop_loss": float,
    "support": float,
    "resistance": float,
    "reasoning": "...",
    "risk_note": "...",
    "winner": "bull|bear|neutral",
    "winner_reason": "..."
}}
"""
    response = chat([
        {'role': 'system', 'content': '你是首席投资官，擅长在多空冲突中做出明确决策。你只输出 JSON，不输出任何解释性文字。'},
        {'role': 'user', 'content': prompt},
    ], temperature=temperature, max_tokens=800, retries=1, retry_delay=2.0)
    return _parse_agent_response(response, 'judge')


def _parse_agent_response(response: Optional[str], agent_type: str) -> Dict:
    """解析 Agent 的 JSON 响应，失败时返回安全默认值。"""
    if not response:
        return _default_response(agent_type)
    
    # 尝试提取 JSON 块
    text = response.strip()
    if '```json' in text:
        text = text.split('```json')[1].split('```')[0].strip()
    elif '```' in text:
        text = text.split('```')[1].split('```')[0].strip()
    
    try:
        data = json.loads(text)
        return _normalize_response(data, agent_type)
    except Exception:
        # 再尝试从文本中找第一个 { ... }
        try:
            start = text.find('{')
            end = text.rfind('}')
            if start != -1 and end != -1 and end > start:
                data = json.loads(text[start:end+1])
                return _normalize_response(data, agent_type)
        except Exception:
            pass
    
    return _default_response(agent_type)


def _normalize_response(data: Dict, agent_type: str) -> Dict:
    """规范化响应字段。"""
    if agent_type == 'bull':
        return {
            'core_arguments': data.get('core_arguments', []),
            'target_price': float(data.get('target_price', 0) or 0),
            'target_price_reason': data.get('target_price_reason', ''),
            'thesis_breakers': data.get('thesis_breakers', []),
            'score': int(data.get('bull_score', data.get('score', 50)) or 50),
            'confidence': int(data.get('confidence', 50) or 50),
            'summary': data.get('summary', ''),
        }
    elif agent_type == 'bear':
        return {
            'core_arguments': data.get('core_arguments', []),
            'target_price': float(data.get('target_price', 0) or 0),
            'target_price_reason': data.get('target_price_reason', ''),
            'thesis_breakers': data.get('thesis_breakers', []),
            'score': int(data.get('bear_score', data.get('score', 50)) or 50),
            'confidence': int(data.get('confidence', 50) or 50),
            'summary': data.get('summary', ''),
        }
    else:  # judge
        return {
            'signal': data.get('signal', '中性'),
            'weighted_score': float(data.get('weighted_score', 50) or 50),
            'confidence': int(data.get('confidence', 50) or 50),
            'position_pct': float(data.get('position_pct', 0) or 0),
            'target_price': float(data.get('target_price', 0) or 0),
            'stop_loss': float(data.get('stop_loss', 0) or 0),
            'support': float(data.get('support', 0) or 0),
            'resistance': float(data.get('resistance', 0) or 0),
            'reasoning': data.get('reasoning', ''),
            'risk_note': data.get('risk_note', ''),
            'winner': data.get('winner', 'neutral'),
            'winner_reason': data.get('winner_reason', ''),
        }


def _default_response(agent_type: str) -> Dict:
    if agent_type == 'bull':
        return {
            'core_arguments': ['Agent 调用失败，无观点'],
            'target_price': 0,
            'target_price_reason': '',
            'thesis_breakers': [],
            'score': 50,
            'confidence': 50,
            'summary': 'Agent 调用失败',
        }
    elif agent_type == 'bear':
        return {
            'core_arguments': ['Agent 调用失败，无观点'],
            'target_price': 0,
            'target_price_reason': '',
            'thesis_breakers': [],
            'score': 50,
            'confidence': 50,
            'summary': 'Agent 调用失败',
        }
    else:
        return {
            'signal': '中性',
            'weighted_score': 50,
            'confidence': 50,
            'position_pct': 0,
            'target_price': 0,
            'stop_loss': 0,
            'support': 0,
            'resistance': 0,
            'reasoning': 'Judge Agent 调用失败',
            'risk_note': '',
            'winner': 'neutral',
            'winner_reason': '',
        }


def run_llm_debate(ticker: str, name: str = '', sector: str = '', category: str = '个股',
                   technical: Optional[Dict] = None, fundamental: Optional[Dict] = None,
                   news: Optional[Dict] = None, macro_report: Optional[Dict] = None) -> Dict:
    """运行完整的多 LLM 独立 Agent 辩论。
    
    返回包含 bull_report, bear_report, judge_report 的字典。
    """
    print(f"  [LLM辩论] {name}({ticker}): Bull/Bear Agent 并行...")
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as executor:
        bull_future = executor.submit(
            bull_agent, ticker, name, sector, category, technical, fundamental, news, macro_report
        )
        bear_future = executor.submit(
            bear_agent, ticker, name, sector, category, technical, fundamental, news, macro_report
        )
        try:
            bull = bull_future.result(timeout=25)
        except Exception as e:
            print(f"  ⚠️ Bull Agent 超时/失败: {e}")
            bull = _default_response('bull')
        try:
            bear = bear_future.result(timeout=25)
        except Exception as e:
            print(f"  ⚠️ Bear Agent 超时/失败: {e}")
            bear = _default_response('bear')
    
    print(f"  [LLM辩论] {name}({ticker}): Judge Agent...")
    try:
        judge = judge_agent(ticker, name, sector, category, bull, bear, technical, fundamental, news, macro_report)
    except Exception as e:
        print(f"  ⚠️ Judge Agent 超时/失败: {e}")
        judge = _default_response('judge')
    
    return {
        'bull_report': bull,
        'bear_report': bear,
        'judge_report': judge,
        'bull_score': bull.get('score', 50),
        'bear_score': bear.get('score', 50),
        'net_score': bull.get('score', 50) - bear.get('score', 50),
        'weighted_score': judge.get('weighted_score', 50),
        'signal': judge.get('signal', '中性'),
        'confidence': judge.get('confidence', 50),
        'position_pct': judge.get('position_pct', 0),
        'target_price': judge.get('target_price', 0),
        'stop_loss': judge.get('stop_loss', 0),
        'support': judge.get('support', 0),
        'resistance': judge.get('resistance', 0),
        'reasoning': judge.get('reasoning', ''),
        'risk_note': judge.get('risk_note', ''),
        'winner': judge.get('winner', 'neutral'),
        'winner_reason': judge.get('winner_reason', ''),
    }


if __name__ == '__main__':
    # 简单测试
    result = run_llm_debate(
        ticker='600028',
        name='中国石化',
        sector='油气开采及服务',
        category='个股',
        technical={'score': 55, 'tech_snapshot': {'rsi_14': 45, 'ma20': 6.0}},
        fundamental={'score': 60, 'fundamentals': {'pe_ratio': 10}},
        news={'sentiment_score': 0.1},
        macro_report={'macro_score': 50, 'macro_signal': 'neutral'},
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
