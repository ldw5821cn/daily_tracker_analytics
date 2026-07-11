# LLM 信号组合回测评估 Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** 用 vectorbt 对当前 `agentic_predictions` 表中的 LLM 信号做组合级回测，评估多/空/中性信号的胜率、夏普、最大回撤、Calmar，并与买入持有/沪深 300 对比。

**Architecture:** 新增独立模块 `multi_agent/strategy/backtest_engine.py`，读取 `agentic_predictions` + 历史行情，构造按日调仓的 vectorbt 组合，输出指标和图表。CLI 脚本 `multi_agent/scripts/run_llm_backtest.py` 一键运行。不改动现有预测流程，只新增只读评估层。

**Tech Stack:** Python 3.12, vectorbt, pandas, polars, sqlite3, yfinance/akshare。

---

### Task 1: 确认数据schema与安装vectorbt

**Objective:** 确认 `agentic_predictions` 表字段和 `watchlist.json` 映射，并安装 vectorbt 到 etf_tracker venv。

**Files:**
- Read: `multi_agent/data/llm_predictions.db` schema
- Read: `multi_agent/watchlist.json`
- Read: `etf_tracker/.venv/bin/python` availability

**Step 1:** 检查 `agentic_predictions` 表结构
```bash
cd /home/liudawei/github/daily_tracker_analytics
. etf_tracker/.venv/bin/activate
python - <<'PY'
import sqlite3
conn = sqlite3.connect('multi_agent/data/llm_predictions.db')
for row in conn.execute("PRAGMA table_info(agentic_predictions)"):
    print(row)
for row in conn.execute("SELECT * FROM agentic_predictions WHERE pred_date='2026-07-11' LIMIT 3"):
    print(row)
PY
```
Expected: 看到字段名、至少 3 条样例。

**Step 2:** 安装 vectorbt
```bash
. etf_tracker/.venv/bin/activate
uv pip install vectorbt --quiet
python -c "import vectorbt as vbt; print(vbt.__version__)"
```
Expected: 版本号输出，无报错。

**Step 3:** Commit
```bash
git add -A
git commit -m "chore: add vectorbt dependency for LLM signal backtest" || true
```

---

### Task 2: 创建行情获取函数

**Objective:** 写一个只读函数，根据 `ticker` 和 `category` 拉取历史 OHLCV，支持 ETF/个股/期货。

**Files:**
- Create: `multi_agent/strategy/backtest_engine.py` (initial version)

**Step 1:** 写函数 `fetch_ohlcv(ticker, category, start, end)`
- 个股/ETF: 用 akshare 或 yfinance 获取日线
- 期货: 用 `futures_simulator.fetch_futures_price` 或 akshare 期货主力连续合约
- 返回 pandas DataFrame with columns: `open, high, low, close, volume`

```python
def fetch_ohlcv(ticker: str, category: str, start: str, end: str) -> pd.DataFrame:
    ...
```

**Step 2:** 在 `backtest_engine.py` 底部加测试代码
```python
if __name__ == '__main__':
    df = fetch_ohlcv('MA', 'future', '2026-01-01', '2026-07-11')
    print(df.tail())
    df = fetch_ohlcv('588200', 'ETF', '2026-01-01', '2026-07-11')
    print(df.tail())
```

**Step 3:** 运行
```bash
python multi_agent/strategy/backtest_engine.py
```
Expected: 打印两个 DataFrame 的尾部。

**Step 4:** Commit
```bash
git add multi_agent/strategy/backtest_engine.py
git commit -m "feat: add ohlcv fetcher for etf/stock/future backtest"
```

---

### Task 3: 读取预测信号并构造目标权重

**Objective:** 从 `agentic_predictions` 读取最近 N 日预测，按信号生成每日目标权重。

**Files:**
- Modify: `multi_agent/strategy/backtest_engine.py`

**Step 1:** 写函数 `load_signals(pred_date=None, horizon=1)`
- 从 `agentic_predictions` 读取指定日期信号
- 返回 DataFrame: columns `date, ticker, category, signal, confidence`

**Step 2:** 写函数 `build_target_weights(signals_df, mode='equal')`
- `mode='equal'`: bullish 标的等权，bearish 等权做空，neutral 权重 0
- 按 category 分别处理：ETF、个股、期货各自成组合，避免混算

```python
def build_target_weights(signals_df: pd.DataFrame, mode='equal') -> pd.DataFrame:
    ...
```

**Step 3:** 在 `__main__` 中测试
```python
signals = load_signals('2026-07-11')
weights = build_target_weights(signals, mode='equal')
print(weights.head(20))
```

**Step 4:** 运行并验证
```bash
python multi_agent/strategy/backtest_engine.py
```
Expected: 看到权重表，多/空/中性区分正确。

**Step 5:** Commit
```bash
git add multi_agent/strategy/backtest_engine.py
git commit -m "feat: load LLM signals and build target weights"
```

---

### Task 4: 用 vectorbt 跑组合回测

**Objective:** 实现 `run_backtest(weights_df, prices_df)`，输出回测指标。

**Files:**
- Modify: `multi_agent/strategy/backtest_engine.py`

**Step 1:** 写函数 `run_backtest(weights_df, prices_df, freq='1d')`
- 用 `vbt.Portfolio.from_orders` 或 `from_holding` 做对比
- 输入：每日目标权重矩阵（ticker × date），价格 close
- 输出：dict with `total_return, sharpe, max_drawdown, calmar, win_rate, trades`

```python
def run_backtest(weights_df: pd.DataFrame, prices_df: pd.DataFrame, freq='1d') -> dict:
    ...
```

**Step 2:** 写基准对比：买入持有（所有信号等权买入）和沪深 300（ETF 组合用）

**Step 3:** 在 `__main__` 中跑 ETF/个股/期货三个组合
```python
for category in ['ETF', '个股', '期货']:
    sig = signals[signals.category == category]
    weights = build_target_weights(sig)
    prices = ...  # 拉取该 category 所有标的 close
    result = run_backtest(weights, prices)
    print(category, result)
```

**Step 4:** 运行
```bash
python multi_agent/strategy/backtest_engine.py
```
Expected: 三个 category 的回测指标打印出来。

**Step 5:** Commit
```bash
git add multi_agent/strategy/backtest_engine.py
git commit -m "feat: vectorbt backtest engine for LLM signals"
```

---

### Task 5: 创建 CLI 脚本

**Objective:** 新增 `multi_agent/scripts/run_llm_backtest.py`，可配置 date/category/mode。

**Files:**
- Create: `multi_agent/scripts/run_llm_backtest.py`

**Step 1:** 写 CLI
```python
parser.add_argument('--date', default='latest')
parser.add_argument('--category', default='ETF,个股,期货')
parser.add_argument('--mode', default='equal', choices=['equal', 'confidence'])
parser.add_argument('--output', default='multi_agent/data/llm_backtest_results.json')
```

**Step 2:** 运行
```bash
python multi_agent/scripts/run_llm_backtest.py --category ETF --date 2026-07-11
```
Expected: 输出 JSON 文件 + 终端打印指标。

**Step 3:** Commit
```bash
git add multi_agent/scripts/run_llm_backtest.py
git commit -m "feat: CLI for LLM signal backtest"
```

---

### Task 6: 接入日报和页面展示

**Objective:** 把回测结果展示在 `prediction.html` 和微信日报里。

**Files:**
- Modify: `scripts/generate_pages.py`
- Modify: `multi_agent/scripts/daily_agentic_report.py`

**Step 1:** 在 `run_llm_backtest.py` 输出 JSON 后，generate_pages 读取该 JSON 并加入回测指标区块。

**Step 2:** 日报中加入一句话：
> "LLM 信号组合回测：ETF 夏普 X / 最大回撤 Y；个股 ...；期货 ..."

**Step 3:** 运行完整链路
```bash
python multi_agent/scripts/run_llm_backtest.py
python scripts/generate_pages.py
python multi_agent/scripts/daily_agentic_report.py
```

Expected: 页面和日报均包含回测结果。

**Step 4:** Commit + push
```bash
git add -A
git commit -m "feat: integrate LLM backtest into page and daily report"
bash scripts/deploy_reports.sh
```

---

## Verification Checklist

- [ ] vectorbt 已安装
- [ ] 行情获取覆盖 ETF/个股/期货
- [ ] 权重生成正确（多/空/中性区分）
- [ ] 组合回测输出指标（return, sharpe, max_dd, calmar, win_rate）
- [ ] 三个 category 都能独立跑通
- [ ] CLI 可运行
- [ ] 日报和页面展示回测结果
- [ ] 数据库二进制未提交（已 gitignore）

## Notes

- 不改动 `agentic_predictions` 写入逻辑（只读评估）
- 不改动现有 `technical_analyst.py` 接口
- 若某标的历史行情缺失，跳过并记录
- 期货用主力连续合约，价格用收盘价
