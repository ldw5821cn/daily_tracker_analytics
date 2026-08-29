#!/usr/bin/env python3
"""同步 hermes cron list 输出到 cron_status.json，并刷新 cron_status.html。

Usage:
    python3 multi_agent/scripts/update_cron_status.py [--pages-only]
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta

ROOT = '/home/liudawei/github/daily_tracker_analytics'
STATUS_PATH = os.path.join(ROOT, 'multi_agent', 'data', 'cron_status.json')
GENERATOR_PATH = os.path.join(ROOT, 'scripts', 'generate_pages.py')


def _parse_cron_list(text: str) -> list:
    """解析 hermes cron list 的纯文本输出。"""
    jobs = []
    current = None
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        # job header: "  <job_id> [active]" or "  <job_id> [paused]"
        m = re.match(r'^\s+([a-f0-9]+)\s+\[(\w+)\]', line)
        if m:
            if current:
                jobs.append(current)
            current = {
                'job_id': m.group(1),
                'state': m.group(2),
                'enabled': m.group(2) == 'active',
                'name': '',
                'schedule': '',
                'deliver': '',
                'next_run_at': '',
                'last_run_at': '',
                'last_status': '',
                'last_delivery_error': '',
                'script': '',
                'mode': '',
            }
            i += 1
            continue
        if current is None:
            i += 1
            continue
        # 键值行，统一格式
        kv = re.match(r'^\s+(\S[^:]+):\s*(.*)$', line)
        if kv:
            key = kv.group(1).strip()
            val = kv.group(2).strip()
            if key == 'Name':
                current['name'] = val
            elif key == 'Schedule':
                current['schedule'] = val
            elif key == 'Deliver':
                current['deliver'] = val
            elif key == 'Next run':
                current['next_run_at'] = val
            elif key == 'Last run':
                # 格式: 2026-08-28T08:22:23.067917+08:00  ok
                lm = re.match(r'^(\S+)\s+(.*)$', val)
                if lm:
                    current['last_run_at'] = lm.group(1)
                    rest = lm.group(2)
                    # 可能是 "ok" 或 "error: ..."
                    if rest.startswith('error:'):
                        current['last_status'] = 'error'
                        current['last_delivery_error'] = rest[len('error:'):].strip()
                    else:
                        current['last_status'] = 'ok'
                        current['last_delivery_error'] = ''
            elif key == 'Script':
                current['script'] = val
            elif key == 'Mode':
                current['mode'] = val
        # 投递失败警告行
        elif 'Delivery failed:' in line or 'delivery error:' in line:
            err = line.strip()
            if 'delivery error:' in err:
                err = err.split('delivery error:')[-1].strip()
            current['last_delivery_error'] = err
        i += 1
    if current:
        jobs.append(current)
    return jobs


def _fetch_cron_list() -> list:
    result = subprocess.run(
        ['hermes', 'cron', 'list'],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        print(f"hermes cron list failed: {result.stderr}", file=sys.stderr)
        raise RuntimeError(result.stderr)
    return _parse_cron_list(result.stdout)


def update_status() -> dict:
    jobs = _fetch_cron_list()
    tz = timezone(timedelta(hours=8))
    data = {
        'generated_at': datetime.now(tz).isoformat(),
        'count': len(jobs),
        'jobs': jobs,
    }
    os.makedirs(os.path.dirname(STATUS_PATH), exist_ok=True)
    with open(STATUS_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"已同步 {len(jobs)} 个 cron job 到 {STATUS_PATH}")
    return data


def refresh_pages():
    env = os.environ.copy()
    env['PYTHONPATH'] = os.path.join(ROOT, 'multi_agent')
    subprocess.run(
        [sys.executable, GENERATOR_PATH],
        cwd=ROOT, env=env, check=True, timeout=180,
    )
    print("已刷新 cron_status.html")


def main():
    parser = argparse.ArgumentParser(description='同步 hermes cron 状态到 Pages')
    parser.add_argument('--pages-only', action='store_true', help='仅刷新页面，不重新拉取 cron 列表')
    args = parser.parse_args()

    if not args.pages_only:
        update_status()
    refresh_pages()


if __name__ == '__main__':
    main()
