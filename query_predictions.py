import sqlite3
import json
import sys

conn = sqlite3.connect('/home/liudawei/github/daily_tracker_analytics/multi_agent/data/llm_predictions.db')
cursor = conn.cursor()

# Get all predictions for 2026-11-24
cursor.execute("""
    SELECT ticker, name, signal, category, weighted_score, backtest_summary, current_price, sector,
           confidence, position_pct, horizon_1d, horizon_5d, horizon_10d
    FROM agentic_predictions 
    WHERE pred_date='2026-11-24' 
    ORDER BY category, weighted_score DESC
""")
rows = cursor.fetchall()

# Parse and organize
results = []
for r in rows:
    ticker, name, signal, cat, score, bt_summary, price, sector, conf, pos, h1d, h5d, h10d = r
    
    # Parse backtest summary
    ret_60d = None
    dd_60d = None
    sharpe_60d = None
    if bt_summary:
        try:
            bt = json.loads(bt_summary)
            ret_60d = bt.get('return_60d')
            dd_60d = bt.get('max_dd_60d')
            sharpe_60d = bt.get('sharpe_60d')
        except:
            pass
    
    results.append({
        'ticker': ticker,
        'name': name,
        'signal': signal,
        'category': cat,
        'score': score,
        'price': price,
        'sector': sector,
        'ret_60d': ret_60d,
        'dd_60d': dd_60d,
        'sharpe_60d': sharpe_60d,
        'confidence': conf,
        'position_pct': pos,
        'h1d': h1d,
        'h5d': h5d,
        'h10d': h10d,
    })

# Group by category
from collections import defaultdict
groups = defaultdict(list)
for r in results:
    groups[r['category']].append(r)

# Print organized report
for cat in ['ETF', '个股', '期货', 'US']:
    items = groups.get(cat, [])
    if not items:
        continue
    
    print(f"\n{'='*60}")
    print(f"📂 {cat} 共{len(items)}只")
    print(f"{'='*60}")
    
    for item in items:
        emoji = '🔥' if item['signal'] in ('bullish', '看多') else '❄️' if item['signal'] in ('bearish', '看空') else '➖'
        ret_s = f"{item['ret_60d']:+.1f}%" if item['ret_60d'] is not None else 'N/A'
        dd_s = f"{item['dd_60d']:.1f}%" if item['dd_60d'] is not None else 'N/A'
        sh_s = f"{item['sharpe_60d']:.2f}" if item['sharpe_60d'] is not None else 'N/A'
        price_s = f"{item['price']:.2f}" if item['price'] is not None else 'N/A'
        
        # Build horizon string
        horizons = []
        if item['h1d']: horizons.append(f"1日{item['h1d']}")
        if item['h5d']: horizons.append(f"5日{item['h5d']}")
        if item['h10d']: horizons.append(f"10日{item['h10d']}")
        h_str = ' | '.join(horizons) if horizons else ''
        
        print(f"{emoji} {item['name']}({item['ticker']}) [{item['sector']}]")
        print(f"   信号:{item['signal']} 60日{ret_s} 回撤{dd_s} 夏普{sh_s} 现价{price_s}")
        if h_str:
            print(f"   预测: {h_str}")
        print()

conn.close()
