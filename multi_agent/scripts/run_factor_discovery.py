"""因子发现 Agent：规则组合 + LLM 增强。

默认模式：从规则模板组合生成候选因子，在 Top 标的上回测，保存有效的。
LLM 模式（--llm）：当 DeepSeek 可用时，让 LLM 提出新的因子代码。
"""
import json
import os
import re
import sys
from typing import List, Dict

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'multi_agent'))

from core.llm_client import chat
from core.backtest_utils import parse_backtest_summary

FACTOR_DB_PATH = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'llm_factors.json')


# 规则基线因子池：由技术指标组合而成
BASE_FACTOR_TEMPLATES = [
    {
        'name': '趋势动量共振',
        'description': '价格上穿 MA60 且 5 日动量为正做多，跌破 MA60 做空',
        'code': "signal = np.where((close > ma60) & (momentum_5d > 0), 1, np.where(close < ma60, -1, 0))\nsignal = pd.Series(signal, index=close.index).shift(1).fillna(0)",
    },
    {
        'name': 'RSI均值回归',
        'description': 'RSI 低于 30 且向上拐头做多，高于 70 且向下拐头做空',
        'code': "rsi_diff = rsi_14.diff()\nsignal = np.where((rsi_14 < 30) & (rsi_diff > 0), 1, np.where((rsi_14 > 70) & (rsi_diff < 0), -1, 0))\nsignal = pd.Series(signal, index=close.index).shift(1).fillna(0)",
    },
    {
        'name': 'MACD趋势跟踪',
        'description': 'MACD 柱状由负转正做多，由正转负做空',
        'code': "hist_prev = macd_hist.shift(1)\nsignal = np.where((macd_hist > 0) & (hist_prev <= 0), 1, np.where((macd_hist < 0) & (hist_prev >= 0), -1, 0))\nsignal = pd.Series(signal, index=close.index).shift(1).fillna(0)",
    },
    {
        'name': 'BOLL突破反转',
        'description': '触及布林下轨做多，触及布林上轨做空',
        'code': "signal = np.where(close <= boll_down * 1.01, 1, np.where(close >= boll_up * 0.99, -1, 0))\nsignal = pd.Series(signal, index=close.index).shift(1).fillna(0)",
    },
    {
        'name': '放量突破',
        'description': '放量突破 20 日均线做多，跌破 20 日均线且缩量做空',
        'code': "signal = np.where((close > ma20) & (vol_ratio > 1.5), 1, np.where((close < ma20) & (vol_ratio < 0.8), -1, 0))\nsignal = pd.Series(signal, index=close.index).shift(1).fillna(0)",
    },
    {
        'name': 'RSI动量过滤',
        'description': '均线多头排列且 RSI 在 40-70 区间做多，空头排列且 RSI 高位做空',
        'code': "ma_aligned = (ma5 > ma10) & (ma10 > ma20)\nsignal = np.where(ma_aligned & (rsi_14 > 40) & (rsi_14 < 70), 1, np.where((ma5 < ma10) & (rsi_14 > 60), -1, 0))\nsignal = pd.Series(signal, index=close.index).shift(1).fillna(0)",
    },
]


def _load_existing_factors() -> List[Dict]:
    if not os.path.exists(FACTOR_DB_PATH):
        return []
    try:
        with open(FACTOR_DB_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data.get('factors', [])
    except Exception:
        return []


def _load_top_tickers(n: int = 5) -> List[Dict]:
    import sqlite3
    db = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'llm_predictions.db')
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    latest = conn.execute("SELECT MAX(pred_date) FROM agentic_predictions").fetchone()[0]
    rows = conn.execute(
        "SELECT ticker, name, category, backtest_summary FROM agentic_predictions WHERE pred_date=? AND category IN ('个股','ETF') ORDER BY weighted_score DESC LIMIT ?",
        (latest, n)
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d['bt'] = parse_backtest_summary(d.get('backtest_summary', ''))
        result.append(d)
    return result


def _extract_code_blocks(text: str) -> List[str]:
    blocks = re.findall(r'```python\n(.*?)```', text, re.DOTALL)
    if not blocks:
        blocks = re.findall(r'```\n(.*?)```', text, re.DOTALL)
    return blocks


def _execute_factor_code(ticker: str, code: str) -> Dict:
    """在真实数据上执行因子代码，计算简单回测表现。"""
    try:
        import pandas as pd
        import numpy as np
        from core.data_layer import get_stock_data, calc_technical_indicators
        df, info = get_stock_data(ticker)
        df = calc_technical_indicators(df)

        close = df['close']
        ma5 = df['ma5']
        ma10 = df['ma10']
        ma20 = df['ma20']
        ma60 = df['ma60']
        rsi_14 = df['rsi_14']
        macd_hist = df['macd_hist']
        momentum_5d = df['momentum_5d']
        boll_up = df['boll_up']
        boll_down = df['boll_down']
        vol_ratio = df['vol_ratio']

        namespace = {
            'np': np, 'pd': pd,
            'close': close, 'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60,
            'rsi_14': rsi_14, 'macd_hist': macd_hist, 'momentum_5d': momentum_5d,
            'boll_up': boll_up, 'boll_down': boll_down, 'vol_ratio': vol_ratio,
        }
        exec(code, namespace)

        signal = namespace.get('signal')
        if signal is None:
            return {'error': '代码未定义 signal 变量'}

        df['signal'] = pd.Series(signal, index=df.index).fillna(0)
        df['returns'] = df['close'].pct_change()
        df['strategy_returns'] = df['signal'].shift(1) * df['returns']

        total_return = df['strategy_returns'].sum()
        vol = df['strategy_returns'].std() * np.sqrt(252)
        sharpe = (df['strategy_returns'].mean() / df['strategy_returns'].std()) * np.sqrt(252) if df['strategy_returns'].std() > 0 else 0
        cumulative = (1 + df['strategy_returns']).cumprod()
        max_dd = ((cumulative - cumulative.cummax()) / cumulative.cummax()).min()

        return {
            'total_return': round(total_return * 100, 2),
            'annual_vol': round(vol * 100, 2),
            'sharpe': round(sharpe, 2),
            'max_drawdown': round(max_dd * 100, 2),
            'trades': int((df['signal'].diff() != 0).sum()),
        }
    except Exception as e:
        return {'error': str(e)}


def _sanitize(obj):
    """将 numpy 类型转换为 Python 原生类型，以便 JSON 序列化。"""
    import numpy as np
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, (np.bool_, np.bool)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj


def _generate_llm_factor(top_tickers: List[Dict]) -> Dict:
    """尝试让 LLM 提出新因子。"""
    top_context = "\n".join([
        f"{t['ticker']} {t['name']}({t['category']}): 60日收益{t['bt'].get('return_60d',0):+.1f}%, 夏普{t['bt'].get('sharpe_60d',0):.2f}"
        for t in top_tickers
    ])

    prompt = f"""请设计一个 A 股量价因子。

可用数据列：close, ma5, ma10, ma20, ma60, rsi_14, macd_hist, momentum_5d, boll_up, boll_down, vol_ratio。

当前强势股：
{top_context}

请输出：
1. 因子名（≤10字）
2. 逻辑描述（≤50字）
3. Python 代码（必须定义 signal 变量，取值为 1/-1/0，不要 import 其他模块）
4. 原理（≤50字）

注意：代码不要 lookahead，用 shift(1) 避免未来函数。"""

    try:
        resp = chat([{'role': 'user', 'content': prompt}], temperature=0.7, max_tokens=1200)
    except Exception as e:
        return {'error': f'LLM 调用异常: {e}'}

    if not resp:
        return {'error': 'LLM 未返回结果'}

    blocks = _extract_code_blocks(resp)
    if not blocks:
        return {'error': 'LLM 未返回代码块', 'raw': resp}
    code = blocks[0]

    name = desc = rationale = ''
    for line in resp.split('\n'):
        if line.startswith('1.') or '因子名' in line:
            name = re.sub(r'^\d+\.\s*', '', line).replace('因子名', '').replace('：', '').strip()
        elif line.startswith('2.') or '逻辑' in line:
            desc = re.sub(r'^\d+\.\s*', '', line).replace('逻辑描述', '').replace('逻辑', '').replace('：', '').strip()
        elif line.startswith('4.') or '原理' in line:
            rationale = re.sub(r'^\d+\.\s*', '', line).replace('原理', '').replace('：', '').strip()

    if not name:
        name = 'LLM_因子_' + os.urandom(2).hex()

    return {'name': name, 'description': desc, 'code': code, 'rationale': rationale, 'source': 'llm'}


def evaluate_factor(factor: Dict, tickers: List[str]) -> Dict:
    """在多个标的上测试因子。"""
    results = []
    for t in tickers[:3]:
        r = _execute_factor_code(t, factor['code'])
        r['ticker'] = t
        results.append(r)

    valid = [r for r in results if 'error' not in r]
    if not valid:
        return {**factor, 'evaluation': results, 'passed': False, 'score': -999}

    avg_sharpe = sum(r['sharpe'] for r in valid) / len(valid)
    avg_return = sum(r['total_return'] for r in valid) / len(valid)
    avg_drawdown = sum(r['max_drawdown'] for r in valid) / len(valid)

    # 综合评分：收益 * 夏普；保存 Top 3 正收益因子
    score = avg_return * max(avg_sharpe, 0)
    passed = score > 0 and avg_return > 0

    return {
        **factor,
        'evaluation': results,
        'avg_sharpe': round(avg_sharpe, 2),
        'avg_return': round(avg_return, 2),
        'avg_drawdown': round(avg_drawdown, 2),
        'score': round(score, 2),
        'passed': passed,
        'created_at': '',
    }


def run_factor_discovery(use_llm: bool = False, num_llm: int = 1):
    existing = _load_existing_factors()
    top = _load_top_tickers(5)
    tickers = [t['ticker'] for t in top]

    candidates = []

    # 1. 规则基线因子
    for tpl in BASE_FACTOR_TEMPLATES:
        candidates.append({**tpl, 'source': 'rule'})

    # 2. LLM 生成因子（可选）
    if use_llm:
        for _ in range(num_llm):
            f = _generate_llm_factor(top)
            if 'error' not in f:
                candidates.append(f)
            else:
                print(f"[factor_discovery] LLM 生成失败: {f['error']}", file=sys.stderr)

    # 3. 去重：已存在同名的不再测试
    existing_names = {x['name'] for x in existing}
    candidates = [c for c in candidates if c['name'] not in existing_names]

    # 4. 回测并保存
    evaluated = []
    for c in candidates:
        ev = evaluate_factor(c, tickers)
        evaluated.append(ev)

    # 保存表现最好的 3 个（score > 0）
    passed = [ev for ev in evaluated if ev['passed']]
    passed.sort(key=lambda x: x['score'], reverse=True)
    for ev in passed[:3]:
        existing.append(ev)

    os.makedirs(os.path.dirname(FACTOR_DB_PATH), exist_ok=True)
    with open(FACTOR_DB_PATH, 'w', encoding='utf-8') as f:
        json.dump({'factors': _sanitize(existing)}, f, ensure_ascii=False, indent=2)

    return evaluated


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--llm', action='store_true', help='同时尝试 LLM 生成因子')
    parser.add_argument('--num-llm', type=int, default=1, help='LLM 生成因子数量')
    args = parser.parse_args()

    results = run_factor_discovery(use_llm=args.llm, num_llm=args.num_llm)
    passed = [r for r in results if r.get('passed')]
    print(f"测试 {len(results)} 个因子，{len(passed)} 个通过")
    for r in results:
        status = '✅通过' if r.get('passed') else '❌未通过'
        print(f"\n{status} {r['name']} ({r.get('source','rule')})")
        print(f"  逻辑: {r['description']}")
        print(f"  平均夏普: {r.get('avg_sharpe')} | 平均收益: {r.get('avg_return')}% | 平均回撤: {r.get('avg_drawdown')}%")
