#!/usr/bin/env python3
"""合并分片 revenue JSON 为单个文件。"""
import glob
import json
import os
from datetime import datetime

CACHE_DIR = '/home/liudawei/github/daily_tracker_analytics/multi_agent/data/fundamentals_cache'

def main():
    today = datetime.now().strftime('%Y-%m-%d')
    files = sorted(glob.glob(f'{CACHE_DIR}/{today}_revenue_*.json'))
    merged = {}
    for path in files:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        merged.update(data.get('data', {}))

    output = {
        "date": today,
        "count": len(merged),
        "data": merged,
    }
    out_path = f'{CACHE_DIR}/{today}_revenue.json'
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f'合并完成: {out_path}，共 {len(merged)} 只')
    # 清理分片
    for path in files:
        os.remove(path)
        print(f'已删除 {path}')


if __name__ == '__main__':
    main()
