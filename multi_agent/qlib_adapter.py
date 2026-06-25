"""
Qlib 风格最小适配器 (Qlib-style adapter)
- 不依赖 pyqlib 安装, 仅借鉴其 DataHandler -> Dataset -> Model -> Workflow 架构
- 复用现有 data_layer 的数据管道
- 用 LightGBM 回归预测未来 N 日收益率, 并与现有 MLDirectionPredictor 对比
"""
import os
import sys
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import json
import warnings
warnings.filterwarnings('ignore')

try:
    import lightgbm as lgb
except ImportError:
    raise ImportError('请先安装 lightgbm: uv pip install lightgbm')

# 复用现有数据层
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.data_layer import get_stock_data, calc_technical_indicators
from analysts.ml_predictor import MLDirectionPredictor


class QlibDataHandler:
    """Qlib 风格的 DataHandler: 加载原始数据并计算特征"""

    def __init__(self, ticker: str, start_date: Optional[str] = None, end_date: Optional[str] = None):
        self.ticker = ticker
        self.start_date = start_date
        self.end_date = end_date
        self.df = self._load_data()

    def _load_data(self) -> pd.DataFrame:
        df, _ = get_stock_data(self.ticker, calibrate=False)
        df = calc_technical_indicators(df)
        df = df.reset_index().rename(columns={'index': 'date'})
        df['date'] = pd.to_datetime(df['date'])
        if self.start_date:
            df = df[df['date'] >= pd.to_datetime(self.start_date)]
        if self.end_date:
            df = df[df['date'] <= pd.to_datetime(self.end_date)]
        df = df.sort_values('date').reset_index(drop=True)
        return df

    def get_feature_cols(self) -> List[str]:
        """返回可用于模型训练的特征列"""
        feature_cols = []
        # 价格动量
        for w in [1, 3, 5, 10, 20]:
            col = f'ret_{w}d'
            self.df[col] = self.df['close'].pct_change(w)
            feature_cols.append(col)
        # 均线位置
        for ma in ['ma5', 'ma10', 'ma20', 'ma60']:
            if ma in self.df.columns:
                col = f'{ma}_ratio'
                self.df[col] = self.df['close'] / self.df[ma] - 1
                feature_cols.append(col)
        # 技术指标
        for col in ['rsi6', 'rsi14', 'rsi24', 'macd', 'macd_signal', 'macd_hist']:
            if col in self.df.columns:
                feature_cols.append(col)
        # 成交量特征
        if 'volume' in self.df.columns:
            self.df['vol_ratio_5'] = self.df['volume'] / self.df['volume'].rolling(5).mean()
            self.df['vol_ratio_20'] = self.df['volume'] / self.df['volume'].rolling(20).mean()
            feature_cols.extend(['vol_ratio_5', 'vol_ratio_20'])
        # 波动率
        self.df['volatility_20'] = self.df['close'].pct_change().rolling(20).std() * np.sqrt(252)
        self.df['volatility_60'] = self.df['close'].pct_change().rolling(60).std() * np.sqrt(252)
        feature_cols.extend(['volatility_20', 'volatility_60'])
        return feature_cols


class QlibDataset:
    """Qlib 风格的 Dataset: 生成 (X, y) 训练/测试集"""

    def __init__(self, handler: QlibDataHandler, feature_cols: List[str], horizon: int = 5, train_ratio: float = 0.8):
        self.handler = handler
        self.feature_cols = feature_cols
        self.horizon = horizon
        self.train_ratio = train_ratio
        self.df = handler.df.copy()
        self._build_label()
        self._drop_na()

    def _build_label(self):
        self.df['label'] = self.df['close'].shift(-self.horizon) / self.df['close'] - 1

    def _drop_na(self):
        cols = self.feature_cols + ['label']
        self.df = self.df.dropna(subset=cols).reset_index(drop=True)

    def split(self) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        n = len(self.df)
        train_end = int(n * self.train_ratio)
        train_df = self.df.iloc[:train_end]
        test_df = self.df.iloc[train_end:]
        X_train = train_df[self.feature_cols].values
        y_train = train_df['label'].values
        X_test = test_df[self.feature_cols].values
        y_test = test_df['label'].values
        return X_train, y_train, X_test, y_test, test_df


class QlibModel:
    """Qlib 风格的 Model: LightGBM 回归"""

    def __init__(self, params: Optional[Dict] = None):
        self.params = params or {
            'objective': 'regression',
            'metric': 'rmse',
            'boosting_type': 'gbdt',
            'num_leaves': 31,
            'learning_rate': 0.05,
            'feature_fraction': 0.9,
            'bagging_fraction': 0.8,
            'bagging_freq': 5,
            'verbose': -1,
            'seed': 42,
        }
        self.model = None

    def fit(self, X_train: np.ndarray, y_train: np.ndarray):
        train_data = lgb.Dataset(X_train, label=y_train)
        self.model = lgb.train(self.params, train_data, num_boost_round=100)
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        if self.model is None:
            raise ValueError('模型未训练')
        return self.model.predict(X)


class QlibWorkflow:
    """Qlib 风格的 Workflow: 端到端流程"""

    def __init__(self, ticker: str, horizon: int = 5, start_date: Optional[str] = None, end_date: Optional[str] = None):
        self.ticker = ticker
        self.horizon = horizon
        self.handler = QlibDataHandler(ticker, start_date, end_date)
        self.feature_cols = self.handler.get_feature_cols()
        self.model = QlibModel()
        self.metrics = {}

    def run(self) -> Dict:
        # 每个 horizon 单独训练一个模型, 避免标签混叠
        dataset = QlibDataset(self.handler, self.feature_cols, self.horizon)
        X_train, y_train, X_test, y_test, test_df = dataset.split()
        if len(X_train) < 50 or len(X_test) < 10:
            return {'error': '训练/测试样本不足'}
        self.model.fit(X_train, y_train)
        y_pred = self.model.predict(X_test)
        self.metrics = self._evaluate(y_test, y_pred)
        latest = self.handler.df.iloc[-1]
        latest_features = latest[self.feature_cols].values.reshape(1, -1)
        latest_pred = self.model.predict(latest_features)[0]
        return {
            'ticker': self.ticker,
            'horizon': self.horizon,
            'feature_count': len(self.feature_cols),
            'train_samples': len(X_train),
            'test_samples': len(X_test),
            'metrics': self.metrics,
            'latest_prediction': self._build_prediction(latest, latest_pred, y_test),
            'test_df': test_df[['date', 'close']].copy(),
            'test_actual': y_test.tolist(),
            'test_pred': y_pred.tolist(),
        }

    def _build_prediction(self, latest: pd.Series, pred_return: float, y_test: np.ndarray) -> Dict:
        """生成直观的走势预判"""
        current_price = float(latest['close'])
        predicted_price = current_price * (1 + pred_return)
        # 基于历史预测误差构建置信区间
        std_err = float(np.std(y_test - np.mean(y_test)))
        lower = current_price * (1 + pred_return - 1.5 * std_err)
        upper = current_price * (1 + pred_return + 1.5 * std_err)
        # 支撑位/压力位: 近期最低/最高
        recent = self.handler.df.tail(20)
        support = float(recent['low'].min())
        resistance = float(recent['high'].max())
        signal = '看多' if pred_return > 0.01 else '看空' if pred_return < -0.01 else '震荡'
        return {
            'date': str(latest['date']),
            'current_price': round(current_price, 4),
            'horizon_days': self.horizon,
            'predicted_return': round(float(pred_return), 6),
            'predicted_price': round(predicted_price, 4),
            'target_price': round(predicted_price, 4),
            'support_price': round(support, 4),
            'resistance_price': round(resistance, 4),
            'confidence_lower': round(lower, 4),
            'confidence_upper': round(upper, 4),
            'signal': signal,
            'strength': '强' if abs(pred_return) > 0.03 else '中' if abs(pred_return) > 0.01 else '弱',
        }

    @staticmethod
    def _evaluate(y_true: np.ndarray, y_pred: np.ndarray) -> Dict:
        mse = np.mean((y_true - y_pred) ** 2)
        rmse = np.sqrt(mse)
        mae = np.mean(np.abs(y_true - y_pred))
        ic = np.corrcoef(y_true, y_pred)[0, 1] if len(y_true) > 1 else 0.0
        direction_acc = np.mean((y_true > 0) == (y_pred > 0))
        return {
            'mse': round(float(mse), 6),
            'rmse': round(float(rmse), 6),
            'mae': round(float(mae), 6),
            'ic': round(float(ic), 4),
            'direction_acc': round(float(direction_acc), 4),
        }

    def predict_multi_horizon(self, horizons: List[int] = None) -> pd.DataFrame:
        """预测多个未来周期的走势"""
        if horizons is None:
            horizons = [1, 3, 5, 10, 20, 30]
        latest = self.handler.df.iloc[-1]
        current_price = float(latest['close'])
        rows = []
        for h in horizons:
            dataset = QlibDataset(self.handler, self.feature_cols, h)
            X_train, y_train, X_test, y_test, _ = dataset.split()
            if len(X_train) < 50 or len(X_test) < 10:
                continue
            model = QlibModel()
            model.fit(X_train, y_train)
            feat = latest[self.feature_cols].values.reshape(1, -1)
            pred_ret = model.predict(feat)[0]
            pred_price = current_price * (1 + pred_ret)
            std_err = float(np.std(y_test - np.mean(y_test)))
            signal = '看多' if pred_ret > 0.01 else '看空' if pred_ret < -0.01 else '震荡'
            rows.append({
                '周期(日)': h,
                '预测收益': f"{pred_ret:.2%}",
                '预测价格': round(pred_price, 4),
                '置信下限': round(current_price * (1 + pred_ret - 1.5 * std_err), 4),
                '置信上限': round(current_price * (1 + pred_ret + 1.5 * std_err), 4),
                '信号': signal,
            })
        return pd.DataFrame(rows)


def compare_with_existing(ticker: str, horizon: int = 5):
    """对比 Qlib 风格 LightGBM 与现有 MLDirectionPredictor"""
    print(f'\n=== 标的: {ticker} ===')
    wf = QlibWorkflow(ticker, horizon=horizon)
    result = wf.run()
    if 'error' in result:
        print(f'[Qlib] {result["error"]}')
        return

    pred = result['latest_prediction']
    print(f'\n📈 最新走势预判（未来 {horizon} 日）')
    print(f"  当前价: {pred['current_price']}")
    print(f"  信号: {pred['signal']}({pred['strength']})")
    print(f"  目标价: {pred['target_price']}")
    print(f"  预测收益: {pred['predicted_return']:.2%}")
    print(f"  支撑位: {pred['support_price']}")
    print(f"  压力位: {pred['resistance_price']}")
    print(f"  置信区间: [{pred['confidence_lower']}, {pred['confidence_upper']}]")

    print(f"\n📊 多周期走势预判")
    multi = wf.predict_multi_horizon([1, 3, 5, 10, 20, 30])
    print(multi.to_string(index=False))

    print(f"\n[模型测试指标]")
    print(f"  {json.dumps(result['metrics'], ensure_ascii=False)}")

    # 现有 MLDirectionPredictor
    df = wf.handler.df
    existing = MLDirectionPredictor.predict(df, days=[1, 3, 5, 10, 20, 30], ticker=ticker, cache_to_disk=False)
    print('\n[现有 MLDirectionPredictor 对比]')
    if 'error' in existing:
        print(f"  {existing['error']}")
    else:
        for p in existing.get('predictions', []):
            print(f"  day={p['day']:2d}: 方向={p['pred_direction']:6s}, 概率={p['prob']:.2f}, 预测收益={p['pred_return']:.2%}")


if __name__ == '__main__':
    for ticker in ['516150', '515880', '601991']:
        compare_with_existing(ticker, horizon=5)
