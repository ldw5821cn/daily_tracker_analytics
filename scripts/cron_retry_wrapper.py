#!/usr/bin/env python3
"""
Cron 任务重试 wrapper
- 执行指定命令，失败时自动重试
- 将任务与 Gateway 推送解耦：先本地保存结果，再尝试推送
"""
import subprocess
import sys
import time
import os
import json
import argparse
from datetime import datetime

LOCK_DIR = os.path.expanduser("~/.hermes/cron/locks")
RESULT_DIR = os.path.expanduser("~/.hermes/cron/results")
os.makedirs(LOCK_DIR, exist_ok=True)
os.makedirs(RESULT_DIR, exist_ok=True)


def run_with_retry(cmd, max_retries=3, delay_sec=120, timeout=600):
    """执行命令，失败后重试"""
    for attempt in range(1, max_retries + 1):
        print(f"[retry] Attempt {attempt}/{max_retries}: {' '.join(cmd)}")
        try:
            result = subprocess.run(
                cmd,
                timeout=timeout,
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                print("[retry] Success")
                return result
            else:
                print(f"[retry] Failed with code {result.returncode}")
                print(result.stdout[-1000:] if result.stdout else "")
                print(result.stderr[-1000:] if result.stderr else "")
        except subprocess.TimeoutExpired as e:
            print(f"[retry] Timeout after {timeout}s")
            if e.stdout:
                print(e.stdout[-1000:])
            if e.stderr:
                print(e.stderr[-1000:])
        except Exception as e:
            print(f"[retry] Exception: {e}")

        if attempt < max_retries:
            print(f"[retry] Waiting {delay_sec}s before retry...")
            time.sleep(delay_sec)

    return None


def acquire_lock(task_name):
    lock_file = os.path.join(LOCK_DIR, f"{task_name}.lock")
    if os.path.exists(lock_file):
        # 检查锁是否过期（超过2小时）
        mtime = os.path.getmtime(lock_file)
        if time.time() - mtime < 7200:
            print(f"[retry] Task {task_name} already running, skip")
            return False
        else:
            print(f"[retry] Stale lock found, remove it")
            os.remove(lock_file)
    with open(lock_file, 'w') as f:
        f.write(str(os.getpid()))
    return True


def release_lock(task_name):
    lock_file = os.path.join(LOCK_DIR, f"{task_name}.lock")
    if os.path.exists(lock_file):
        os.remove(lock_file)


def save_result(task_name, success, output):
    result_file = os.path.join(RESULT_DIR, f"{task_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(result_file, 'w', encoding='utf-8') as f:
        json.dump({
            'task': task_name,
            'time': datetime.now().isoformat(),
            'success': success,
            'output': output[-3000:] if output else ''
        }, f, ensure_ascii=False, indent=2)
    return result_file


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--name', required=True, help='任务名称')
    parser.add_argument('--max-retries', type=int, default=3)
    parser.add_argument('--delay', type=int, default=120)
    parser.add_argument('--timeout', type=int, default=600)
    parser.add_argument('cmd', nargs='+', help='要执行的命令')
    args = parser.parse_args()

    if not acquire_lock(args.name):
        sys.exit(0)

    try:
        result = run_with_retry(
            args.cmd,
            max_retries=args.max_retries,
            delay_sec=args.delay,
            timeout=args.timeout
        )
        success = result is not None
        output = result.stdout if result else 'All retries failed'
        result_file = save_result(args.name, success, output)
        print(f"[retry] Result saved to {result_file}")

        if not success:
            print("[retry] Task failed after all retries")
            sys.exit(1)
    finally:
        release_lock(args.name)


if __name__ == '__main__':
    main()
