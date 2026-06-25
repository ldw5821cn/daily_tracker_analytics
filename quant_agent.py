#!/usr/bin/env python3
"""
📊 Quant Agent - 统一入口
集中管理多Agent量化分析系统的所有功能

用法:
  quant-agent daily                           # 跑全部标的日报
  quant-agent analyze <ticker> [--name 名称]  # 单标的深度分析
  quant-agent watch [--interval 60]           # 实时行情快照 (持续用--monitor)
  quant-agent scan                            # 多策略技术扫描
  quant-agent list                            # 查看关注列表
  quant-agent add <ticker> --name 名称        # 添加标的到关注列表
  quant-agent remove <ticker>                 # 移除标的
  quant-agent snapshot                        # 一键快照：行情+评分
"""
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'multi_agent'))

from core.watchlist import get_stocks_as_tuples, list_stocks, add_stock, remove_stock
from core.realtime_monitor import get_realtime_status
from core.data_layer import get_stock_data, calc_technical_indicators, get_realtime_price
from analysts.strategy_scanner import batch_scan


def cmd_daily():
    """执行完整日报"""
    from daily_report import generate_wechat_report
    stocks = get_stocks_as_tuples()
    report = generate_wechat_report(stocks)
    print(report)


def cmd_analyze(ticker, name=""):
    """单标的深度分析"""
    from orchestrator import analyze_stock
    result = analyze_stock(ticker, name)
    print(result.get('full_report', print_short_summary(result)))
    return result


def print_short_summary(result):
    """简要总结"""
    v = result['verdict']
    t = result['technical_report']
    f = result.get('fundamental_report')
    lines = [
        f"🏛️ {result['name']}({result['ticker']})",
        f"价格: {result['current_price']}元",
        f"评级: {v['rating']} (综合{v['weighted_score']})",
        f"建议: {v['recommendation']}",
        f"技术: {t['rating']}({t['score']}/100) | 基本面: {f['rating']}({f['score']}/100)" if f else "",
        f"Bull({v['bull_score']}) vs Bear({v['bear_score']}) | 净信号{v['net_signal']:+d}",
    ]
    return "\n".join(l for l in lines if l)


def cmd_watch(interval=60, monitor=False):
    """实时行情"""
    if monitor:
        from core.realtime_monitor import monitor_realtime
        monitor_realtime(interval=interval)
    else:
        print(get_realtime_status())


def cmd_scan():
    """多策略技术扫描"""
    stocks = get_stocks_as_tuples()
    batch_scan(stocks)


def cmd_snapshot():
    """一键快照：实时行情 + 各标的当前评分"""
    stocks = get_stocks_as_tuples()
    
    print(f"📊 量化快照 | {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")
    
    # 实时行情
    print(get_realtime_status(stocks))
    print()
    
    # 各标的今日评分（从策略扫描快速获取）
    from analysts.strategy_scanner import scan_stock
    for ticker, name in stocks:
        try:
            sr = scan_stock(ticker, name)
            if 'error' not in sr:
                icon = "🟢" if sr['total_score'] >= 20 else "🟡" if sr['total_score'] >= 10 else "🔴"
                print(f"{icon} {name}: 策略{sr['total_score']}/100 | {sr['best_strategy']} | {sr['signal_count']}个信号")
        except:
            pass


def cmd_polars_scan(tickers=None):
    """Polars 向量化高速扫描"""
    from analysts.polars_scanner import scan_single_polars
    stocks = get_stocks_as_tuples()
    if tickers:
        stocks = [(t, n) for t, n in stocks if t in tickers]
    
    print(f"⚡ Polars 向量化扫描 | {len(stocks)}个标的")
    print(f"{'='*50}")
    
    for ticker, name in stocks:
        r = scan_single_polars(ticker, name)
        if 'error' not in r:
            active = [s for s in r['strategy_results'] if s['score'] > 0]
            scores_str = ' | '.join([f"{s['name']}({s['score']})" for s in active])
            print(f"  {name}: {r['total_score']}/100 | {r['best_strategy']} | {scores_str}" if scores_str else f"  {name}: {r['total_score']}/100 | 无信号")
        else:
            print(f"  ❌ {name}: {r['error']}")


def cmd_ai_strategy(description, name=None):
    """AI 生成并注册策略"""
    from analysts.ai_strategy_generator import generate_and_register
    generate_and_register(description, name)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='Quant Agent - 多Agent量化分析统一入口')
    sub = parser.add_subparsers(dest='cmd')
    
    # daily
    sub.add_parser('daily', help='跑全部标的日报')
    
    # analyze
    p_analyze = sub.add_parser('analyze', help='单标的深度分析')
    p_analyze.add_argument('ticker', help='股票代码')
    p_analyze.add_argument('--name', '-n', default='', help='股票名称')
    
    # watch
    p_watch = sub.add_parser('watch', help='实时行情')
    p_watch.add_argument('--interval', '-i', type=int, default=60)
    p_watch.add_argument('--monitor', '-m', action='store_true', help='持续监控模式')
    
    # scan
    sub.add_parser('scan', help='多策略技术扫描')
    
    # list
    sub.add_parser('list', help='查看关注列表')
    
    # add
    p_add = sub.add_parser('add', help='添加标的')
    p_add.add_argument('ticker', help='股票代码')
    p_add.add_argument('--name', '-n', required=True, help='股票名称')
    p_add.add_argument('--category', '-c', default='个股', help='分类(EIF/个股)')
    
    # remove
    p_rm = sub.add_parser('remove', help='移除标的')
    p_rm.add_argument('ticker', help='股票代码')
    
    # snapshot
    sub.add_parser('snapshot', help='一键快照')
    
    # polars-scan
    p_polars = sub.add_parser('polars-scan', help='Polars向量化扫描（高速）')
    p_polars.add_argument('--tickers', '-t', nargs='+', help='指定标的代码（默认全部）')
    
    # ai-strategy
    p_ai = sub.add_parser('ai-strategy', help='AI生成并注册新策略')
    p_ai.add_argument('description', help='策略描述（如"连续3日放量上涨"）')
    p_ai.add_argument('--name', '-n', help='策略名称（可选）')
    
    # backtest
    p_bt = sub.add_parser('backtest', help='VectorBT专业回测')
    p_bt.add_argument('--ticker', '-t', help='股票代码（默认全部标的）')
    p_bt.add_argument('--strategy', '-s', default='compare',
                      choices=['golden_cross','trend_breakout','ma_bullish','rsi_oversold','compare'],
                      help='策略')
    p_bt.add_argument('--commission', '-c', type=float, default=0.0003, help='手续费率(默认万3)')
    
    # predict
    p_pred = sub.add_parser('predict', help='多模型融合预测(LSTM+XGB+LGB+RF)')
    p_pred.add_argument('--ticker', '-t', help='股票代码（默认全部）')
    p_pred.add_argument('--days', '-d', type=int, default=5, help='预测天数')
    
    args = parser.parse_args()
    
    if args.cmd == 'daily':
        cmd_daily()
    elif args.cmd == 'analyze':
        cmd_analyze(args.ticker, args.name)
    elif args.cmd == 'watch':
        cmd_watch(args.interval, args.monitor)
    elif args.cmd == 'scan':
        cmd_scan()
    elif args.cmd == 'list':
        list_stocks()
    elif args.cmd == 'add':
        add_stock(args.ticker, args.name, args.category)
        print(f"✅ 已添加 {args.name}({args.ticker})")
    elif args.cmd == 'remove':
        remove_stock(args.ticker)
        print(f"✅ 已移除 {args.ticker}")
    elif args.cmd == 'snapshot':
        cmd_snapshot()
    elif args.cmd == 'polars-scan':
        cmd_polars_scan(args.tickers)
    elif args.cmd == 'ai-strategy':
        cmd_ai_strategy(args.description, args.name)
    elif args.cmd == 'backtest':
        from analysts.vectorbt_backtest import compare_strategies, run_vectorbt_backtest, run_strategy_on_stocks
        if args.strategy == 'compare':
            if args.ticker:
                compare_strategies(args.ticker)
            else:
                from core.watchlist import get_stocks_as_tuples
                for t, n in get_stocks_as_tuples():
                    compare_strategies(t, n)
                    print()
        else:
            if args.ticker:
                r = run_vectorbt_backtest(args.ticker, strategy=args.strategy, commission_pct=args.commission)
                print(r)
            else:
                run_strategy_on_stocks(args.strategy)
    elif args.cmd == 'predict':
        from analysts.multi_model_predictor import analyze, format_report
        if args.ticker:
            print(format_report(analyze(args.ticker, '', args.days)))
        else:
            from core.watchlist import get_stocks_as_tuples
            for t, n in get_stocks_as_tuples():
                print(format_report(analyze(t, n, args.days)))
                print()
    else:
        parser.print_help()
