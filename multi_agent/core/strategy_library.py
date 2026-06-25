"""
策略库 - 统一技术指标策略定义

同时服务两个回测入口:
  1. vectorbt_backtest.py  (事件驱动回测)
  2. etf_tracker.py BacktestEngine (多周期统计回测)

新增策略 >= 15 种，覆盖：
  - 趋势跟踪
  - 均值回归
  - 动量/突破
  - 波动率
  - 量价
  - 复合信号
"""
import numpy as np
import pandas as pd
from typing import Callable, Dict, Tuple


def _cross_up(series_a: pd.Series, series_b: pd.Series) -> pd.Series:
    """a 上穿 b"""
    return (series_a > series_b) & (series_a.shift(1) <= series_b.shift(1))


def _cross_down(series_a: pd.Series, series_b: pd.Series) -> pd.Series:
    """a 下穿 b"""
    return (series_a < series_b) & (series_a.shift(1) >= series_b.shift(1))


# ========================= 策略信号函数 =========================

def signal_golden_cross(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    """MACD金叉买入，死叉卖出"""
    entries = _cross_up(df['macd_dif'], df['macd_dea'])
    exits = _cross_down(df['macd_dif'], df['macd_dea'])
    return entries, exits


def signal_ema_cross(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    """EMA12/26 金叉买入，死叉卖出"""
    ema12 = df['close'].ewm(span=12, adjust=False).mean()
    ema26 = df['close'].ewm(span=26, adjust=False).mean()
    entries = _cross_up(ema12, ema26)
    exits = _cross_down(ema12, ema26)
    return entries, exits


def signal_ma_bullish(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    """均线多头排列买入，破坏则卖出"""
    entries = (df['ma5'] > df['ma10']) & (df['ma10'] > df['ma20'])
    exits = (df['ma5'] <= df['ma10']) | (df['ma10'] <= df['ma20'])
    return entries, exits


def signal_ma_cross(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    """MA5 上穿 MA20 买入，下穿卖出"""
    entries = _cross_up(df['ma5'], df['ma20'])
    exits = _cross_down(df['ma5'], df['ma20'])
    return entries, exits


def signal_trend_breakout(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    """收盘价突破 MA20 买入，跌破 MA20 卖出"""
    entries = _cross_up(df['close'], df['ma20'])
    exits = _cross_down(df['close'], df['ma20'])
    return entries, exits


def signal_rsi_oversold(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    """RSI<30 买入，RSI>70 卖出"""
    entries = df['rsi_14'] < 30
    exits = df['rsi_14'] > 70
    return entries, exits


def signal_rsi_reversal(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    """RSI 从超卖区回升买入，从超买区回落卖出"""
    entries = (df['rsi_14'].shift(1) < 35) & (df['rsi_14'] >= 35)
    exits = (df['rsi_14'].shift(1) > 65) & (df['rsi_14'] <= 65)
    return entries, exits


def signal_kdj_oversold(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    """KDJ J<0 超卖买入，J>100 超买卖出"""
    entries = df['kdj_j'] < 0
    exits = df['kdj_j'] > 100
    return entries, exits


def signal_boll_reversion(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    """触及布林带下轨买入，触及上轨卖出"""
    entries = df['close'] <= df['boll_down'] * 1.02
    exits = df['close'] >= df['boll_up'] * 0.98
    return entries, exits


def signal_boll_breakout(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    """布林带开口突破：收盘站上上轨买入，跌破中轨卖出"""
    entries = df['close'] > df['boll_up']
    exits = df['close'] < df['boll_mid']
    return entries, exits


def signal_momentum_5d(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    """5日动量 > 3% 买入，< -3% 卖出"""
    mom = df['momentum_5d']
    entries = mom > 3
    exits = mom < -3
    return entries, exits


def signal_momentum_10d(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    """10日动量 > 5% 买入，< -5% 卖出"""
    mom = df['momentum_10d']
    entries = mom > 5
    exits = mom < -5
    return entries, exits


def signal_donchian_breakout(df: pd.DataFrame, window: int = 20) -> Tuple[pd.Series, pd.Series]:
    """唐奇安通道突破：突破前 N 日高点买入，跌破前 N 日低点卖出"""
    upper = df['high'].rolling(window=window).max().shift(1)
    lower = df['low'].rolling(window=window).min().shift(1)
    entries = df['close'] > upper
    exits = df['close'] < lower
    return entries, exits


def signal_volume_breakout(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    """放量突破 MA20：成交量 > 1.5 倍均量且收盘站上 MA20 买入"""
    entries = (df['vol_ratio'] > 1.5) & (df['close'] > df['ma20'])
    exits = (df['vol_ratio'] < 0.7) & (df['close'] < df['ma20'])
    return entries, exits


def signal_atr_breakout(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    """ATR 波动率突破：当日涨幅 > 1.5×ATR 买入，跌幅 < -1.5×ATR 卖出"""
    daily_ret = df['close'].pct_change()
    atr_pct = df['atr_14'] / df['close'].shift(1)
    entries = daily_ret > 1.5 * atr_pct
    exits = daily_ret < -1.5 * atr_pct
    return entries, exits


def signal_volatility_squeeze(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    """波动率压缩后突破：布林带宽度 20 日最低且收盘突破上轨买入"""
    bandwidth = (df['boll_up'] - df['boll_down']) / df['boll_mid']
    squeeze = bandwidth == bandwidth.rolling(20).min()
    entries = squeeze.shift(1) & (df['close'] > df['boll_up'])
    exits = df['close'] < df['boll_mid']
    return entries, exits


def signal_macd_ma20_combo(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    """复合策略：MACD金叉 + 收盘价站上MA20 + 放量"""
    macd_cross = _cross_up(df['macd_dif'], df['macd_dea'])
    above_ma20 = df['close'] > df['ma20']
    volume = df['vol_ratio'] > 1.2
    entries = macd_cross & above_ma20 & volume
    exits = _cross_down(df['macd_dif'], df['macd_dea']) | (df['close'] < df['ma20'] * 0.97)
    return entries, exits


def signal_ema_rsi_combo(df: pd.DataFrame) -> Tuple[pd.Series, pd.Series]:
    """复合策略：EMA12上穿EMA26 + RSI 不在超买区"""
    ema12 = df['close'].ewm(span=12, adjust=False).mean()
    ema26 = df['close'].ewm(span=26, adjust=False).mean()
    entries = _cross_up(ema12, ema26) & (df['rsi_14'] < 65)
    exits = _cross_down(ema12, ema26) | (df['rsi_14'] > 75)
    return entries, exits


# ========================= 策略注册表 =========================

STRATEGIES: Dict[str, Dict] = {
    'golden_cross': {'name': 'MACD金叉', 'fn': signal_golden_cross, 'category': '趋势'},
    'ema_cross': {'name': 'EMA金叉', 'fn': signal_ema_cross, 'category': '趋势'},
    'ma_bullish': {'name': '均线多头排列', 'fn': signal_ma_bullish, 'category': '趋势'},
    'ma_cross': {'name': 'MA5/MA20交叉', 'fn': signal_ma_cross, 'category': '趋势'},
    'trend_breakout': {'name': '趋势突破MA20', 'fn': signal_trend_breakout, 'category': '趋势'},
    'rsi_oversold': {'name': 'RSI超卖', 'fn': signal_rsi_oversold, 'category': '均值回归'},
    'rsi_reversal': {'name': 'RSI回升', 'fn': signal_rsi_reversal, 'category': '均值回归'},
    'kdj_oversold': {'name': 'KDJ超卖', 'fn': signal_kdj_oversold, 'category': '均值回归'},
    'boll_reversion': {'name': '布林带回归', 'fn': signal_boll_reversion, 'category': '均值回归'},
    'boll_breakout': {'name': '布林带突破', 'fn': signal_boll_breakout, 'category': '突破'},
    'momentum_5d': {'name': '5日动量', 'fn': signal_momentum_5d, 'category': '动量'},
    'momentum_10d': {'name': '10日动量', 'fn': signal_momentum_10d, 'category': '动量'},
    'donchian_breakout': {'name': '唐奇安通道突破', 'fn': signal_donchian_breakout, 'category': '突破'},
    'volume_breakout': {'name': '放量突破', 'fn': signal_volume_breakout, 'category': '量价'},
    'atr_breakout': {'name': 'ATR波动突破', 'fn': signal_atr_breakout, 'category': '波动率'},
    'volatility_squeeze': {'name': '波动率压缩', 'fn': signal_volatility_squeeze, 'category': '波动率'},
    'macd_ma20_combo': {'name': 'MACD+MA20+放量', 'fn': signal_macd_ma20_combo, 'category': '复合'},
    'ema_rsi_combo': {'name': 'EMA+RSI过滤', 'fn': signal_ema_rsi_combo, 'category': '复合'},
}


def get_strategy(name: str) -> Callable:
    """获取策略信号函数"""
    info = STRATEGIES.get(name)
    if not info:
        raise KeyError(f"未知策略: {name}，可用: {list(STRATEGIES.keys())}")
    return info['fn']


def list_strategies() -> Dict[str, str]:
    """列出所有策略名称"""
    return {k: v['name'] for k, v in STRATEGIES.items()}


def list_strategies_by_category() -> Dict[str, list]:
    """按类别分组"""
    cats = {}
    for k, v in STRATEGIES.items():
        cats.setdefault(v['category'], []).append((k, v['name']))
    return cats


# ========================= 事件驱动回测辅助函数 =========================

def run_event_backtest(df: pd.DataFrame, strategy_fn: Callable,
                       commission_pct: float = 0.0003,
                       slippage_pct: float = 0.001,
                       stop_loss: float = None,
                       take_profit: float = None,
                       init_cash: float = 100000.0) -> Dict:
    """
    简化的向量化事件驱动回测（不依赖 vectorbt）
    返回与 vectorbt 类似的统计指标
    """
    df = df.copy()
    entries, exits = strategy_fn(df)

    position = 0  # 0=空仓, 1=持仓
    trades = []
    entry_idx = None
    entry_price = None
    equity = [init_cash]

    close = df['close'].astype(float).values
    entry_arr = entries.fillna(False).values
    exit_arr = exits.fillna(False).values

    for i in range(1, len(df)):
        # 当前收盘价
        price = close[i]
        prev_price = close[i - 1]

        # 止损/止盈检查（基于前一天持仓）
        if position == 1 and entry_price is not None:
            ret = (price - entry_price) / entry_price
            if stop_loss is not None and ret <= stop_loss:
                exit_arr[i] = True
            if take_profit is not None and ret >= take_profit:
                exit_arr[i] = True

        # 交易执行（按收盘价，含滑点）
        if position == 0 and entry_arr[i]:
            entry_price = price * (1 + slippage_pct)
            entry_idx = i
            position = 1
        elif position == 1 and exit_arr[i]:
            exit_price = price * (1 - slippage_pct)
            ret = (exit_price - entry_price) / entry_price - 2 * commission_pct
            trades.append({
                'entry_price': entry_price,
                'exit_price': exit_price,
                'return': ret,
            })
            position = 0
            entry_price = None

        # 权益曲线
        if position == 1 and entry_price is not None:
            current_ret = (price - entry_price) / entry_price
        else:
            current_ret = 0
        equity.append(init_cash * (1 + sum(t['return'] for t in trades) + current_ret))

    if not trades:
        return {
            'total_return': 0.0,
            'max_drawdown': 0.0,
            'sharpe_ratio': 0.0,
            'win_rate': 0.0,
            'total_trades': 0,
        }

    returns = [t['return'] for t in trades]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]

    equity_series = pd.Series(equity)
    peak = equity_series.expanding().max()
    drawdown = (equity_series - peak) / peak

    avg_ret = np.mean(returns)
    std_ret = np.std(returns) or 1e-9
    sharpe = avg_ret / std_ret * np.sqrt(252)

    return {
        'total_return': round(sum(returns) * 100, 2),
        'max_drawdown': round(drawdown.min() * 100, 2),
        'sharpe_ratio': round(sharpe, 2),
        'win_rate': round(len(wins) / len(returns) * 100, 2),
        'total_trades': len(trades),
        'avg_win': round(np.mean(wins) * 100, 2) if wins else 0,
        'avg_loss': round(np.mean(losses) * 100, 2) if losses else 0,
        'best_trade': round(max(returns) * 100, 2),
        'worst_trade': round(min(returns) * 100, 2),
    }


# ========================= 扫描打分辅助函数 =========================

def scan_signal_score(df: pd.DataFrame, strategy_fn: Callable, max_score: int = 100) -> Tuple[int, list]:
    """用最新一根 K 线对策略进行打分，用于选股器"""
    if df is None or len(df) < 30:
        return 0, []

    entries, exits = strategy_fn(df)
    latest = df.iloc[-1]
    reasons = []
    score = 0

    # 最新一天是否触发买入信号
    if entries.iloc[-1]:
        score += int(max_score * 0.6)
        reasons.append("触发买入信号")

    # 最近 5 天内是否触发买入
    if entries.iloc[-5:].any():
        score += int(max_score * 0.2)
        reasons.append("近期有买入信号")

    # 最新一天是否触发卖出信号（扣分）
    if exits.iloc[-1]:
        score -= int(max_score * 0.3)
        reasons.append("触发卖出信号")

    return max(0, score), reasons


if __name__ == "__main__":
    print("可用策略列表:")
    for cat, items in list_strategies_by_category().items():
        print(f"\n[{cat}]")
        for sid, name in items:
            print(f"  {sid:20s} - {name}")
