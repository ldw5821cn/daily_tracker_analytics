#!/usr/bin/env python3
"""为重点标的生成新闻/公告 LLM 分析。

输出：multi_agent/data/news_sentiment.json
日报可读取该文件展示 Top 标的的最新舆情。
"""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, PROJECT_ROOT)

from multi_agent.core.news_engine import get_unstructured_data
from multi_agent.core.llm_client import summarize_news
from multi_agent.core.backtest_utils import sort_by_backtest

OUTPUT_PATH = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'news_sentiment.json')
TARGET_WEIGHTS_PATH = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'target_weights.json')
DB_PATH = os.path.join(PROJECT_ROOT, 'multi_agent', 'data', 'llm_predictions.db')


def _load_top_targets(n: int = 5):
    """从 agentic_predictions 读取最新预测，取综合分最高的 n 只。"""
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    latest = conn.execute("SELECT MAX(pred_date) FROM agentic_predictions").fetchone()[0]
    rows = conn.execute(
        "SELECT ticker, name, category, signal, confidence, backtest_summary FROM agentic_predictions WHERE pred_date=?",
        (latest,)
    ).fetchall()
    conn.close()

    rows = [dict(r) for r in rows]
    rows = sort_by_backtest(rows)
    # 优先取重点看多/看空
    bulls = [r for r in rows if r['signal'] == 'bullish'][:n]
    bears = [r for r in rows if r['signal'] == 'bearish'][:n]
    seen = {r['ticker'] for r in bulls + bears}
    # 补充其他
    for r in rows:
        if r['ticker'] not in seen and len(seen) < n * 2:
            bulls.append(r)
            seen.add(r['ticker'])
    return bulls + bears


def _analyze_one(item: dict) -> dict:
    ticker = item['ticker']
    name = item.get('name', '')
    category = item.get('category', '个股')

    data = get_unstructured_data(ticker, name, category)
    all_items = data['news'] + data['notices']

    llm_summary = summarize_news(ticker, name, all_items)

    # 简单情绪统计（fallback）
    pos = sum(1 for x in all_items if any(w in x['title'] for w in ['涨','升','突破','利好','增长','盈利','增持','回购']))
    neg = sum(1 for x in all_items if any(w in x['title'] for w in ['跌','降','破','利空','亏损','减持','处罚','退市']))
    sentiment = '积极' if pos > neg else '消极' if neg > pos else '中性'

    return {
        'ticker': ticker,
        'name': name,
        'category': category,
        'signal': item.get('signal'),
        'confidence': item.get('confidence'),
        'news_count': len(data['news']),
        'notice_count': len(data['notices']),
        'sentiment': sentiment,
        'llm_summary': llm_summary or f"近7天{len(all_items)}条新闻/公告，情绪{sentiment}（未配置 LLM API，使用规则摘要）",
        'latest_titles': [x['title'] for x in all_items[:3]],
        'fetched_at': data['fetched_at'],
    }


def run_news_analysis():
    targets = _load_top_targets(5)
    results = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(_analyze_one, t): t for t in targets}
        for f in as_completed(futures):
            try:
                results.append(f.result())
            except Exception as e:
                print(f"[news_analysis] error: {e}", file=sys.stderr)

    with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
        json.dump({'date': _load_latest_date(), 'items': results}, f, ensure_ascii=False, indent=2)
    return results


def _load_latest_date():
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    latest = conn.execute("SELECT MAX(pred_date) FROM agentic_predictions").fetchone()[0]
    conn.close()
    return latest


if __name__ == '__main__':
    results = run_news_analysis()
    print(f"生成 {OUTPUT_PATH}，共 {len(results)} 只标的")
    for r in results:
        print(f"\n{r['ticker']} {r['name']} | {r['sentiment']}")
        print(f"  {r['llm_summary']}")
        print(f"  最新标题: {r['latest_titles'][:2]}")
