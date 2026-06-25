#!/usr/bin/env python3
"""
大师思维工具箱 · 模块

集成8位投资大师的思维框架为可量化的分析指标：
- 炒股养家：情绪周期（赚钱效应/亏钱效应/热点分级/赢面仓位）
- 利弗莫尔：关键点突破（最小阻力路线/领头羊识别）
- 巴菲特：护城河分析（基本面定性框架）
- 格雷厄姆：安全边际（估值定量框架）

用于板块扫描器、评分引擎、每日反思的定性补充。
"""

import json
from datetime import datetime
from collections import defaultdict
from typing import Dict, List, Optional, Tuple


# ════════════════════════════════════════════════════
# 炒股养家 · 情绪周期分析
# ════════════════════════════════════════════════════

def sentiment_cycle_analysis(stocks_data: List[Dict]) -> Dict:
    """
    分析市场情绪周期阶段
    
    输入：股票行情列表（每只含 changepercent, trade, amount, turnoverrate, code, name）
    输出：情绪阶段 + 赚钱效应指标
    
    情绪阶段判定：
    - 冰点：上涨比例<20%，亏钱效应主导
    - 回暖：上涨比例20-40%，赚钱效应初现
    - 活跃：上涨比例40-60%，热点清晰
    - 高潮：上涨比例>60%，过度乐观
    - 退潮：上涨比例从高位回落+亏钱板块增多
    """
    if not stocks_data:
        return {'phase': 'unknown', 'confidence': 0}
    
    total = len(stocks_data)
    if total == 0:
        return {'phase': 'unknown', 'confidence': 0}
    
    up_count = sum(1 for s in stocks_data if float(s.get('changepercent', 0)) > 0)
    up_ratio = up_count / total
    
    # 涨停/大跌统计
    limit_up = sum(1 for s in stocks_data if float(s.get('changepercent', 0)) > 9.5)
    limit_down = sum(1 for s in stocks_data if float(s.get('changepercent', 0)) < -9.5)
    big_up = sum(1 for s in stocks_data if 5 < float(s.get('changepercent', 0)) <= 9.5)
    big_down = sum(1 for s in stocks_data if float(s.get('changepercent', 0)) < -5)
    
    # 平均涨跌幅
    avg_chg = sum(float(s.get('changepercent', 0)) for s in stocks_data) / total
    
    # 热点集中度（涨停+大涨占比）
    hot_ratio = (limit_up + big_up) / total
    
    # 情绪阶段判定
    if up_ratio < 0.2:
        phase = '冰点'
        confidence = max(0, 1 - up_ratio * 5)
    elif up_ratio < 0.35:
        phase = '回暖'
        confidence = up_ratio * 2
    elif up_ratio < 0.55:
        phase = '活跃'
        confidence = 0.6 + (up_ratio - 0.35) * 2
    elif up_ratio < 0.7:
        phase = '高潮'
        confidence = 0.7 + (0.7 - up_ratio) * 2  # 越接近0.7越偏向高潮
    else:
        phase = '高潮（过热）'
        confidence = 0.5  # 过热时降低置信度
    
    return {
        'phase': phase,
        'confidence': round(confidence, 2),
        'up_ratio': round(up_ratio, 3),
        'avg_change': round(avg_chg, 2),
        'limit_up_count': limit_up,
        'limit_down_count': limit_down,
        'hot_ratio': round(hot_ratio, 3),
        'up_count': up_count,
        'total': total,
    }


def sector_hot_classification(sector_data: Dict) -> str:
    """
    热点三级分类法（炒股养家）
    
    主流热点：板块内多只涨停+龙头明确+量能放大+持续>3天
    次主流：板块有个股涨停+跟风一般+量能适中
    非主流：零星上涨+无龙头+量能萎缩
    """
    stocks = sector_data.get('stocks', [])
    if not stocks:
        return '非主流'
    
    limit_up = sum(1 for s in stocks if float(s.get('changepercent', 0)) > 9.5)
    big_up = sum(1 for s in stocks if 5 < float(s.get('changepercent', 0)) <= 9.5)
    avg_chg = sum(float(s.get('changepercent', 0)) for s in stocks) / len(stocks)
    total_amt = sum(float(s.get('amount', 0)) for s in stocks)
    
    # 龙头判断（涨幅+成交额综合最高）
    top_stock = max(stocks, key=lambda s: float(s.get('changepercent', 0)) * float(s.get('amount', 0)))
    leader_chg = float(top_stock.get('changepercent', 0))
    
    if limit_up >= 2 and leader_chg > 9.5 and avg_chg > 3:
        return '主流热点'
    elif limit_up >= 1 and avg_chg > 1:
        return '次主流'
    else:
        return '非主流'


def win_rate_position(win_prob: float) -> Tuple[float, str]:
    """
    赢面仓位法（炒股养家）
    
    赢面<60%: 观望或极小仓
    60-70%: 小仓(20-30%)
    70-80%: 中仓(40-60%)
    80-90%: 大仓(60-80%)
    90%+: 重仓(80-100%)
    """
    if win_prob < 0.6:
        return 0.1, '观望或极小仓'
    elif win_prob < 0.7:
        return 0.25, '小仓试探'
    elif win_prob < 0.8:
        return 0.5, '中仓布局'
    elif win_prob < 0.9:
        return 0.7, '大仓出击'
    else:
        return 0.9, '重仓进攻'


# ════════════════════════════════════════════════════
# 利弗莫尔 · 关键点分析
# ════════════════════════════════════════════════════

def key_point_breakout(klines: List[float], lookback: int = 20) -> Dict:
    """
    关键点突破分析
    
    输入：收盘价序列（最近N天）
    返回：是否突破关键点、突破类型、距前高距离
    
    突破类型：
    - 前高突破：创lookback天新高
    - 平台突破：突破近期窄幅整理区间
    - 均线突破：突破重要均线
    """
    if len(klines) < lookback:
        lookback = len(klines)
    if lookback < 5:
        return {'breakout': False, 'type': 'none'}
    
    recent = klines[-lookback:]
    current = recent[-1]
    prev_high = max(recent[:-1])
    prev_low = min(recent[:-1])
    
    # 前高突破
    at_new_high = current >= prev_high
    pct_from_high = (current / prev_high - 1) * 100 if prev_high > 0 else 0
    
    # 平台突破（近段时间窄幅震荡后突破）
    recent_range = recent[-10:] if len(recent) >= 10 else recent
    range_width = (max(recent_range) - min(recent_range)) / min(recent_range) * 100 if min(recent_range) > 0 else 0
    at_plateau_breakout = range_width < 8 and current == max(recent_range)
    
    # 趋势方向判断
    ma5 = sum(klines[-5:]) / 5
    ma10 = sum(klines[-10:]) / 10 if len(klines) >= 10 else ma5
    ma20 = sum(klines[-20:]) / 20 if len(klines) >= 20 else ma10
    uptrend = ma5 > ma10 > ma20
    
    breakout_type = 'none'
    if at_new_high and pct_from_high <= 3:
        breakout_type = '前高突破'
    elif at_plateau_breakout and uptrend:
        breakout_type = '平台突破'
    elif pct_from_high <= 1 and uptrend:
        breakout_type = '逼近前高'
    
    return {
        'breakout': breakout_type != 'none',
        'type': breakout_type,
        'pct_from_high': round(pct_from_high, 2),
        'prev_high': prev_high,
        'current': current,
        'uptrend': uptrend,
        'range_width': round(range_width, 2),
    }


def leader_identification(sector_stocks: List[Dict]) -> Optional[Dict]:
    """
    领头羊识别（利弗莫尔）
    
    板块内涨幅+成交额综合评分最高者
    """
    if not sector_stocks:
        return None
    
    def leader_score(s):
        chg = float(s.get('changepercent', 0))
        amt = float(s.get('amount', 0))
        # 涨幅权重高，成交额作为辅助
        return chg * 0.6 + (amt / 1e8) * 0.4 if amt > 0 else chg
    
    leaders = sorted(sector_stocks, key=lambda s: leader_score(s), reverse=True)
    top = leaders[0] if leaders else None
    if top:
        return {
            'code': top.get('code', ''),
            'name': top.get('name', ''),
            'changepercent': top.get('changepercent', 0),
            'leader_score': round(leader_score(top), 2),
        }
    return None


# ════════════════════════════════════════════════════
# 巴菲特 · 护城河分析框架（定性辅助）
# ════════════════════════════════════════════════════

def moat_indicators(stock: Dict) -> Dict:
    """
    巴菲特护城河定量辅助指标
    
    基于公开行情数据估算护城河强弱
    字段：pe, mktcap, amount, turnoverrate, name
    """
    pe = float(stock.get('pe', 0))
    mktcap = float(stock.get('mktcap', 0))  # 总市值
    amount = float(stock.get('amount', 0))   # 成交额
    tr = float(stock.get('turnoverrate', 0)) # 换手率
    name = stock.get('name', '')
    
    signals = []
    
    # 品牌效应（名称含知名品牌关键词）
    brand_kws = ['茅台', '五粮液', '伊利', '海天', '格力', '美的', '海尔', 
                 '招商', '平安', '中信', '中金', '腾讯', '阿里', '华为', '宁德']
    brand_moat = any(kw in name for kw in brand_kws)
    if brand_moat:
        signals.append('品牌护城河')
    
    # 高市值低换手 = 机构集中持股（护城河强）
    low_turnover_moat = mktcap > 500e8 and tr < 2
    if low_turnover_moat:
        signals.append('机构集中')
    
    # 合理PE高市值 = 稳定的护城河企业
    stable_moat = 15 < pe < 35 and mktcap > 1000e8
    if stable_moat:
        signals.append('稳定龙头')
    
    return {
        'moat_signals': signals,
        'moat_count': len(signals),
        'brand_moat': brand_moat,
        'low_turnover_moat': low_turnover_moat,
        'stable_moat': stable_moat,
    }


# ════════════════════════════════════════════════════
# 格雷厄姆 · 安全边际分析
# ════════════════════════════════════════════════════

def safety_margin_analysis(stock: Dict) -> Dict:
    """
    格雷厄姆安全边际分析
    
    输入：单只股票行情数据
    输出：安全边际评分和信号
    
    定量标准：
    - PE低于行业平均60% → 低估
    - PB < 1.5 → 破净/接近
    - 股价低于净流动资产2/3 → 烟蒂
    """
    pe = float(stock.get('pe', 0))
    mktcap = float(stock.get('mktcap', 0))
    price = float(stock.get('trade', 0))
    name = stock.get('name', '')
    
    signals = []
    score = 0
    
    # PE安全边际
    if 0 < pe <= 10:
        signals.append('极端低估(PE<10)')
        score += 3
    elif 10 < pe <= 15:
        signals.append('低估(PE 10-15)')
        score += 2
    elif 15 < pe <= 25:
        signals.append('合理估值')
        score += 1
    elif pe <= 0:
        signals.append('亏损(无安全边际)')
        score -= 2
    elif pe > 50:
        signals.append('高估(PE>50)')
        score -= 1
    
    # 市值安全边际
    if mktcap > 0:
        if mktcap < 50e8:
            signals.append('小微市值(高风险)')
            score -= 1
        elif mktcap < 200e8:
            signals.append('中小市值(弹性大)')
            score += 1
    
    return {
        'graham_score': score,
        'signals': signals,
        'pe_safe': 0 < pe <= 15,
        'verdict': '有安全边际' if score >= 2 else '安全边际不足' if score > 0 else '风险偏高',
    }


# ════════════════════════════════════════════════════
# 综合大师评分调整
# ════════════════════════════════════════════════════

def master_adjustment(stock: Dict, sector_data: Optional[Dict] = None,
                      klines: Optional[List[float]] = None) -> Dict:
    """
    综合大师思维框架的评分调整
    
    返回调整分 + 理由
    """
    adjustments = []
    total_adj = 0
    
    # 1. 巴菲特护城河加成
    moat = moat_indicators(stock)
    if moat['moat_count'] >= 2:
        total_adj += 3
        adjustments.append(f"护城河+3({','.join(moat['moat_signals'])})")
    elif moat['moat_count'] >= 1:
        total_adj += 1
        adjustments.append(f"护城河+1({','.join(moat['moat_signals'])})")
    
    # 2. 格雷厄姆安全边际
    gm = safety_margin_analysis(stock)
    if gm['graham_score'] >= 2:
        total_adj += gm['graham_score']
        adjustments.append(f"安全边际+{gm['graham_score']}({','.join(gm['signals'])})")
    
    # 3. 利弗莫尔关键点
    if klines and len(klines) >= 5:
        kp = key_point_breakout(klines)
        if kp['breakout']:
            if kp['type'] == '前高突破':
                total_adj += 2
                adjustments.append(f"关键点+2({kp['type']})")
            elif kp['type'] == '平台突破':
                total_adj += 3
                adjustments.append(f"关键点+3({kp['type']})")
            if kp['uptrend']:
                total_adj += 1
                adjustments.append("上升趋势+1")
    
    # 4. 炒股养家 - 板块热点加分
    if sector_data:
        hot_class = sector_hot_classification({'stocks': sector_data})
        if hot_class == '主流热点' and sector_data:
            leader = leader_identification(sector_data)
            if leader and leader.get('code') == stock.get('code'):
                total_adj += 2
                adjustments.append(f"板块龙头+2({hot_class})")
    
    return {
        'adjustment': total_adj,
        'reasons': adjustments,
        'moat': moat,
        'graham': gm,
    }


# ════════════════════════════════════════════════════
# 大师思维映射（用于每日反思）
# ════════════════════════════════════════════════════

MASTER_PERSPECTIVES = {
    '巴菲特': {
        'check': ['买入的企业价值变了吗？', '还有安全边际吗？', '护城河还在吗？'],
        'principle': '以合理价格买入伟大公司，然后什么都不做',
    },
    '利弗莫尔': {
        'check': ['最小阻力方向是什么？', '关键点被突破了吗？', '趋势还在吗？'],
        'principle': '不预测，只等市场证明。关键点突破前不动。',
    },
    '炒股养家': {
        'check': ['赚钱效应还在吗？', '情绪周期在哪个阶段？', '我是在做对的事吗？'],
        'principle': '做的是短线，但看的是更大的局。买入机会，卖出风险。',
    },
    '格雷厄姆': {
        'check': ['价格低于内在价值吗？', '有足够的安全边际吗？', '分散够了吗？'],
        'principle': '价格是你付出的，价值是你得到的。安全边际是投资的核心。',
    },
}


def get_reflection_prompts() -> List[str]:
    """生成每日反思的大师视角提示"""
    prompts = []
    for master, info in MASTER_PERSPECTIVES.items():
        for check in info['check']:
            prompts.append(f"[{master}] {check}")
    return prompts
