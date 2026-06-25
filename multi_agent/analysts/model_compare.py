"""
多模型对比评估: trend_predictor / improved_predictor / ml_predictor / llm_predictor

输出:
- 各模型在不同 horizon 上的方向准确率、MAE、涨跌精确率
- Markdown 对比报告
- 推荐最优模型
"""
import os
import sys
import json
import argparse
from datetime import datetime
from typing import List, Tuple, Dict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analysts.prediction_validator import PredictionValidator


DEFAULT_TICKERS: List[Tuple[str, str]] = [
    ('516150', '稀土ETF'),
    ('515880', '通信ETF'),
]

MODELS = ['trend_predictor', 'improved_predictor', 'ml_predictor']


def compare_models(tickers: List[Tuple[str, str]] = None,
                   horizons: List[int] = [1, 3, 5, 10],
                   test_days: int = 60,
                   models: List[str] = None) -> Tuple[Dict, str]:
    """对比多个模型在多个标的上的表现"""
    if tickers is None:
        tickers = DEFAULT_TICKERS
    if models is None:
        models = MODELS

    pv = PredictionValidator()
    all_results = {m: [] for m in models}
    per_ticker = {}

    print(f"\n📊 模型对比: {models}")
    print(f"   标的: {[n for _, n in tickers]}")
    print(f"   周期: {horizons}")
    print(f"   回测天数: {test_days}")
    print("=" * 70)

    for ticker, name in tickers:
        per_ticker[ticker] = {}
        for model in models:
            print(f"  ▶ {name}({ticker}) - {model} ...", end=' ', flush=True)
            try:
                results = pv.rolling_backtest(
                    ticker, name,
                    horizons=horizons,
                    test_days=test_days,
                    model=model
                )
                all_results[model].extend(results)
                per_ticker[ticker][model] = pv.evaluate(results)
                print(f"样本 {len(results)}")
            except Exception as e:
                print(f"失败: {e}")
                per_ticker[ticker][model] = {'error': str(e)}

    # 聚合
    summary = {}
    for model in models:
        summary[model] = pv.evaluate(all_results[model])

    report = generate_report(tickers, models, horizons, summary, per_ticker, test_days)
    return summary, report


def generate_report(tickers, models, horizons, summary, per_ticker, test_days) -> str:
    lines = ["# 多模型预测对比报告", ""]
    lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**回测天数**: {test_days} | **周期**: {horizons}")
    lines.append(f"**标的**: {', '.join([f'{n}({t})' for t, n in tickers])}")
    lines.append("")

    lines.append("## 总体对比")
    lines.append("")
    lines.append("| 模型 | 周期 | 方向准确率 | 涨精确率 | 跌精确率 | 观望比例 | MAE | RMSE |")
    lines.append("|------|------|-----------|---------|---------|---------|-----|------|")
    for model in models:
        for h in horizons:
            s = summary.get(model, {}).get(h, {})
            if 'error' in s:
                lines.append(f"| {model} | {h}日 | - | - | - | - | - | - |")
                continue
            lines.append(
                f"| {model} | {h}日 | {s.get('direction_accuracy', 0)}% | "
                f"{s.get('up_precision', 0)}% | {s.get('down_precision', 0)}% | "
                f"{s.get('flat_ratio', 0)}% | {s.get('mae_return', 0)}% | {s.get('rmse_return', 0)}% |"
            )
    lines.append("")

    lines.append("## 按标的细分")
    lines.append("")
    for ticker, name in tickers:
        lines.append(f"### {name} ({ticker})")
        lines.append("")
        lines.append("| 模型 | 周期 | 方向准确率 | MAE | 实际平均涨跌 |")
        lines.append("|------|------|-----------|-----|-------------|")
        for model in models:
            ev = per_ticker.get(ticker, {}).get(model, {})
            for h in horizons:
                s = ev.get(h, {}) if isinstance(ev, dict) and 'error' not in ev else {}
                if 'error' in s or not s:
                    lines.append(f"| {model} | {h}日 | - | - | - |")
                    continue
                lines.append(
                    f"| {model} | {h}日 | {s.get('direction_accuracy', 0)}% | "
                    f"{s.get('mae_return', 0)}% | {s.get('mean_actual_return', 0):+.4f}% |"
                )
        lines.append("")

    # 推荐最优模型
    best = pick_best(summary, models, horizons)
    lines.append("## 模型推荐")
    lines.append("")
    for h, m in best.items():
        acc = summary.get(m, {}).get(h, {}).get('direction_accuracy', 0)
        lines.append(f"- **{h}日预测**: {m} (方向准确率 {acc}%)")
    lines.append("")
    lines.append("建议: 用推荐模型生成每日预测, 持续跟踪并在周末重新评估。")
    lines.append("")

    return "\n".join(lines)


def pick_best(summary: Dict, models: List[str], horizons: List[int]) -> Dict:
    """按方向准确率选每个周期最优模型"""
    best = {}
    for h in horizons:
        candidates = []
        for m in models:
            s = summary.get(m, {}).get(h, {})
            if 'error' in s:
                continue
            acc = s.get('direction_accuracy', 0)
            if acc > 0:
                candidates.append((acc, m))
        best[h] = max(candidates, key=lambda x: x[0])[1] if candidates else models[0]
    return best


def save_report(report: str, output_dir: str = None) -> str:
    if output_dir is None:
        output_dir = os.path.expanduser(
            '~/github/daily_tracker_analytics/reports/model_compare'
        )
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"model_compare_{datetime.now().strftime('%Y%m%d_%H%M')}.md")
    with open(path, 'w', encoding='utf-8') as f:
        f.write(report)
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='多模型预测对比')
    parser.add_argument('--tickers', '-t', nargs='+', default=['516150', '515880'],
                        help='股票代码列表')
    parser.add_argument('--names', '-n', nargs='+', default=['稀土ETF', '通信ETF'],
                        help='股票名称列表')
    parser.add_argument('--horizons', type=int, nargs='+', default=[1, 3, 5, 10],
                        help='预测周期')
    parser.add_argument('--test-days', type=int, default=60, help='回测天数')
    parser.add_argument('--models', '-m', nargs='+', default=MODELS,
                        help=f'模型列表, 可选 {MODELS}')
    parser.add_argument('--output', '-o', default=None, help='报告保存目录')
    args = parser.parse_args()

    tickers = list(zip(args.tickers, args.names))
    summary, report = compare_models(
        tickers=tickers,
        horizons=args.horizons,
        test_days=args.test_days,
        models=args.models
    )
    print(report)
    path = save_report(report, args.output)
    print(f"\n✅ 报告已保存: {path}")
    print(f"\n最优模型选择:")
    best = pick_best(summary, args.models, args.horizons)
    for h, m in best.items():
        print(f"  {h}日: {m}")
