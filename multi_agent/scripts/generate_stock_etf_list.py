#!/usr/bin/env python3
"""根据目标权重生成股票/ETF 雪球调仓清单。

输出格式：
    买入/卖出/持有 标的代码 目标仓位(元) 目标权重 理由

用户拿到清单后在雪球 APP 手动同步。

A 股交易约束（已写入清单逻辑）：
- 个股不能做空：负权重仅作为"减持/卖出"建议，不生成空头仓位。
- 买入金额不足一手（100 股）时，标记为"不操作（金额不足一手）"。
- ETF 做空需要融券账户，清单中标注[融券]。
- 港股/跨境 ETF 标注[跨境]，交易规则与 A 股不同。
"""
import json
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
TARGET_WEIGHTS_PATH = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'target_weights.json')
OUTPUT_PATH = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'stock_etf_rebalance_list.json')

TOTAL_PORTFOLIO_VALUE = 50000.0  # 雪球组合总市值约 5 万

# 港股/跨境 ETF 代码前缀（常见）
_CROSS_BORDER_ETFS = {'513', '159', '18', '51'}


def _is_cross_border_etf(ticker: str) -> bool:
    """粗略判断是否为港股/跨境 ETF（5 开头或 159 开头）。"""
    return ticker.startswith(('513', '159', '518'))


def _stock_min_lot_amount(price: float) -> float:
    """A 股最小 1 手 = 100 股所需金额。"""
    return 100.0 * price


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
    # 注意：个股负权重在 A 股不能做空，计算金额时先取绝对值参与缩放，再处理为建议。
    stock_etf_total = sum(abs(t['target_weight']) for t in targets)
    scale = TOTAL_PORTFOLIO_VALUE * total_exposure / stock_etf_total if stock_etf_total > 0 else 1

    rows = []
    for t in targets:
        ticker = t['ticker']
        category = t['category']
        signal = t['signal']
        raw_weight = t['target_weight']
        price = t.get('current_price', 0) or 0

        # 基础信息
        item = {
            'ticker': ticker,
            'name': t['name'],
            'category': category,
            'signal': signal,
            'target_weight': round(raw_weight, 4),
            'reason': t.get('reason', ''),
            'current_price': price,
            'target_price': t.get('target_price', 0),
            'stop_loss': t.get('stop_loss', 0),
        }

        # A 股交易约束处理
        if category == '个股':
            if raw_weight > 0:
                amount = raw_weight * scale
                min_lot = _stock_min_lot_amount(price)
                if amount < min_lot:
                    item['action'] = '不操作'
                    item['target_amount'] = 0.0
                    item['constraint_note'] = f'金额不足一手(¥{min_lot:.0f})'
                else:
                    item['action'] = '买入'
                    item['target_amount'] = round(amount, 2)
                    item['constraint_note'] = ''
            else:
                # A 股个股不能做空，只给减持建议
                item['action'] = '减持/卖出'
                item['target_amount'] = 0.0
                item['constraint_note'] = 'A 股个股不能做空，仅作为卖出参考'

        elif category == 'ETF':
            amount = raw_weight * scale
            if raw_weight > 0:
                item['action'] = '买入'
                item['target_amount'] = round(amount, 2)
                item['constraint_note'] = '跨境ETF' if _is_cross_border_etf(ticker) else ''
            else:
                # ETF 做空需融券
                item['action'] = '融券卖出' if amount != 0 else '持有'
                item['target_amount'] = round(abs(amount), 2) if amount != 0 else 0.0
                notes = ['需融券账户']
                if _is_cross_border_etf(ticker):
                    notes.append('跨境ETF')
                item['constraint_note'] = ' '.join(notes)
        else:
            item['action'] = '持有'
            item['target_amount'] = 0.0
            item['constraint_note'] = ''

        rows.append(item)

    # 汇总
    long_amount = sum(r['target_amount'] for r in rows if r['action'] == '买入')
    short_amount = sum(r['target_amount'] for r in rows if r['action'] == '融券卖出')
    skipped = sum(1 for r in rows if r['action'] == '不操作')

    result = {
        'date': weights.get('date'),
        'total_portfolio_value': TOTAL_PORTFOLIO_VALUE,
        'stock_etf_total_weight': round(stock_etf_total, 4),
        'long_amount': round(long_amount, 2),
        'short_amount': round(short_amount, 2),
        'skipped_count': skipped,
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
    lines.append(f"买入金额合计: ¥{result['long_amount']:.0f} | 融券金额: ¥{result['short_amount']:.0f} | 跳过 {result['skipped_count']} 只")
    lines.append("")
    lines.append(f"{'操作':8s} {'代码':10s} {'名称':12s} {'目标金额':>10s} {'权重':>6s} {'备注':16s} {'理由'}")
    lines.append("-" * 100)
    for item in result['items']:
        if item['target_amount'] == 0 and item['action'] in ('持有',):
            continue
        note = item['constraint_note']
        if note:
            note = f"[{note}]"
        lines.append(
            f"{item['action']:8s} {item['ticker']:10s} {item['name']:12s} "
            f"¥{item['target_amount']:>8.0f} {item['target_weight']*100:>5.1f}% {note:16s} {item['reason'][:30]}"
        )
    return "\n".join(lines)


if __name__ == '__main__':
    result = generate_stock_etf_list()
    if 'error' in result:
        print(f"❌ {result['error']}")
        sys.exit(1)
    print(format_text(result))
    print(f"\n已保存: {OUTPUT_PATH}")
