"""
#3 LLM新闻情绪分析 — 用大模型替代关键词匹配做新闻情绪判断
用法: python3 scripts/llm_news_sentiment.py "新闻标题和内容"
"""
import sys, os, json, urllib.request, urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'multi_agent'))

# 从news_analyst导入现有的新闻抓取功能
from analysts.news_analyst import fetch_news_for_stock

# Hermes Web UI API 配置
HERMES_API_BASE = os.getenv('HERMES_API_BASE', 'http://localhost:8642')
HERMES_PROFILE = os.getenv('HERMES_PROFILE', 'default')


def call_llm(prompt, timeout=30):
    """调用 Hermes LLM 分析文本"""
    api_key = os.getenv('HERMES_API_KEY', '')
    headers = {
        'Content-Type': 'application/json',
        'X-Hermes-Profile': HERMES_PROFILE,
    }
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'

    payload = {
        'input': prompt,
        'provider': 'custom:deepseek',
        'model': 'deepseek-v4-flash',
        'timeout_ms': timeout * 1000,
    }

    try:
        req = urllib.request.Request(
            f'{HERMES_API_BASE}/api/chat-run/runs',
            data=json.dumps(payload).encode(),
            headers=headers,
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read())
            return result.get('reply', '')
    except Exception as e:
        return f'[LLM调用失败: {e}]'


def llm_sentiment_analysis(title, content=""):
    """用 LLM 分析新闻情绪"""
    text = title
    if content:
        text += '。' + content[:500]

    prompt = f"""你是一个专业的金融新闻情绪分析师。请分析以下中文金融新闻的情绪倾向。

规则：
- 返回 JSON 格式，不要包含其他文字
- sentiment: "positive" | "negative" | "neutral"
- score: -1.0 到 1.0 之间的浮点数（正=积极，负=消极）
- reason: 简要原因（20字以内）

新闻：{text[:800]}"""

    reply = call_llm(prompt)
    try:
        result = json.loads(reply.strip())
        return {
            'sentiment': result.get('sentiment', 'neutral'),
            'score': float(result.get('score', 0)),
            'reason': result.get('reason', ''),
            'method': 'llm',
        }
    except:
        # LLM 失败时回退到规则匹配
        return _keyword_fallback(text)


def _keyword_fallback(text):
    """关键词匹配回退"""
    from analysts.news_analyst import POSITIVE_WORDS, NEGATIVE_WORDS
    pos_count = sum(1 for w in POSITIVE_WORDS if w in text)
    neg_count = sum(1 for w in NEGATIVE_WORDS if w in text)
    score = (pos_count - neg_count) / max(pos_count + neg_count, 1)
    sentiment = 'positive' if score > 0.2 else 'negative' if score < -0.2 else 'neutral'
    return {'sentiment': sentiment, 'score': score, 'reason': f'关键词: +{pos_count}/-{neg_count}', 'method': 'keyword'}


def analyze_all_news(stocks=None):
    """批量分析多只股票的新闻情绪"""
    if stocks is None:
        stocks = [('600206', '有研新材'), ('601991', '大唐发电')]

    results = []
    for ticker, name in stocks:
        print(f'分析 {name}({ticker}) 新闻...')
        news_list = fetch_news_for_stock(ticker, name)
        sentiments = []
        for news in news_list[:5]:
            sentiment = llm_sentiment_analysis(news.get('title', ''), news.get('content', ''))
            sentiments.append({'title': news.get('title', ''), **sentiment})

        overall_score = sum(s.get('score', 0) for s in sentiments) / max(len(sentiments), 1)
        results.append({
            'ticker': ticker, 'name': name,
            'news_count': len(sentiments),
            'overall_sentiment': 'positive' if overall_score > 0.2 else 'negative' if overall_score < -0.2 else 'neutral',
            'overall_score': round(overall_score, 2),
            'details': sentiments,
        })
        print(f'  -> 总体情绪: {results[-1]["overall_sentiment"]} (评分{results[-1]["overall_score"]:+.2f})')

    return results


if __name__ == '__main__':
    if len(sys.argv) > 1:
        text = ' '.join(sys.argv[1:])
        result = llm_sentiment_analysis(text)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        results = analyze_all_news()
        print(json.dumps(results, ensure_ascii=False, indent=2))
