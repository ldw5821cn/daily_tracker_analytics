"""
自适应预测器 - 根据历史回测表现自动选择最优模型

策略:
- 维护各模型在各 horizon 上的滚动回测得分
- 每天收盘后重新评估, 选择方向准确率最高的模型
- 输出统一格式的预测结果
"""
import os
import sys
import json
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analysts.prediction_validator import PredictionValidator
from analysts.predictor import TrendPredictor
from analysts.improved_predictor import ImprovedPredictor
from analysts.ml_predictor import MLDirectionPredictor
from analysts.llm_predictor import LLMPredictor


MODEL_MAP = {
    'trend_predictor': TrendPredictor,
    'improved_predictor': ImprovedPredictor,
    'ml_predictor': MLDirectionPredictor,
    'llm_predictor': LLMPredictor,
}

DEFAULT_HORIZONS = [1, 3, 5, 10]
DEFAULT_TEST_DAYS = 30


class AdaptivePredictor:
    """自适应模型选择器"""

    def __init__(self, db_path: Optional[str] = None):
        self.validator = PredictionValidator(db_path=db_path)

    def select_best_models(self, ticker: str, name: str = "",
                           horizons: List[int] = None,
                           test_days: int = DEFAULT_TEST_DAYS,
                           candidates: List[str] = None,
                           df: pd.DataFrame = None) -> Dict[int, str]:
        """为指定标的每个 horizon 选择历史回测最优模型 (统一回测)"""
        if horizons is None:
            horizons = DEFAULT_HORIZONS
        if candidates is None:
            candidates = ['trend_predictor', 'improved_predictor', 'ml_predictor']

        scores = {h: {} for h in horizons}
        for model in candidates:
            try:
                results = self.validator.rolling_backtest(
                    ticker, name, horizons=horizons,
                    test_days=test_days, model=model,
                    df=df
                )
                for h in horizons:
                    sub = [r for r in results if r['horizon'] == h]
                    if not sub:
                        scores[h][model] = -999
                        continue
                    s = self.validator.evaluate(sub)
                    sh = s.get(h, {})
                    acc = sh.get('direction_accuracy', 0)
                    mae = sh.get('mae_return', 999)
                    scores[h][model] = acc - mae * 2
            except Exception as e:
                print(f"  ⚠️ {model} 回测失败: {e}")
                for h in horizons:
                    scores[h][model] = -999

        best = {}
        for h in horizons:
            if scores[h]:
                best[h] = max(scores[h], key=scores[h].get)
            else:
                best[h] = 'ml_predictor'
        return best

    def predict(self, ticker: str, name: str = "",
                df: pd.DataFrame = None,
                horizons: List[int] = None,
                test_days: int = DEFAULT_TEST_DAYS,
                candidates: List[str] = None) -> Dict:
        """生成自适应预测"""
        if horizons is None:
            horizons = DEFAULT_HORIZONS

        if df is None:
            df = self.validator._get_data_cached(ticker)

        best_models = self.select_best_models(
            ticker, name, horizons=horizons,
            test_days=test_days, candidates=candidates,
            df=df
        )

        current_price = float(df.iloc[-1]['close'])
        predictions = []
        model_votes = []

        for h in horizons:
            model_name = best_models.get(h, 'ml_predictor')
            model_cls = MODEL_MAP.get(model_name, MLDirectionPredictor)
            try:
                if model_name == 'llm_predictor':
                    pred = model_cls.predict(df, days=h, ticker=ticker, name=name)
                else:
                    pred = model_cls.predict(df, days=h, ticker=ticker)

                if 'error' in pred:
                    raise ValueError(pred['error'])

                p = next((x for x in pred['predictions'] if x['day'] == h), None)
                if p is None:
                    raise ValueError(f"模型 {model_name} 未返回 {h} 日预测")
                predictions.append({
                    'day': h,
                    'pred_price': p['pred_price'],
                    'pred_return': p['pred_return'],
                    'pred_direction': p['pred_direction'],
                    'model': model_name,
                    'prob': p.get('prob', 0.5),
                })
                model_votes.append(model_name)
            except Exception as e:
                # 降级到 ml_predictor
                fallback = MLDirectionPredictor.predict(df, days=h, ticker=ticker)
                p = next((x for x in fallback['predictions'] if x['day'] == h), None)
                if p:
                    predictions.append({
                        'day': h,
                        'pred_price': p['pred_price'],
                        'pred_return': p['pred_return'],
                        'pred_direction': p['pred_direction'],
                        'model': 'ml_predictor(fallback)',
                        'prob': p.get('prob', 0.5),
                    })
                    model_votes.append('ml_predictor')

        avg_return = sum(p['pred_return'] for p in predictions) / len(predictions) if predictions else 0
        trend = '看涨' if avg_return > 0.005 else '看跌' if avg_return < -0.005 else '震荡'
        zones = calc_trading_zones(df, current_price, trend)
        return {
            'ticker': ticker,
            'name': name,
            'current_price': round(current_price, 4),
            'forecast_date': df.index[-1].strftime('%Y-%m-%d'),
            'predictions': predictions,
            'avg_return': round(avg_return, 6),
            'trend': trend,
            'best_models': best_models,
            'model_votes': {m: model_votes.count(m) for m in set(model_votes)},
            'trading_zones': zones,
        }


def calc_trading_zones(df: pd.DataFrame, current_price: float, trend: str) -> Dict:
    """
    根据近期波动率(ATR)与趋势给出开仓/买入/卖出区间。

    看多时:
      - 买入区间: 回踩吸纳区 [price - 1.5*ATR, price - 0.2*ATR]
      - 开仓区间: 突破进场区 [price - 0.2*ATR, price + 0.5*ATR]
      - 卖出区间: 止盈目标区 [price + 1.5*ATR, price + 2.5*ATR]
    看空/震荡时:
      - 买入区间: 更低观望区 [price - 2.0*ATR, price - 0.5*ATR]
      - 开仓区间: 当前观望区 [price - 0.5*ATR, price + 0.5*ATR]
      - 卖出区间: 反弹减仓/止损区 [price + 0.5*ATR, price + 1.5*ATR]
    """
    if len(df) < 20:
        return {'open': '-', 'buy': '-', 'sell': '-', 'atr': 0}

    high = df['high'].iloc[-20:]
    low = df['low'].iloc[-20:]
    close = df['close'].iloc[-20:]
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = float(tr.mean())

    is_bull = trend == '看涨'
    def fmt(a, b):
        a, b = round(min(a, b), 3), round(max(a, b), 3)
        return f"{a} - {b}"

    if is_bull:
        buy_zone = fmt(current_price - 1.5 * atr, current_price - 0.2 * atr)
        open_zone = fmt(current_price - 0.2 * atr, current_price + 0.5 * atr)
        sell_zone = fmt(current_price + 1.5 * atr, current_price + 2.5 * atr)
    else:
        buy_zone = fmt(current_price - 2.0 * atr, current_price - 0.5 * atr)
        open_zone = fmt(current_price - 0.5 * atr, current_price + 0.5 * atr)
        sell_zone = fmt(current_price + 0.5 * atr, current_price + 1.5 * atr)

    return {
        'open': open_zone,
        'buy': buy_zone,
        'sell': sell_zone,
        'atr': round(atr, 4),
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='自适应预测')
    parser.add_argument('--ticker', '-t', default='516150', help='股票代码')
    parser.add_argument('--name', '-n', default='稀土ETF', help='股票名称')
    parser.add_argument('--test-days', type=int, default=30, help='模型选择回测天数')
    args = parser.parse_args()

    ap = AdaptivePredictor()
    pred = ap.predict(args.ticker, args.name, test_days=args.test_days)
    print(json.dumps(pred, ensure_ascii=False, indent=2))
