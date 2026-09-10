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
            
            # 如果明确指定了 name，先精确匹配 custom_providers
            if name:
                for cp in cfg.get('custom_providers', []):
                    if cp.get('name') == name and cp.get('api_key'):
                        return cp
                # 匹配 model.provider
                model_cfg = cfg.get('model', {})
                provider_name = model_cfg.get('provider')
                if provider_name == name:
                    for cp in cfg.get('custom_providers', []):
                        if cp.get('name') == provider_name and cp.get('api_key'):
                            return cp
            
            # 然后尝试 auxiliary.vision（仅当没指定 name 或 name 匹配 vision provider）
            aux = cfg.get('auxiliary', {})
            vision = aux.get('vision', {})
            if vision.get('api_key') and vision.get('provider'):
                provider = vision['provider']
                if not name or provider == name or name in provider.lower():
                    base_url = vision.get('base_url', '')
                    if not base_url:
                        if 'kimi' in provider.lower() or 'moonshot' in provider.lower():
                            base_url = 'https://api.moonshot.cn/v1'
                        elif 'deepseek' in provider.lower():
                            base_url = 'https://api.deepseek.com'
                    return {
                        'name': provider,
                        'api_key': vision['api_key'],
                        'base_url': base_url,
                        'model': vision.get('model', 'kimi-for-coding')
                    }
            
            # 如果 name 是默认 provider，fallback 到第一个带 api_key 的自定义 provider
            if name:
                for cp in cfg.get('custom_providers', []):
                    if cp.get('api_key') and cp.get('base_url'):
                        return cp
        except Exception:
            continue
    return {}


def _load_hermes_kimi_coding():
    """从 Hermes config 的 model.providers.kimi-coding 加载 kimi-coding 配置。
    用户要求：项目内 LLM（含反思）走 kimi-coding，不再走 deepseek。"""
    cfg_paths = [
        os.path.expanduser('~/.hermes/config.yaml'),
        os.path.expanduser('~/.hermes/config.yml'),
    ]
    for path in cfg_paths:
        if not os.path.exists(path):
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                cfg = yaml.safe_load(f)
            if not cfg:
                continue
            providers = cfg.get('model', {}).get('providers', {})
            kimi = providers.get('kimi-coding', {})
            if kimi.get('api_key'):
                return {
                    'name': 'kimi-coding',
                    'api_key': kimi['api_key'],
                    'base_url': kimi.get('base_url', 'https://api.moonshot.cn/v1'),
                    'model': kimi.get('model', 'kimi-for-coding'),
                }
        except Exception:
            continue
    return {}


def _get_client():
    try:
        import openai
    except ImportError:
        return None

    # 优先从 Hermes config 加载 deepseek（已充值，恢复使用）
    hermes = _load_hermes_provider('deepseek')
    if hermes:
        api_key = hermes['api_key']
        base_url = hermes.get('base_url')
        default_model = hermes.get('model', 'deepseek-chat')
        os.environ.setdefault('LLM_MODEL', default_model)
    else:
        # fallback: 尝试加载 kimi-coding
        hermes = _load_hermes_kimi_coding()
        if hermes:
            api_key = hermes['api_key']
            base_url = hermes.get('base_url', 'https://api.moonshot.cn/v1')
            default_model = hermes.get('model', 'kimi-for-coding')
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
    # timeout=30 + max_retries=0：openai 默认 600s 超时会拖垮批量任务（2026-08-26 实测 LLM API 降级时
    # 单次调用挂 10 分钟，整批 186 标的连环超时）。重试交给 chat() 的显式 retries 循环。
    kwargs['timeout'] = float(os.environ.get('LLM_TIMEOUT', '30'))
    kwargs['max_retries'] = 0
    return openai.OpenAI(**kwargs)


def chat(messages: List[Dict[str, str]],
         model: Optional[str] = None,
         temperature: float = 0.3,
         max_tokens: int = 800,
         retries: int = 3,
         retry_delay: float = 5.0) -> Optional[str]:
    """
    发送 chat completion 请求。无 API key 时返回 None。
    增加模型 fallback：部分模型（如 deepseek-v4-flash）会返回空内容，自动切换到 deepseek-chat 重试。
    增加连接重试：网络抖动/限流时自动重试（指数退避），避免一次性连接错误中断批量预测。
    """
    import time
    client = _get_client()
    if client is None:
        return None

    _model = model or os.getenv('OPENAI_MODEL') or os.getenv('LLM_MODEL') or 'gpt-4o-mini'
    # 根据当前模型决定 fallback 策略
    fallback_models = []
    if 'deepseek' in _model.lower():
        # deepseek 系列：fallback 到 deepseek 其他模型
        # ⚠️ 2026-09-10：deepseek-chat 放最前——deepseek-v4-flash/v4-pro 是推理模型，
        # 长 prompt 下推理会吃掉全部 max_tokens 导致 content 为空（静默浪费 3 次重试），
        # 而 deepseek-chat 非推理、稳定返回内容。同时 config.yaml 里的模型名可能过期
        # （如 deepseek-v4.1-flash → HTTP 400），第一个模型失败后应尽快落到可用模型。
        fallback_models = ['deepseek-chat', 'deepseek-v4-pro', 'deepseek-v4-flash']
    elif 'kimi' in _model.lower():
        # kimi 系列：fallback 到 kimi 其他模型
        fallback_models = ['kimi-k2-5-or-latest', 'kimi-for-coding']
    else:
        # 其他模型：fallback 到常见模型
        fallback_models = ['deepseek-v4-flash', 'deepseek-chat']
    if _model == 'deepseek-chat':
        fallback_models = ['deepseek-v4-flash', 'deepseek-v4-pro']
    elif _model == 'deepseek-reasoner':
        fallback_models = ['deepseek-chat', 'kimi-for-coding']

    attempts = [_model] + fallback_models
    last_error = None
    for attempt_model in attempts:
        for attempt in range(retries):
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
                        print(f"[llm_client] 模型 {_model} 无有效返回，已 fallback 到 {attempt_model}", file=sys.stderr)
                    return content
            except Exception as e:
                last_error = e
                # ⚠️ 2026-09-10：400 invalid_request_error（模型名不存在/请求非法）不可重试——
                # 立即跳出该模型的重试循环，避免 5s+10s+15s 白等（曾导致整批 LLM 调用超时）。
                if '400' in str(e) or 'invalid_request_error' in str(e):
                    print(f"[llm_client] 模型 {attempt_model} 请求非法(400)，跳过重试: {str(e)[:160]}", file=sys.stderr)
                    break
                is_last = (attempt == retries - 1)
                print(f"[llm_client] 模型 {attempt_model} 调用失败({attempt+1}/{retries}): {e}", file=sys.stderr)
                if not is_last:
                    time.sleep(retry_delay * (attempt + 1))  # 指数退避：5s, 10s, 15s
                continue
    if last_error:
        print(f"[llm_client] 所有模型均失败，最后错误: {last_error}", file=sys.stderr)
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
