#!/usr/bin/env python3
"""根据目标权重生成股票/ETF 雪球调仓清单。

输出格式：
    买入/卖出/持有 标的代码 目标仓位(元) 目标权重 理由

用户拿到清单后在雪球 APP 手动同步。
"""
import json
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
TARGET_WEIGHTS_PATH = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'target_weights.json')
OUTPUT_PATH = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'stock_etf_rebalance_list.json')

TOTAL_PORTFOLIO_VALUE = 50000.0  # 雪球组合总市值约 5 万


def generate_stock_etf_list():
    if not os.path.exists(TARGET_WEIGHTS_PATH):
        return {'error': '目标权重文件不存在，请先运行 run_allocator.py'}

    with open(TARGET_WEIGHTS_PATH, 'r', encoding='utf-8') as f:
        weights = json.load(f)

    total_exposure = weights.get('total_exposure', 0.7)
    # 只取股票/ETF，按目标权重排序
    targets = [t for t in weights.get('targets', [])
               if t.get('category') in ('个股', 'ETF')]
    targets = sorted(targets, key=lambda x: abs(x['target_weight']), reverse=True)

    # 把权重映射到金额：股票+ETF 目标权重之和约 80%（个股50+ETF30），按总仓位 5 万
    # 这里用等比例缩放，使总持仓金额 = TOTAL_PORTFOLIO_VALUE * 目标总敞口
    stock_etf_total = sum(abs(t['target_weight']) for t in targets)
    scale = TOTAL_PORTFOLIO_VALUE * total_exposure / stock_etf_total if stock_etf_total > 0 else 1

    rows = []
    for t in targets:
        amount = t['target_weight'] * scale
        rows.append({
            'ticker': t['ticker'],
            'name': t['name'],
            'category': t['category'],
            'signal': t['signal'],
            'target_weight': round(t['target_weight'], 4),
            'target_amount': round(amount, 2),
            'action': '买入' if amount > 0 else '卖出' if amount < 0 else '持有',
            'reason': t.get('reason', ''),
            'current_price': t.get('current_price', 0),
            'target_price': t.get('target_price', 0),
            'stop_loss': t.get('stop_loss', 0),
        })

    result = {
        'date': weights.get('date'),
        'total_portfolio_value': TOTAL_PORTFOLIO_VALUE,
        'stock_etf_total_weight': round(stock_etf_total, 4),
        'items': rows,
    }

    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    return result


def format_text(result) -> str:
    lines = []
    lines.append(f"📋 股票/ETF 调仓清单 ({result['date']})")
    lines.append(f"组合市值: ¥{result['total_portfolio_value']:.0f}")
    lines.append(f"股票+ETF 目标权重合计: {result['stock_etf_total_weight']:.1%}")
    lines.append("")
    lines.append(f"{'操作':4s} {'代码':10s} {'名称':12s} {'目标金额':>10s} {'权重':>6s} {'理由'}")
    lines.append("-" * 80)
    for item in result['items']:
        if item['target_amount'] == 0:
            continue
        lines.append(
            f"{item['action']:4s} {item['ticker']:10s} {item['name']:12s} "
            f"¥{item['target_amount']:>8.0f} {item['target_weight']*100:>5.1f}% {item['reason'][:30]}"
        )
    return "\n".join(lines)


if __name__ == '__main__':
    result = generate_stock_etf_list()
    if 'error' in result:
        print(f"❌ {result['error']}")
        sys.exit(1)
    print(format_text(result))
    print(f"\n已保存: {OUTPUT_PATH}")
