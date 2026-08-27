#!/usr/bin/env python3
"""价格预警检查脚本：监控 ETF/个股是否跌破年线，触发后推送通知。

用法：
    . etf_tracker/.venv/bin/activate
    python3 etf_tracker/scripts/check_price_alerts.py

配置文件：etf_tracker/watchlist_price_alerts.json
数据源优先级：腾讯 K线（前复权）→ akshare fallback
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import requests

ROOT = Path('/home/liudawei/github/daily_tracker_analytics')
ALERT_FILE = ROOT / 'etf_tracker' / 'watchlist_price_alerts.json'


def fetch_tencent_kline(code: str, exchange: str = 'sh', days: int = 500) -> Optional[Dict]:
    """从腾讯接口获取前复权日线，返回 DataFrame 所需结构。"""
    symbol = f"{exchange}{code}"
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,,,{days},qfq"
    try:
        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
        resp.raise_for_status()
        raw = resp.json()
        # 腾讯返回结构: {'code':..., 'msg':..., 'data': {symbol: {'qfqday': [...], 'qt':...}}}
        stock_data = raw.get('data', {}).get(symbol) if isinstance(raw.get('data'), dict) else None
        if not isinstance(stock_data, dict):
            return None
        klines = stock_data.get('qfqday') or stock_data.get('day')
        if not klines or len(klines) < 250:
            return None
        return {
            'dates': [k[0] for k in klines],
            'closes': [float(k[2]) for k in klines],
        }
    except Exception as e:
        print(f"  腾讯K线失败 {code}: {e}")
        return None


def fetch_akshare_kline(code: str, days: int = 500) -> Optional[Dict]:
    """akshare fallback。"""
    try:
        import akshare as ak
        start_date = (datetime.now() - timedelta(days=days * 2)).strftime('%Y%m%d')
        df = ak.fund_etf_hist_em(symbol=code, period='daily', start_date=start_date, adjust='qfq')
        if df is None or len(df) < 250:
            return None
        return {
            'dates': df['日期'].astype(str).tolist(),
            'closes': df['收盘'].astype(float).tolist(),
        }
    except Exception as e:
        print(f"  akshare fallback 失败 {code}: {e}")
        return None


def load_alerts() -> List[Dict]:
    if not ALERT_FILE.exists():
        return []
    with open(ALERT_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_alerts(alerts: List[Dict]):
    with open(ALERT_FILE, 'w', encoding='utf-8') as f:
        json.dump(alerts, f, ensure_ascii=False, indent=2)


def send_notification(title: str, body: str):
    """优先使用 hermes_wechat_pusher，否则打印。"""
    wechat_path = ROOT / 'etf_tracker' / 'hermes_wechat_pusher.py'
    if wechat_path.exists():
        import subprocess
        try:
            # hermes_wechat_pusher 本身不提供命令行参数，直接 import 调用
            sys.path.insert(0, str(ROOT / 'etf_tracker'))
            from hermes_wechat_pusher import HermesWeChatPusher
            pusher = HermesWeChatPusher()
            pusher.send_text_message(f"{title}\n\n{body}")
            return
        except Exception as e:
            print(f"  微信推送失败: {e}")
    print(f"[NOTIFY] {title}\n{body}")


def check_alerts():
    alerts = load_alerts()
    today = datetime.now().strftime('%Y-%m-%d')
    triggered_any = False

    for alert in alerts:
        code = alert['code']
        exchange = alert.get('exchange', 'sh').lower()
        name = alert.get('name', code)
        condition = alert.get('condition', 'close < ma250')

        data = fetch_tencent_kline(code, exchange)
        if data is None:
            data = fetch_akshare_kline(code)
        if data is None or len(data['closes']) < 250:
            print(f"[{code}] 数据不足，跳过")
            alert['last_checked'] = today
            continue

        closes = data['closes']
        ma250 = sum(closes[-250:]) / 250
        latest_close = closes[-1]
        latest_date = data['dates'][-1]

        alert['last_checked'] = today
        alert['last_close'] = latest_close
        alert['last_ma250'] = ma250
        alert['last_date'] = latest_date

        is_triggered = False
        if condition == 'close < ma250':
            is_triggered = latest_close < ma250
        elif condition == 'close > ma250':
            is_triggered = latest_close > ma250

        prev_triggered = alert.get('triggered', False)
        if is_triggered and not prev_triggered:
            title = f"🛎️ {name}({code}) 跌破年线"
            body = (
                f"日期：{latest_date}\n"
                f"收盘价：{latest_close:.3f}\n"
                f"年线(MA250)：{ma250:.3f}\n"
                f"偏离：{(latest_close / ma250 - 1) * 100:.2f}%\n\n"
                f"用户设定：跌破年线时通知买入。"
            )
            send_notification(title, body)
            alert['triggered'] = True
            alert['triggered_at'] = today
            triggered_any = True
            print(f"[{code}] ✅ 触发预警: 收盘 {latest_close} < 年线 {ma250}")
        elif not is_triggered and prev_triggered:
            # 恢复后重置触发状态，允许下次再次触发
            alert['triggered'] = False
            alert.pop('triggered_at', None)
            print(f"[{code}] 已重新站上年线，重置触发状态")
        else:
            status = "已触发" if is_triggered else "未触发"
            print(f"[{code}] {status}: 收盘 {latest_close}, 年线 {ma250}, 偏离 {(latest_close / ma250 - 1) * 100:.2f}%")

    save_alerts(alerts)
    return triggered_any


if __name__ == '__main__':
    check_alerts()
