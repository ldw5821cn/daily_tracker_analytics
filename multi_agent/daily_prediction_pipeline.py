#!/usr/bin/env python3
"""
每日预测优化流水线

运行内容:
1. 对 watchlist 中每个标的做自适应预测 (选最优模型)
2. 保存预测到数据库
3. 验证昨日已到期的预测
4. 运行模型对比, 输出报告
5. 生成汇总 Markdown 报告

用法:
    cd /home/liudawei/github/daily_tracker_analytics/multi_agent
    /home/liudawei/github/daily_tracker_analytics/etf_tracker/.venv/bin/python daily_prediction_pipeline.py
"""
import os
# 限制 sklearn/openmp 线程, 避免多进程/并发时 CPU 打满
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('MKL_NUM_THREADS', '1')
os.environ.setdefault('OMP_NUM_THREADS', '1')
os.environ.setdefault('VECLIB_MAXIMUM_THREADS', '1')
os.environ.setdefault('NUMEXPR_NUM_THREADS', '1')

import sys
import json
import argparse
import tempfile
from datetime import datetime, timedelta
from typing import List, Tuple, Dict, Optional
import pandas as pd
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analysts.prediction_validator import PredictionValidator
from analysts.adaptive_predictor import AdaptivePredictor
from analysts.model_compare import compare_models, save_report
from core.prediction_data import PredictionDataStore
from core.data_layer import get_stock_data, calc_technical_indicators


def get_watchlist() -> List[Tuple[str, str]]:
    """获取关注标的"""
    try:
        from core.watchlist import get_stocks_as_tuples
        return get_stocks_as_tuples()
    except Exception:
        return [
            ('516150', '稀土ETF'),
            ('515880', '通信ETF'),
            ('159611', '电力ETF'),
            ('512480', '半导体ETF'),
            ('588200', '科创芯片ETF'),
        ]


def _prefetch_ticker_data(ticker_name: Tuple[str, str]) -> Tuple[str, str, Optional[str]]:
    """预取单个标的完整数据, 保存到临时 parquet, 返回路径"""
    ticker, name = ticker_name
    try:
        df, _ = get_stock_data(ticker, calibrate=False)
        if df is None or df.empty:
            return ticker, name, None
        df = calc_technical_indicators(df)
        # 保存到仓库
        ds = PredictionDataStore()
        ds.save_market_data(ticker, df, source='sina')
        ds.save_features(ticker, df)
        # 保存到临时文件供子进程读取
        tmp = tempfile.NamedTemporaryFile(suffix='.parquet', delete=False)
        tmp.close()
        df.to_parquet(tmp.name, index=True)
        return ticker, name, tmp.name
    except Exception as e:
        print(f"  ❌ {name}({ticker}) 数据预取失败: {e}")
        return ticker, name, None


def _predict_one(args: Tuple[str, str, str, List[int], int]) -> Dict:
    """单个标的自适应预测 (子进程: 从 parquet 读数据)"""
    ticker, name, parquet_path, horizons, test_days = args
    try:
        df = pd.read_parquet(parquet_path)
        ap = AdaptivePredictor()
        pred = ap.predict(ticker, name, df=df, horizons=horizons, test_days=test_days)
        records = []
        for p in pred.get('predictions', []):
            records.append({
                'ticker': ticker,
                'name': name,
                'forecast_date': pred['forecast_date'],
                'horizon': p['day'],
                'model': 'adaptive_' + p['model'],
                'current_price': pred['current_price'],
                'pred_price': p['pred_price'],
                'pred_return': p['pred_return'],
                'pred_direction': p['pred_direction'],
            })
        return {
            'success': True,
            'ticker': ticker,
            'name': name,
            'records': records,
            'best_models': pred.get('best_models', {}),
            'predictions': pred.get('predictions', []),
        }
    except Exception as e:
        return {'success': False, 'ticker': ticker, 'name': name, 'error': str(e)}
    finally:
        try:
            os.unlink(parquet_path)
        except Exception:
            pass


def run_daily_pipeline(horizons: List[int] = None,
                       test_days: int = 20,
                       output_dir: str = None,
                       send_weixin: bool = True,
                       max_workers: int = 4,
                       tickers: List[Tuple[str, str]] = None):
    if horizons is None:
        horizons = [1, 3, 5, 10]
    if output_dir is None:
        output_dir = os.path.expanduser(
            '~/github/daily_tracker_analytics/reports/daily_prediction'
        )
    if tickers is None:
        tickers = get_watchlist()
    os.makedirs(output_dir, exist_ok=True)

    today = datetime.now().strftime('%Y-%m-%d')

    pv = PredictionValidator()
    ap = AdaptivePredictor()

    lines = [f"# 每日预测优化报告 ({today})", ""]
    lines.append(f"**标的数**: {len(tickers)}")
    lines.append(f"**周期**: {horizons}")
    lines.append("")

    # 1. 预取数据
    print(f"\n📥 预取 {len(tickers)} 个标的行情数据...")
    ticker_path_map = {}
    for ticker, name in tickers:
        _, _, path = _prefetch_ticker_data((ticker, name))
        if path:
            ticker_path_map[ticker] = (name, path)
    print(f"✅ 成功预取 {len(ticker_path_map)} 个标的")

    # 2. 并行生成自适应预测
    lines.append("## 自适应预测结果")
    lines.append("")
    lines.append("| 标的 | 周期 | 模型 | 预测方向 | 预测价 | 预测涨跌 | 概率 |")
    lines.append("|------|------|------|---------|--------|---------|------|")

    all_forecasts = []
    tasks = [(t, ticker_path_map[t][0], ticker_path_map[t][1], horizons, test_days)
             for t in ticker_path_map]

    print(f"\n🚀 启动 {max_workers} 进程并行预测...")
    results = []
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        for res in executor.map(_predict_one, tasks):
            results.append(res)

    for res in results:
        if not res['success']:
            print(f"  ❌ {res['name']}({res['ticker']}) 失败: {res.get('error')}")
            lines.append(f"| {res['name']} | - | - | error | - | - | - |")
            continue
        ticker = res['ticker']
        name = res['name']
        print(f"\n🔮 {name}({ticker}) best_models={res['best_models']}")
        for p in res['predictions']:
            lines.append(
                f"| {name} | {p['day']}日 | {p['model']} | {p['pred_direction']} | "
                f"{p['pred_price']} | {p['pred_return']*100:+.2f}% | {p['prob']} |"
            )
        all_forecasts.extend(res['records'])

    pv._save_forecasts(all_forecasts)
    ds = PredictionDataStore()
    ds.save_forecasts(all_forecasts)
    print(f"\n💾 已保存 {len(all_forecasts)} 条预测")

    # 清理可能残留的临时文件
    for _, path in ticker_path_map.values():
        try:
            if os.path.exists(path):
                os.unlink(path)
        except Exception:
            pass

    # 3. 验证昨日预测
    lines.append("")
    lines.append("## 昨日预测验证")
    lines.append("")
    verified_records = []
    try:
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        df_fc = ds.get_forecasts(forecast_date=yesterday)
        for _, row in df_fc.iterrows():
            actual_date = (datetime.strptime(row['forecast_date'], '%Y-%m-%d') +
                           timedelta(days=int(row['horizon']))).strftime('%Y-%m-%d')
            if actual_date != today:
                continue
            actual_df = ds.load_market_data(row['ticker'], days=10)
            if actual_df.empty or actual_date not in actual_df.index:
                continue
            actual_price = float(actual_df.loc[actual_date, 'close'])
            current_price = row['current_price']
            actual_return = actual_price / current_price - 1 if current_price else 0
            actual_direction = 'up' if actual_return > 0 else 'down' if actual_return < 0 else 'flat'
            pred_direction = row['pred_direction']
            direction_correct = int(pred_direction == actual_direction) if pred_direction != 'flat' else 0
            verified_records.append({
                'ticker': row['ticker'],
                'forecast_date': row['forecast_date'],
                'horizon': row['horizon'],
                'pred_direction': pred_direction,
                'actual_direction': actual_direction,
                'pred_return': row['pred_return'],
                'actual_return': actual_return,
                'direction_correct': direction_correct,
            })
        if verified_records:
            lines.append("| 标的 | 预测日 | 周期 | 预测方向 | 实际方向 | 预测涨跌 | 实际涨跌 | 是否正确 |")
            lines.append("|------|--------|------|---------|---------|---------|---------|----------|")
            for v in verified_records:
                lines.append(
                    f"| {v['ticker']} | {v['forecast_date']} | {v['horizon']}日 | "
                    f"{v['pred_direction']} | {v['actual_direction']} | "
                    f"{v['pred_return']*100:+.2f}% | {v['actual_return']*100:+.2f}% | "
                    f"{'✅' if v['direction_correct'] else '❌'} |"
                )
        else:
            lines.append("今日无到期预测可验证。")
    except Exception as e:
        lines.append(f"验证失败: {e}")

    # 4. 模型对比 (可选, 默认关闭以节省时间; 可通过 --compare 开启)
    if False:
        lines.append("")
        lines.append("## 模型对比 (最近20天)")
        lines.append("")
        try:
            summary, compare_report = compare_models(
                tickers=tickers,
                horizons=horizons,
                test_days=test_days,
                models=['trend_predictor', 'improved_predictor', 'ml_predictor']
            )
            in_table = False
            for line in compare_report.split('\n'):
                if line.startswith('## 总体对比'):
                    in_table = True
                    continue
                if in_table and line.startswith('## '):
                    break
                if in_table:
                    lines.append(line)
            compare_path = save_report(compare_report, os.path.join(output_dir, 'model_compare'))
            lines.append(f"\n完整对比报告: {compare_path}")
        except Exception as e:
            lines.append(f"模型对比失败: {e}")

    # 5. 保存日报
    report = "\n".join(lines)
    report_path = os.path.join(output_dir, f"daily_prediction_{today}.md")
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\n✅ 日报已保存: {report_path}")

    # 6. 微信推送摘要
    if send_weixin:
        try:
            from core.notifier import send_weixin_message
            summary_text = f"📊 每日预测优化完成\n日期: {today}\n标的: {len(tickers)}\n预测条数: {len(all_forecasts)}\n报告: {report_path}"
            send_weixin_message(summary_text)
            print("📩 微信推送已发送")
        except Exception as e:
            print(f"微信推送失败: {e}")

    return report_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='每日预测优化流水线')
    parser.add_argument('--horizons', type=int, nargs='+', default=[1, 3, 5, 10])
    parser.add_argument('--test-days', type=int, default=20)
    parser.add_argument('--output-dir', default=None)
    parser.add_argument('--no-weixin', action='store_true', help='不发送微信通知')
    parser.add_argument('--max-workers', type=int, default=4, help='并行进程数')
    parser.add_argument('--limit', type=int, default=None, help='限制处理的标的数量')
    parser.add_argument('--category', type=str, default=None, help='只处理指定分类(如 ETF)')
    args = parser.parse_args()

    tickers = get_watchlist()
    if args.category:
        try:
            from core.watchlist import load_list
            stocks = load_list()
            tickers = [(s['ticker'], s['name']) for s in stocks
                       if s.get('category') == args.category]
        except Exception:
            pass
    if args.limit:
        tickers = tickers[:args.limit]

    run_daily_pipeline(
        horizons=args.horizons,
        test_days=args.test_days,
        output_dir=args.output_dir,
        send_weixin=not args.no_weixin,
        max_workers=args.max_workers,
        tickers=tickers
    )
