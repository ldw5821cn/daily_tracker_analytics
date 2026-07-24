#!/usr/bin/env python3
import sys, os, json, sqlite3
from datetime import datetime, timedelta

PROJECT_ROOT = '/home/liudawei/github/daily_tracker_analytics'
MULTI_AGENT = os.path.join(PROJECT_ROOT, 'multi_agent')
sys.path.insert(0, MULTI_AGENT)
sys.path.insert(0, PROJECT_ROOT)

from scripts.morning_validation import _get_conn, _validate_row

def validate_date(pred_date, output_path):
    conn = _get_conn()
    rows = conn.execute("SELECT * FROM agentic_predictions WHERE pred_date=?", (pred_date,)).fetchall()
    results = []
    for row in rows:
        r = _validate_row(row, pred_date)
        results.append(r)
    conn.close()

    by_category = {}
    overall = {'total': 0, 'correct': 0}
    for r in results:
        if r['note'] != 'ok':
            continue
        cat = r['category']
        by_category.setdefault(cat, {'total': 0, 'correct': 0})
        by_category[cat]['total'] += 1
        if r['direction_correct']:
            by_category[cat]['correct'] += 1
            overall['correct'] += 1
        overall['total'] += 1

    out = {
        'pred_date': pred_date,
        'validate_date': (datetime.strptime(pred_date, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d'),
        'direction_threshold': 1.5,
        'overall': {
            'total': overall['total'],
            'correct': overall['correct'],
            'accuracy': round(overall['correct'] / max(overall['total'], 1) * 100, 2),
        },
        'by_category': {k: {'total': v['total'], 'correct': v['correct'], 'accuracy': round(v['correct']/max(v['total'],1)*100,2)} for k, v in by_category.items()},
        'items': results,
    }
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"{pred_date} 验证: {out['overall']['correct']}/{out['overall']['total']} = {out['overall']['accuracy']}%")
    return out

if __name__ == '__main__':
    validate_date('2026-07-22', os.path.join(MULTI_AGENT, 'data', 'morning_validation_0722.json'))
    validate_date('2026-07-23', os.path.join(MULTI_AGENT, 'data', 'morning_validation_0723.json'))
