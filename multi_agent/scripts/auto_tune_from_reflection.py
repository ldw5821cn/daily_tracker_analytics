#!/usr/bin/env python3
"""根据 LLM 反思自动调整预测系统超参数（写入 JSON，不直接改代码）。

安全机制：
1. 每天最多调整一次（同 pred_date 只调一次）
2. 权重单次调整幅度不超过 ±0.08
3. 阈值单次调整不超过 ±3
4. 准确率 ≥ 70% 时不调整（市场噪音）
"""
from __future__ import annotations

import json
import os
from datetime import datetime

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
REFLECTION_PATH = os.path.join(MULTI_AGENT, 'data', 'prediction_reflection.json')
PARAMS_PATH = os.path.join(MULTI_AGENT, 'config', 'predictor_params.json')
LOG_PATH = os.path.join(MULTI_AGENT, 'data', 'auto_tuning_log.json')


def _load_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _clamp(value, min_val, max_val):
    return max(min_val, min(max_val, value))


def apply_auto_tuning():
    refl = _load_json(REFLECTION_PATH)
    if not refl:
        print('❌ 未找到 reflection，跳过自动调参')
        return False

    accuracy = refl.get('accuracy', 0)
    pred_date = refl.get('pred_date')
    params = _load_json(PARAMS_PATH)
    if not params:
        # 首次创建默认参数
        params = {
            'weights': {'technical': 0.30, 'fundamental': 0.25, 'sentiment': 0.15, 'macro': 0.10, 'debate': 0.20},
            'threshold': {'strong_bull': 62, 'bull': 55, 'neutral_high': 52, 'neutral_low': 48, 'bear': 43, 'strong_bear': 38},
            'hard_rules': {'macro_bearish_block_bullish': True, 'macro_bearish_force_bearish_if_tech_below': 55, 'macro_bearish_score_threshold': 50},
        }

    # 安全：同 pred_date 不重复调参
    last_update = params.get('updated_at', '')
    if last_update and last_update.startswith(pred_date):
        print(f'[auto_tune] {pred_date} 已调参过，跳过')
        return False

    # 安全：准确率 ≥ 70% 不调整
    if accuracy >= 70:
        print(f'[auto_tune] 准确率 {accuracy}% ≥ 70%，不调整')
        return False

    by_signal = refl.get('by_signal', {})
    bullish_err = by_signal.get('bullish', {}).get('error_rate', 0)
    neutral_err = by_signal.get('neutral', {}).get('error_rate', 0)
    bearish_err = by_signal.get('bearish', {}).get('error_rate', 0)

    text = (refl.get('llm_reflection', '') + ' ' + ' '.join(refl.get('key_suggestions', []))).lower()
    changes = []

    # 1. 宏观 bearish 时看多失败率高 → 提高宏观权重，降低技术面权重
    if bullish_err > 60 or '拦截看涨' in text or '禁止生成看涨' in text:
        params['weights']['macro'] = _clamp(params['weights']['macro'] + 0.05, 0.05, 0.30)
        params['weights']['technical'] = _clamp(params['weights']['technical'] - 0.05, 0.10, 0.45)
        params['weights']['debate'] = _clamp(params['weights']['debate'] + 0.03, 0.10, 0.40)
        params['hard_rules']['macro_bearish_block_bullish'] = True
        changes.append('宏观 bearish 拦截：提高 macro/debate，降低 technical')

    # 2. 中性信号过度乐观 → 收窄 neutral 区间
    if neutral_err > 50 or '中性' in text:
        params['threshold']['neutral_high'] = _clamp(params['threshold']['neutral_high'] - 1, 51, 58)
        params['threshold']['neutral_low'] = _clamp(params['threshold']['neutral_low'] + 1, 42, 49)
        changes.append('收窄 neutral 区间')

    # 3. 整体准确率偏低 → 全面提高阈值门槛
    if accuracy < 60:
        params['threshold']['bull'] = _clamp(params['threshold']['bull'] + 1, 52, 62)
        params['threshold']['bear'] = _clamp(params['threshold']['bear'] - 1, 38, 48)
        changes.append('提高 bullish/bearish 阈值门槛')

    # 4. 看空信号表现很好 → 保持 bearish 阈值不动，不挤压高胜率区间
    if bearish_err < 15:
        changes.append('bearish 信号错误率低，保持 bearish 区间')

    if not changes:
        print('[auto_tune] 无需要调整的参数')
        return False

    # 归一化权重
    total = sum(params['weights'].values())
    params['weights'] = {k: round(v / total, 3) for k, v in params['weights'].items()}

    params['updated_at'] = datetime.now().isoformat()
    params['updated_by'] = 'auto_tune_from_reflection'
    params['updated_reason'] = ' | '.join(changes)
    params['trigger_accuracy'] = accuracy
    params['trigger_pred_date'] = pred_date

    _save_json(PARAMS_PATH, params)

    # 记录日志
    logs = _load_json(LOG_PATH)
    logs.append({
        'date': datetime.now().isoformat(),
        'pred_date': pred_date,
        'accuracy': accuracy,
        'changes': changes,
        'new_params': params,
    })
    _save_json(LOG_PATH, logs)

    print(f'[auto_tune] 已调整参数，原因: {" | ".join(changes)}')
    print(f'[auto_tune] 新权重: {params["weights"]}')
    print(f'[auto_tune] 新阈值: {params["threshold"]}')
    return True


if __name__ == '__main__':
    apply_auto_tuning()
