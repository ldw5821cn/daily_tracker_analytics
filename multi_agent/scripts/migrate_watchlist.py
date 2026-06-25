"""
全量ETF列表迁移 - 从老系统 config.json 提取并写入新系统 watchlist
"""
import json, os, shutil

OLD_CONFIG = os.path.expanduser('~/daily-_tracker_analytics/etf_tracker/config.json')
NEW_WATCHLIST = os.path.expanduser('~/daily_tracker_analytics/etf_tracker/multi_agent/watchlist.json')

with open(OLD_CONFIG) as f:
    cfg = json.load(f)

# 提取老系统的完整ETF配置
etfs = cfg.get('etfs', [])

# 补全老系统中没有但在用的个股
extra_stocks = [
    {'code': '601991', 'name': '大唐发电', 'theme': '电力', 'sector': '电力'},
    {'code': '600206', 'name': '有研新材', 'theme': '稀土/半导体材料', 'sector': '半导体材料'},
]

# 构建新 watchlist
watchlist = []

# 所有A股ETF
for etf in etfs:
    code = etf.get('code', '')
    name = etf.get('name', '')
    # 跳过美股（QQQ/SPY/SOXX/BOTZ/GRAB）
    if not code.isdigit():
        continue
    watchlist.append({
        'ticker': code,
        'name': name,
        'category': 'ETF',
        'theme': etf.get('theme', ''),
        'sector': etf.get('sector', ''),
    })

# 补充个股
for s in extra_stocks:
    watchlist.append({
        'ticker': s['code'],
        'name': s['name'],
        'category': '个股',
        'theme': s.get('theme', ''),
        'sector': s.get('sector', ''),
    })

# 去重（按ticker）
seen = set()
unique = []
for w in watchlist:
    if w['ticker'] not in seen:
        seen.add(w['ticker'])
        unique.append(w)

# 写入新系统
os.makedirs(os.path.dirname(NEW_WATCHLIST), exist_ok=True)
with open(NEW_WATCHLIST, 'w', encoding='utf-8') as f:
    json.dump(unique, f, ensure_ascii=False, indent=2)

print(f'迁移完成: {len(unique)}个标的')
etf_count = len([w for w in unique if w.get('category') == 'ETF'])
stock_count = len([w for w in unique if w.get('category') == '个股'])
print(f'  ETF: {etf_count}个')
print(f'  个股: {stock_count}个')
print()
print('=== 完整列表 ===')
for w in unique:
    theme = w.get('theme', '')
    sector = w.get('sector', '')
    print(f'  {w["ticker"]:6s} {w["name"]:<20s} {w["category"]:<4s} {theme:<12s} {sector}')
