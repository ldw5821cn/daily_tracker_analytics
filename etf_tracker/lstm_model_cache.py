#!/usr/bin/env python3
"""
LSTM 模型缓存管理器
支持收盘后训练 LSTM，第二天早上直接加载使用
"""

import os
import json
import pickle
import shutil
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
from pathlib import Path
import numpy as np

# 缓存根目录
CACHE_ROOT = os.path.expanduser("~/etf_tracker/models")
MODEL_INFO_FILE = "model_info.json"


def get_model_dir(etf_code: str) -> str:
    """获取某 ETF 的 LSTM 模型缓存目录"""
    code = str(etf_code).replace(".", "_")
    model_dir = os.path.join(CACHE_ROOT, code)
    os.makedirs(model_dir, exist_ok=True)
    return model_dir


def is_cache_valid(etf_code: str, max_age_days: int = 1) -> bool:
    """
    检查某 ETF 的 LSTM 缓存是否有效
    默认：缓存不超过 1 天（即只能第二天使用前一天收盘后的训练结果）
    """
    model_dir = get_model_dir(etf_code)
    info_path = os.path.join(model_dir, MODEL_INFO_FILE)
    
    if not os.path.exists(info_path):
        return False
    
    try:
        with open(info_path, 'r') as f:
            info = json.load(f)
        
        train_date = datetime.strptime(info['train_date'], '%Y-%m-%d').date()
        today = datetime.now().date()
        
        # 检查是否所有子模型文件都存在
        for ts in info.get('timesteps', []):
            model_path = os.path.join(model_dir, f'model_ts{ts}.keras')
            if not os.path.exists(model_path):
                return False
        
        scaler_x_path = os.path.join(model_dir, 'scaler_X.joblib')
        scaler_y_path = os.path.join(model_dir, 'scaler_y.joblib')
        if not os.path.exists(scaler_x_path) or not os.path.exists(scaler_y_path):
            return False
        
        age = (today - train_date).days
        return 0 <= age <= max_age_days
    
    except Exception as e:
        print(f"  ⚠️ 检查 LSTM 缓存失败 ({etf_code}): {e}")
        return False


def save_lstm_model(etf_code: str, models: Dict, scaler_X, scaler_y) -> bool:
    """
    保存 LSTM 多窗口模型
    
    Args:
        etf_code: ETF 代码
        models: {'lstm_ts5': {'model': keras_model, 'timesteps': 5, ...}, ...}
        scaler_X: 特征标准化器
        scaler_y: 目标标准化器
    """
    try:
        model_dir = get_model_dir(etf_code)
        
        # 清理旧模型（可选：保留最近2个版本）
        _cleanup_old_models(model_dir, keep_versions=2)
        
        train_date = datetime.now().strftime('%Y-%m-%d')
        timesteps = []
        
        for key, model_info in models.items():
            ts = model_info['timesteps']
            timesteps.append(ts)
            model = model_info['model']
            model_path = os.path.join(model_dir, f'model_ts{ts}.keras')
            model.save(model_path)
        
        # 保存 scaler
        with open(os.path.join(model_dir, 'scaler_X.joblib'), 'wb') as f:
            pickle.dump(scaler_X, f)
        with open(os.path.join(model_dir, 'scaler_y.joblib'), 'wb') as f:
            pickle.dump(scaler_y, f)
        
        # 保存元信息
        info = {
            'train_date': train_date,
            'train_datetime': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'timesteps': sorted(timesteps),
            'n_features': scaler_X.n_features_in_ if hasattr(scaler_X, 'n_features_in_') else None,
            'etf_code': etf_code
        }
        
        with open(os.path.join(model_dir, MODEL_INFO_FILE), 'w') as f:
            json.dump(info, f, indent=2)
        
        print(f"  ✅ LSTM 模型已保存: {model_dir} (训练日期: {train_date})")
        return True
    
    except Exception as e:
        print(f"  ❌ 保存 LSTM 模型失败 ({etf_code}): {e}")
        return False


def load_lstm_model(etf_code: str) -> Optional[Dict]:
    """
    加载 LSTM 多窗口模型
    
    Returns:
        {'lstm_ts5': {'model': keras_model, 'timesteps': 5, 'scaler_X': ..., 'scaler_y': ...}, ...}
        或 None（加载失败）
    """
    try:
        import tensorflow as tf
        
        model_dir = get_model_dir(etf_code)
        info_path = os.path.join(model_dir, MODEL_INFO_FILE)
        
        with open(info_path, 'r') as f:
            info = json.load(f)
        
        # 加载 scaler
        with open(os.path.join(model_dir, 'scaler_X.joblib'), 'rb') as f:
            scaler_X = pickle.load(f)
        with open(os.path.join(model_dir, 'scaler_y.joblib'), 'rb') as f:
            scaler_y = pickle.load(f)
        
        models = {}
        for ts in info['timesteps']:
            model_path = os.path.join(model_dir, f'model_ts{ts}.keras')
            # 加载模型
            import tensorflow as tf
            from multi_model_predictor import AttentionWeightedSum
            custom_objects = {'AttentionWeightedSum': AttentionWeightedSum}
            model = tf.keras.models.load_model(model_path, safe_mode=False, custom_objects=custom_objects)
            models[f'lstm_ts{ts}'] = {
                'model': model,
                'timesteps': ts,
                'scaler_X': scaler_X,
                'scaler_y': scaler_y,
                'feature_cols': list(range(scaler_X.n_features_in_)) if hasattr(scaler_X, 'n_features_in_') else list(range(scaler_X.scale_.shape[0]))
            }
        
        print(f"  ✅ 加载 LSTM 缓存 ({etf_code}, 训练日期: {info['train_date']})")
        return models
    
    except Exception as e:
        print(f"  ❌ 加载 LSTM 模型失败 ({etf_code}): {e}")
        return None


def _cleanup_old_models(model_dir: str, keep_versions: int = 2):
    """保留最近几个版本的模型，删除旧版本"""
    try:
        # 当前实现：每次保存覆盖同名文件，暂不需要版本目录
        # 如果需要多版本，可以按日期分子目录
        pass
    except Exception:
        pass


def list_cached_models() -> Dict[str, str]:
    """列出所有已缓存的 LSTM 模型"""
    result = {}
    if not os.path.exists(CACHE_ROOT):
        return result
    
    for code in os.listdir(CACHE_ROOT):
        model_dir = os.path.join(CACHE_ROOT, code)
        info_path = os.path.join(model_dir, MODEL_INFO_FILE)
        if os.path.isdir(model_dir) and os.path.exists(info_path):
            try:
                with open(info_path, 'r') as f:
                    info = json.load(f)
                result[code] = info['train_date']
            except Exception:
                pass
    return result


def remove_model_cache(etf_code: str):
    """删除某 ETF 的 LSTM 缓存"""
    model_dir = get_model_dir(etf_code)
    if os.path.exists(model_dir):
        shutil.rmtree(model_dir)
        print(f"  🗑️ 已删除 LSTM 缓存: {etf_code}")


if __name__ == '__main__':
    # 简单测试
    print("已缓存的 LSTM 模型:")
    for code, date in list_cached_models().items():
        print(f"  {code}: {date}")
