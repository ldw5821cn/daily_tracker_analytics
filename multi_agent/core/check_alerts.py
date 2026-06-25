"""
盘中异动检查脚本（供 cron 调用）
检测到异动时：
1. 输出告警文本 → cron 推送到微信
2. 保存报告到 reports/ → GitHub Pages 可查看

全部使用北京时间 (UTC+8)
"""
import sys
import os
from datetime import datetime, timezone, timedelta
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
MULTI_AGENT_ROOT = os.path.dirname(os.path.dirname(BASE))
ETF_TRACKER_ROOT = os.path.dirname(MULTI_AGENT_ROOT)
if MULTI_AGENT_ROOT not in sys.path:
    sys.path.insert(0, MULTI_AGENT_ROOT)
if ETF_TRACKER_ROOT not in sys.path:
    sys.path.insert(0, ETF_TRACKER_ROOT)
if os.path.join(MULTI_AGENT_ROOT, 'core') not in sys.path:
    sys.path.insert(0, os.path.join(MULTI_AGENT_ROOT, 'core'))

from watchlist import get_stocks_as_tuples
from data_layer import get_realtime_price, get_stock_data, calc_technical_indicators

# 北京时间
BJ_TZ = timezone(timedelta(hours=8))
REPORTS_DIR = os.path.expanduser('~/daily_tracker_analytics/etf_tracker/reports')


def bj_now():
    """获取当前北京时间"""
    return datetime.now(BJ_TZ)


def bj_date_str():
    return bj_now().strftime('%Y-%m-%d')


def bj_time_str():
    return bj_now().strftime('%H:%M')


def save_report(text, prefix='alert'):
    """保存报告到 reports/ 目录"""
    os.makedirs(REPORTS_DIR, exist_ok=True)
    date_str = bj_date_str().replace('-', '')
    time_str = bj_now().strftime('%H%M%S')
    path = os.path.join(REPORTS_DIR, '{}_{}_{}.md'.format(prefix, date_str, time_str))
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    return path


def check_realtime_alerts(stocks=None):
    """检查实时行情异动"""
    if stocks is None:
        stocks = get_stocks_as_tuples()
    
    alerts = []
    for ticker, name in stocks:
        try:
            import io
            from contextlib import redirect_stdout
            with io.StringIO() as buf, redirect_stdout(buf):
                rt = get_realtime_price(ticker)
            if not rt:
                continue
        except:
            continue
        
        price = rt['price']
        prev_close = rt.get('prev_close', 0) or price
        change_pct = ((price / prev_close) - 1) * 100 if prev_close > 0 else 0
        high = rt.get('high', 0)
        low = rt.get('low', 0)
        
        # 涨跌幅异动
        if change_pct >= 5.0:
            alerts.append({
                'time': bj_time_str(),
                'ticker': ticker, 'name': name,
                'price': price, 'change_pct': change_pct,
                'type': '快速拉升', 'detail': '涨幅{:.1f}%'.format(change_pct)
            })
        elif change_pct <= -5.0:
            alerts.append({
                'time': bj_time_str(),
                'ticker': ticker, 'name': name,
                'price': price, 'change_pct': change_pct,
                'type': '快速下跌', 'detail': '跌幅{:.1f}%'.format(change_pct)
            })
        
        # 振幅异动
        if high > 0 and low > 0:
            amplitude = ((high / low) - 1) * 100
            if amplitude >= 7.0:
                alerts.append({
                    'time': bj_time_str(),
                    'ticker': ticker, 'name': name,
                    'price': price, 'change_pct': change_pct,
                    'type': '大幅波动', 'detail': '振幅{:.1f}%'.format(amplitude)
                })
    
    return alerts


def check_price_level_alerts(stocks=None):
    """检查价格突破关键均线（静默模式）"""
    if stocks is None:
        stocks = get_stocks_as_tuples()
    
    # 临时关闭数据源日志
    import logging
    logging.getLogger().setLevel(logging.ERROR)
    
    alerts = []
    for ticker, name in stocks:
        try:
            # 静默获取数据
            import io
            from contextlib import redirect_stdout
            with io.StringIO() as buf, redirect_stdout(buf):
                df, _ = get_stock_data(ticker, calibrate=False)
                df = calc_technical_indicators(df)
            l = df.iloc[-1]
            cp = float(l['close'])
            
            for ma_key, ma_name in [('ma5','MA5'), ('ma10','MA10'), ('ma20','MA20')]:
                if pd.notna(l[ma_key]):
                    mv = float(l[ma_key])
                    if len(df) >= 2:
                        prev = df.iloc[-2]
                        prev_cp = float(prev['close'])
                        prev_mv = float(prev[ma_key]) if pd.notna(prev[ma_key]) else mv
                        if prev_cp <= prev_mv and cp > mv and abs(cp-mv)/mv < 0.02:
                            alerts.append({
                                'time': '收盘',
                                'ticker': ticker, 'name': name,
                                'price': cp, 'change_pct': 0,
                                'type': '技术突破', 'detail': '突破{}({:.2f})'.format(ma_name, mv)
                            })
                        elif prev_cp >= prev_mv and cp < mv and abs(cp-mv)/mv < 0.02:
                            alerts.append({
                                'time': '收盘',
                                'ticker': ticker, 'name': name,
                                'price': cp, 'change_pct': 0,
                                'type': '技术破位', 'detail': '跌破{}({:.2f})'.format(ma_name, mv)
                            })
        except:
            pass
    
    return alerts


def generate_report(alerts):
    """生成完整报告文本"""
    lines = []
    lines.append('## 盘中异动报告')
    lines.append('')
    lines.append('**日期**: {}'.format(bj_date_str()))
    lines.append('**时间**: {}'.format(bj_time_str()))
    lines.append('')
    
    if not alerts:
        lines.append('无异动')
        return '\n'.join(lines)
    
    # 按类型分组
    types = {}
    for a in alerts:
        t = a['type']
        if t not in types:
            types[t] = []
        types[t].append(a)
    
    # 按 alert 时间先后排序（整体）
    alerts = sorted(alerts, key=lambda x: (x.get('time', ''), x['ticker'], x['type']))
    
    # 按类型分组 + 每组按时间排序
    for t in sorted(types.keys()):
        items = sorted(types[t], key=lambda x: (x.get('time', ''), x['ticker'], x['type']))
        icon = {'快速拉升': '📈', '快速下跌': '📉', '大幅波动': '⚠️', '技术突破': '🟢', '技术破位': '🔴'}.get(t, '📌')
        lines.append('### {} {} ({}条)'.format(icon, t, len(items)))
        lines.append('')
        for a in items[:5]:
            chg_str = ' | {:+.2f}%'.format(a['change_pct']) if a['change_pct'] != 0 else ''
            lines.append('- **{}**({}): {} 当前{}{}'.format(
                a['name'], a['ticker'], a['detail'], a['price'], chg_str))
        lines.append('')
    
    lines.append('---')
    lines.append('⚠️ 仅供参考')
    return '\n'.join(lines)


def main(silent=False):
    """主入口"""
    alerts = []
    alerts.extend(check_realtime_alerts())
    alerts.extend(check_price_level_alerts())
    
    report = generate_report(alerts)
    
    # 保存报告
    if alerts:
        path = save_report(report, prefix='alert')
        print('📁 异动报告已保存: {}'.format(path))
    
    # 输出告警（供cron推送到微信）
    if alerts:
        wechat_lines = []
        wechat_lines.append('🚨 **盘中异动检测**')
        wechat_lines.append('🕐 {} {}'.format(bj_date_str(), bj_time_str()))
        wechat_lines.append('')
        for a in alerts[:5]:
            icon_map = {'快速拉升': '📈', '快速下跌': '📉', '大幅波动': '⚠️', '技术突破': '🟢', '技术破位': '🔴'}
            icon = icon_map.get(a['type'], '📌')
            wechat_lines.append('{} **{}**({}): {}'.format(icon, a['name'], a['ticker'], a['detail']))
            if a['change_pct'] != 0:
                wechat_lines.append('   当前 {} | {:+.2f}%'.format(a['price'], a['change_pct']))
        wechat_lines.append('')
        wechat_lines.append('⚠️ 仅供参考')
        print('\n'.join(wechat_lines))
    elif not silent:
        print('✅ {} 盘中无异动'.format(bj_time_str()))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='盘中异动检查')
    parser.add_argument('--silent', action='store_true', help='无异动时不输出')
    args = parser.parse_args()
    main(silent=args.silent)
