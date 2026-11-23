"""主题新闻情绪分析器。

读取 last30days 收集的新闻/讨论标题，调用 LLM 给出 bullish/bearish/neutral 判断。
"""
import json
import os
import sys
from typing import List, Dict

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'multi_agent'))

try:
    from core.llm_client import chat
except Exception:
    chat = None


def _build_prompt(topic: str, entries: List[Dict]) -> str:
    """构建情绪分析 prompt。"""
    lines = []
    for i, e in enumerate(entries[:10], 1):
        title = e.get('title', '') or e.get('text', '')[:120]
        source = e.get('source', 'unknown')
        date = e.get('date', '') or '日期未知'
        lines.append(f"{i}. [{source} {date}] {title}")

    prompt = f"""你是一位专业的 A 股市场情绪分析师。请根据以下关于"{topic}"的最新新闻和社交媒体讨论，判断该主题对 A 股相关板块的短期影响方向。

讨论列表：
{chr(10).join(lines)}

请从以下维度分析：
1. 是否有明确的政策利好/利空？
2. 是否有产业层面的重大突破或风险事件？
3. 市场情绪是乐观、谨慎还是悲观？
4. 对 A 股相关板块短期（1-3天）的偏向：看涨、看跌还是中性？

请严格按以下 JSON 格式输出，不要包含其他内容：
{{
  "sentiment": "bullish" | "bearish" | "neutral",
  "confidence": 0-100,
  "reason": "一句话总结判断理由",
  "key_drivers": ["驱动因素1", "驱动因素2"]
}}
"""
    return prompt


def analyze_sentiment(topic: str, entries: List[Dict]) -> Dict:
    """分析单个主题的情绪。"""
    if not entries:
        return {
            'sentiment': 'neutral',
            'confidence': 0,
            'reason': '暂无相关讨论',
            'key_drivers': []
        }

    if chat is None:
        return {
            'sentiment': 'neutral',
            'confidence': 0,
            'reason': 'LLM 客户端不可用',
            'key_drivers': []
        }

    prompt = _build_prompt(topic, entries)
    try:
        response = chat(
            messages=[
                {'role': 'system', 'content': '你是专业的 A 股市场情绪分析师，只输出 JSON 格式结果。'},
                {'role': 'user', 'content': prompt}
            ],
            model=os.getenv('LLM_MODEL'),
            temperature=0.3,
            max_tokens=400,
            retries=2,
            retry_delay=3.0
        )
        if not response:
            return {
                'sentiment': 'neutral',
                'confidence': 0,
                'reason': 'LLM 无返回',
                'key_drivers': []
            }

        # 提取 JSON
        text = response.strip()
        if '```json' in text:
            start = text.find('```json') + 7
            end = text.find('```', start)
            text = text[start:end].strip()
        elif '```' in text:
            start = text.find('```') + 3
            end = text.find('```', start)
            text = text[start:end].strip()

        result = json.loads(text)
        sentiment = result.get('sentiment', 'neutral')
        if sentiment not in ['bullish', 'bearish', 'neutral']:
            sentiment = 'neutral'
        return {
            'sentiment': sentiment,
            'confidence': int(result.get('confidence', 50)),
            'reason': result.get('reason', ''),
            'key_drivers': result.get('key_drivers', [])
        }
    except Exception as e:
        return {
            'sentiment': 'neutral',
            'confidence': 0,
            'reason': f'分析失败: {str(e)[:100]}',
            'key_drivers': []
        }


def sentiment_icon(sentiment: str) -> str:
    """情绪图标。"""
    return {
        'bullish': '🔥 看涨',
        'bearish': '⚠️ 看跌',
        'neutral': '➖ 中性'
    }.get(sentiment, '➖ 中性')
