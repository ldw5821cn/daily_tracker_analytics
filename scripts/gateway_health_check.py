#!/usr/bin/env python3
"""
Gateway 平台状态监控脚本

检查 Gateway 是否运行、各平台连接状态、推送是否失败。
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def check_gateway_process():
    """检查 Gateway 进程"""
    result = subprocess.run(
        ['pgrep', '-a', '-f', 'gateway run'],
        capture_output=True,
        text=True
    )
    # 过滤掉 grep 自身和 bash wrapper
    lines = [
        l for l in result.stdout.strip().split('\n')
        if l and 'grep' not in l.lower() and 'hermes_cli.main' in l
    ]
    running = bool(lines)
    return {
        'running': running,
        'pid': lines[0].split()[0] if running else None,
        'cmd': lines[0] if running else None
    }


def check_gateway_log():
    """检查 Gateway 日志中的平台连接状态"""
    log_path = Path.home() / '.hermes' / 'logs' / 'gateway.log'
    if not log_path.exists():
        return {
            'log_exists': False,
            'platforms': [],
            'recent_errors': []
        }
    
    # 读取最近100行
    try:
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()[-200:]
    except Exception as e:
        return {
            'log_exists': True,
            'read_error': str(e),
            'platforms': [],
            'recent_errors': []
        }
    
    platforms = set()
    recent_errors = []
    
    for line in lines:
        if '[Weixin] Connected' in line or 'weixin: inbound' in line:
            platforms.add('weixin')
        if '[Feishu] Connected' in line:
            platforms.add('feishu')
        if 'Gateway running with' in line:
            platforms.add('gateway_ready')
        
        # 收集错误
        if 'ERROR' in line:
            if 'rate limited' in line.lower():
                recent_errors.append('微信限流')
            elif 'Provider authentication failed' in line:
                recent_errors.append('飞书认证失败')
            elif 'send failed' in line.lower():
                recent_errors.append('发送失败')
    
    return {
        'log_exists': True,
        'platforms': list(platforms),
        'recent_errors': list(set(recent_errors))
    }


def check_hermes_status():
    """使用 hermes status 检查"""
    try:
        result = subprocess.run(
            ['hermes', 'status'],
            capture_output=True,
            text=True,
            timeout=10
        )
        return {
            'success': result.returncode == 0,
            'output': result.stdout[:500]
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }


def main():
    """主函数"""
    print(f"=== Gateway 健康检查 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ===\n")
    
    process = check_gateway_process()
    log = check_gateway_log()
    status = check_hermes_status()
    
    # 进程状态
    if process['running']:
        print(f"✅ Gateway 运行中 (PID: {process['pid']})")
    else:
        print("❌ Gateway 未运行")
    
    # 平台连接
    print(f"\n平台连接状态:")
    if 'gateway_ready' in log['platforms']:
        print("  ✅ Gateway 已就绪")
    if 'weixin' in log['platforms']:
        print("  ✅ 微信已连接")
    else:
        print("  ⚠️ 微信未连接/无最近活动")
    if 'feishu' in log['platforms']:
        print("  ✅ 飞书已连接")
    else:
        print("  ⚠️ 飞书未连接/无最近活动")
    
    # 错误
    if log['recent_errors']:
        print(f"\n最近错误:")
        for error in log['recent_errors']:
            print(f"  ⚠️ {error}")
    else:
        print(f"\n✅ 最近无错误")
    
    # hermes status
    print(f"\nhermes status:")
    if status['success']:
        print(status['output'][:300])
    else:
        print(f"  ❌ {status.get('error', '状态检查失败')}")
    
    # 返回码
    if process['running'] and 'feishu' in log['platforms']:
        print("\n✅ Gateway 健康")
        return 0
    else:
        print("\n❌ Gateway 异常")
        return 1


if __name__ == '__main__':
    sys.exit(main())
