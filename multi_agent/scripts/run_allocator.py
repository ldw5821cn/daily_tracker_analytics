"""CLI: 运行目标权重分配器。

Usage:
    python multi_agent/scripts/run_allocator.py
"""
import sys
import os

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'multi_agent'))

from strategy.portfolio_allocator import allocate, OUTPUT_PATH

if __name__ == '__main__':
    result = allocate()
    if 'error' in result:
        print(f"❌ {result['error']}")
        sys.exit(1)
    print(f"✅ 目标权重已保存: {OUTPUT_PATH}")
    print(f"   总敞口 {result['total_exposure']:.2%} | 做多 {result['long_exposure']:.2%} | 做空 {result['short_exposure']:.2%} | 净敞口 {result['net_exposure']:.2%}")
    print(f"   标的数: {result['total_targets']} (多{result['long_targets']}/空{result['short_targets']})")
