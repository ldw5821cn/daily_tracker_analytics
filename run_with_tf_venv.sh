#!/bin/bash
# 使用 TensorFlow venv 运行 ETF 跟踪系统
source /home/zhihu/tf_venv/bin/activate
cd /home/zhihu/daily-_tracker_analytics/etf_tracker
python3 etf_tracker.py "$@"
