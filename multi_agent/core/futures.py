"""
期货行情模块 — 数据获取 + 技术分析 + 趋势预测
- 实时行情: Sina Finance JSONP API
- 历史K线: Sina Finance + AKShare
- 技术指标: 复用 data_layer.calc_technical_indicators
"""
import urllib.request
import re
import json
import pandas as pd
import numpy as np
from datetime import datetime

# ===== 期货主力合约映射 =====
FUTURES_MAP = [
    ('CU0', '沪铜'), ('AL0', '沪铝'), ('ZN0', '沪锌'),
    ('AU0', '黄金'), ('AG0', '白银'),
    ('RB0', '螺纹钢'), ('I0', '铁矿石'),
    ('SC0', '原油'), ('MA0', '甲醇'),
    ('CF0', '棉花'), ('SR0', '白糖'),
    ('C0', '玉米'), ('P0', '棕榈油'),
    ('HC0', '热卷'), ('TA0', 'PTA'),
    ('V0', 'PVC'), ('RM0', '菜粕'),
    ('OI0', '菜油'), ('JM0', '焦煤'),
    ('J0', '焦炭'), ('SM0', '硅锰'),
]

# 板块分组（用于报告分类）
CATEGORIES = {
    '有色': ['CU0', 'AL0', 'ZN0', 'AU0', 'AG0'],
    '黑色': ['RB0', 'HC0', 'I0', 'JM0', 'J0', 'SM0'],
    '能化': ['SC0', 'MA0', 'TA0', 'V0'],
    '农产品': ['CF0', 'SR0', 'C0', 'P0', 'RM0', 'OI0'],
}

# ===== 数据获取 =====

def get_futures_quotes():
    """获取期货主力合约实时行情（新浪）"""
    results = []
    for code, name in FUTURES_MAP:
        try:
            url = 'https://hq.sinajs.cn/list=nf_{}'.format(code)
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0',
                'Referer': 'https://finance.sina.com.cn',
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                text = resp.read().decode('gbk')
            match = re.search(r'"([^"]+)"', text)
            if match and len(match.group(1)) > 10:
                parts = match.group(1).split(',')
                if len(parts) > 8:
                    latest = float(parts[7]) if parts[7] else 0
                    prev = float(parts[4]) if parts[4] else latest
                    chg = ((latest / prev) - 1) * 100 if prev > 0 else 0
                    results.append({
                        'name': name, 'code': code,
                        'price': round(latest, 2),
                        'change_pct': round(chg, 2),
                        'high': float(parts[2]) if parts[2] else 0,
                        'low': float(parts[3]) if parts[3] else 0,
                        'open': float(parts[1]) if parts[1] else 0,
                    })
        except:
            pass
    return results


def get_futures_kline_data(code, datalen=500):
    """
    获取期货主力合约日K线（新浪期货API）
    返回标准 OHLCV DataFrame: columns = [open, high, low, close, volume]
    """
    try:
        url = ('https://stock.finance.sina.com.cn/futures/api/jsonp.php/'
               'var%20_data_=/InnerFuturesNewService.getDailyKLine'
               '?symbol={}'.format(code))
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://finance.sina.com.cn',
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode('utf-8')
        match = re.search(r'\[.*\]', text)
        if not match:
            return None
        data = json.loads(match.group())
        if len(data) < 20:
            return None
        rows = []
        for item in data[-datalen:]:
            rows.append({
                'date': item['d'],
                'open': float(item['o']),
                'high': float(item['h']),
                'low': float(item['l']),
                'close': float(item['c']),
                'volume': float(item['v']),
            })
        df = pd.DataFrame(rows)
        df['date'] = pd.to_datetime(df['date'])
        df.set_index('date', inplace=True)
        df.sort_index(inplace=True)
        return df
    except Exception as e:
        return None


def get_futures_report():
    """生成期货行情报告文本"""
    data = get_futures_quotes()
    if not data:
        return "期货数据获取失败"

    up = sum(1 for d in data if d['change_pct'] >= 0)
    down = len(data) - up

    lines = []
    lines.append("## 期货行情")
    lines.append("")
    lines.append("整体: {}涨 {}跌".format(up, down))

    for cat, members in CATEGORIES.items():
        items = [d for d in data if d['code'] in members]
        if items:
            avg = sum(d['change_pct'] for d in items) / len(items)
            icon = "\U0001F7E2" if avg >= 0 else "\U0001F534"
            lines.append("  {} {}: {:+.2f}% ({}个)".format(icon, cat, avg, len(items)))

    lines.append("")
    sorted_data = sorted(data, key=lambda d: d['change_pct'], reverse=True)
    lines.append("**涨幅前三**:")
    for d in sorted_data[:3]:
        lines.append("  \U0001F7E2 {}: {:+.2f}%".format(d['name'], d['change_pct']))
    lines.append("**跌幅前三**:")
    for d in sorted_data[-3:]:
        if d['change_pct'] < 0:
            lines.append("  \U0001F534 {}: {:+.2f}%".format(d['name'], d['change_pct']))

    return "\n".join(lines)


def analyze_futures_trend(code, name=''):
    """
    对单个期货品种做技术分析 + 趋势预测
    返回 dict: {code, name, technical_result, backtest, signal, forecast}
    """
    # 若未传入 name，自动从 FUTURES_MAP 映射
    if not name:
        name = dict(FUTURES_MAP).get(code, code)
    df = get_futures_kline_data(code)
    if df is None:
        return {'code': code, 'name': name, 'error': '数据获取失败'}

    # 计算技术指标
    from core.data_layer import calc_technical_indicators, multi_period_backtest
    df = calc_technical_indicators(df)

    # 多周期回测
    backtest = multi_period_backtest(df)

    # 信号判定
    latest = df.iloc[-1]
    signal = _judge_signal(df, latest)
    result = {
        'code': code,
        'name': name,
        'price': round(float(latest['close']), 2),
        'ma_trend': _get_ma_trend(df),
        'rsi': round(float(latest.get('rsi_14', 50)), 1),
        'macd': '多头' if latest.get('macd_hist', 0) > 0 else '空头',
        'boll_pos': '上轨' if float(latest['close']) > float(latest.get('boll_up', 0))
                     else '下轨' if float(latest['close']) < float(latest.get('boll_down', 0))
                     else '中轨',
        'signal': signal,
        'backtest': backtest,
        'trend_5d': round(float(latest.get('momentum_5d', 0)), 2),
        'trend_20d': round(float(latest.get('momentum_20d', 0)), 2),
        'forecast': _build_forecast(df, latest),
    }
    return result


def _build_forecast(df, latest):
    """基于近期动量和波动率生成走势预判"""
    close = float(latest['close'])
    # 用 5 日、20 日动量加权得到未来预期收益
    mom5 = float(latest.get('momentum_5d', 0)) / 100  # 百分比转小数
    mom20 = float(latest.get('momentum_20d', 0)) / 100
    # 加权: 短期动量 0.6, 中期动量 0.4, 并做收缩
    expected_ret = (mom5 * 0.6 + mom20 * 0.4) * 0.5
    target_price = close * (1 + expected_ret)
    # 支撑位/压力位: 近 20 日低/高点
    recent = df.tail(20)
    support = float(recent['low'].min())
    resistance = float(recent['high'].max())
    # 年化波动率 -> N 日波动率
    daily_vol = df['close'].pct_change().std()
    if pd.isna(daily_vol) or daily_vol == 0:
        daily_vol = 0.01
    # 多周期预测
    rows = []
    for days in [1, 3, 5, 10, 20]:
        ret = expected_ret * np.sqrt(days / 5)  # 收益随时间平方根缩放
        vol = daily_vol * np.sqrt(days)
        pred_price = close * (1 + ret)
        signal = '看多' if ret > 0.01 else '看空' if ret < -0.01 else '震荡'
        rows.append({
            'days': days,
            'predicted_price': round(pred_price, 2),
            'predicted_return': f"{ret:+.2%}",
            'lower': round(close * (1 + ret - 1.5 * vol), 2),
            'upper': round(close * (1 + ret + 1.5 * vol), 2),
            'signal': signal,
        })
    return {
        'target_price': round(target_price, 2),
        'expected_return': f"{expected_ret:+.2%}",
        'support': round(support, 2),
        'resistance': round(resistance, 2),
        'multi_period': rows,
    }


def _judge_signal(df, latest):
    """期货信号判定：多周期趋势+RSI+MACD"""
    bullish, bearish = 0, 0
    # RSI
    rsi = float(latest.get('rsi_14', 50))
    if rsi > 70: bearish += 1       # 超买
    elif rsi < 30: bullish += 1     # 超卖
    # MACD
    if float(latest.get('macd_hist', 0)) > 0: bullish += 1
    else: bearish += 1
    # 均线趋势
    if float(latest.get('ma_5d', 0)) > float(latest.get('ma_20d', 0)):
        bullish += 1
    else:
        bearish += 1
    # 多空综合
    if bullish > bearish: return '看多'
    elif bearish > bullish: return '看空'
    return '中性'


def _get_ma_trend(df):
    """均线趋势描述"""
    ma5 = float(df['close'].rolling(5).mean().iloc[-1])
    ma20 = float(df['close'].rolling(20).mean().iloc[-1])
    ma60 = float(df['close'].rolling(60).mean().iloc[-1]) if len(df) >= 60 else ma20
    if ma5 > ma20 > ma60: return '多头排列'
    if ma5 < ma20 < ma60: return '空头排列'
    return '震荡整理'


if __name__ == "__main__":
    # Test
    result = analyze_futures_trend('CU0', '沪铜')
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
