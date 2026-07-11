"""统一的 LLM 客户端（OpenAI 兼容）。

配置优先级：
1. OPENAI_API_KEY + OPENAI_BASE_URL（可选）
2. OPENROUTER_API_KEY + OPENROUTER_BASE_URL（可选）
3. 环境无 key 时返回 None，调用方使用 fallback
"""
import os
import sys
from typing import Optional, List, Dict

# 尝试加载 .env
try:
    from dotenv import load_dotenv
    _proj = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for _env in [os.path.join(_proj, '.env'), os.path.join(_proj, 'etf_tracker', '.env')]:
        if os.path.exists(_env):
            load_dotenv(_env, override=False)
except Exception:
    pass


def _get_client():
    try:
        import openai
    except ImportError:
        return None

    api_key = os.getenv('OPENAI_API_KEY') or os.getenv('OPENROUTER_API_KEY') or os.getenv('LLM_API_KEY')
    base_url = os.getenv('OPENAI_BASE_URL') or os.getenv('OPENROUTER_BASE_URL') or os.getenv('LLM_BASE_URL')

    if not api_key:
        return None

    kwargs = {'api_key': api_key}
    if base_url:
        kwargs['base_url'] = base_url
    return openai.OpenAI(**kwargs)


def chat(messages: List[Dict[str, str]],
         model: Optional[str] = None,
         temperature: float = 0.3,
         max_tokens: int = 800) -> Optional[str]:
    """
    发送 chat completion 请求。无 API key 时返回 None。
    """
    client = _get_client()
    if client is None:
        return None

    _model = model or os.getenv('OPENAI_MODEL') or os.getenv('LLM_MODEL') or 'gpt-4o-mini'
    try:
        resp = client.chat.completions.create(
            model=_model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content
    except Exception as e:
        print(f"[llm_client] LLM 调用失败: {e}", file=sys.stderr)
        return None


def summarize_news(ticker: str, name: str, news_items: List[Dict]) -> Optional[str]:
    """用 LLM 对新闻做摘要和情绪判断。"""
    if not news_items:
        return None

    text = "\n---\n".join([
        f"标题: {n.get('title','')}\n日期: {n.get('date','')}\n来源: {n.get('source','')}\n摘要: {n.get('desc','')[:300]}"
        for n in news_items[:8]
    ])

    prompt = f"""你是资深财经分析师。请阅读以下关于 {name}({ticker}) 的最新新闻，给出：
1. 一句话总结（30字以内）
2. 情绪判断：积极/中性/消极
3. 对股价的主要影响：利好/利空/中性
4. 关键风险点（如有）

新闻内容：
{text}

请用中文简洁输出。"""

    return chat([{'role': 'user', 'content': prompt}], temperature=0.2, max_tokens=400)


if __name__ == '__main__':
    r = chat([{'role': 'user', 'content': '你好'}])
    print('LLM 可用' if r else 'LLM 未配置（缺少 API key 或 openai 包）')
