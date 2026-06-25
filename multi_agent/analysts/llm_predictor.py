"""
基于大模型(Kimi)的方向预测器

思路:
- 将近期 K 线、技术指标、市场摘要构造成结构化 prompt
- 调用 Kimi API 输出方向、概率、目标价、理由
- 返回与 ml_predictor / trend_predictor 兼容的格式

环境变量:
    KIMI_API_KEY: Kimi API Key
    KIMI_BASE_URL: 可选, 默认 https://api.moonshot.cn/v1
    KIMI_MODEL: 可选, 默认 kimi-latest
"""
import os
import json
import re
from typing import Dict, List
from datetime import datetime
import numpy as np
import pandas as pd


def _call_kimi(prompt: str, temperature: float = 0.2) -> str:
    """调用 Kimi API, 返回文本(兼容 Coding Plan 和 Moonshot Platform)"""
    import requests

    api_key = os.getenv('MOONSHOT_API_KEY') or os.getenv('KIMI_API_KEY')
    if not api_key:
        return json.dumps({
            'error': '未配置 MOONSHOT_API_KEY 或 KIMI_API_KEY',
            'direction': 'flat',
            'probability': 0.34,
            'target_price': None,
            'reason': 'API key 缺失, 退化为观望'
        })

    # sk-kimi- 前缀 = Kimi Coding Plan, 目前只返回 thinking 不适合做 JSON 预测
    is_coding_key = api_key.startswith('sk-kimi-')
    if is_coding_key:
        return json.dumps({
            'error': '检测到 Kimi Coding Plan key, 该 key 不适合结构化 JSON 输出。'
                     '请使用 Moonshot Platform API Key (MOONSHOT_API_KEY)',
            'direction': 'flat',
            'probability': 0.34,
            'target_price': None,
            'reason': 'Kimi Coding Plan key 不兼容'
        })

    base_url = os.getenv('KIMI_BASE_URL', 'https://api.moonshot.cn/v1')
    model = os.getenv('KIMI_MODEL', 'kimi-latest')

    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    payload = {
        'model': model,
        'messages': [
            {'role': 'system', 'content': '你是一名资深量化分析师, 擅长根据技术面和市场情绪判断短期方向。只输出 JSON。'},
            {'role': 'user', 'content': prompt}
        ],
        'temperature': temperature,
        'max_tokens': 512,
    }
    try:
        resp = requests.post(f"{base_url}/chat/completions", headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        return data['choices'][0]['message']['content']
    except Exception as e:
        return json.dumps({
            'error': f'API 调用失败: {e}',
            'direction': 'flat',
            'probability': 0.34,
            'target_price': None,
            'reason': 'API 调用失败, 退化为观望'
        })


def _extract_json(text: str) -> Dict:
    """从模型输出中提取 JSON"""
    text = text.strip()
    # 去掉 markdown 代码块
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
    try:
        return json.loads(text)
    except Exception:
        # 尝试用正则匹配第一个 JSON 对象
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                pass
    return {'error': 'JSON 解析失败', 'raw': text}


def _build_prompt(ticker: str, name: str, df: pd.DataFrame, days: List[int]) -> str:
    """构建给 Kimi 的 prompt"""
    current_price = float(df.iloc[-1]['close'])
    recent = df.tail(20).copy()
    recent['date'] = recent.index.strftime('%Y-%m-%d')

    # 关键指标
    last = df.iloc[-1]
    prev = df.iloc[-5]
    indicators = {
        'current_price': round(current_price, 4),
        'ma5': round(last.get('ma5', np.nan), 4),
        'ma10': round(last.get('ma10', np.nan), 4),
        'ma20': round(last.get('ma20', np.nan), 4),
        'rsi14': round(last.get('rsi14', np.nan), 2),
        'macd_hist': round(last.get('macd_hist', np.nan), 4),
        'vol_ratio': round(last.get('volume', 0) / df['volume'].tail(20).mean(), 2) if 'volume' in df.columns else None,
        '20d_volatility': round(df['close'].tail(20).pct_change().std() * np.sqrt(252), 4),
        '5d_return': round(current_price / float(prev['close']) - 1, 4) if len(df) >= 5 else None,
    }

    # 最近 10 根 K 线摘要
    candles = []
    for _, row in recent.tail(10).iterrows():
        candles.append({
            'date': row['date'],
            'open': round(float(row['open']), 4) if 'open' in row else None,
            'high': round(float(row['high']), 4) if 'high' in row else None,
            'low': round(float(row['low']), 4) if 'low' in row else None,
            'close': round(float(row['close']), 4),
            'volume': int(row['volume']) if 'volume' in row and pd.notna(row['volume']) else None,
        })

    prompt = f"""请基于以下 A 股/ETF 技术面数据, 判断未来 {days} 个交易日的涨跌方向。

标的: {name} ({ticker})
当前价: {current_price}
关键指标: {json.dumps(indicators, ensure_ascii=False, default=str)}

最近 10 根 K线:
{json.dumps(candles, ensure_ascii=False, default=str)}

请输出严格 JSON 格式:
{{
    "direction": "up" | "down" | "flat",
    "probability": 0.0~1.0,
    "target_price": 数字,
    "reason": "50字以内的分析理由",
    "confidence": "high" | "medium" | "low"
}}

注意:
- direction 表示未来 {min(days)}~{max(days)} 个交易日收盘价的综合方向
- probability 表示对该方向的确信程度
- target_price 给出预期目标价
"""
    return prompt


class LLMPredictor:
    """大模型方向预测器(可选增强, 需配置 MOONSHOT_API_KEY)"""

    @classmethod
    def predict(cls, df: pd.DataFrame, days=5, ticker: str = '', name: str = '') -> Dict:
        """
        生成大模型预测; 如果未配置 MOONSHOT_API_KEY 则返回 error, 由调用方降级到 ML
        """
        if not os.getenv('MOONSHOT_API_KEY'):
            return {
                'error': '未配置 MOONSHOT_API_KEY, LLM 预测不可用。'
                         '可在 ~/.hermes/.env 添加 MOONSHOT_API_KEY=sk-xxx (Moonshot Platform)',
                'fallback': 'ml_predictor'
            }

        if isinstance(days, int):
            horizon_days = list(range(1, min(days, 5) + 1))
        else:
            horizon_days = sorted([int(d) for d in days])[:5]

        if df is None or len(df) < 30:
            return {'error': '数据不足'}

        current_price = float(df.iloc[-1]['close'])
        prompt = _build_prompt(ticker, name, df, horizon_days)

        try:
            raw = _call_kimi(prompt)
            result = _extract_json(raw)
        except Exception as e:
            return {'error': f'LLM 调用失败: {e}'}

        if 'error' in result:
            return {'error': result.get('error', '未知错误'), 'raw': result.get('raw', '')}

        direction = result.get('direction', 'flat')
        if direction not in ('up', 'down', 'flat'):
            direction = 'flat'

        prob = float(result.get('probability', 0.5))
        target_price = result.get('target_price')
        if target_price is None or not isinstance(target_price, (int, float)):
            # 根据方向简单估算
            if direction == 'up':
                target_price = current_price * (1 + prob * 0.02)
            elif direction == 'down':
                target_price = current_price * (1 - prob * 0.02)
            else:
                target_price = current_price

        pred_return = target_price / current_price - 1

        predictions = []
        for day in horizon_days:
            # 对不同 horizon 做衰减
            decay = 1.0 if day == 1 else 0.7 if day <= 3 else 0.5
            day_return = pred_return * decay
            day_price = current_price * (1 + day_return)
            predictions.append({
                'day': day,
                'pred_price': round(float(day_price), 4),
                'pred_return': round(float(day_return), 6),
                'pred_direction': direction,
                'prob': round(float(prob), 4),
                'reason': result.get('reason', ''),
                'confidence': result.get('confidence', 'medium'),
            })

        avg_return = float(np.mean([p['pred_return'] for p in predictions]))
        return {
            'current_price': round(float(current_price), 4),
            'predictions': predictions,
            'avg_return': round(float(avg_return), 6),
            'trend': '看涨' if avg_return > 0.005 else '看跌' if avg_return < -0.005 else '震荡',
            'confidence': round(float(abs(avg_return) * 1000 + prob * 30), 1),
            'llm_raw': result,
        }


if __name__ == "__main__":
    import sys
    sys.path.insert(0, '/home/liudawei/github/daily_tracker_analytics/multi_agent')
    from core.data_layer import get_stock_data, calc_technical_indicators

    ticker, name = '516150', '稀土ETF'
    df, _ = get_stock_data(ticker, calibrate=False)
    df = calc_technical_indicators(df)
    pred = LLMPredictor.predict(df, days=5, ticker=ticker, name=name)
    print(json.dumps(pred, ensure_ascii=False, indent=2, default=str))
