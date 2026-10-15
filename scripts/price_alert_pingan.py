#!/usr/bin/env python3
"""
平安银行价格监控
触发条件:
  - 跌到 11.20 → 补仓机会
  - 跌到 11.10 → 更好机会
  - 跌破 11.27 → 止损警告
"""

import sys
import os
sys.path.insert(0, '/home/liudawei/github/daily_tracker_analytics/multi_agent')
os.chdir('/home/liudawei/github/daily_tracker_analytics')

from hithink_client import HiThinkFinanceClient

def check_price():
    client = HiThinkFinanceClient()
    
    # 获取平安银行K线
    kline = client.get_kline('000001.SZ', '2026-10-01', '2026-10-15', interval='1d')
    
    if not kline:
        print("❌ 无法获取数据")
        return
    
    current = kline[-1].get('close_price', 0)
    print(f"平安银行当前价格: {current:.2f}")
    
    alerts = []
    
    # 检查触发条件
    if current <= 11.10:
        alerts.append(f"🟢 极佳补仓机会! 价格 {current:.2f} <= 11.10")
    elif current <= 11.20:
        alerts.append(f"🟡 补仓机会! 价格 {current:.2f} <= 11.20")
    
    if current < 11.27:
        alerts.append(f"🔴 止损警告! 价格 {current:.2f} 跌破支撑位 11.27")
    
    # 输出结果
    if alerts:
        print("\n⚠️  触发提醒:")
        for alert in alerts:
            print(f"  {alert}")
        sys.exit(1)  # 非零退出码表示触发提醒
    else:
        print(f"\n✅ 价格正常 (11.20-11.30 区间)")

if __name__ == '__main__':
    check_price()
