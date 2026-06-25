#!/usr/bin/env python3
"""
多模型多算法交叉验证预测系统
融合: LightGBM, XGBoost, Random Forest, LSTM, ARIMA, Prophet, 传统技术指标
"""

import os
import sys
import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import warnings
warnings.filterwarnings('ignore')

# 添加 akshare 路径 (仅在非虚拟环境时)
if not hasattr(sys, 'real_prefix') and sys.base_prefix == sys.prefix:
    sys.path.insert(0, '/home/zhihu/.linuxbrew/Cellar/python@3.10/3.10.9/lib/python3.10/site-packages')

# 导入 LSTM 模型缓存
import lstm_model_cache as lstm_cache

# 尝试导入各种模型
try:
    import lightgbm as lgb
    LIGHTGBM_AVAILABLE = True
except ImportError:
    LIGHTGBM_AVAILABLE = False
    print("  ⚠️ LightGBM 未安装")

try:
    import xgboost as xgb
    XGBOOST_AVAILABLE = True
except ImportError:
    XGBOOST_AVAILABLE = False
    print("  ⚠️ XGBoost 未安装")

try:
    from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
    from sklearn.linear_model import Ridge, Lasso, ElasticNet
    from sklearn.preprocessing import StandardScaler, MinMaxScaler
    from sklearn.model_selection import TimeSeriesSplit, cross_val_score
    from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False
    print("  ⚠️ Scikit-learn 未安装")

try:
    import tensorflow as tf
    from tensorflow.keras.models import Sequential, Model
    from tensorflow.keras.layers import LSTM, Dense, Dropout, Bidirectional
    from tensorflow.keras.layers import Conv1D, MaxPooling1D, Flatten, Reshape, Multiply, Lambda, Input, Layer
    from tensorflow.keras.callbacks import EarlyStopping, ReduceLROnPlateau
    from tensorflow.keras.optimizers import Adam
    import tensorflow.keras.backend as K
    KERAS_AVAILABLE = True
    
    @tf.keras.utils.register_keras_serializable()
    class AttentionWeightedSum(Layer):
        def __init__(self, axis=1, **kwargs):
            self.axis = axis
            super().__init__(**kwargs)
        
        def call(self, inputs):
            return tf.reduce_sum(inputs, axis=self.axis)
        
        def compute_output_shape(self, input_shape):
            return input_shape[:self.axis] + input_shape[self.axis+1:]
        
        def get_config(self):
            config = super().get_config()
            config.update({'axis': self.axis})
            return config
    
except ImportError:
    KERAS_AVAILABLE = False
    AttentionWeightedSum = None

try:
    from statsmodels.tsa.arima.model import ARIMA
    from statsmodels.tsa.holtwinters import ExponentialSmoothing
    STATSMODELS_AVAILABLE = True
except ImportError:
    STATSMODELS_AVAILABLE = False
    print("  ⚠️ Statsmodels 未安装")

try:
    from prophet import Prophet
    PROPHET_AVAILABLE = True
except ImportError:
    PROPHET_AVAILABLE = False
    print("  ⚠️ Prophet 未安装")


class FeatureEngineer:
    """特征工程 - 构建丰富的预测特征"""
    
    @staticmethod
    def create_features(df: pd.DataFrame, lookback_days: int = 20) -> pd.DataFrame:
        """
        创建丰富的特征集
        参考微软AI-Edu量化交易案例的特征工程方法
        """
        df = df.copy()
        
        # 1. 基础价格特征
        df['returns'] = df['close'].pct_change()
        df['log_returns'] = np.log(df['close'] / df['close'].shift(1))
        
        # 2. 滞后特征 (Lag Features) - 微软案例核心方法
        for lag in [1, 2, 3, 5, 10, 15, 20]:
            df[f'close_lag_{lag}'] = df['close'].shift(lag)
            df[f'volume_lag_{lag}'] = df['volume'].shift(lag)
            df[f'returns_lag_{lag}'] = df['returns'].shift(lag)
        
        # 3. 滚动统计特征
        for window in [5, 10, 20, 60]:
            # 价格滚动统计
            df[f'close_ma_{window}'] = df['close'].rolling(window=window).mean()
            df[f'close_std_{window}'] = df['close'].rolling(window=window).std()
            df[f'close_max_{window}'] = df['close'].rolling(window=window).max()
            df[f'close_min_{window}'] = df['close'].rolling(window=window).min()
            
            # 成交量滚动统计
            df[f'volume_ma_{window}'] = df['volume'].rolling(window=window).mean()
            df[f'volume_std_{window}'] = df['volume'].rolling(window=window).std()
            
            # 收益率滚动统计
            df[f'returns_mean_{window}'] = df['returns'].rolling(window=window).mean()
            df[f'returns_std_{window}'] = df['returns'].rolling(window=window).std()
            
            # 波动率
            df[f'volatility_{window}'] = df['returns'].rolling(window=window).std() * np.sqrt(252)
        
        # 4. 技术指标特征
        # RSI
        for period in [6, 14, 21]:
            delta = df['close'].diff()
            gain = delta.where(delta > 0, 0).rolling(window=period).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
            rs = gain / loss
            df[f'rsi_{period}'] = 100 - (100 / (1 + rs))
        
        # MACD
        ema_fast = df['close'].ewm(span=12, adjust=False).mean()
        ema_slow = df['close'].ewm(span=26, adjust=False).mean()
        df['macd'] = ema_fast - ema_slow
        df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
        df['macd_hist'] = df['macd'] - df['macd_signal']
        
        # 布林带
        df['bb_middle'] = df['close'].rolling(window=20).mean()
        bb_std = df['close'].rolling(window=20).std()
        df['bb_upper'] = df['bb_middle'] + 2 * bb_std
        df['bb_lower'] = df['bb_middle'] - 2 * bb_std
        df['bb_position'] = (df['close'] - df['bb_lower']) / (df['bb_upper'] - df['bb_lower'])
        df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_middle']
        
        # KDJ
        low_list = df['low'].rolling(window=9, min_periods=9).min()
        high_list = df['high'].rolling(window=9, min_periods=9).max()
        rsv = (df['close'] - low_list) / (high_list - low_list) * 100
        df['kdj_k'] = rsv.ewm(com=2, adjust=False).mean()
        df['kdj_d'] = df['kdj_k'].ewm(com=2, adjust=False).mean()
        df['kdj_j'] = 3 * df['kdj_k'] - 2 * df['kdj_d']
        
        # ATR
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df['atr_14'] = tr.rolling(window=14).mean()
        df['atr_ratio'] = df['atr_14'] / df['close']
        
        # 5. 价格形态特征
        df['price_momentum'] = df['close'] - df['close'].shift(10)
        df['price_acceleration'] = df['price_momentum'] - df['price_momentum'].shift(5)
        
        # 6. 成交量特征
        df['volume_price_trend'] = df['volume'] * df['returns']
        df['volume_momentum'] = df['volume'] / df['volume'].shift(5) - 1
        
        # 7. 目标变量 - 未来N日收益率
        for future in [1, 3, 5]:
            df[f'target_{future}d'] = df['close'].shift(-future) / df['close'] - 1
        
        return df
    
    @staticmethod
    def prepare_ml_data(df: pd.DataFrame, target_col: str = 'target_1d', 
                       test_size: int = 20) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, List[str]]:
        """
        准备机器学习数据
        返回: X_train, X_test, y_train, y_test, feature_names
        """
        # 删除包含NaN的行
        df_clean = df.dropna()
        
        # 特征列（排除目标变量和原始数据列）
        exclude_cols = ['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 
                       'target_1d', 'target_3d', 'target_5d', 'returns', 'log_returns']
        feature_cols = [col for col in df_clean.columns if col not in exclude_cols]
        
        X = df_clean[feature_cols].values
        y = df_clean[target_col].values
        
        # 时间序列分割 - 避免数据泄露
        split_idx = len(X) - test_size
        X_train, X_test = X[:split_idx], X[split_idx:]
        y_train, y_test = y[:split_idx], y[split_idx:]
        
        return X_train, X_test, y_train, y_test, feature_cols


class CrossValidator:
    """交叉验证器 - 时间序列专用"""
    
    @staticmethod
    def time_series_cv(model, X: np.ndarray, y: np.ndarray, n_splits: int = 5) -> Dict:
        """
        时间序列交叉验证
        避免未来数据泄露
        """
        tscv = TimeSeriesSplit(n_splits=n_splits)
        scores = {
            'mse': [],
            'mae': [],
            'rmse': [],
            'mape': [],
            'r2': []
        }
        
        for train_idx, val_idx in tscv.split(X):
            X_train_cv, X_val_cv = X[train_idx], X[val_idx]
            y_train_cv, y_val_cv = y[train_idx], y[val_idx]
            
            model.fit(X_train_cv, y_train_cv)
            y_pred = model.predict(X_val_cv)
            
            scores['mse'].append(mean_squared_error(y_val_cv, y_pred))
            scores['mae'].append(mean_absolute_error(y_val_cv, y_pred))
            scores['rmse'].append(np.sqrt(mean_squared_error(y_val_cv, y_pred)))
            scores['r2'].append(r2_score(y_val_cv, y_pred))
            
            # MAPE
            mape = np.mean(np.abs((y_val_cv - y_pred) / (y_val_cv + 1e-8))) * 100
            scores['mape'].append(mape)
        
        # 计算平均值和标准差
        return {
            metric: {
                'mean': np.mean(values),
                'std': np.std(values),
                'values': values
            }
            for metric, values in scores.items()
        }


class MultiModelPredictor:
    """多模型预测器 - 集成多种算法"""
    
    def __init__(self, etf_code: Optional[str] = None):
        self.models = {}
        self.cv_results = {}
        self.feature_importance = {}
        self.scalers = {}
        self.etf_code = etf_code
        
    def train_lightgbm(self, X_train: np.ndarray, y_train: np.ndarray, 
                       X_test: np.ndarray, y_test: np.ndarray,
                       feature_names: List[str]) -> Dict:
        """训练 LightGBM 模型"""
        if not LIGHTGBM_AVAILABLE:
            return {'error': 'LightGBM not available'}
        
        print("  训练 LightGBM 模型...")
        
        # 创建数据集
        train_data = lgb.Dataset(X_train, label=y_train)
        valid_data = lgb.Dataset(X_test, label=y_test, reference=train_data)
        
        # 参数设置 - 参考微软AI-Edu案例
        params = {
            'objective': 'regression',
            'metric': 'rmse',
            'boosting_type': 'gbdt',
            'num_leaves': 31,
            'learning_rate': 0.05,
            'feature_fraction': 0.9,
            'bagging_fraction': 0.8,
            'bagging_freq': 5,
            'verbose': -1,
            'min_data_in_leaf': 20,
            'lambda_l2': 0.1
        }
        
        # 训练
        model = lgb.train(
            params,
            train_data,
            num_boost_round=1000,
            valid_sets=[train_data, valid_data],
            valid_names=['train', 'valid'],
            callbacks=[lgb.early_stopping(stopping_rounds=50), lgb.log_evaluation(period=0)]
        )
        
        self.models['lightgbm'] = model
        
        # 特征重要性
        importance = model.feature_importance(importance_type='gain')
        self.feature_importance['lightgbm'] = {
            name: imp for name, imp in zip(feature_names, importance)
        }
        
        # 预测
        y_pred_train = model.predict(X_train, num_iteration=model.best_iteration)
        y_pred_test = model.predict(X_test, num_iteration=model.best_iteration)
        
        return {
            'model': 'LightGBM',
            'train_rmse': np.sqrt(mean_squared_error(y_train, y_pred_train)),
            'test_rmse': np.sqrt(mean_squared_error(y_test, y_pred_test)),
            'test_mae': mean_absolute_error(y_test, y_pred_test),
            'test_r2': r2_score(y_test, y_pred_test),
            'best_iteration': model.best_iteration
        }
    
    def train_xgboost(self, X_train: np.ndarray, y_train: np.ndarray,
                     X_test: np.ndarray, y_test: np.ndarray,
                     feature_names: List[str]) -> Dict:
        """训练 XGBoost 模型"""
        if not XGBOOST_AVAILABLE:
            return {'error': 'XGBoost not available'}
        
        print("  训练 XGBoost 模型...")
        
        # 参数设置
        params = {
            'objective': 'reg:squarederror',
            'eval_metric': 'rmse',
            'max_depth': 6,
            'learning_rate': 0.05,
            'subsample': 0.8,
            'colsample_bytree': 0.8,
            'reg_lambda': 1.0,
            'reg_alpha': 0.1
        }
        
        dtrain = xgb.DMatrix(X_train, label=y_train, feature_names=feature_names)
        dtest = xgb.DMatrix(X_test, label=y_test, feature_names=feature_names)
        
        evallist = [(dtrain, 'train'), (dtest, 'eval')]
        
        model = xgb.train(
            params,
            dtrain,
            num_boost_round=1000,
            evals=evallist,
            early_stopping_rounds=50,
            verbose_eval=False
        )
        
        self.models['xgboost'] = model
        
        # 特征重要性
        importance = model.get_score(importance_type='gain')
        self.feature_importance['xgboost'] = importance
        
        # 预测
        y_pred_train = model.predict(dtrain)
        y_pred_test = model.predict(dtest)
        
        return {
            'model': 'XGBoost',
            'train_rmse': np.sqrt(mean_squared_error(y_train, y_pred_train)),
            'test_rmse': np.sqrt(mean_squared_error(y_test, y_pred_test)),
            'test_mae': mean_absolute_error(y_test, y_pred_test),
            'test_r2': r2_score(y_test, y_pred_test),
            'best_iteration': model.best_iteration
        }
    
    def train_random_forest(self, X_train: np.ndarray, y_train: np.ndarray,
                         X_test: np.ndarray, y_test: np.ndarray,
                         feature_names: List[str]) -> Dict:
        """训练随机森林模型"""
        if not SKLEARN_AVAILABLE:
            return {'error': 'Scikit-learn not available'}
        
        print("  训练 Random Forest 模型...")
        
        model = RandomForestRegressor(
            n_estimators=200,
            max_depth=10,
            min_samples_split=5,
            min_samples_leaf=2,
            max_features='sqrt',
            random_state=42,
            n_jobs=-1
        )
        
        model.fit(X_train, y_train)
        self.models['random_forest'] = model
        
        # 特征重要性
        self.feature_importance['random_forest'] = {
            name: imp for name, imp in zip(feature_names, model.feature_importances_)
        }
        
        # 预测
        y_pred_train = model.predict(X_train)
        y_pred_test = model.predict(X_test)
        
        return {
            'model': 'Random Forest',
            'train_rmse': np.sqrt(mean_squared_error(y_train, y_pred_train)),
            'test_rmse': np.sqrt(mean_squared_error(y_test, y_pred_test)),
            'test_mae': mean_absolute_error(y_test, y_pred_test),
            'test_r2': r2_score(y_test, y_pred_test)
        }
    
    def train_lstm(self, X_train: np.ndarray, y_train: np.ndarray,
                  X_test: np.ndarray, y_test: np.ndarray,
                  feature_names: List[str]) -> Dict:
        """训练增强版 LSTM 深度学习模型 (CNN+BiLSTM+Attention+多时间窗口融合)"""
        if not KERAS_AVAILABLE:
            return {'error': 'TensorFlow/Keras not available'}
        
        print("  训练 LSTM 模型 (CNN+BiLSTM+Attention)...")
        
        # 数据标准化
        scaler_X = StandardScaler()
        scaler_y = StandardScaler()
        
        X_train_scaled = scaler_X.fit_transform(X_train)
        X_test_scaled = scaler_X.transform(X_test)
        y_train_scaled = scaler_y.fit_transform(y_train.reshape(-1, 1)).flatten()
        y_test_scaled = scaler_y.transform(y_test.reshape(-1, 1)).flatten()
        
        self.scalers['lstm_X'] = scaler_X
        self.scalers['lstm_y'] = scaler_y
        
        # 多时间窗口融合
        timesteps_list = [5, 10, 20]
        models = {}
        histories = {}
        
        # 保存训练时的特征列，预测时保持维度一致
        feature_cols = list(range(X_train.shape[1]))
        
        def create_sequences(X, y, timesteps):
            X_seq, y_seq = [], []
            for i in range(len(X) - timesteps):
                X_seq.append(X[i:i+timesteps])
                y_seq.append(y[i+timesteps])
            return np.array(X_seq), np.array(y_seq)
        
        # 对每个时间窗口训练一个子模型
        for ts in timesteps_list:
            if len(X_train_scaled) <= ts + 10:
                continue
            
            X_train_seq, y_train_seq = create_sequences(X_train_scaled, y_train_scaled, ts)
            X_test_seq, y_test_seq = create_sequences(X_test_scaled, y_test_scaled, ts)
            
            if len(X_train_seq) < 30 or len(X_test_seq) < 3:
                continue
            
            # 构建 CNN + BiLSTM + Attention 模型
            model = self._build_cnn_bilstm_attention_model(ts, X_train.shape[1])
            
            early_stop = EarlyStopping(
                monitor='val_loss', patience=15, restore_best_weights=True, verbose=0
            )
            lr_reduce = ReduceLROnPlateau(
                monitor='val_loss', factor=0.5, patience=8, min_lr=1e-7, verbose=0
            )
            
            history = model.fit(
                X_train_seq, y_train_seq,
                validation_split=0.15,
                epochs=150,
                batch_size=16,
                callbacks=[early_stop, lr_reduce],
                verbose=0
            )
            
            models[f'lstm_ts{ts}'] = {
                'model': model,
                'timesteps': ts,
                'scaler_X': scaler_X,
                'scaler_y': scaler_y,
                'feature_cols': feature_cols
            }
            histories[f'lstm_ts{ts}'] = history
        
        if not models:
            return {'error': 'Not enough data for LSTM training'}
        
        self.models['lstm'] = models
        
        # 保存 LSTM 模型到本地缓存（收盘后训练时可用）
        if self.etf_code:
            lstm_cache.save_lstm_model(self.etf_code, models, scaler_X, scaler_y)
        
        # 评估：使用主时间窗口(10)的测试集
        main_ts = 10 if 'lstm_ts10' in models else list(models.keys())[0].replace('lstm_ts', '')
        main_ts = int(main_ts)
        X_test_seq_main, y_test_seq_main = create_sequences(X_test_scaled, y_test_scaled, main_ts)
        X_train_seq_main, y_train_seq_main = create_sequences(X_train_scaled, y_train_scaled, main_ts)
        
        # 多窗口集成预测 - 对整个序列进行预测
        y_pred_test = self._lstm_predict_sequence(models, X_test_seq_main, scaler_y)
        y_pred_train = self._lstm_predict_sequence(models, X_train_seq_main, scaler_y)
        
        # 反标准化实际值
        y_test_actual = scaler_y.inverse_transform(y_test_seq_main.reshape(-1, 1)).flatten()
        y_train_actual = scaler_y.inverse_transform(y_train_seq_main.reshape(-1, 1)).flatten()
        
        return {
            'model': 'LSTM (CNN+BiLSTM+Attention)',
            'train_rmse': np.sqrt(mean_squared_error(y_train_actual, y_pred_train)),
            'test_rmse': np.sqrt(mean_squared_error(y_test_actual, y_pred_test)),
            'test_mae': mean_absolute_error(y_test_actual, y_pred_test),
            'test_r2': r2_score(y_test_actual, y_pred_test),
            'epochs_trained': max([len(h.history['loss']) for h in histories.values()]) if histories else 0,
            'windows': list(models.keys())
        }
    
    def _lstm_predict_sequence(self, models: Dict, X_seq: np.ndarray, scaler_y) -> np.ndarray:
        """对完整序列进行多窗口 LSTM 集成预测"""
        all_predictions = []
        weights = []
        
        for key, model_info in models.items():
            ts = model_info['timesteps']
            model = model_info['model']
            
            # 如果序列长度不匹配，跳过
            if X_seq.shape[1] != ts:
                continue
            
            pred_scaled = model.predict(X_seq, verbose=0).flatten()
            pred = scaler_y.inverse_transform(pred_scaled.reshape(-1, 1)).flatten()
            all_predictions.append(pred)
            weights.append(np.sqrt(ts))
        
        if not all_predictions:
            return np.zeros(len(X_seq))
        
        weights = np.array(weights)
        weights = weights / weights.sum()
        
        # 加权平均
        ensemble = np.zeros(len(X_seq))
        for pred, w in zip(all_predictions, weights):
            ensemble += pred * w
        
        return ensemble
    
    def _build_cnn_bilstm_attention_model(self, timesteps: int, n_features: int):
        """构建 CNN + BiLSTM + Attention 模型"""
        inputs = Input(shape=(timesteps, n_features))
        
        # CNN 特征提取
        x = Conv1D(filters=64, kernel_size=3, padding='same', activation='relu')(inputs)
        x = MaxPooling1D(pool_size=2, padding='same')(x)
        x = Conv1D(filters=32, kernel_size=3, padding='same', activation='relu')(x)
        
        # BiLSTM 层
        x = Bidirectional(LSTM(64, return_sequences=True))(x)
        x = Dropout(0.25)(x)
        x = Bidirectional(LSTM(32, return_sequences=True))(x)
        x = Dropout(0.25)(x)
        
        # Attention 机制
        attention = Dense(1, activation='tanh', name='attention_dense')(x)
        attention = Flatten(name='attention_flatten')(attention)
        attention = tf.keras.layers.Activation('softmax', name='attention_softmax')(attention)
        
        # 计算 BiLSTM 输出到当前层的时间步数（考虑 CNN+MaxPool 后长度减半）
        from math import ceil
        ts_after_cnn = ceil(timesteps / 2)  # MaxPooling1D(pool_size=2) 后长度减半
        attention = Reshape((ts_after_cnn, 1), name='attention_reshape')(attention)
        weighted = Multiply(name='attention_weighted')([x, attention])
        context = AttentionWeightedSum(axis=1, name='attention_weighted_sum')(weighted)
        
        # 输出层
        x = Dense(32, activation='relu')(context)
        x = Dropout(0.2)(x)
        x = Dense(16, activation='relu')(x)
        outputs = Dense(1)(x)
        
        model = Model(inputs=inputs, outputs=outputs)
        model.compile(optimizer=Adam(learning_rate=0.001), loss='mse', metrics=['mae'])
        
        return model
    
    def _lstm_multi_window_predict(self, models: Dict, X_scaled: np.ndarray, 
                                   reference_ts: int) -> np.ndarray:
        """多时间窗口 LSTM 集成预测"""
        predictions = []
        weights = []
        
        for key, model_info in models.items():
            ts = model_info['timesteps']
            model = model_info['model']
            
            if len(X_scaled) <= ts:
                continue
            
            # 取最后 reference_ts 行，但只使用最后 ts 行作为序列输入
            X_seq = np.array([X_scaled[-ts:]])
            pred = model.predict(X_seq, verbose=0).flatten()[0]
            predictions.append(pred)
            # 时间窗口越长，权重越高
            weights.append(np.sqrt(ts))
        
        if not predictions:
            return np.array([0.0])
        
        weights = np.array(weights)
        weights = weights / weights.sum()
        predictions = np.array(predictions)
        
        return np.array([np.sum(predictions * weights)])
    
    def predict_lstm(self, df_features: pd.DataFrame) -> float:
        """使用训练好的 LSTM 模型预测最新一日收益"""
        if 'lstm' not in self.models:
            return 0.0
        
        models = self.models['lstm']
        scaler_X = list(models.values())[0]['scaler_X']
        scaler_y = list(models.values())[0]['scaler_y']
        feature_cols = list(models.values())[0].get('feature_cols', None)
        
        exclude_cols = ['date', 'open', 'high', 'low', 'close', 'volume', 'amount',
                       'target_1d', 'target_3d', 'target_5d', 'returns', 'log_returns']
        all_feature_cols = [col for col in df_features.columns if col not in exclude_cols]
        
        # 如果缓存记录了训练时的特征列，使用缓存的维度
        if feature_cols is not None and len(feature_cols) != len(all_feature_cols):
            if len(feature_cols) <= len(all_feature_cols):
                all_feature_cols = all_feature_cols[:len(feature_cols)]
        
        X_latest = df_features[all_feature_cols].values
        X_scaled = scaler_X.transform(X_latest)
        
        predictions = []
        weights = []
        
        for key, model_info in models.items():
            ts = model_info['timesteps']
            model = model_info['model']
            
            if len(X_scaled) < ts:
                continue
            
            X_seq = np.array([X_scaled[-ts:]])
            pred_scaled = model.predict(X_seq, verbose=0).flatten()[0]
            pred = scaler_y.inverse_transform([[pred_scaled]])[0][0]
            predictions.append(pred)
            weights.append(np.sqrt(ts))
        
        if not predictions:
            return 0.0
        
        weights = np.array(weights)
        weights = weights / weights.sum()
        predictions = np.array(predictions)
        
        return float(np.sum(predictions * weights))
    
    def train_arima(self, df: pd.DataFrame) -> Dict:
        """训练 ARIMA 模型"""
        if not STATSMODELS_AVAILABLE:
            return {'error': 'Statsmodels not available'}
        
        print("  训练 ARIMA 模型...")
        
        try:
            # 使用收盘价
            series = df['close'].dropna()
            
            # 自动选择参数 (简化版，实际可用auto_arima)
            model = ARIMA(series, order=(5, 1, 0))
            fitted = model.fit()
            
            self.models['arima'] = fitted
            
            # 预测
            predictions = fitted.predict(start=len(series)-20, end=len(series)-1)
            actual = series.tail(20).values
            
            return {
                'model': 'ARIMA',
                'test_rmse': np.sqrt(mean_squared_error(actual, predictions)),
                'test_mae': mean_absolute_error(actual, predictions),
                'aic': fitted.aic,
                'bic': fitted.bic
            }
        except Exception as e:
            return {'error': f'ARIMA training failed: {str(e)}'}
    
    def train_prophet(self, df: pd.DataFrame) -> Dict:
        """训练 Prophet 模型"""
        if not PROPHET_AVAILABLE:
            return {'error': 'Prophet not available'}
        
        print("  训练 Prophet 模型...")
        
        try:
            # 准备数据
            prophet_df = pd.DataFrame({
                'ds': pd.to_datetime(df['date']),
                'y': df['close'].values
            }).dropna()
            
            # 分割训练测试
            train_size = int(len(prophet_df) * 0.9)
            train_df = prophet_df.iloc[:train_size]
            test_df = prophet_df.iloc[train_size:]
            
            # 训练
            model = Prophet(
                yearly_seasonality=True,
                weekly_seasonality=True,
                daily_seasonality=False,
                changepoint_prior_scale=0.05
            )
            model.fit(train_df)
            
            self.models['prophet'] = model
            
            # 预测
            future = model.make_future_dataframe(periods=len(test_df))
            forecast = model.predict(future)
            
            predictions = forecast['yhat'].tail(len(test_df)).values
            actual = test_df['y'].values
            
            return {
                'model': 'Prophet',
                'test_rmse': np.sqrt(mean_squared_error(actual, predictions)),
                'test_mae': mean_absolute_error(actual, predictions)
            }
        except Exception as e:
            return {'error': f'Prophet training failed: {str(e)}'}
    
    def train_all_models(self, df: pd.DataFrame, target_col: str = 'target_1d', skip_lstm: bool = False) -> Dict:
        """训练所有可用模型"""
        print("\n=== 多模型训练开始 ===")
        
        # 特征工程
        print("\n1. 特征工程...")
        df_features = FeatureEngineer.create_features(df)
        
        # 准备数据
        X_train, X_test, y_train, y_test, feature_names = \
            FeatureEngineer.prepare_ml_data(df_features, target_col=target_col)
        
        print(f"   训练样本: {len(X_train)}, 测试样本: {len(X_test)}")
        print(f"   特征数量: {len(feature_names)}")
        
        results = {}
        
        # 1. LightGBM
        if LIGHTGBM_AVAILABLE:
            print("\n2. LightGBM")
            results['lightgbm'] = self.train_lightgbm(
                X_train, y_train, X_test, y_test, feature_names
            )
        
        # 2. XGBoost
        if XGBOOST_AVAILABLE:
            print("\n3. XGBoost")
            results['xgboost'] = self.train_xgboost(
                X_train, y_train, X_test, y_test, feature_names
            )
        
        # 3. Random Forest
        if SKLEARN_AVAILABLE:
            print("\n4. Random Forest")
            results['random_forest'] = self.train_random_forest(
                X_train, y_train, X_test, y_test, feature_names
            )
        
        # 4. LSTM
        if KERAS_AVAILABLE and not skip_lstm:
            print("\n5. LSTM")
            results['lstm'] = self.train_lstm(
                X_train, y_train, X_test, y_test, feature_names
            )
        elif skip_lstm:
            print("\n5. LSTM (跳过，将使用缓存模型)")
        
        # 5. ARIMA
        if STATSMODELS_AVAILABLE:
            print("\n6. ARIMA")
            results['arima'] = self.train_arima(df)
        
        # 6. Prophet
        if PROPHET_AVAILABLE:
            print("\n7. Prophet")
            results['prophet'] = self.train_prophet(df)
        
        # 在train_all_models中保存训练结果
        self.training_results = results
        
        print("\n=== 多模型训练完成 ===")
        return results
    
    def load_cached_lstm(self, df: pd.DataFrame) -> bool:
        """尝试加载缓存的 LSTM 模型，并准备其他模型（树模型+ARIMA）"""
        if not self.etf_code:
            return False
        
        if not lstm_cache.is_cache_valid(self.etf_code):
            return False
        
        cached_models = lstm_cache.load_lstm_model(self.etf_code)
        if not cached_models:
            return False
        
        self.models['lstm'] = cached_models
        print(f"  ✅ LSTM 使用隔夜缓存模型 ({self.etf_code})")
        return True
    
    def ensemble_predict(self, df: pd.DataFrame, days: int = 5) -> Dict:
        """
        集成预测 - 多模型加权融合
        """
        print("\n=== 集成预测 ===")
        
        # 特征工程
        df_features = FeatureEngineer.create_features(df)
        
        # 获取最新特征
        latest_features = df_features.iloc[-1:].copy()
        
        predictions = {}
        
        # 1. LightGBM 预测
        if 'lightgbm' in self.models:
            print("  LightGBM 预测...")
            # 准备特征
            exclude_cols = ['date', 'open', 'high', 'low', 'close', 'volume', 'amount',
                          'target_1d', 'target_3d', 'target_5d', 'returns', 'log_returns']
            feature_cols = [col for col in df_features.columns if col not in exclude_cols]
            
            X_latest = df_features[feature_cols].iloc[-1:].values
            pred = self.models['lightgbm'].predict(X_latest)[0]
            predictions['lightgbm'] = {
                'return_1d': pred,
                'price_1d': df['close'].iloc[-1] * (1 + pred)
            }
        
        # 2. XGBoost 预测
        if 'xgboost' in self.models:
            print("  XGBoost 预测...")
            import xgboost as xgb_lib
            dlatest = xgb_lib.DMatrix(X_latest, feature_names=feature_cols)
            pred = self.models['xgboost'].predict(dlatest)[0]
            predictions['xgboost'] = {
                'return_1d': pred,
                'price_1d': df['close'].iloc[-1] * (1 + pred)
            }
        
        # 3. Random Forest 预测
        if 'random_forest' in self.models:
            print("  Random Forest 预测...")
            pred = self.models['random_forest'].predict(X_latest)[0]
            predictions['random_forest'] = {
                'return_1d': pred,
                'price_1d': df['close'].iloc[-1] * (1 + pred)
            }
        
        # 4. LSTM 预测
        if 'lstm' in self.models:
            print("  LSTM 预测...")
            pred = self.predict_lstm(df_features)
            predictions['lstm'] = {
                'return_1d': pred,
                'price_1d': df['close'].iloc[-1] * (1 + pred)
            }
        
        # 5. ARIMA 预测
        if 'arima' in self.models:
            print("  ARIMA 预测...")
            forecast = self.models['arima'].forecast(steps=days)
            current_price = df['close'].iloc[-1]
            # Handle both pandas Series and numpy array
            if hasattr(forecast, 'values'):
                forecast_values = forecast.values
            else:
                forecast_values = np.array(forecast)
            predictions['arima'] = {
                'return_1d': (forecast_values[0] - current_price) / current_price,
                'price_1d': forecast_values[0]
            }
        
        # 5. Prophet 预测
        if 'prophet' in self.models:
            print("  Prophet 预测...")
            future = self.models['prophet'].make_future_dataframe(periods=days)
            forecast = self.models['prophet'].predict(future)
            pred_price = forecast['yhat'].iloc[-days:].values[0]
            current_price = df['close'].iloc[-1]
            predictions['prophet'] = {
                'return_1d': (pred_price - current_price) / current_price,
                'price_1d': pred_price
            }
        
        # 加权融合 - 根据历史表现动态调整权重
        training_results = getattr(self, 'training_results', None)
        weights = self._calculate_weights(training_results)
        
        ensemble_return = 0
        total_weight = 0
        
        for model_name, pred in predictions.items():
            if model_name in weights:
                weight = weights[model_name]
                ensemble_return += pred['return_1d'] * weight
                total_weight += weight
        
        if total_weight > 0:
            ensemble_return /= total_weight
        
        # 重新归一化权重（只针对实际参与预测的模型）
        active_weights = {k: v for k, v in weights.items() if k in predictions}
        if active_weights:
            total = sum(active_weights.values())
            active_weights = {k: v / total for k, v in active_weights.items()}
        
        current_price = df['close'].iloc[-1]
        
        return {
            'individual_predictions': predictions,
            'ensemble': {
                'return_1d': ensemble_return,
                'price_1d': current_price * (1 + ensemble_return),
                'confidence': self._calculate_confidence(predictions, active_weights),
                'weights_used': active_weights
            }
        }
    
    def _calculate_weights(self, training_results=None) -> Dict:
        """
        根据模型历史表现计算动态权重
        表现越好，权重越高
        """
        # 默认权重
        default_weights = {
            'lightgbm': 0.20,
            'xgboost': 0.20,
            'random_forest': 0.15,
            'lstm': 0.20,
            'arima': 0.10,
            'prophet': 0.15
        }
        
        # 优先使用训练结果(test_rmse)计算权重
        if training_results:
            weights = {}
            total_inv_rmse = 0
            
            for model_name, result in training_results.items():
                if 'error' in result:
                    continue
                if 'test_rmse' in result:
                    inv_rmse = 1 / (result['test_rmse'] + 1e-8)
                    weights[model_name] = inv_rmse
                    total_inv_rmse += inv_rmse
            
            if total_inv_rmse > 0:
                weights = {k: v / total_inv_rmse for k, v in weights.items()}
                # 平滑处理：避免某个模型权重过高
                weights = {k: 0.1 + 0.9 * v for k, v in weights.items()}
                total = sum(weights.values())
                weights = {k: v / total for k, v in weights.items()}
                return weights
        
        # 如果有交叉验证结果，根据RMSE调整权重
        if hasattr(self, 'cv_results') and self.cv_results:
            weights = {}
            total_inv_rmse = 0
            
            for model_name, cv_result in self.cv_results.items():
                if 'rmse' in cv_result:
                    inv_rmse = 1 / (cv_result['rmse']['mean'] + 1e-8)
                    weights[model_name] = inv_rmse
                    total_inv_rmse += inv_rmse
            
            if total_inv_rmse > 0:
                weights = {k: v / total_inv_rmse for k, v in weights.items()}
                return weights
        
        return default_weights
    
    def _calculate_confidence(self, predictions: Dict, weights: Dict) -> float:
        """
        计算集成预测置信度
        基于模型间一致性
        """
        returns = [pred['return_1d'] for pred in predictions.values()]
        
        if len(returns) < 2:
            return 0.5
        
        # 计算预测一致性
        mean_return = np.mean(returns)
        std_return = np.std(returns)
        
        # 变异系数越小，一致性越高
        cv = std_return / (abs(mean_return) + 1e-8)
        
        # 转换为置信度 (0-1)
        confidence = 1 / (1 + cv)
        confidence = min(confidence, 0.95)
        
        return confidence
    
    def get_feature_importance_summary(self, top_n: int = 20) -> pd.DataFrame:
        """获取特征重要性汇总"""
        importance_data = []
        
        for model_name, importance in self.feature_importance.items():
            for feature, score in importance.items():
                importance_data.append({
                    'model': model_name,
                    'feature': feature,
                    'importance': score
                })
        
        df = pd.DataFrame(importance_data)
        
        # 按特征汇总
        summary = df.groupby('feature')['importance'].mean().sort_values(ascending=False)
        
        return summary.head(top_n)


class AdvancedPredictionReport:
    """高级预测报告生成器"""
    
    @staticmethod
    def generate_report(predictor: MultiModelPredictor, df: pd.DataFrame, 
                         training_results: Dict, ensemble_result: Dict) -> str:
        """生成综合预测报告"""
        
        report = []
        report.append("=" * 60)
        report.append("多模型交叉验证预测报告")
        report.append("=" * 60)
        report.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append(f"标的: 516150 稀土ETF嘉实")
        report.append(f"当前价格: {df['close'].iloc[-1]:.3f}")
        report.append("")
        
        # 1. 模型训练结果
        report.append("-" * 60)
        report.append("【模型训练结果】")
        report.append("-" * 60)
        
        for model_name, result in training_results.items():
            if 'error' in result:
                report.append(f"{model_name}: {result['error']}")
            else:
                report.append(f"\n{result['model']}:")
                report.append(f"  测试RMSE: {result.get('test_rmse', 'N/A')}")
                report.append(f"  测试MAE: {result.get('test_mae', 'N/A')}")
                report.append(f"  测试R²: {result.get('test_r2', 'N/A')}")
        
        # 2. 集成预测结果
        report.append("\n" + "-" * 60)
        report.append("【集成预测结果】")
        report.append("-" * 60)
        
        ensemble = ensemble_result['ensemble']
        report.append(f"\n预测1日收益: {ensemble['return_1d']*100:+.2f}%")
        report.append(f"预测1日价格: {ensemble['price_1d']:.3f}")
        report.append(f"置信度: {ensemble['confidence']*100:.1f}%")
        report.append(f"\n使用权重:")
        for model, weight in ensemble['weights_used'].items():
            report.append(f"  {model}: {weight*100:.1f}%")
        
        # 3. 各模型预测对比
        report.append("\n" + "-" * 60)
        report.append("【各模型预测对比】")
        report.append("-" * 60)
        
        for model_name, pred in ensemble_result['individual_predictions'].items():
            report.append(f"{model_name:15s}: {pred['return_1d']*100:+.2f}% -> {pred['price_1d']:.3f}")
        
        # 4. 特征重要性
        report.append("\n" + "-" * 60)
        report.append("【Top 10 重要特征】")
        report.append("-" * 60)
        
        top_features = predictor.get_feature_importance_summary(10)
        for feature, importance in top_features.items():
            report.append(f"{feature:30s}: {importance:.4f}")
        
        # 5. 交易建议
        report.append("\n" + "-" * 60)
        report.append("【交易建议】")
        report.append("-" * 60)
        
        return_1d = ensemble['return_1d']
        confidence = ensemble['confidence']
        
        if return_1d > 0.02 and confidence > 0.7:
            advice = "强烈看涨 - 建议买入"
        elif return_1d > 0.01 and confidence > 0.5:
            advice = "看涨 - 建议轻仓买入"
        elif return_1d > -0.01 and confidence > 0.5:
            advice = "震荡偏强 - 建议观望"
        elif return_1d < -0.02 and confidence > 0.7:
            advice = "强烈看跌 - 建议卖出"
        elif return_1d < -0.01 and confidence > 0.5:
            advice = "看跌 - 建议减仓"
        else:
            advice = "方向不明 - 建议观望"
        
        report.append(f"\n{advice}")
        report.append(f"\n风险提示:")
        report.append(f"  - 预测置信度较低({confidence*100:.1f}%)，建议谨慎操作")
        report.append(f"  - 模型基于历史数据，无法预测黑天鹅事件")
        report.append(f"  - 建议结合基本面分析和技术分析综合判断")
        
        report.append("\n" + "=" * 60)
        
        return "\n".join(report)


if __name__ == "__main__":
    # 测试多模型预测
    print("多模型交叉验证预测系统")
    print("=" * 60)
    
    # 检查可用模型
    print("\n可用模型:")
    print(f"  LightGBM: {LIGHTGBM_AVAILABLE}")
    print(f"  XGBoost: {XGBOOST_AVAILABLE}")
    print(f"  Scikit-learn: {SKLEARN_AVAILABLE}")
    print(f"  TensorFlow/Keras: {KERAS_AVAILABLE}")
    print(f"  Statsmodels: {STATSMODELS_AVAILABLE}")
    print(f"  Prophet: {PROPHET_AVAILABLE}")
    
    # 获取数据
    import akshare as ak
    print("\n获取 516150 数据...")
    df = ak.fund_etf_hist_em(symbol='516150', period='daily', 
                             start_date='20240101', adjust='qfq')
    df.columns = ['date', 'open', 'close', 'high', 'low', 'volume', 'amount', 
                  'amplitude', 'pct_change', 'change', 'turnover']
    
    print(f"数据量: {len(df)} 条")
    
    # 训练模型
    predictor = MultiModelPredictor()
    training_results = predictor.train_all_models(df, target_col='target_1d')
    
    # 集成预测
    ensemble_result = predictor.ensemble_predict(df, days=5)
    
    # 生成报告
    report = AdvancedPredictionReport.generate_report(
        predictor, df, training_results, ensemble_result
    )
    
    print("\n" + report)
    
    # 保存报告
    report_path = f'/home/zhihu/etf_tracker/reports/multi_model_prediction_{datetime.now().strftime("%Y%m%d")}.txt'
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f"\n报告已保存: {report_path}")
