#!/bin/bash
# 分 5 批并行拉取主营构成数据
set -e
cd /home/liudawei/github/daily_tracker_analytics
. etf_tracker/.venv/bin/activate

for b in 0 1 2 3 4; do
  python3 multi_agent/scripts/fetch_revenue_composition.py \
    --batch $b --batches 5 \
    --output multi_agent/data/fundamentals_cache/2026-08-24_revenue_${b}.json > /tmp/revenue_batch_${b}.log 2>&1 &
done
wait

echo "All batches done"
for b in 0 1 2 3 4; do
  tail -1 /tmp/revenue_batch_${b}.log
done
