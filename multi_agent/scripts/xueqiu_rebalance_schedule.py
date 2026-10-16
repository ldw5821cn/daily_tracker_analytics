#!/usr/bin/env python3
"""预约雪球调仓 - 开盘自动执行"""
import json
import os
import sys
from datetime import datetime, timedelta

REPO = '/home/liudawei/github/daily_tracker_analytics'
sys.path.insert(0, REPO)

def schedule_rebalance():
    """设置预约调仓"""
    config_path = f'{REPO}/multi_agent/config/xueqiu_config.json'
    
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    # 标记需要调仓
    config['pending_rebalance'] = {
        'portfolio': 'ZH3680472',
        'scheduled_at': '2026-10-19 09:30:00',
        'status': 'pending',
        'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }
    
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    
    print('✅ 已设置预约调仓')
    print(f'   组合: ZH3680472')
    print(f'   时间: 2026-10-19 09:30:00')
    print()
    print('开盘后将自动执行同步')

if __name__ == '__main__':
    schedule_rebalance()
