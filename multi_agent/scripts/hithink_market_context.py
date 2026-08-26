#!/usr/bin/env python3
"""从同花顺缓存生成市场情绪和资金面文本摘要，注入 LLM 预测 prompt。

Usage:
    . etf_tracker/.venv/bin/activate
    python3 multi_agent/scripts/hithink_market_context.py
"""
import json
import os
from datetime import datetime
from pathlib import Path

ROOT = Path('/home/liudawei/github/daily_tracker_analytics')
CACHE_DIR = ROOT / 'multi_agent' / 'data' / 'hithink_cache'
FUNDAMENTALS_DIR = ROOT / 'multi_agent' / 'data' / 'fundamentals_cache'
OUTPUT_DIR = ROOT / 'multi_agent' / 'data' / 'market_context_cache'


def _load_json(path: Path):
    if not path.exists():
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _thscode_to_code(thscode: str) -> str:
    return thscode.split('.')[0]


def _format_amount(value):
    if value is None:
        return '-'
    try:
        v = float(value)
    except (TypeError, ValueError):
        return '-'
    if abs(v) >= 1e8:
        return f'{v/1e8:.2f}亿'
    if abs(v) >= 1e4:
        return f'{v/1e4:.2f}万'
    return f'{v:.2f}'


def build_hithink_context(date_str: str = None) -> str:
    today = date_str or datetime.now().strftime('%Y-%m-%d')
    limit_up = _load_json(CACHE_DIR / f'limit_up_pool_{today}.json').get('item', [])
    limit_down = _load_json(CACHE_DIR / f'limit_down_pool_{today}.json').get('item', [])
    limit_break = _load_json(CACHE_DIR / f'limit_break_pool_{today}.json').get('item', [])
    ladder = _load_json(CACHE_DIR / f'limit_up_ladder_{today}.json')
    hot = _load_json(CACHE_DIR / f'hot_stock_list_{today}.json').get('item', [])
    dt = _load_json(CACHE_DIR / f'dragon_tiger_list_{today}.json')

    # 概念分布 - 修复：concept_list 可能为空或嵌套
    concept_counts = {}
    for item in limit_up:
        concepts = item.get('concept_list') or []
        if isinstance(concepts, str):
            concepts = [{'name': c.strip()} for c in concepts.split(',') if c.strip()]
        for c in concepts:
            if isinstance(c, dict):
                name = c.get('name', '')
            elif isinstance(c, str):
                name = c.strip()
            else:
                continue
            if name:
                concept_counts[name] = concept_counts.get(name, 0) + 1
    top_concepts = sorted(concept_counts.items(), key=lambda x: x[1], reverse=True)[:8]

    # 连板高度
    ladder_items = ladder.get('item', [])
    max_board = 0
    if ladder_items:
        boards = ladder_items[0].get('boards', {})
        for key in ['seven_over', 'six_board', 'five_board', 'four_board', 'three_board', 'two_board']:
            if boards.get(key):
                max_board = {
                    'seven_over': 7, 'six_board': 6, 'five_board': 5,
                    'four_board': 4, 'three_board': 3, 'two_board': 2,
                }.get(key, 0)
                break

    # 热股榜
    hot_lines = []
    for item in hot[:5]:
        hot_lines.append(f"{_thscode_to_code(item.get('thscode', ''))} {item.get('name', '')}（热度 {item.get('heat', '')}）")

    # 龙虎榜净买入
    dt_lines = []
    for s in dt.get('stock_items', [])[:5]:
        net = s.get('net_value', 0)
        color = '净买入' if net >= 0 else '净卖出'
        dt_lines.append(f"{_thscode_to_code(s.get('thscode', ''))} {s.get('name', '')} {color} {_format_amount(net)}")

    context = f"""【同花顺市场资金面/情绪面摘要（{today}）】
- 市场情绪：涨停 {len(limit_up)} 只，跌停 {len(limit_down)} 只，炸板 {len(limit_break)} 只，最高连板 {max_board} 板。
- 涨停概念分布：{', '.join(f"{name}({cnt}只)" for name, cnt in top_concepts) or '暂无数据'}。
- 热股榜 Top5：{'; '.join(hot_lines) or '暂无数据'}。
- 龙虎榜动向：{'; '.join(dt_lines) or '暂无数据'}。
"""
    return context.strip()


def save_context():
    today = datetime.now().strftime('%Y-%m-%d')
    context = build_hithink_context(today)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / 'hithink_context.txt'
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(context)
    print(f'[hithink_context] {out_path}')
    return context


if __name__ == '__main__':
    print(save_context())
