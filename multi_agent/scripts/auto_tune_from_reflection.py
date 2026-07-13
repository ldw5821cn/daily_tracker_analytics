#!/usr/bin/env python3
"""根据 LLM 反思自动调整预测系统超参数，并输出 A/B 测试对比。

调整规则：
1. 若 reflection 指出"宏观 bearish 时看多信号失败率高"，则：
   - 宏观 bearish 时禁止生成 bullish 信号（强制转 neutral/bearish）
   - 提高宏观权重
2. 若 reflection 指出"中性信号在熊市被过度乐观"，则：
   - 降低 neutral 阈值范围，使更多 neutral 偏向 bearish
3. 若 reflection 指出"技术面在下跌趋势误导"，则：
   - 降低技术面权重，提高 debate/宏观权重
4. 若整体准确率 < 60%，全面提高阈值门槛
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, MULTI_AGENT)
sys.path.insert(0, PROJECT_ROOT)

REFLECTION_PATH = os.path.join(MULTI_AGENT, 'data', 'prediction_reflection.json')
PREDICTOR_PATH = os.path.join(MULTI_AGENT, 'analysts', 'agentic_predictor.py')
OUTPUT_PATH = os.path.join(MULTI_AGENT, 'data', 'auto_tuning_log.json')


def _load_reflection():
    if not os.path.exists(REFLECTION_PATH):
        return {}
    with open(REFLECTION_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def _parse_reflection(refl):
    text = refl.get('llm_reflection', '') + ' ' + ' '.join(refl.get('key_suggestions', []))
    text = text.lower()
    signals = {
        'bearish_bullish_block': any(k in text for k in ['禁止生成看涨', '宏观偏空', 'bearish', '压制看涨', '拦截看涨']),
        'neutral_downgrade': any(k in text for k in ['中性信号', '过度乐观', 'neutral', '中性']),
        'technical_misleading': any(k in text for k in ['技术面', 'technical', '滞后', '误导']),
        'low_accuracy': refl.get('accuracy', 100) < 60,
        'high_bullish_error': refl.get('by_signal', {}).get('bullish', {}).get('error_rate', 0) > 60,
        'high_neutral_error': refl.get('by_signal', {}).get('neutral', {}).get('error_rate', 0) > 50,
    }
    return signals


def _apply_tuning(signals, accuracy):
    """返回需要 patch 的 (path, old_string, new_string) 列表。"""
    patches = []

    # 1. 宏观 bearish 时禁止看涨
    if signals['bearish_bullish_block'] or signals['high_bullish_error']:
        patches.append((
            PREDICTOR_PATH,
            '''    if macro_signal == 'bearish' and individual_signal == 'bullish':
        return -base * strength * 2, "宏观 bearish 压制 bullish"''',
            '''    if macro_signal == 'bearish' and individual_signal == 'bullish':
        # 强干预：宏观熊市下完全禁止 bullish 信号，直接推至 bearish 区间
        return -20, "宏观 bearish 禁止 bullish"'''
        ))
        # 同时提高宏观权重
        patches.append((
            PREDICTOR_PATH,
            "    'macro': 0.10,      # 新增宏观权重",
            "    'macro': 0.18,      # 复盘后提高：宏观 bearish 时技术面容易失效"
        ))
        patches.append((
            PREDICTOR_PATH,
            "    'technical': 0.30,  # 市场环境动荡时技术面容易失效，适当降低",
            "    'technical': 0.22,  # 复盘后降低：下跌趋势中技术面常逆势误导"
        ))
        patches.append((
            PREDICTOR_PATH,
            "    'debate': 0.20,",
            "    'debate': 0.25,      # 复盘后提高：多空辩论比滞后技术更可靠"
        ))

    # 2. 中性信号在熊市降级
    if signals['neutral_downgrade'] or signals['high_neutral_error']:
        patches.append((
            PREDICTOR_PATH,
            "    'neutral_high': 55,\n    'neutral_low': 45,",
            "    'neutral_high': 52,\n    'neutral_low': 48,   # 复盘后收窄：减少 neutral 区间，避免熊市中性陷阱"
        ))

    # 3. 整体准确率 < 60% 时全面提高门槛
    if signals['low_accuracy']:
        patches.append((
            PREDICTOR_PATH,
            "    'bull': 55,\n    'bear': 45,",
            "    'bull': 58,\n    'bear': 42,   # 复盘后提高门槛：低准确率环境下信号需更严格"
        ))

    return patches


def apply_auto_tuning():
    refl = _load_reflection()
    if not refl:
        print('❌ 未找到 reflection，跳过自动调参')
        return []

    accuracy = refl.get('accuracy', 0)
    signals = _parse_reflection(refl)
    print(f'[auto_tune] 当前准确率 {accuracy}%，识别信号: {signals}')

    patches = _apply_tuning(signals, accuracy)
    if not patches:
        print('[auto_tune] 无需要调整的参数')
        return []

    for path, old, new in patches:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            if old not in content:
                print(f'  ⚠️ 跳过（未匹配）: {old[:60]}...')
                continue
            content = content.replace(old, new)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f'  ✅ 已调整: {old[:60]}... → {new[:60]}...')
        except Exception as e:
            print(f'  ❌ 调整失败: {e}')

    # 记录调参日志
    log = {
        'date': datetime.now().isoformat(),
        'pred_date': refl.get('pred_date'),
        'accuracy': accuracy,
        'signals': signals,
        'patches': [{'old': o[:120], 'new': n[:120]} for _, o, n in patches],
    }
    logs = []
    if os.path.exists(OUTPUT_PATH):
        with open(OUTPUT_PATH, 'r', encoding='utf-8') as f:
            logs = json.load(f)
    logs.append(log)
    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)

    return patches


if __name__ == '__main__':
    apply_auto_tuning()
