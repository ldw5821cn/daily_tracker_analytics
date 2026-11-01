#!/usr/bin/env python3
"""
Cron 健康检查脚本

检查所有 Hermes cron 任务是否正常运行：
- 任务是否过期未执行
- 最近执行是否失败
- 推送是否失败
"""

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def get_cron_list():
    """获取 cron 任务列表（解析文本）"""
    try:
        result = subprocess.run(
            ['hermes', 'cron', 'list'],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode != 0:
            print(f"❌ 无法获取 cron 列表: {result.stderr}")
            return []
        return parse_cron_output(result.stdout)
    except Exception as e:
        print(f"❌ 获取 cron 列表失败: {e}")
        return []


def parse_cron_output(output):
    """解析 hermes cron list 文本输出"""
    jobs = []
    current = None
    
    for raw_line in output.split('\n'):
        line = raw_line.rstrip()
        if not line:
            continue
        
        # 新任务开始：任务ID行格式为 "  e350db62fac6 [active]"（2个空格）
        # 其他属性行是4个空格，需区分
        if line.startswith('  ') and not line.startswith('    ') and '[' in line and ']' in line:
            if current:
                jobs.append(current)
            parts = line.split()
            job_id = parts[0].strip()
            status = parts[1].strip('[]')
            current = {
                'id': job_id,
                'status': status,
                'name': '',
                'schedule': '',
                'last_run_at': '',
                'last_run_status': '',
                'delivery_failed': False,
                'delivery_error': ''
            }
        elif current and line.startswith('Name:'):
            current['name'] = line.split(':', 1)[1].strip()
        elif current and line.startswith('Schedule:'):
            current['schedule'] = line.split(':', 1)[1].strip()
        elif current and line.startswith('Last run:'):
            # 解析: 2026-10-30T08:06:58.105855+08:00  ok
            rest = line.split(':', 1)[1].strip()
            if rest:
                parts = rest.split('  ', 1)
                current['last_run_at'] = parts[0].strip()
                if len(parts) > 1:
                    current['last_run_status'] = parts[1].strip()
        elif current and 'Delivery failed' in line:
            current['delivery_failed'] = True
            current['delivery_error'] += line + '; '
    
    if current:
        jobs.append(current)
    
    return jobs


def parse_time(time_str):
    """解析 ISO 时间字符串"""
    if not time_str:
        return None
    try:
        # 处理带时区的时间
        return datetime.fromisoformat(time_str.replace('Z', '+00:00'))
    except Exception:
        return None


def check_job_health(job):
    """检查单个任务健康状态"""
    issues = []
    now = datetime.now(timezone.utc)
    
    # 检查最后执行时间
    last_run = parse_time(job.get('last_run_at'))
    if last_run:
        hours_since_last = (now - last_run).total_seconds() / 3600
        schedule = job.get('schedule', '')
        
        # 根据调度频率判断
        if 'every 120m' in schedule or 'every' in schedule:
            # 高频任务
            if hours_since_last > 4:
                issues.append(f"超过4小时未执行 (已 {hours_since_last:.1f} 小时)")
        elif '15 8' in schedule or '30 8' in schedule or '45 8' in schedule or '50 8' in schedule:
            # 盘前任务
            if hours_since_last > 6:
                issues.append(f"盘前任务超过6小时未执行")
        elif '15 30' in schedule or '35 15' in schedule or '40 15' in schedule:
            # 盘后任务
            if hours_since_last > 6:
                issues.append(f"盘后任务超过6小时未执行")
        elif '0 9' in schedule or '0 8' in schedule:
            # 每日任务
            if hours_since_last > 26:
                issues.append(f"每日任务超过26小时未执行")
    
    # 检查最后执行状态
    last_status = job.get('last_run_status', '')
    if last_status and 'ok' not in str(last_status).lower():
        issues.append(f"上次执行状态异常: {last_status}")
    
    # 检查推送失败
    if job.get('delivery_failed'):
        error = job.get('delivery_error', '')
        if 'weixin' in error.lower():
            issues.append("微信推送失败")
        if 'feishu' in error.lower():
            issues.append("飞书推送失败")
    
    return issues


def main():
    """主函数"""
    print(f"=== Cron 健康检查 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ===\n")
    
    jobs = get_cron_list()
    if not jobs:
        sys.exit(1)
    
    healthy_count = 0
    warning_count = 0
    
    for job in jobs:
        name = job.get('name', 'Unknown')
        job_id = job.get('id', 'Unknown')
        issues = check_job_health(job)
        
        if issues:
            warning_count += 1
            print(f"⚠️ {name} ({job_id[:12]})")
            for issue in issues:
                print(f"   - {issue}")
        else:
            healthy_count += 1
    
    print(f"\n{'='*50}")
    print(f"健康任务: {healthy_count}")
    print(f"异常任务: {warning_count}")
    print(f"总计: {len(jobs)}")
    
    if warning_count > 0:
        sys.exit(1)
    else:
        print("\n✅ 所有 cron 任务健康")


if __name__ == '__main__':
    main()
