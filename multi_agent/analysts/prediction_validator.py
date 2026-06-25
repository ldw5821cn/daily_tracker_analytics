"""
预测验证器 - 验证 TrendPredictor / ImprovedPredictor 的预测准确性

功能:
1. 滚动回测: 在历史数据上每天收盘后做 1/3/5/10 日预测, 用未来真实价格验证
2. 保存预测: 每日收盘后将当日预测写入 SQLite, 方便后续验证
3. 周末批量回测: 每周六验证过去一周的所有预测
4. 偏差分析: 按标的、周期、模型聚合准确率与误差, 输出改进建议

用法:
    from analysts.prediction_validator import PredictionValidator
    pv = PredictionValidator()
    results = pv.rolling_backtest('516150', '稀土ETF', horizons=[1,3,5,10], test_days=120)
    pv.generate_report(results, title='516150 滚动回测报告')
"""
import os
import sys
import json
import sqlite3
import warnings
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.data_layer import get_stock_data, calc_technical_indicators
from core.prediction_data import PredictionDataStore, DataValidationError
from analysts.predictor import TrendPredictor
from analysts.improved_predictor import ImprovedPredictor
from analysts.ml_predictor import MLDirectionPredictor


DEFAULT_DB_PATH = os.path.expanduser(
    '~/github/daily_tracker_analytics/multi_agent/data/prediction_data.db'
)


class PredictionValidator:
    """预测验证器"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.store = PredictionDataStore(db_path=self.db_path)
        self._init_db_legacy()

    def _init_db_legacy(self):
        """保留旧表兼容"""
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS forecasts_old (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    name TEXT,
                    forecast_date TEXT NOT NULL,
                    horizon INTEGER NOT NULL,
                    model TEXT NOT NULL,
                    current_price REAL,
                    pred_price REAL,
                    pred_return REAL,
                    pred_direction TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_forecasts_old_ticker_date
                    ON forecasts_old(ticker, forecast_date);
            ''')

    # ---------- 数据获取 ----------

    @staticmethod
    def _get_data(ticker: str, end_date: Optional[str] = None) -> pd.DataFrame:
        """获取并计算技术指标, 同时保存到数据仓库"""
        df, _ = get_stock_data(ticker, calibrate=False)
        df = calc_technical_indicators(df)
        # 保存到数据仓库
        try:
            store = PredictionDataStore()
            store.save_market_data(ticker, df, source='sina')
            store.save_features(ticker, df)
        except DataValidationError as e:
            print(f"  ⚠️ 数据校验警告: {e}")
        return df

    def _get_data_cached(self, ticker: str, end_date: Optional[str] = None,
                         df: pd.DataFrame = None) -> pd.DataFrame:
        """优先从数据库读取, 不足再从接口拉取; 或直接使用传入的 df"""
        if df is not None and not df.empty:
            return df
        df = self.store.load_market_data(ticker, end_date=end_date)
        if df is not None and len(df) >= 200:
            # 合并特征 (默认转回下划线列名)
            feats = self.store.load_features(ticker, end_date=end_date, name_map=True)
            if not feats.empty:
                df = df.join(feats, how='left')
            return df
        return self._get_data(ticker, end_date=end_date)

    # ---------- 预测生成 ----------

    def generate_trend_forecast(self, ticker: str, name: str = "",
                                forecast_date: Optional[str] = None,
                                horizons: List[int] = None,
                                model: str = 'trend_predictor',
                                df: pd.DataFrame = None) -> Dict:
        """
        生成指定日期的多周期预测, 并保存到数据库
        """
        if horizons is None:
            horizons = [1, 3, 5, 10]
        df = self._get_data_cached(ticker, df=df)
        if forecast_date:
            fd = pd.Timestamp(forecast_date)
            df = df[df.index <= fd]
        if len(df) < 60:
            return {'error': '数据不足'}

        current_price = float(df.iloc[-1]['close'])
        pred_date = df.index[-1].strftime('%Y-%m-%d')

        max_horizon = max(horizons)
        if model == 'improved_predictor':
            pred = ImprovedPredictor.predict(df, days=max_horizon, ticker=ticker)
        elif model == 'ml_predictor':
            pred = MLDirectionPredictor.predict(df, days=max_horizon, ticker=ticker)
        else:
            pred = TrendPredictor.predict(df, days=max_horizon)
        if 'error' in pred:
            return pred

        records = []
        for p in pred.get('predictions', []):
            day = p['day']
            if day not in horizons:
                continue
            pred_return = p.get('pred_return', p.get('pred_return_pct', 0))
            if abs(pred_return) > 0.5:
                pred_return = pred_return / 100
            pred_direction = p.get('pred_direction', p.get('direction', ''))
            if pred_direction in ('涨',):
                pred_direction = 'up'
            elif pred_direction in ('跌',):
                pred_direction = 'down'
            elif pred_direction in ('平', '震荡'):
                pred_direction = 'flat'
            records.append({
                'ticker': ticker,
                'name': name or ticker,
                'forecast_date': pred_date,
                'horizon': day,
                'model': model,
                'current_price': round(current_price, 4),
                'pred_price': round(float(p['pred_price']), 4),
                'pred_return': round(float(pred_return), 6),
                'pred_direction': pred_direction,
            })

        self.store.save_forecasts(records)
        return {
            'ticker': ticker,
            'name': name,
            'forecast_date': pred_date,
            'current_price': current_price,
            'predictions': records,
        }

    def _save_forecasts(self, records: List[Dict]):
        """保留兼容: 调用新数据仓库"""
        self.store.save_forecasts(records)

    @staticmethod
    def _normalize_prediction(pred: Dict, current_price: float) -> Dict:
        """统一 TrendPredictor / ImprovedPredictor 输出格式"""
        out = {}
        for p in pred.get('predictions', []):
            day = int(p['day'])
            pred_return = float(p.get('pred_return', 0))
            # TrendPredictor 返回百分比, ImprovedPredictor 返回小数
            if abs(pred_return) >= 0.5 and not p.get('pred_direction'):
                pred_return = pred_return / 100

            pred_direction = p.get('pred_direction', p.get('direction', ''))
            if pred_direction in ('涨',):
                pred_direction = 'up'
            elif pred_direction in ('跌',):
                pred_direction = 'down'
            elif pred_direction in ('平', '震荡'):
                pred_direction = 'flat'
            elif pred_return > 0.003:
                pred_direction = 'up'
            elif pred_return < -0.003:
                pred_direction = 'down'
            else:
                pred_direction = 'flat'

            out[day] = {
                'day': day,
                'pred_price': float(p['pred_price']),
                'pred_return': pred_return,
                'pred_direction': pred_direction,
            }
        return out

    # ---------- 滚动回测 ----------

    def rolling_backtest(self, ticker: str, name: str = "",
                         horizons: List[int] = None,
                         min_history: int = 100,
                         test_days: int = 120,
                         model: str = 'trend_predictor',
                         df: pd.DataFrame = None) -> List[Dict]:
        """
        在历史数据上做滚动回测, 同时保存回测数据到仓库
        """
        if horizons is None:
            horizons = [1, 3, 5, 10]
        df = self._get_data_cached(ticker, df=df)
        if len(df) < min_history + max(horizons) + test_days:
            print(f"  ⚠️ {ticker} 数据量不足, 将使用全部可用数据")

        results = []
        end_idx = len(df) - max(horizons)
        start_idx = max(min_history, end_idx - test_days)

        run_id = f"bt_{ticker}_{model}_{test_days}_{datetime.now().strftime('%Y%m%d%H%M%S')}"

        for i in range(start_idx, end_idx):
            hist = df.iloc[:i]
            if len(hist) < min_history:
                continue
            forecast_date = hist.index[-1]
            current_price = float(hist.iloc[-1]['close'])

            max_horizon = max(horizons)
            try:
                if model == 'improved_predictor':
                    pred = ImprovedPredictor.predict(hist, days=max_horizon, ticker=ticker, cache_to_disk=False)
                elif model == 'ml_predictor':
                    pred = MLDirectionPredictor.predict(hist, days=max_horizon, ticker=ticker, cache_to_disk=False)
                else:
                    pred = TrendPredictor.predict(hist, days=max_horizon)
            except Exception as e:
                continue
            if 'error' in pred:
                continue

            norm = self._normalize_prediction(pred, current_price)
            for day in horizons:
                p = norm.get(day)
                if p is None:
                    continue
                actual_idx = i + day - 1
                if actual_idx >= len(df):
                    continue
                actual_price = float(df.iloc[actual_idx]['close'])
                actual_return = actual_price / current_price - 1
                actual_direction = 'up' if actual_return > 0 else 'down' if actual_return < 0 else 'flat'
                pred_return = p['pred_return']
                pred_direction = p['pred_direction']

                if pred_direction == 'flat':
                    direction_correct = 0
                else:
                    direction_correct = int(pred_direction == actual_direction)

                results.append({
                    'ticker': ticker,
                    'name': name,
                    'forecast_date': forecast_date.strftime('%Y-%m-%d'),
                    'horizon': day,
                    'model': model,
                    'current_price': round(current_price, 4),
                    'pred_price': round(p['pred_price'], 4),
                    'pred_return': round(pred_return, 6),
                    'pred_direction': pred_direction,
                    'actual_date': df.index[actual_idx].strftime('%Y-%m-%d'),
                    'actual_price': round(actual_price, 4),
                    'actual_return': round(actual_return, 6),
                    'actual_direction': actual_direction,
                    'direction_correct': direction_correct,
                    'return_error': round(abs(pred_return - actual_return), 6),
                })

        # 保存回测结果
        for day in horizons:
            day_records = [r for r in results if r['horizon'] == day]
            if day_records:
                self.store.save_backtest_records(run_id, ticker, model, day,
                                                 test_days, day_records)
        return results

    # ---------- 评估 ----------

    @staticmethod
    def evaluate(results: List[Dict]) -> Dict:
        """评估回测结果, 按 horizon 聚合"""
        if not results:
            return {'error': '无回测结果'}

        df = pd.DataFrame(results)
        summary = {}
        for horizon in sorted(df['horizon'].unique()):
            sub = df[df['horizon'] == horizon]
            non_flat_pred = sub[sub['pred_direction'] != 'flat']
            direction_acc = non_flat_pred['direction_correct'].mean() * 100 if len(non_flat_pred) > 0 else 0

            summary[horizon] = {
                'samples': len(sub),
                'direction_accuracy': round(direction_acc, 2),
                'up_precision': round(
                    (sub[(sub['pred_direction'] == 'up') & (sub['actual_direction'] == 'up')].shape[0] /
                     max(sub[sub['pred_direction'] == 'up'].shape[0], 1)) * 100, 2),
                'down_precision': round(
                    (sub[(sub['pred_direction'] == 'down') & (sub['actual_direction'] == 'down')].shape[0] /
                     max(sub[sub['pred_direction'] == 'down'].shape[0], 1)) * 100, 2),
                'mae_return': round(sub['return_error'].mean() * 100, 4),
                'rmse_return': round(np.sqrt((sub['return_error'] ** 2).mean()) * 100, 4),
                'mean_actual_return': round(sub['actual_return'].mean() * 100, 4),
                'mean_pred_return': round(sub['pred_return'].mean() * 100, 4),
                'pred_up_ratio': round((sub['pred_direction'] == 'up').mean() * 100, 2),
                'actual_up_ratio': round((sub['actual_direction'] == 'up').mean() * 100, 2),
                'flat_ratio': round((sub['pred_direction'] == 'flat').mean() * 100, 2),
            }
        return summary

    @staticmethod
    def diagnose(summary: Dict) -> List[str]:
        """根据评估结果给出诊断与改进建议"""
        suggestions = []
        for horizon, s in summary.items():
            acc = s['direction_accuracy']
            mae = s['mae_return']
            if acc < 50:
                suggestions.append(
                    f"{horizon}日方向准确率 {acc}% 低于随机水平, 建议: "
                    "(1) 加入更多特征(成交量、资金流向、行业指数); "
                    "(2) 改用分类模型(LightGBM/XGBoost)替代回归拟合; "
                    "(3) 过滤低波动震荡行情, 只在趋势明确时预测。"
                )
            elif acc < 55:
                suggestions.append(
                    f"{horizon}日方向准确率 {acc}% 勉强, 建议: "
                    "增加滚动窗口特征或引入外部市场情绪指标。"
                )
            if mae > 3:
                suggestions.append(
                    f"{horizon}日收益率 MAE {mae}% 过大, 预测幅度失真, 建议: "
                    "对预测收益率做收缩校准(shrinkage)或改用分位数回归。"
                )
            if abs(s['pred_up_ratio'] - s['actual_up_ratio']) > 15:
                suggestions.append(
                    f"{horizon}日预测上涨比例({s['pred_up_ratio']}%)与实际({s['actual_up_ratio']}%)偏差过大, "
                    "模型存在方向偏见, 建议加入类别平衡或调整阈值。"
                )
        if not suggestions:
            suggestions.append("整体表现可接受, 建议持续跟踪并增加样本量。")
        return suggestions

    # ---------- 报告生成 ----------

    def generate_report(self, results: List[Dict], title: str = "预测回测报告") -> str:
        """生成 Markdown 报告"""
        if not results:
            return f"# {title}\n\n无回测结果。"

        summary = self.evaluate(results)
        if 'error' in summary:
            return f"# {title}\n\n{summary['error']}"

        lines = [f"# {title}", ""]
        lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append(f"**标的数**: {len(set(r['ticker'] for r in results))}")
        lines.append(f"**总样本数**: {len(results)}")
        lines.append("")

        lines.append("## 各周期预测准确率汇总")
        lines.append("")
        lines.append("| 周期 | 样本数 | 方向准确率 | 涨精确率 | 跌精确率 | 观望比例 | 收益率MAE | 收益率RMSE | 实际平均涨跌 | 预测平均涨跌 |")
        lines.append("|------|--------|-----------|---------|---------|---------|----------|-----------|-------------|-------------|")
        for horizon in sorted(summary.keys()):
            s = summary[horizon]
            lines.append(
                f"| {horizon}日 | {s['samples']} | {s['direction_accuracy']}% | "
                f"{s['up_precision']}% | {s['down_precision']}% | {s['flat_ratio']}% | "
                f"{s['mae_return']}% | {s['rmse_return']}% | {s['mean_actual_return']:+.4f}% | {s['mean_pred_return']:+.4f}% |"
            )
        lines.append("")

        lines.append("## 按标的细分")
        lines.append("")
        df = pd.DataFrame(results)
        for ticker in sorted(df['ticker'].unique()):
            sub_df = df[df['ticker'] == ticker]
            name = sub_df.iloc[0]['name']
            lines.append(f"### {name} ({ticker})")
            lines.append("")
            lines.append("| 周期 | 样本 | 方向准确率 | MAE | 平均实际涨跌 |")
            lines.append("|------|------|-----------|-----|-------------|")
            for horizon in sorted(sub_df['horizon'].unique()):
                hdf = sub_df[sub_df['horizon'] == horizon]
                non_flat_pred = hdf[hdf['pred_direction'] != 'flat']
                acc = round(non_flat_pred['direction_correct'].mean() * 100, 2) if len(non_flat_pred) > 0 else 0
                mae = round(hdf['return_error'].mean() * 100, 4)
                avg_ret = round(hdf['actual_return'].mean() * 100, 4)
                lines.append(f"| {horizon}日 | {len(hdf)} | {acc}% | {mae}% | {avg_ret:+.4f}% |")
            lines.append("")

        lines.append("## 诊断与改进建议")
        lines.append("")
        for sug in self.diagnose(summary):
            lines.append(f"- {sug}")
        lines.append("")

        lines.append("## 最近 10 条预测明细")
        lines.append("")
        lines.append("| 标的 | 预测日 | 周期 | 预测方向 | 实际方向 | 预测涨跌 | 实际涨跌 | 误差 |")
        lines.append("|------|--------|------|---------|---------|---------|---------|------|")
        for r in results[-10:]:
            lines.append(
                f"| {r['ticker']} | {r['forecast_date']} | {r['horizon']}日 | "
                f"{r['pred_direction']} | {r['actual_direction']} | "
                f"{r['pred_return']*100:+.2f}% | {r['actual_return']*100:+.2f}% | {r['return_error']*100:.2f}% |"
            )
        lines.append("")

        return "\n".join(lines)

    # ---------- 保存预测 & 验证已保存预测 ----------

    def save_daily_forecasts(self, tickers: List[Tuple[str, str]],
                             forecast_date: Optional[str] = None,
                             horizons: List[int] = [1, 3, 5, 10],
                             model: str = 'trend_predictor'):
        """为多个标的生成并保存当日预测"""
        saved = []
        for ticker, name in tickers:
            try:
                res = self.generate_trend_forecast(ticker, name, forecast_date, horizons, model=model)
                if 'error' not in res:
                    saved.append((ticker, name, res['forecast_date']))
            except Exception as e:
                print(f"  ❌ {ticker} 预测保存失败: {e}")
        return saved

    def verify_saved_forecasts(self, verify_date: Optional[str] = None,
                               horizons: List[int] = [1, 3, 5, 10]) -> List[Dict]:
        """
        验证数据库中所有尚未验证的预测。
        新数据库结构简化: forecasts 中没有 id, 直接用最近实际价格验证。
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if verify_date:
                rows = conn.execute('''
                    SELECT f.* FROM forecasts f
                    WHERE f.horizon IN ({seq})
                      AND date(?, '-' || f.horizon || ' days') <= f.forecast_date
                '''.format(seq=','.join('?' * len(horizons))), (verify_date, *horizons)).fetchall()
            else:
                rows = conn.execute('''
                    SELECT f.* FROM forecasts f
                    WHERE f.horizon IN ({seq})
                '''.format(seq=','.join('?' * len(horizons))), (*horizons,)).fetchall()

            verified = []
            for row in rows:
                f = dict(row)
                actual_date = (datetime.strptime(f['forecast_date'], '%Y-%m-%d') +
                               timedelta(days=f['horizon'])).strftime('%Y-%m-%d')

                # 从数据仓库读实际价格
                actual_row = conn.execute(
                    'SELECT close FROM raw_market_data WHERE ticker=? AND trade_date=?',
                    (f['ticker'], actual_date)
                ).fetchone()
                if not actual_row:
                    try:
                        df = self._get_data(f['ticker'])
                        actual_row = conn.execute(
                            'SELECT close FROM raw_market_data WHERE ticker=? AND trade_date=?',
                            (f['ticker'], actual_date)
                        ).fetchone()
                    except Exception:
                        actual_row = None
                if not actual_row:
                    continue

                actual_price = float(actual_row['close'])
                current_price = f['current_price']
                actual_return = actual_price / current_price - 1 if current_price else 0
                actual_direction = 'up' if actual_return > 0 else 'down' if actual_return < 0 else 'flat'
                pred_direction = f['pred_direction']
                direction_correct = int(pred_direction == actual_direction) if pred_direction != 'flat' else 0
                return_error = abs(f['pred_return'] - actual_return)

                verified.append({
                    'forecast_id': f.get('id'),
                    'ticker': f['ticker'],
                    'forecast_date': f['forecast_date'],
                    'horizon': f['horizon'],
                    'pred_direction': pred_direction,
                    'actual_direction': actual_direction,
                    'pred_return': f['pred_return'],
                    'actual_return': actual_return,
                    'direction_correct': direction_correct,
                    'return_error': return_error,
                })
            conn.commit()
        return verified

    # ---------- 周末批量回测 ----------

    def weekend_batch_report(self, tickers: Optional[List[Tuple[str, str]]] = None,
                             week_end_date: Optional[str] = None,
                             horizons: List[int] = [1, 3, 5, 10],
                             test_days: int = 120,
                             output_dir: Optional[str] = None,
                             model: str = 'trend_predictor') -> str:
        """
        周末批量回测报告:
        1. 对关注标的做滚动回测
        2. 验证本周已保存的预测
        3. 生成 Markdown 报告并保存
        """
        if tickers is None:
            from core.watchlist import get_stocks_as_tuples
            tickers = get_stocks_as_tuples()

        if output_dir is None:
            output_dir = os.path.expanduser(
                '~/github/daily_tracker_analytics/reports/prediction_validation'
            )
        os.makedirs(output_dir, exist_ok=True)

        end_date = (pd.Timestamp(week_end_date) if week_end_date else pd.Timestamp.now()).strftime('%Y-%m-%d')
        title = f"预测验证周报 ({end_date}) - {model}"

        all_results = []
        print(f"\n📊 {title}")
        print(f"{'='*60}")
        for ticker, name in tickers:
            print(f"  ▶ {name}({ticker}) 滚动回测中...")
            try:
                res = self.rolling_backtest(ticker, name, horizons=horizons,
                                            test_days=test_days, model=model)
                all_results.extend(res)
                print(f"     生成 {len(res)} 条回测记录")
            except Exception as e:
                print(f"     ❌ 失败: {e}")

        print("\n  🔍 验证本周已保存预测...")
        verified = self.verify_saved_forecasts(verify_date=end_date, horizons=horizons)
        print(f"     验证 {len(verified)} 条")

        report = self.generate_report(all_results, title=title)

        if verified:
            report += "\n\n## 本周已保存预测验证\n\n"
            report += "| 标的 | 预测日 | 周期 | 预测方向 | 实际方向 | 预测涨跌 | 实际涨跌 | 是否正确 |\n"
            report += "|------|--------|------|---------|---------|---------|---------|----------|\n"
            for v in verified:
                report += (
                    f"| {v['ticker']} | {v['forecast_date']} | {v['horizon']}日 | "
                    f"{v['pred_direction']} | {v['actual_direction']} | "
                    f"{v['pred_return']*100:+.2f}% | {v['actual_return']*100:+.2f}% | "
                    f"{'✅' if v['direction_correct'] else '❌'} |\n"
                )

        report_path = os.path.join(output_dir, f"weekly_validation_{end_date}_{model}.md")
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report)
        print(f"\n✅ 报告已保存: {report_path}")
        return report_path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='预测验证器')
    parser.add_argument('--ticker', '-t', default='516150', help='股票代码')
    parser.add_argument('--name', '-n', default='', help='股票名称')
    parser.add_argument('--weekend', '-w', action='store_true', help='生成周末批量报告')
    parser.add_argument('--save-forecast', '-s', action='store_true', help='保存当日预测')
    parser.add_argument('--verify', '-v', action='store_true', help='验证已保存预测')
    parser.add_argument('--horizons', type=int, nargs='+', default=[1, 3, 5, 10], help='预测周期')
    parser.add_argument('--test-days', type=int, default=120, help='滚动回测天数')
    parser.add_argument('--date', '-d', default=None, help='指定日期(YYYY-MM-DD)')
    parser.add_argument('--model', '-m', default='trend_predictor',
                        choices=['trend_predictor', 'improved_predictor', 'ml_predictor'],
                        help='预测模型')
    args = parser.parse_args()

    pv = PredictionValidator()

    if args.weekend:
        pv.weekend_batch_report(week_end_date=args.date, horizons=args.horizons,
                                test_days=args.test_days, model=args.model)
    elif args.save_forecast:
        res = pv.generate_trend_forecast(args.ticker, args.name, args.date, args.horizons, model=args.model)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.verify:
        verified = pv.verify_saved_forecasts(verify_date=args.date, horizons=args.horizons)
        print(f"验证 {len(verified)} 条预测")
        for v in verified[:10]:
            print(v)
    else:
        results = pv.rolling_backtest(args.ticker, args.name,
                                      horizons=args.horizons, test_days=args.test_days,
                                      model=args.model)
        summary = {str(k): v for k, v in pv.evaluate(results).items()}
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        report = pv.generate_report(results, title=f"{args.ticker} {args.model} 预测回测报告")
        print(report)
