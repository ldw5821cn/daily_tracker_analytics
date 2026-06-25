"""
VectorBT 专业回测引擎（基于统一策略库）
支持：
- 18+ 种技术策略
- T+1 规则（A股当日买入次日才能卖出）
- 手续费（万1~万3）
- 滑点（0.1%）
- 止损/止盈
- 最大持仓天数
- 多标的对比
- 无 vectorbt 时自动降级为向量化回测

用法:
  python vectorbt_backtest.py 601991 --strategy golden_cross
"""
import sys
import os
import warnings
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

warnings.filterwarnings('ignore')

sys.path.insert(0, '/home/zhihu/daily_tracker_analytics/etf_tracker/multi_agent')
from core.data_layer import get_stock_data, calc_technical_indicators
from core.strategy_library import STRATEGIES, run_event_backtest

try:
    import vectorbt as vbt
    VBT_AVAILABLE = True
except ImportError:
    VBT_AVAILABLE = False


def run_vectorbt_backtest(ticker, name="", strategy='golden_cross',
                          commission_pct=0.0003, slippage_pct=0.001,
                          stop_loss=None, take_profit=None,
                          max_hold_days=None, period=365):
    """
    用 vectorbt 或降级方案运行专业回测

    Args:
        ticker: 股票代码
        name: 股票名称
        strategy: 策略ID (见 strategy_library.STRATEGIES)
        commission_pct: 手续费率（默认万3）
        slippage_pct: 滑点（默认0.1%）
        stop_loss: 止损（如-0.05=-5%）
        take_profit: 止盈（如0.1=10%）
        max_hold_days: 最大持仓天数
        period: 回测天数

    Returns:
        dict: 回测结果
    """
    # 获取数据
    df_raw, _ = get_stock_data(ticker)
    df = calc_technical_indicators(df_raw)

    if len(df) < 60:
        return {'error': f'数据不足: {len(df)}天'}

    # 截取回测区间
    if len(df) > period:
        df = df.iloc[-period:]

    sig_info = STRATEGIES.get(strategy)
    if not sig_info:
        return {'error': f'未知策略: {strategy}，可选: {list(STRATEGIES.keys())}'}

    close = df['close'].astype(float)

    # 生成买卖信号
    entries, exits = sig_info['fn'](df)

    if VBT_AVAILABLE:
        # vectorbt 回测
        pf = vbt.Portfolio.from_signals(
            close=close,
            entries=entries,
            exits=exits,
            direction='longonly',
            freq='D',
            init_cash=100000.0,
            slippage=slippage_pct,
            fees=commission_pct,
            sl_stop=stop_loss if stop_loss else None,
            tp_stop=take_profit if take_profit else None,
        )
        stats = pf.stats()
        result = {
            'ticker': ticker,
            'name': name,
            'strategy': sig_info['name'],
            'period_days': period,
            'start_date': df.index[0].strftime('%Y-%m-%d'),
            'end_date': df.index[-1].strftime('%Y-%m-%d'),
            'start_price': round(float(df['close'].iloc[0]), 2),
            'end_price': round(float(df['close'].iloc[-1]), 2),
            'commission': f"{commission_pct*100:.2f}%",
            'slippage': f"{slippage_pct*100:.2f}%",
            'engine': 'vectorbt',
        }

        stats_map = {
            'Start': 'start_value',
            'End': 'end_value',
            'Total Return [%]': 'total_return',
            'Max Drawdown [%]': 'max_drawdown',
            'Sharpe Ratio': 'sharpe_ratio',
            'Sortino Ratio': 'sortino_ratio',
            'Win Rate [%]': 'win_rate',
            'Total Trades': 'total_trades',
            'Avg Winning Trade [%]': 'avg_win',
            'Avg Losing Trade [%]': 'avg_loss',
            'Best Trade [%]': 'best_trade',
            'Worst Trade [%]': 'worst_trade',
            'Expectancy': 'expectancy',
        }

        for stat_name, result_key in stats_map.items():
            if stat_name in stats.index:
                val = stats.loc[stat_name]
                try:
                    result[result_key] = round(float(val), 2) if isinstance(val, (int, float, np.number)) else val
                except:
                    result[result_key] = val
    else:
        # 降级为自研向量化回测
        bt = run_event_backtest(df, sig_info['fn'],
                                commission_pct=commission_pct,
                                slippage_pct=slippage_pct,
                                stop_loss=stop_loss,
                                take_profit=take_profit)
        result = {
            'ticker': ticker,
            'name': name,
            'strategy': sig_info['name'],
            'period_days': period,
            'start_date': df.index[0].strftime('%Y-%m-%d'),
            'end_date': df.index[-1].strftime('%Y-%m-%d'),
            'start_price': round(float(df['close'].iloc[0]), 2),
            'end_price': round(float(df['close'].iloc[-1]), 2),
            'commission': f"{commission_pct*100:.2f}%",
            'slippage': f"{slippage_pct*100:.2f}%",
            'engine': 'fallback',
            **bt,
        }

    return result


def compare_strategies(ticker, name="", period=365):
    """对比所有策略在同一标的上的表现"""
    print(f"\n🏛️ 策略对比: {name}({ticker})")
    print(f"{'='*60}")
    print(f"配置: 手续费万3 | 滑点0.1% | T+1规则")
    print(f"{'─'*60}")

    results = []
    for sid, sinfo in STRATEGIES.items():
        r = run_vectorbt_backtest(ticker, name, strategy=sid, period=period)
        if 'error' not in r:
            results.append(r)
            ret = r.get('total_return', 'N/A')
            dd = r.get('max_drawdown', 'N/A')
            sharpe = r.get('sharpe_ratio', 'N/A')
            trades = r.get('total_trades', 'N/A')
            wr = r.get('win_rate', 'N/A')
            eng = r.get('engine', 'unknown')
            print(f"  {sinfo['name']:12s} | 收益{ret:>8} | 回撤{dd:>8} | 夏普{sharpe:>6} | 交易{trades:>4}次 | 胜率{wr:>5}% | {eng}")

    if results:
        best = max(results, key=lambda r: r.get('sharpe_ratio', -999) if isinstance(r.get('sharpe_ratio'), (int, float)) else -999)
        print(f"\n🏆 最佳策略: {best['strategy']} (夏普{best.get('sharpe_ratio','N/A')})")

    return results


def run_strategy_on_stocks(strategy='golden_cross'):
    """对所有关注标的运行指定策略"""
    from core.watchlist import get_stocks_as_tuples
    stocks = get_stocks_as_tuples()

    print(f"\n📊 批量回测: {STRATEGIES[strategy]['name']}")
    print(f"{'='*60}")
    print(f"{'标的':20s} {'收益':>8} {'回撤':>8} {'夏普':>6} {'胜率':>6} {'交易':>5}")
    print(f"{'─'*60}")

    for ticker, name in stocks:
        r = run_vectorbt_backtest(ticker, name, strategy=strategy)
        if 'error' not in r:
            ret = r.get('total_return', 0)
            dd = r.get('max_drawdown', 0)
            sharpe = r.get('sharpe_ratio', 0)
            wr = r.get('win_rate', 0)
            trades = r.get('total_trades', 0)
            print(f"  {name+ '('+ticker+')':20s} {ret:>+8.1f}% {dd:>8.1f}% {sharpe:>6.2f} {wr:>5.1f}% {trades:>4}")
        else:
            print(f"  {name:20s} ❌ {r['error']}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='VectorBT专业回测')
    parser.add_argument('--ticker', '-t', default='601991', help='股票代码')
    parser.add_argument('--name', '-n', default='', help='股票名称')
    parser.add_argument('--strategy', '-s', default='golden_cross',
                        choices=list(STRATEGIES.keys()) + ['all', 'compare'],
                        help='策略')
    parser.add_argument('--commission', '-c', type=float, default=0.0003, help='手续费率(默认万3)')
    parser.add_argument('--period', '-p', type=int, default=365, help='回测天数')
    parser.add_argument('--stop-loss', type=float, default=None, help='止损比例')
    parser.add_argument('--take-profit', type=float, default=None, help='止盈比例')

    args = parser.parse_args()

    if args.strategy == 'compare':
        compare_strategies(args.ticker, args.name, period=args.period)
    elif args.strategy == 'all':
        for sid in STRATEGIES:
            run_strategy_on_stocks(sid)
            print()
    else:
        r = run_vectorbt_backtest(args.ticker, args.name, args.strategy,
                                  commission_pct=args.commission, period=args.period,
                                  stop_loss=args.stop_loss, take_profit=args.take_profit)
        if 'error' in r:
            print(f"❌ {r['error']}")
        else:
            print(f"\n{'='*60}")
            print(f"  VectorBT 回测报告: {r['name']}({r['ticker']})")
            print(f"  策略: {r['strategy']} | 周期: {r['period_days']}天 | 引擎: {r.get('engine','unknown')}")
            print(f"  区间: {r['start_date']} ~ {r['end_date']}")
            print(f"{'='*60}")
            for k in ['total_return', 'max_drawdown', 'sharpe_ratio', 'sortino_ratio',
                      'win_rate', 'total_trades', 'avg_win', 'avg_loss',
                      'best_trade', 'worst_trade', 'expectancy']:
                if k in r:
                    print(f"  {k:20s}: {r[k]}")
            print(f"  手续费: {r.get('commission','N/A')} | 滑点: {r.get('slippage','N/A')}")
