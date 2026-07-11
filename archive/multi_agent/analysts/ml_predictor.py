"""
基于 sklearn 的方向分类预测器

思路:
- 特征工程: 价格动量、均线斜率、RSI、MACD、成交量、波动率
- 目标: 未来 H 日收盘价相对当前涨跌方向 (up/down/flat)
- 模型: HistGradientBoostingClassifier (轻量快速)
- 校准: 按历史真实上涨比例做概率校准, 避免方向偏见
- 缓存: 同一天同一个 ticker 的模型结果在内存复用, 并持久化到磁盘实现跨进程/跨运行缓存
"""
import hashlib
import os
import time
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
from typing import List, Dict
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
import warnings
import joblib
warnings.filterwarnings('ignore')


class ModelCache:
    """
    跨进程/跨运行的模型磁盘缓存。
    缓存 key: (ticker, 数据 hash, horizon)
    缓存文件包含 model/scaler/base_rate/data_hash/created_at。
    """
    DEFAULT_CACHE_DIR = os.path.expanduser('~/.cache/hermes/ml_models')
    RETENTION_DAYS = 7

    def __init__(self, cache_dir: str = None, retention_days: int = None):
        self.cache_dir = cache_dir or self.DEFAULT_CACHE_DIR
        self.retention_days = retention_days or self.RETENTION_DAYS
        os.makedirs(self.cache_dir, exist_ok=True)

    @staticmethod
    def _data_hash(df: pd.DataFrame, horizon: int) -> str:
        """基于收盘价 + horizon 的数据指纹"""
        close = df['close'].astype(float).values
        raw = close[-100:].tobytes() + str(close[-1]).encode() + str(horizon).encode()
        return hashlib.md5(raw).hexdigest()

    def _cache_path(self, ticker: str, data_hash: str, horizon: int) -> str:
        safe_ticker = str(ticker).replace('/', '_')
        return os.path.join(
            self.cache_dir,
            f"{safe_ticker}_{data_hash}_{horizon}.joblib"
        )

    def load(self, ticker: str, df: pd.DataFrame, horizon: int) -> Dict:
        """加载缓存, 若数据 hash 不匹配或文件不存在返回 None"""
        data_hash = self._data_hash(df, horizon)
        path = self._cache_path(ticker, data_hash, horizon)
        if not os.path.exists(path):
            return None
        try:
            data = joblib.load(path)
            if data.get('data_hash') != data_hash:
                try:
                    os.unlink(path)
                except Exception:
                    pass
                return None
            data['_from_disk_cache'] = True
            return data
        except Exception:
            return None

    def save(self, ticker: str, df: pd.DataFrame, horizon: int, payload: Dict):
        """保存模型到磁盘缓存"""
        data_hash = self._data_hash(df, horizon)
        path = self._cache_path(ticker, data_hash, horizon)
        payload = dict(payload)
        payload['data_hash'] = data_hash
        payload['ticker'] = ticker
        payload['horizon'] = horizon
        payload['created_at'] = datetime.now().isoformat()
        joblib.dump(payload, path, compress=3)
        return path

    def cleanup(self, max_age_days: int = None):
        """清理过期缓存, 默认保留 7 天"""
        max_age_days = max_age_days or self.retention_days
        cutoff = time.time() - max_age_days * 24 * 3600
        removed = 0
        for fname in os.listdir(self.cache_dir):
            fpath = os.path.join(self.cache_dir, fname)
            if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
                try:
                    os.unlink(fpath)
                    removed += 1
                except Exception:
                    pass
        return removed

    def list_files(self, ticker: str = None) -> List[str]:
        """列出缓存文件, 可指定 ticker"""
        files = [os.path.join(self.cache_dir, f) for f in os.listdir(self.cache_dir)
                 if os.path.isfile(os.path.join(self.cache_dir, f))]
        if ticker is None:
            return files
        safe_ticker = str(ticker).replace('/', '_')
        return [f for f in files if os.path.basename(f).startswith(safe_ticker + '_')]


# 全局磁盘缓存实例, 子进程会各自实例化但访问同一目录
_MODEL_DISK_CACHE = ModelCache()


class MLDirectionPredictor:
    """机器学习方向预测器"""

    FLAT_THRESHOLD = 0.003  # 涨跌幅绝对值小于此值视为 flat

    # 内存缓存: key -> (ticker_hash, horizon, model_dict)
    _MODEL_CACHE = {}

    @classmethod
    def get_cache(cls) -> ModelCache:
        return _MODEL_DISK_CACHE

    @classmethod
    def _cache_key(cls, ticker: str, df: pd.DataFrame, horizon: int) -> str:
        """基于数据指纹 + horizon 生成缓存 key"""
        return f"{ticker}_{ModelCache._data_hash(df, horizon)}_{horizon}"

    @classmethod
    def _get_cached_model(cls, ticker: str, df: pd.DataFrame, horizon: int) -> Dict:
        mem_key = cls._cache_key(ticker, df, horizon)
        if mem_key in cls._MODEL_CACHE:
            return cls._MODEL_CACHE[mem_key]
        data = _MODEL_DISK_CACHE.load(ticker, df, horizon)
        if data:
            cls._MODEL_CACHE[mem_key] = data
        return data

    @classmethod
    def _set_cached_model(cls, ticker: str, df: pd.DataFrame, horizon: int, model: Dict):
        mem_key = cls._cache_key(ticker, df, horizon)
        cls._MODEL_CACHE[mem_key] = model
        _MODEL_DISK_CACHE.save(ticker, df, horizon, model)

    @classmethod
    def clear_cache(cls, max_age_days: int = None):
        """清理缓存接口, 可手动调用"""
        return _MODEL_DISK_CACHE.cleanup(max_age_days=max_age_days)

    @staticmethod
    def build_features(df: pd.DataFrame) -> np.ndarray:
        """构建特征矩阵 (单行)"""
        close = df['close'].astype(float).values
        volume = df['volume'].astype(float).values if 'volume' in df.columns else np.ones(len(df))
        features = []

        # 价格动量
        for window in [1, 3, 5, 10, 20]:
            if len(close) > window:
                features.append((close[-1] / close[-window - 1] - 1))
            else:
                features.append(0.0)

        # 均线斜率
        for ma in ['ma5', 'ma10', 'ma20', 'ma60']:
            if ma in df.columns and pd.notna(df[ma].iloc[-1]):
                arr = df[ma].astype(float).values
                slope = (arr[-1] - arr[-min(10, len(arr))]) / arr[-1] if arr[-1] != 0 else 0
                features.append(slope)
            else:
                features.append(0.0)

        # 技术指标
        for col in ['rsi6', 'rsi14', 'rsi24', 'macd', 'macd_signal', 'macd_hist']:
            if col in df.columns and pd.notna(df[col].iloc[-1]):
                features.append(float(df[col].iloc[-1]))
            else:
                features.append(0.0)

        # 成交量比
        vol_mean5 = np.mean(volume[-5:]) if len(volume) >= 5 else np.mean(volume)
        vol_mean20 = np.mean(volume[-20:]) if len(volume) >= 20 else np.mean(volume)
        features.append(volume[-1] / vol_mean5 if vol_mean5 > 0 else 1.0)
        features.append(volume[-1] / vol_mean20 if vol_mean20 > 0 else 1.0)

        # 波动率
        returns = np.diff(close) / close[:-1]
        features.append(np.std(returns[-20:]) * np.sqrt(252) if len(returns) >= 20 else 0.0)
        features.append(np.std(returns[-60:]) * np.sqrt(252) if len(returns) >= 60 else 0.0)

        return np.array(features).reshape(1, -1)

    @staticmethod
    def build_features_batch(df: pd.DataFrame, required_len: int = 60) -> np.ndarray:
        """
        向量化批量构建特征矩阵, 每行对应当前索引
        返回 shape (n, n_features), 前 required_len 行无效
        """
        close = df['close'].astype(float).values
        volume = df['volume'].astype(float).values if 'volume' in df.columns else np.ones(len(df))
        n = len(df)
        feats = []

        # 价格动量
        for window in [1, 3, 5, 10, 20]:
            arr = np.full(n, np.nan)
            if n > window:
                arr[window:] = close[window:] / close[:-window] - 1
            feats.append(arr)

        # 均线斜率
        for ma in ['ma5', 'ma10', 'ma20', 'ma60']:
            arr = np.full(n, np.nan)
            if ma in df.columns:
                m = df[ma].astype(float).values
                valid = m != 0
                arr[valid] = (m[valid] - np.roll(m, 9)[valid]) / m[valid]
            feats.append(arr)

        # 技术指标
        for col in ['rsi6', 'rsi14', 'rsi24', 'macd', 'macd_signal', 'macd_hist']:
            arr = np.full(n, np.nan)
            if col in df.columns:
                arr[:] = df[col].astype(float).values
            feats.append(arr)

        # 成交量比
        vol5 = pd.Series(volume).rolling(5, min_periods=1).mean().values
        vol20 = pd.Series(volume).rolling(20, min_periods=1).mean().values
        feats.append(np.where(vol5 > 0, volume / vol5, 1.0))
        feats.append(np.where(vol20 > 0, volume / vol20, 1.0))

        # 波动率
        rets = np.diff(close) / close[:-1]
        vol20d = pd.Series(np.concatenate([[np.nan], rets])).rolling(20, min_periods=1).std().values * np.sqrt(252)
        vol60d = pd.Series(np.concatenate([[np.nan], rets])).rolling(60, min_periods=1).std().values * np.sqrt(252)
        feats.append(vol20d)
        feats.append(vol60d)

        X = np.column_stack(feats)
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
        return X

    @classmethod
    def train(cls, df: pd.DataFrame, horizon: int, ticker: str = '', cache_to_disk: bool = True) -> Dict:
        """
        用滚动历史数据训练分类模型 (HistGradientBoostingClassifier 快速版 + 缓存)
        """
        cached = cls._get_cached_model(ticker, df, horizon)
        if cached:
            return cached

        close = df['close'].astype(float).values
        n = len(df)
        if n < horizon + 60:
            return {'error': '数据不足'}

        X = cls.build_features_batch(df)

        # 标签: i 处对应 future_ret = close[i+horizon-1] / close[i-1] - 1
        y = np.full(n, '', dtype=object)
        valid_idx = []
        for i in range(60, n - horizon + 1):
            future_ret = close[i + horizon - 1] / close[i - 1] - 1
            if future_ret > cls.FLAT_THRESHOLD:
                y[i] = 'up'
            elif future_ret < -cls.FLAT_THRESHOLD:
                y[i] = 'down'
            else:
                y[i] = 'flat'
            valid_idx.append(i)

        X_train = X[valid_idx]
        y_train = y[valid_idx]

        if len(X_train) < 50:
            return {'error': '有效训练样本不足'}

        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X_train)
        X_scaled = np.nan_to_num(X_scaled, nan=0.0, posinf=0.0, neginf=0.0)

        model = HistGradientBoostingClassifier(
            max_iter=30, max_depth=3, learning_rate=0.2,
            early_stopping=False, random_state=42
        )
        model.fit(X_scaled, y_train)

        up_ratio = (y_train == 'up').mean()
        result = {'model': model, 'scaler': scaler, 'base_rate': float(up_ratio)}
        if cache_to_disk:
            cls._set_cached_model(ticker, df, horizon, result)
        else:
            # 仅内存缓存, 不持久化
            mem_key = cls._cache_key(ticker, df, horizon)
            cls._MODEL_CACHE[mem_key] = result
        return result

    @classmethod
    def predict_proba(cls, df: pd.DataFrame, horizon: int, models: Dict = None) -> Dict:
        """预测未来 horizon 日方向概率"""
        x = cls.build_features(df)
        if models is None or 'error' in models:
            # 无模型时退化为简单动量
            ret = df['close'].iloc[-1] / df['close'].iloc[-min(10, len(df))] - 1
            if ret > 0.01:
                return {'up': 0.55, 'down': 0.45, 'flat': 0.0}
            elif ret < -0.01:
                return {'up': 0.45, 'down': 0.55, 'flat': 0.0}
            else:
                return {'up': 0.33, 'down': 0.33, 'flat': 0.34}

        scaler = models['scaler']
        model = models['model']
        x_scaled = scaler.transform(x)

        proba_arr = model.predict_proba(x_scaled)[0]
        classes = list(model.classes_)
        proba = {str(c): float(proba_arr[i]) for i, c in enumerate(classes)}

        # 确保包含 flat
        for k in ['up', 'down', 'flat']:
            proba.setdefault(k, 0.0)

        return proba

    @classmethod
    def predict(cls, df: pd.DataFrame, days=5, ticker: str = '', cache_to_disk: bool = True) -> Dict:
        """生成未来多日的方向预测和价格预测"""
        if isinstance(days, int):
            days = [1, 3, 5, 10] if days >= 10 else list(range(1, days + 1))
        days = [int(d) for d in days]
        if df is None or len(df) < 100:
            return {'error': '数据不足'}

        current_price = float(df.iloc[-1]['close'])
        predictions = []

        for day in days:
            models = cls.train(df, horizon=day, ticker=ticker, cache_to_disk=cache_to_disk)
            if 'error' in models:
                continue
            proba = cls.predict_proba(df, horizon=day, models=models)

            direction = max(proba, key=proba.get)
            # 提高 flat 阈值, 避免低置信方向预测
            if direction == 'flat' or max(proba.values()) < 0.5:
                pred_direction = 'flat'
                prob = proba['flat']
            elif direction == 'up':
                pred_direction = 'up'
                prob = proba['up']
            else:
                pred_direction = 'down'
                prob = proba['down']

            # 幅度: 用历史同方向平均收益 × 概率强度, 但做收缩校准
            close = df['close'].astype(float).values
            future_rets = []
            for i in range(60, len(close) - day):
                ret = close[i + day - 1] / close[i - 1] - 1
                label = 'up' if ret > cls.FLAT_THRESHOLD else 'down' if ret < -cls.FLAT_THRESHOLD else 'flat'
                if label == pred_direction:
                    future_rets.append(ret)

            if future_rets:
                avg_ret = np.median(future_rets)  # 用中位数更稳健
            else:
                avg_ret = 0.0

            # 收缩: flat 为0, 方向预测用 (prob-0.5)*2 作为强度, 并整体 ×0.5 防止过度乐观
            if pred_direction == 'flat':
                pred_return = 0.0
            else:
                strength = max(0.0, (prob - 0.5) * 2)  # 0~1
                pred_return = avg_ret * strength * 0.5
            pred_price = current_price * (1 + pred_return)

            predictions.append({
                'day': day,
                'pred_price': round(float(pred_price), 4),
                'pred_return': round(float(pred_return), 6),
                'pred_direction': pred_direction,
                'prob': round(float(prob), 4),
                'proba': {k: round(float(v), 4) for k, v in proba.items()},
            })

        if not predictions:
            return {'error': '无法生成预测'}

        avg_return = float(np.mean([p['pred_return'] for p in predictions]))
        return {
            'current_price': round(float(current_price), 4),
            'predictions': predictions,
            'avg_return': round(float(avg_return), 6),
            'trend': '看涨' if avg_return > 0.005 else '看跌' if avg_return < -0.005 else '震荡',
            'confidence': round(float(abs(avg_return) * 1000 + 50), 1),
        }


if __name__ == "__main__":
    import sys
    sys.path.insert(0, '/home/liudawei/github/daily_tracker_analytics/multi_agent')
    from core.data_layer import get_stock_data, calc_technical_indicators

    for ticker, name in [('516150', '稀土ETF'), ('515880', '通信ETF')]:
        df, _ = get_stock_data(ticker, calibrate=False)
        df = calc_technical_indicators(df)
        pred = MLDirectionPredictor.predict(df, days=[1, 3, 5, 10], ticker=ticker)
        print(f"\n=== {name}({ticker}) ===")
        print(pred)
