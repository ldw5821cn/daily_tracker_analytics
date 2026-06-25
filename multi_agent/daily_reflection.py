#!/usr/bin/env python3
"""
 · 策略复盘进化引擎 v2
深入学习外面金融skill库+实战复盘，沉淀自有策略智慧

核心流程：
1. 拉取组合持仓+收益数据
2. 分析每只持仓盈亏原因
3. 识别错误模式（追高/止损不及时/选错板块等）
4. 更新策略参数（权重微调/板块过滤/时序偏见）
5. 沉淀到知识库（长期积累经验）
"""

import sys
import os
import json
import urllib.request
from datetime import datetime, timedelta
from collections import defaultdict, Counter

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'multi_agent'))

DATA_DIR = os.path.join(BASE, 'multi_agent', 'data')
KNOWLEDGE_FILE = os.path.join(DATA_DIR, 'strategy_knowledge.json')
WEIGHTS_FILE = os.path.join(DATA_DIR, 'strategy_weights.json')
os.makedirs(DATA_DIR, exist_ok=True)


def load_knowledge():
    """加载策略知识库"""
    if os.path.exists(KNOWLEDGE_FILE):
        with open(KNOWLEDGE_FILE) as f:
            return json.load(f)
    return {
        'version': '1.0',
        'created_at': datetime.now().isoformat(),
        'total_trades': 0,
        'wins': 0,
        'losses': 0,
        'by_sector': {},           # 板块胜率
        'by_error_type': {},       # 错误类型分布
        'by_market_condition': {}, # 不同大盘环境下的表现
        'by_hold_days': {},        # 持仓天数胜率
        'lessons': [],             # 经验教训列表
        'patterns': [],            # 识别到的模式
        'last_updated': datetime.now().isoformat(),
    }


def save_knowledge(kb):
    kb['last_updated'] = datetime.now().isoformat()
    with open(KNOWLEDGE_FILE, 'w', encoding='utf-8') as f:
        json.dump(kb, f, ensure_ascii=False, indent=2)


def get_portfolio_data():
    """获取组合当前数据"""
    from xueqiu_rebalancer import get_user
    user = get_user()
    s = user.s
    
    # 组合信息
    q = s.get(f'https://xueqiu.com/cubes/quote.json?code=ZH3650487').json()
    nv = float(q['ZH3650487']['net_value'])
    daily = float(q['ZH3650487']['daily_gain'])
    total = float(q['ZH3650487']['total_gain'])
    
    # 持仓
    pos = user.position
    holdings = []
    for p in pos:
        holdings.append({
            'name': p.get('stock_name', ''),
            'symbol': p['stock_code'],
            'market_value': p.get('market_value', 0),
        })
    
    user.s.close()
    return {
        'net_value': nv,
        'daily_gain': daily,
        'total_gain': total,
        'holdings': holdings,
    }


def get_realtime_quotes(stocks):
    """获取实时行情数据"""
    batch_q = []
    for s in stocks:
        code = s['symbol']
        prefix = 'sh' if code.startswith('SH') else 'sz'
        batch_q.append(f'{prefix}{code[2:]}')
    
    if not batch_q:
        return {}
    
    url = 'http://qt.gtimg.cn/q=' + ','.join(batch_q)
    req = urllib.request.Request(url)
    req.add_header('Referer', 'https://xueqiu.com')
    resp = urllib.request.urlopen(req, timeout=10)
    text = resp.read().decode('gbk')
    
    quotes = {}
    for line in text.strip().split(';'):
        if not line.strip():
            continue
        try:
            data = line.split('~')
            if len(data) > 40:
                sc = data[2]
                quotes[sc] = {
                    'price': float(data[3]) if data[3] else 0,
                    'chg_pct': float(data[32]) if data[32] else 0,
                    'high': float(data[33]) if data[33] else 0,
                    'low': float(data[34]) if data[34] else 0,
                    'turnover': float(data[37]) if data[37] else 0,
                    'volume': float(data[6]) if data[6] else 0,
                    'amount': float(data[45]) if data[45] else 0,
                }
        except:
            pass
    return quotes


def analyze_performance(portfolio, quotes, kb):
    """
    分析当前组合表现，识别模式，输出复盘结论
    """
    report = []
    report.append(f"📊 **策略复盘报告** — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    report.append("")
    
    # 整体表现
    total_gain = portfolio['total_gain']
    daily_gain = portfolio['daily_gain']
    gain_emoji = '🟢' if total_gain >= 0 else '🔴'
    report.append(f"{gain_emoji} **组合收益**: {total_gain:+.2f}% (日{daily_gain:+.2f}%)")
    
    # 持仓分析
    holdings = portfolio['holdings']
    winners = 0
    losers = 0
    bad_patterns = []
    
    for h in holdings:
        sym = h['symbol']
        short = sym[2:]
        q = quotes.get(short, {})
        chg = q.get('chg_pct', 0)
        
        if chg >= 3:
            winners += 1
        elif chg < -3:
            losers += 1
            # 识别错误模式
            if chg < -5:
                bad_patterns.append(f"  ⚠️ 重大回撤: {h['name']} ({sym}) {chg:.1f}%")
    
    report.append(f"📋 **持仓健康度**: {winners}只强势 / {len(holdings)-winners-losers}只持平 / {losers}只弱势")
    
    if bad_patterns:
        report.append("")
        report.append("❌ **需关注的问题**:")
        report.extend(bad_patterns)
        report.append(f"  💡 建议: 检查这些股票的买入逻辑是否还在")
    
    # 大盘环境判断
    report.append("")
    report.append("🌍 **大盘环境评估**")
    try:
        # 获取大盘指数
        req = urllib.request.Request('http://qt.gtimg.cn/q=sh000001,sz399001,sz399006')
        req.add_header('Referer', 'https://xueqiu.com')
        resp = urllib.request.urlopen(req, timeout=5)
        idx_text = resp.read().decode('gbk')
        
        for line in idx_text.strip().split(';'):
            if not line.strip():
                continue
            data = line.split('~')
            if len(data) > 32:
                name = data[1]
                chg = data[32] if data[32] else '0'
                idx_chg = float(chg)
                emoji = '🟢' if idx_chg > 0 else ('🔴' if idx_chg < 0 else '⚪')
                report.append(f"  {emoji} {name}: {chg}%")
    except:
        pass
    
    # 智慧沉淀
    report.append("")
    report.append("📝 **策略反思**")
    
    # 从知识库提取相关经验
    if kb.get('lessons'):
        report.append("  过去的经验提醒：")
        for lesson in kb['lessons'][-3:]:  # 最近3条
            report.append(f"  📌 {lesson}")
    
    # 当前操作建议
    report.append("")
    report.append("🎯 **操作建议**")
    if total_gain > 0:
        report.append(f"  组合盈利{total_gain:.2f}%，当前整体健康，持有观察")
    elif total_gain < -5:
        report.append(f"  组合亏损{total_gain:.2f}%，检查止损执行情况")
    else:
        report.append("  组合窄幅波动，耐心持有等待趋势明朗")
    
    # 连续亏损提示（从知识库判断）
    recent_losses = kb.get('consecutive_losses', 0)
    if recent_losses >= 3:
        report.append(f"  ⚠️ 连续{recent_losses}次亏损信号！建议减仓至50%以下")
    
    return '\n'.join(report)


def learn_from_trade(kb, trade_result):
    """
    从一笔交易中学习
    trade_result: {
        'symbol': str,
        'name': str,
        'buy_date': str,
        'sell_date': str or None,
        'buy_price': float,
        'sell_price': float or None,
        'return_pct': float,
        'hold_days': int,
        'reason': str,        # 买入理由
        'sector': str,        # 板块
        'market_cond': str,   # 大盘环境: up/down/sideways
        'error_type': str or None,  # 错误类型
    }
    """
    r = trade_result
    kb['total_trades'] += 1
    
    if r['return_pct'] > 0:
        kb['wins'] += 1
    else:
        kb['losses'] += 1
    
    # 板块胜率
    sector = r.get('sector', '其他')
    if sector not in kb['by_sector']:
        kb['by_sector'][sector] = {'wins': 0, 'losses': 0, 'total_return': 0}
    kb['by_sector'][sector]['total_return'] += r['return_pct']
    if r['return_pct'] > 0:
        kb['by_sector'][sector]['wins'] += 1
    else:
        kb['by_sector'][sector]['losses'] += 1
    
    # 错误类型
    error = r.get('error_type')
    if error:
        if error not in kb['by_error_type']:
            kb['by_error_type'][error] = 0
        kb['by_error_type'][error] += 1
    
    # 持仓天数胜率
    days_bucket = f"{r['hold_days']//5*5}-{(r['hold_days']//5+1)*5}d"
    if days_bucket not in kb['by_hold_days']:
        kb['by_hold_days'][days_bucket] = {'wins': 0, 'losses': 0}
    if r['return_pct'] > 0:
        kb['by_hold_days'][days_bucket]['wins'] += 1
    else:
        kb['by_hold_days'][days_bucket]['losses'] += 1
    
    # 大盘环境
    mc = r.get('market_cond', 'unknown')
    if mc not in kb['by_market_condition']:
        kb['by_market_condition'][mc] = {'wins': 0, 'losses': 0}
    if r['return_pct'] > 0:
        kb['by_market_condition'][mc]['wins'] += 1
    else:
        kb['by_market_condition'][mc]['losses'] += 1
    
    save_knowledge(kb)


def extract_insights(kb):
    """从知识库中提取规律性洞察"""
    insights = []
    
    # 最佳板块
    sector_winrates = []
    for sec, data in kb.get('by_sector', {}).items():
        total = data['wins'] + data['losses']
        if total >= 3:
            wr = data['wins'] / total * 100
            sector_winrates.append((sec, wr, total))
    
    if sector_winrates:
        sector_winrates.sort(key=lambda x: x[1], reverse=True)
        best = sector_winrates[0]
        worst = sector_winrates[-1]
        
        insights.append(f"🏆 **最佳板块**: {best[0]} (胜率{best[1]:.0f}%, {best[2]}笔)")
        if worst[1] < 40:
            insights.append(f"⚠️ **最差板块**: {worst[0]} (胜率{worst[1]:.0f}%, 建议回避)")
    
    # 最佳持仓天数
    hold_wr = []
    for bucket, data in kb.get('by_hold_days', {}).items():
        total = data['wins'] + data['losses']
        if total >= 3:
            wr = data['wins'] / total * 100
            hold_wr.append((bucket, wr, total))
    
    if hold_wr:
        hold_wr.sort(key=lambda x: x[1], reverse=True)
        insights.append(f"⏱️ **最佳持有周期**: {hold_wr[0][0]} (胜率{hold_wr[0][1]:.0f}%)")
    
    # 常见错误模式
    errors = kb.get('by_error_type', {})
    if errors:
        most_common = max(errors.items(), key=lambda x: x[1])
        insights.append(f"🔍 **最常见错误**: {most_common[0]} (出现{most_common[1]}次)")
    
    # 大盘环境偏好
    mc_data = kb.get('by_market_condition', {})
    for cond, data in mc_data.items():
        total = data['wins'] + data['losses']
        if total >= 3:
            wr = data['wins'] / total * 100
            insights.append(f"📈 **{cond}环境下**: 胜率{wr:.0f}% ({total}笔)")
    
    return insights


def auto_tune_weights(kb):
    """根据历史数据自动调整策略权重"""
    weights = {}
    changes = []
    
    # 1. 板块偏好 - 根据胜率调整
    sector_adj = {}
    for sec, data in kb.get('by_sector', {}).items():
        total = data['wins'] + data['losses']
        if total >= 3:
            wr = data['wins'] / total
            sector_adj[sec] = wr
    
    # 2. 持仓周期偏好
    best_hold_days = None
    best_hold_wr = 0
    for bucket, data in kb.get('by_hold_days', {}).items():
        total = data['wins'] + data['losses']
        if total >= 3:
            wr = data['wins'] / total
            if wr > best_hold_wr:
                best_hold_wr = wr
                best_hold_days = bucket
    
    # 3. 错误模式 -> 加规则
    errors = kb.get('by_error_type', {})
    
    weights = {
        'sector_adjustments': sector_adj,
        'best_hold_period': best_hold_days,
        'top_errors': dict(sorted(errors.items(), key=lambda x: x[1], reverse=True)[:5]),
        'total_trades_analyzed': kb['total_trades'],
    }
    
    return weights, changes


def generate_daily_reflection():
    """每日盘后复盘入口"""
    print("=" * 60)
    print("   · 策略复盘进化引擎 v2")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    
    # 加载知识库
    kb = load_knowledge()
    
    # 获取组合数据
    print("\n📡 获取组合数据...")
    try:
        portfolio = get_portfolio_data()
        quotes = get_realtime_quotes(portfolio['holdings'])
        print(f"  组合净值: {portfolio['net_value']:.4f} | 收益: {portfolio['total_gain']:+.2f}%")
    except Exception as e:
        print(f"  ❌ 获取失败: {e}")
        return
    
    # 复盘分析
    print("\n🔍 分析中...")
    report = analyze_performance(portfolio, quotes, kb)
    print(report)
    
    # 提取洞察
    insights = extract_insights(kb)
    if insights:
        print("\n💡 **长期洞察**:")
        for i in insights:
            print(f"  {i}")
    
    # 自动调优
    weights, changes = auto_tune_weights(kb)
    if changes:
        print(f"\n⚙️ 参数调整: {len(changes)}项")
        for c in changes:
            print(f"  {c}")
    
    # 检查连续亏损
    recent_returns = [portfolio['daily_gain']]  # 简化版, 实际应从历史取
    consecutive_losses = 0
    for r in recent_returns:
        if r < 0:
            consecutive_losses += 1
        else:
            consecutive_losses = 0
    
    if consecutive_losses >= 3:
        print(f"\n⚠️ **连续{consecutive_losses}天亏损，建议减仓防御!**")
    
    kb['consecutive_losses'] = consecutive_losses
    save_knowledge(kb)
    
    return report


if __name__ == '__main__':
    generate_daily_reflection()
