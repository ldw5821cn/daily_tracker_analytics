"""
改进版趋势预测器 - 基于多因子规则 + sklearn 集成的方向预测

改进点:
1. 引入技术指标动量、RSI、MACD、成交量、波动率等多因子
2. 使用 RandomForest / GradientBoosting 做方向分类
3. 对预测幅度做基于历史波动率的收缩校准
4. 震荡行情输出 "flat", 避免随机方向噪音
5. 支持跨进程/跨运行的模型磁盘缓存
"""
import hashlib
import os
import time
from datetime import datetime, timedelta
from typing import List, Dict
import numpy as np
import pandas as pd
import warnings
import joblib
warnings.filterwarnings('ignore')


class ImprovedModelCache:
    """
    ImprovedPredictor 跨进程/跨运行模型磁盘缓存。
    缓存 key: (ticker, 特征 hash, horizon)
    缓存文件包含 model/feature_cols/label_hash/created_at。
    """
    DEFAULT_CACHE_DIR = os.path.expanduser('~/.cache/hermes/improved_models')
    RETENTION_DAYS = 7

    def __init__(self, cache_dir: str = None, retention_days: int = None):
        self.cache_dir = cache_dir or self.DEFAULT_CACHE_DIR
        self.retention_days = retention_days or self.RETENTION_DAYS
        os.makedirs(self.cache_dir, exist_ok=True)

    @staticmethod
    def _data_hash(df: pd.DataFrame, feature_cols: List[str], horizon: int) -> str:
        """基于关键列最后 100 行 + horizon 的数据指纹"""
        values = []
        for col in ['close', 'volume'] + sorted(feature_cols):
            if col in df.columns:
                values.append(df[col].astype(float).values[-100:].tobytes())
        raw = b''.join(values) + str(horizon).encode()
        return hashlib.md5(raw).hexdigest()

    def _cache_path(self, ticker: str, data_hash: str, horizon: int) -> str:
        safe_ticker = str(ticker).replace('/', '_')
        return os.path.join(
            self.cache_dir,
            f"{safe_ticker}_{data_hash}_{horizon}.joblib"
        )

    def load(self, ticker: str, df: pd.DataFrame, feature_cols: List[str], horizon: int) -> Dict:
        data_hash = self._data_hash(df, feature_cols, horizon)
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

    def save(self, ticker: str, df: pd.DataFrame, feature_cols: List[str], horizon: int, payload: Dict):
        data_hash = self._data_hash(df, feature_cols, horizon)
        path = self._cache_path(ticker, data_hash, horizon)
        payload = dict(payload)
        payload['data_hash'] = data_hash
        payload['ticker'] = ticker
        payload['horizon'] = horizon
        payload['created_at'] = datetime.now().isoformat()
        joblib.dump(payload, path, compress=3)
        return path

    def cleanup(self, max_age_days: int = None):
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
        files = [os.path.join(self.cache_dir, f) for f in os.listdir(self.cache_dir)
                 if os.path.isfile(os.path.join(self.cache_dir, f))]
        if ticker is None:
            return files
        safe_ticker = str(ticker).replace('/', '_')
        return [f for f in files if os.path.basename(f).startswith(safe_ticker + '_')]


# 全局磁盘缓存实例
_IMPROVED_DISK_CACHE = ImprovedModelCache()


def _build_features(df: pd.DataFrame) -> pd.DataFrame:
    """构建特征集"""
    f = pd.DataFrame(index=df.index)
    close = df['close'].astype(float)

    # 价格动量
    for d in [1, 3, 5, 10, 20]:
        f[f'return_{d}d'] = close.pct_change(d)

    # 相对均线位置
    for ma in [5, 10, 20, 60]:
        col = f'ma{ma}'
        if col in df.columns:
            f[f'price_to_{col}'] = close / df[col].astype(float) - 1

    # 技术指标
    f['rsi_14'] = df['rsi_14'].astype(float) / 100.0
    f['macd_hist'] = df['macd_hist'].astype(float)
    f['kdj_j'] = df['kdj_j'].astype(float)
    f['boll_position'] = (close - df['boll_down'].astype(float)) / (
        df['boll_up'].astype(float) - df['boll_down'].astype(float) + 1e-9)
    f['vol_ratio'] = df['vol_ratio'].astype(float)
    f['atr_14'] = df['atr_14'].astype(float) / close
    f['annual_vol'] = df['annual_vol_20d'].astype(float) / 100.0

    # 成交量动量
    f['vol_ma5_ratio'] = df['volume'].astype(float) / df['vol_ma5'].astype(float).replace(0, np.nan)

    # 历史波动统计
    f['hist_vol_20'] = close.pct_change().rolling(20).std()
    f['skew_20'] = close.pct_change().rolling(20).skew()

    return f


def _train_and_predict(X: pd.DataFrame, y: pd.Series, last_X: pd.Series,
                       ticker: str = '', df: pd.DataFrame = None,
                       feature_cols: List[str] = None, horizon: int = None,
                       cache_to_disk: bool = True) -> dict:
    """训练轻量 GBDT 模型并预测, 带跨进程磁盘缓存"""
    from sklearn.ensemble import HistGradientBoostingClassifier

    feature_cols = feature_cols or list(X.columns)

    # 尝试磁盘缓存命中
    if ticker and df is not None and horizon is not None:
        cached = _IMPROVED_DISK_CACHE.load(ticker, df, feature_cols, horizon)
        if cached:
            model = cached['model']
            proba = model.predict_proba(last_X.values.reshape(1, -1))[0]
            classes = list(model.classes_)
            up_idx = classes.index(1) if 1 in classes else -1
            if up_idx < 0:
                return {'error': '模型未学到上涨类别'}
            return {'prob': float(proba[up_idx]), 'pred': 1 if proba[up_idx] > 0.5 else 0,
                    '_from_disk_cache': True}

    valid_idx = X.dropna().index.intersection(y.dropna().index)
    if len(valid_idx) < 50:
        return {'error': '训练样本不足'}

    X_tr = X.loc[valid_idx]
    y_tr = y.loc[valid_idx]

    try:
        model = HistGradientBoostingClassifier(
            max_iter=30, max_depth=3, learning_rate=0.2,
            early_stopping=False, random_state=42
        )
        model.fit(X_tr, y_tr)
        proba = model.predict_proba(last_X.values.reshape(1, -1))[0]
        classes = list(model.classes_)
        # classes 可能是 [0,1]
        up_idx = classes.index(1) if 1 in classes else -1
        if up_idx < 0:
            return {'error': '模型未学到上涨类别'}
        prob = float(proba[up_idx])
        pred = 1 if prob > 0.5 else 0

        # 保存到磁盘缓存
        if cache_to_disk and ticker and df is not None and horizon is not None:
            _IMPROVED_DISK_CACHE.save(
                ticker, df, feature_cols, horizon,
                {'model': model, 'feature_cols': feature_cols}
            )

        return {'prob': prob, 'pred': pred}
    except Exception as e:
        return {'error': f'模型训练失败: {e}'}


class ImprovedPredictor:
    """改进版预测器"""

    @staticmethod
    def predict(df: pd.DataFrame, days: int = 5, min_train: int = 100, ticker: str = '',
                cache_to_disk: bool = True) -> dict:
        """
        多周期方向预测 + 幅度校准
        """
        if df is None or len(df) < min_train + days + 20:
            return {'error': '数据不足'}

        features = _build_features(df)
        feature_cols = [c for c in features.columns if c not in ['return_1d']]

        current_price = float(df.iloc[-1]['close'])
        predictions = []

        # 计算历史波动率, 用于幅度校准
        hist_daily_vol = float(df['close'].pct_change().dropna().iloc[-60:].std())

        for day in [1, 3, 5, 10]:
            if day > days:
                continue

            # 目标: T+day 收盘价相对 T 收盘价涨跌
            y = (df['close'].shift(-day) / df['close'] - 1).apply(lambda x: 1 if x > 0 else 0)

            X = features[feature_cols].shift(1).dropna()  # 用 T 开盘前的特征预测 T+day
            y = y.loc[X.index]
            last_X = X.iloc[-1]

            res = _train_and_predict(X, y, last_X, ticker=ticker, df=df,
                                     feature_cols=feature_cols, horizon=day,
                                     cache_to_disk=cache_to_disk)
            if 'error' in res:
                continue

            prob = res['prob']
            pred = res['pred']

            # 震荡过滤: 如果 prob 在 0.4-0.6 之间且近期波动低, 输出 flat
            is_choppy = (0.4 < prob < 0.6) and (hist_daily_vol < 0.015)
            direction = 'flat' if is_choppy else ('up' if pred == 1 else 'down')

            # 幅度校准: 基于历史同方向 day 日平均收益 × prob 强度
            if direction == 'flat':
                pred_return = 0.0
            else:
                hist_returns = df['close'].pct_change(day).dropna().iloc[-252:]
                if direction == 'up':
                    avg_ret = hist_returns[hist_returns > 0].mean() if (hist_returns > 0).any() else hist_daily_vol
                else:
                    avg_ret = hist_returns[hist_returns < 0].mean() if (hist_returns < 0).any() else -hist_daily_vol
                if pd.isna(avg_ret):
                    avg_ret = hist_daily_vol if direction == 'up' else -hist_daily_vol

                # 用 prob 强度做收缩
                strength = abs(prob - 0.5) * 2  # 0~1
                pred_return = float(avg_ret) * strength

            pred_price = current_price * (1 + pred_return)
            predictions.append({
                'day': day,
                'pred_price': round(pred_price, 4),
                'pred_return': round(float(pred_return), 6),
                'pred_direction': direction,
                'prob': round(prob, 4),
            })

        if not predictions:
            return {'error': '无法生成预测'}

        avg_return = float(np.mean([p['pred_return'] for p in predictions]))
        return {
            'current_price': round(current_price, 4),
            'predictions': predictions,
            'avg_return': round(avg_return, 6),
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
        res = ImprovedPredictor.predict(df, days=10, ticker=ticker)
        print(f"\n=== {name}({ticker}) ===")
        print(res)
