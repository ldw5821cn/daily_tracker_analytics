#!/usr/bin/env python3
import sys, pandas as pd
sys.path.insert(0, '/home/liudawei/github/daily_tracker_analytics/multi_agent')
from core.warehouse import get_warehouse_conn

conn = get_warehouse_conn()
rows = conn.execute('''
SELECT f.date, f.ticker, f.category, f.signal, f.confidence, f.score, b.close as fwd_close
FROM feature_snapshot f
JOIN daily_bar b ON f.ticker = b.ticker AND b.date = date(f.date, '+5 days')
WHERE f.date <= date('now', '-6 days')
''').fetchall()
conn.close()

df = pd.DataFrame(rows, columns=['date', 'ticker', 'category', 'signal', 'confidence', 'score', 'fwd_close'])
conn = get_warehouse_conn()
rows2 = conn.execute('''
SELECT f.date, f.ticker, b.close as close
FROM feature_snapshot f
JOIN daily_bar b ON f.ticker = b.ticker AND b.date = f.date
''').fetchall()
conn.close()
prices = pd.DataFrame(rows2, columns=['date', 'ticker', 'close'])
df = df.merge(prices, on=['date', 'ticker'])
df['ret_5d'] = df['fwd_close'] / df['close'] - 1

def stats(g):
    n = len(g)
    if n == 0:
        return {'n': 0}
    return pd.Series({
        'n': n,
        'mean_ret_%': round(g['ret_5d'].mean() * 100, 2),
        'win_rate_%': round((g['ret_5d'] > 0).mean() * 100, 2),
        'direction_acc_%': round((
            ((g['signal'] == 'bullish') & (g['ret_5d'] > 0)) |
            ((g['signal'] == 'bearish') & (g['ret_5d'] < 0)) |
            ((g['signal'] == 'neutral') & (g['ret_5d'].abs() <= 0.015))
        ).mean() * 100, 2)
    })

print('=== 技术面信号 5d 回测 ===')
print(df.groupby('signal').apply(stats).to_string())
print('\n=== 按 category ===')
print(df.groupby(['category', 'signal']).apply(stats).to_string())
