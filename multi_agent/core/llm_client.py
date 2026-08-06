"""统一的 LLM 客户端（OpenAI 兼容）。

配置优先级：
1. OPENAI_API_KEY + OPENAI_BASE_URL（可选）
2. OPENROUTER_API_KEY + OPENROUTER_BASE_URL（可选）
3. 环境无 key 时返回 None，调用方使用 fallback
"""
import os
import sys
from typing import Optional, List, Dict

# 尝试加载 Hermes config.yaml
def _load_hermes_provider(name: str = 'deepseek') -> dict:
    try:
        import yaml
    except ImportError:
        return {}
    for path in [os.path.expanduser('~/.hermes/config.yaml'), os.path.expanduser('~/.hermes/config.yml')]:
        if not os.path.exists(path):
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                cfg = yaml.safe_load(f) or {}
            for cp in cfg.get('custom_providers', []):
                if cp.get('name') == name and cp.get('api_key'):
                    return cp
            # 如果 name 是默认 provider，fallback 到第一个带 api_key 的自定义 provider
            for cp in cfg.get('custom_providers', []):
                if cp.get('api_key') and cp.get('base_url'):
                    return cp
        except Exception:
            continue
    return {}


def _get_client():
    try:
        import openai
    except ImportError:
        return None

    hermes = _load_hermes_provider('deepseek')
    if hermes:
        api_key = hermes['api_key']
        base_url = hermes.get('base_url')
        default_model = hermes.get('model', 'deepseek-chat')
        os.environ.setdefault('LLM_MODEL', default_model)
    else:
        api_key = os.getenv('OPENAI_API_KEY') or os.getenv('OPENROUTER_API_KEY') or os.getenv('LLM_API_KEY')
        base_url = os.getenv('OPENAI_BASE_URL') or os.getenv('OPENROUTER_BASE_URL') or os.getenv('LLM_BASE_URL')
        default_model = None

    if not api_key:
        return None

    kwargs = {'api_key': api_key}
    if base_url:
        kwargs['base_url'] = base_url
    if default_model:
        os.environ.setdefault('LLM_MODEL', default_model)
    return openai.OpenAI(**kwargs)


def chat(messages: List[Dict[str, str]],
         model: Optional[str] = None,
         temperature: float = 0.3,
         max_tokens: int = 800) -> Optional[str]:
    """
    发送 chat completion 请求。无 API key 时返回 None。
    增加模型 fallback：部分模型（如 deepseek-v4-flash）会返回空内容，自动切换到 deepseek-chat 重试。
    """
    client = _get_client()
    if client is None:
        return None

    _model = model or os.getenv('OPENAI_MODEL') or os.getenv('LLM_MODEL') or 'gpt-4o-mini'
    fallback_models = ['deepseek-chat']
    if _model == 'deepseek-chat':
        fallback_models = ['deepseek-reasoner']

    for attempt_model in [_model] + fallback_models:
        try:
            resp = client.chat.completions.create(
                model=attempt_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            content = resp.choices[0].message.content
            if content:
                if attempt_model != _model:
                    print(f"[llm_client] 模型 {_model} 返回空，已 fallback 到 {attempt_model}", file=sys.stderr)
                return content
        except Exception as e:
            print(f"[llm_client] 模型 {attempt_model} 调用失败: {e}", file=sys.stderr)
            continue
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
