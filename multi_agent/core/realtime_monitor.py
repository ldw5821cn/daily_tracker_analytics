"""
盘中实时异动监控 - 基于腾讯证券实时行情

策略：
1. 价格异动：盘中涨跌幅超过阈值
2. 量能异动：成交量放大超过阈值
3. 价格突破：实时价突破关键位置（MA5/MA10/布林）
4. 盘中雪崩：快速下跌超过阈值

运行方式：
- 每个 tick 通过腾讯 API 获取所有关注标的实时行情
- 与上次快照对比，检测异动
- 输出异动报告（可推送到微信）
"""
import sys
import os
import json
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, '/home/zhihu/daily_tracker_analytics/etf_tracker/multi_agent')
from core.data_layer import get_realtime_price, get_stock_data, calc_technical_indicators
from core.watchlist import get_stocks_as_tuples


# ==================== 监控配置 ====================

DEFAULT_WATCH_LIST = None  # 运行时从 watchlist 加载

ALERT_CONFIG = {
    'price_up_pct': 3.0,     # 单日涨幅 > 3% 告警
    'price_down_pct': -3.0,  # 单日跌幅 < -3% 告警
    'volume_ratio': 2.0,     # 成交量相对前日放大倍数
    'amplitude_pct': 5.0,    # 振幅 > 5% 告警
    'consecutive_drops': 3,  # 连续N个tick下跌
}


class RealtimeMonitor:
    """盘中实时异动监控"""
    
    def __init__(self, watch_list=None, config=None):
        self.watch_list = watch_list or get_stocks_as_tuples()
        self.config = config or ALERT_CONFIG
        self.snapshots = {}         # ticker -> 上次快照
        self.consecutive_drops = {} # ticker -> 连续下跌次数
        self.alerts_log = []        # 历史告警
        self.prev_closes = {}       # 前收盘价
        self._load_prev_closes()
    
    def _load_prev_closes(self):
        """从历史数据加载前收盘价"""
        for ticker, name in self.watch_list:
            try:
                rt = get_realtime_price(ticker)
                if rt:
                    self.prev_closes[ticker] = rt.get('prev_close', 0)
            except:
                pass
    
    def check(self):
        """
        执行一次监控检查
        
        Returns:
            list of alert dicts
        """
        alerts = []
        
        for ticker, name in self.watch_list:
            try:
                rt = get_realtime_price(ticker)
                if not rt:
                    continue
                
                price = rt['price']
                prev_close = rt['prev_close'] or self.prev_closes.get(ticker, price)
                change_pct = ((price / prev_close) - 1) * 100 if prev_close > 0 else 0
                
                # ========== 检测条件 ==========
                
                # 1. 涨跌幅异动
                if change_pct >= self.config['price_up_pct']:
                    alerts.append(self._make_alert(ticker, name, price, change_pct, 
                                                    "📈 快速拉升", f"涨幅{change_pct:.1f}%"))
                elif change_pct <= self.config['price_down_pct']:
                    alerts.append(self._make_alert(ticker, name, price, change_pct,
                                                    "📉 快速下跌", f"跌幅{change_pct:.1f}%"))
                
                # 2. 振幅异动（从实时high/low看）
                high = rt.get('high', 0)
                low = rt.get('low', 0)
                if high > 0 and low > 0:
                    amplitude = ((high / low) - 1) * 100
                    if amplitude >= self.config['amplitude_pct']:
                        alerts.append(self._make_alert(ticker, name, price, change_pct,
                                                        "⚠️ 大幅波动", f"振幅{amplitude:.1f}%"))
                
                # 3. 连续下跌检测
                if self.snapshots.get(ticker):
                    prev_price = self.snapshots[ticker].get('price', price)
                    if price < prev_price:
                        count = self.consecutive_drops.get(ticker, 0) + 1
                        self.consecutive_drops[ticker] = count
                        if count == self.config['consecutive_drops']:
                            alerts.append(self._make_alert(ticker, name, price, change_pct,
                                                            "↘️ 连续下跌", f"连跌{count}个tick"))
                    else:
                        self.consecutive_drops[ticker] = 0
                else:
                    self.consecutive_drops[ticker] = 0
                
                # 更新快照
                self.snapshots[ticker] = rt
                
            except Exception as e:
                pass
        
        self.alerts_log.extend(alerts)
        return alerts
    
    def _make_alert(self, ticker, name, price, change_pct, alert_type, detail):
        return {
            'time': datetime.now().strftime('%H:%M:%S'),
            'ticker': ticker,
            'name': name,
            'price': price,
            'change_pct': round(change_pct, 2),
            'type': alert_type,
            'detail': detail,
        }
    
    def run_cycle(self, interval=60, max_cycles=0):
        """
        持续监控循环
        
        Args:
            interval: 检查间隔（秒）
            max_cycles: 最大轮次（0=无限）
        """
        cycle = 0
        print(f"🔍 实时异动监控启动 | 标的: {len(self.watch_list)}个 | 间隔: {interval}s")
        print(f"   阈值: 涨幅>{self.config['price_up_pct']}% 跌幅<{self.config['price_down_pct']}% 振幅>{self.config['amplitude_pct']}%")
        print(f"   {'─'*50}")
        
        try:
            while True:
                cycle += 1
                now = datetime.now().strftime('%H:%M:%S')
                
                # 批量获取所有标的实时行情
                start = time.time()
                alerts = self.check()
                elapsed = time.time() - start
                
                # 打印状态
                status_parts = []
                for ticker, name in self.watch_list:
                    rt = self.snapshots.get(ticker)
                    if rt:
                        pc = rt.get('prev_close', 0)
                        cp = rt['price']
                        chg = ((cp / pc) - 1) * 100 if pc > 0 else 0
                        icon = "🟢" if chg > 0 else "🔴" if chg < 0 else "⚪"
                        status_parts.append(f"{icon}{name}({cp}){chg:+.1f}%")
                
                print(f"  [{now}] #{cycle} | {' | '.join(status_parts)} | {elapsed:.1f}s")
                
                # 输出异动
                for a in alerts:
                    print(f"  🚨 [{a['time']}] {a['type']} {a['name']}({a['ticker']}) {a['detail']}")
                
                if max_cycles > 0 and cycle >= max_cycles:
                    print(f"\n  ✅ 完成 {max_cycles} 轮监控")
                    break
                
                time.sleep(interval)
        
        except KeyboardInterrupt:
            print(f"\n  🛑 监控停止")
        
        return self.alerts_log
    
    def get_status_text(self):
        """获取当前状态文本（可用于微信推送）"""
        lines = []
        lines.append(f"📡 **盘中实时监控**")
        lines.append(f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")
        
        for ticker, name in self.watch_list:
            rt = self.snapshots.get(ticker)
            if rt:
                pc = rt.get('prev_close', 0)
                cp = rt['price']
                chg = ((cp / pc) - 1) * 100 if pc > 0 else 0
                icon = "🟢" if chg > 0 else "🔴" if chg < 0 else "⚪"
                lines.append(f"{icon} **{name}**: {cp} ({chg:+.2f}%)")
        
        lines.append("")
        if self.alerts_log:
            recent = self.alerts_log[-5:]
            lines.append(f"⚠️ 最近异动 ({len(recent)}条):")
            for a in recent:
                lines.append(f"  · {a['type']} {a['name']}: {a['detail']}")
        
        return "\n".join(lines)


def monitor_realtime(stocks=None, interval=60, cycles=0):
    """便捷启动实时监控"""
    monitor = RealtimeMonitor(stocks, ALERT_CONFIG)
    return monitor.run_cycle(interval, cycles)


def get_realtime_status(stocks=None):
    """获取一次实时行情快照"""
    if stocks is None:
        stocks = get_stocks_as_tuples()
    
    lines = []
    lines.append(f"📡 **实时行情快照**")
    lines.append(f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    
    for ticker, name in stocks:
        rt = get_realtime_price(ticker)
        if rt:
            cp = rt['price']
            pc = rt.get('prev_close', 0)
            chg = ((cp / pc) - 1) * 100 if pc > 0 else 0
            vol = rt.get('volume', 0)
            pe = rt.get('pe', 0)
            
            icon = "🟢" if chg >= 0 else "🔴"
            lines.append(f"{icon} **{name}({ticker})**: {cp}  ({chg:+.2f}%)")
            lines.append(f"   昨收{pc} | 量{vol/10000:.0f}万 | PE{pe:.1f}")
        else:
            lines.append(f"❌ **{name}({ticker})**: 获取失败")
    
    lines.append("")
    lines.append("⚠️ 数据来自腾讯证券，实时参考")
    
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='盘中实时异动监控')
    parser.add_argument('--mode', '-m', choices=['once', 'monitor'], default='once',
                        help='once=快照 monitor=持续监控')
    parser.add_argument('--interval', '-i', type=int, default=60, help='监控间隔(秒)')
    parser.add_argument('--cycles', '-c', type=int, default=0, help='监控轮次(0=无限)')
    
    args = parser.parse_args()
    
    if args.mode == 'once':
        print(get_realtime_status())
    else:
        monitor_realtime(interval=args.interval, cycles=args.cycles)
