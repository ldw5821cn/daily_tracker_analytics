#!/usr/bin/env python3
"""
雪球组合自动跟踪 - ZH3650823 高股息组合

功能：
1. 拉取最新净值和收益
2. 拉取当前持仓和行业分布
3. 计算用户的真实持仓对比
4. 生成绩效报告

用法：
    python3 multi_agent/xueqiu_tracker.py              # 跟踪 ZH3650823
    python3 multi_agent/xueqiu_tracker.py --all        # 跟踪所有组合
"""

import sys
import os
import json
import urllib.request
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
REPO = BASE.parent


def load_cookies():
    """从 .env 读取雪球 cookies"""
    env_path = REPO / '.env'
    if not env_path.exists():
        return None
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith('XUEQIU_COOKIES='):
                _, val = line.split('=', 1)
                return val
    return None


def fetch_nav(cube_symbol: str, cookies: str) -> dict:
    """拉取组合净值历史"""
    url = f'https://xueqiu.com/cubes/nav_daily/all.json?cube_symbol={cube_symbol}'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Cookie': cookies,
        'Referer': f'https://xueqiu.com/P/{cube_symbol}',
    }
    
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    
    if isinstance(data, list) and len(data) > 0:
        item = data[0]
        nav_list = item.get('list', [])
        if nav_list:
            latest = nav_list[-1]
            return {
                'name': item.get('name', 'N/A'),
                'symbol': cube_symbol,
                'latest_nav': latest['value'],
                'latest_date': latest['date'],
                'total_return': latest['percent'],
                'nav_history': nav_list,
                'days': len(nav_list),
            }
    return {}


def fetch_holdings(cube_symbol: str, cookies: str) -> list:
    """拉取组合当前持仓"""
    url = f'https://xueqiu.com/cubes/rebalancing/current.json?cube_symbol={cube_symbol}'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Cookie': cookies,
        'Referer': f'https://xueqiu.com/P/{cube_symbol}',
    }
    
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    
    last_rb = data.get('last_rb', {})
    holdings = last_rb.get('holdings', [])
    
    result = []
    for h in holdings:
        result.append({
            'name': h.get('stock_name', 'N/A'),
            'symbol': h.get('stock_symbol', 'N/A'),
            'weight': h.get('weight', 0),  # 百分比
            'segment': h.get('segment_name', '其他'),
            'volume': h.get('volume', 0),
        })
    
    return result


def analyze_portfolio(nav: dict, holdings: list) -> dict:
    """分析组合结构"""
    # 行业分布
    sectors = {}
    for h in holdings:
        seg = h['segment']
        sectors[seg] = sectors.get(seg, 0) + h['weight']
    
    # 集中度
    top1 = max(holdings, key=lambda x: x['weight']) if holdings else None
    top3 = sorted(holdings, key=lambda x: x['weight'], reverse=True)[:3]
    top3_weight = sum(h['weight'] for h in top3)
    
    return {
        'nav': nav,
        'holdings': holdings,
        'sectors': sectors,
        'concentration': {
            'top1': {'name': top1['name'], 'weight': top1['weight']} if top1 else None,
            'top3_weight': top3_weight,
        },
        'stock_count': len(holdings),
    }


def generate_report(analysis: dict) -> str:
    """生成跟踪报告"""
    nav = analysis['nav']
    holdings = analysis['holdings']
    sectors = analysis['sectors']
    
    lines = []
    lines.append("=" * 60)
    lines.append(f"📊 雪球组合跟踪: {nav.get('name', 'N/A')}")
    lines.append("=" * 60)
    lines.append(f"\n组合代码: {nav.get('symbol', 'N/A')}")
    lines.append(f"最新净值: {nav.get('latest_nav', 0):.4f} ({nav.get('latest_date', 'N/A')})")
    lines.append(f"累计收益: {nav.get('total_return', 0):+.2f}%")
    lines.append(f"运行天数: {nav.get('days', 0)}")
    
    lines.append(f"\n📈 持仓明细 ({analysis['stock_count']}只):")
    for h in sorted(holdings, key=lambda x: x['weight'], reverse=True):
        lines.append(f"  {h['name']}({h['symbol']}): {h['weight']:.1f}% [{h['segment']}]")
    
    lines.append(f"\n🏭 行业分布:")
    for s, w in sorted(sectors.items(), key=lambda x: x[1], reverse=True):
        lines.append(f"  {s}: {w:.1f}%")
    
    conc = analysis['concentration']
    if conc['top1']:
        lines.append(f"\n🎯 集中度:")
        lines.append(f"  第一大持仓: {conc['top1']['name']} ({conc['top1']['weight']:.1f}%)")
        lines.append(f"  前三持仓占比: {conc['top3_weight']:.1f}%")
    
    # 风险评估
    lines.append(f"\n⚠️ 风险评估:")
    if conc['top3_weight'] > 60:
        lines.append(f"  🔴 集中度偏高: 前三持仓 {conc['top3_weight']:.0f}% > 60%")
    elif conc['top3_weight'] > 40:
        lines.append(f"  🟡 集中度适中: 前三持仓 {conc['top3_weight']:.0f}%")
    else:
        lines.append(f"  🟢 集中度分散: 前三持仓 {conc['top3_weight']:.0f}%")
    
    power_weight = sectors.get('公用事业', 0)
    if power_weight > 40:
        lines.append(f"  🔴 公用事业占比高: {power_weight:.0f}% > 40%")
    elif power_weight > 25:
        lines.append(f"  🟡 公用事业占比适中: {power_weight:.0f}%")
    
    lines.append("\n" + "=" * 60)
    return "\n".join(lines)


def save_to_json(analysis: dict, output_dir: str = None):
    """保存分析结果到 JSON"""
    if output_dir is None:
        output_dir = REPO / 'docs' / 'xueqiu_data'
    os.makedirs(output_dir, exist_ok=True)
    
    date_str = datetime.now().strftime('%Y%m%d')
    output_path = os.path.join(output_dir, f'ZH3650823_{date_str}.json')
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(analysis, f, ensure_ascii=False, indent=2)
    
    return output_path


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--symbol', default='ZH3650823', help='组合代码')
    parser.add_argument('--save', action='store_true', help='保存到 JSON')
    args = parser.parse_args()
    
    print("🔐 加载雪球 cookies...")
    cookies = load_cookies()
    if not cookies:
        print("❌ 未找到 XUEQIU_COOKIES，请检查 .env")
        sys.exit(1)
    
    print(f"📊 拉取组合 {args.symbol} 数据...")
    
    try:
        nav = fetch_nav(args.symbol, cookies)
        print(f"✅ 净值: {nav.get('latest_nav', 0):.4f} ({nav.get('latest_date', 'N/A')})")
        
        holdings = fetch_holdings(args.symbol, cookies)
        print(f"✅ 持仓: {len(holdings)} 只")
        
        analysis = analyze_portfolio(nav, holdings)
        report = generate_report(analysis)
        print(report)
        
        if args.save:
            path = save_to_json(analysis)
            print(f"\n💾 已保存: {path}")
        
    except Exception as e:
        print(f"❌ 错误: {e}")
        import traceback
        traceback.print_exc()
