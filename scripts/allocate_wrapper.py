"""allocate 降级 wrapper：跳过 factor_scoring 与 yfinance 数据校验（2026-08-21）。
multi_agent 不是包，allocator 内部用 from core.xxx / from strategy.xxx 导入，
monkeypatch 必须针对 core.* / strategy.* 模块对象。
"""
import sys, os
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, 'multi_agent'))

import strategy.factor_scoring as fs
fs.compute_factor_scores = lambda tickers: {}   # factor_multiplier 退化为 1.0

import core.data_layer as dl
dl._verify_data = lambda *a, **k: None          # 跳过 yfinance 校验（挂起点）

from strategy.portfolio_allocator import allocate
allocate()
print("ALLOCATE_DONE")
