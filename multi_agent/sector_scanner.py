#!/usr/bin/env python3
"""
全量扫描 + 板块推荐引擎

流程：
  1. 收集全量数据（131标的，~30s）
  2. 机械打分（趋势+动量+流动性+弹性，即时）
  3. 按板块分组取前5 → LLM精选前3
  4. 输出推荐报告

用法：
  python3 sector_scanner.py --output /tmp/sector_top3.json
"""

import sys
import os
import json
import argparse
from datetime import datetime
from typing import Dict, List, Tuple

BASE = '/home/liudawei/github/daily_tracker_analytics'
sys.path.insert(0, f'{BASE}/etf_tracker')
sys.path.insert(0, f'{BASE}/multi_agent')

from predictor import collect_market_data, collect_futures_data, collect_watchlist, is_futures
from predictor import build_prediction_prompt, format_wechat_summary


def compute_composite_score(item: Dict) -> Dict:
    """
    机械打分（0-100）
    
    六大维度量化：
    1. 趋势得分 (30%) - 均线排列
    2. 动量得分 (20%) - RSI位置
    3. 弹性得分 (15%) - 波动率
    4. 流动性得分 (15%) - 量比
    5. 通胀逻辑得分 (10%) - 板块标签
    6. 近期走势 (10%) - 5d+20d综合
    """
    scores = {}
    
    # 1. 趋势得分 (30分)
    trend_score = 15  # 默认中性
    ma5 = item.get('ma5')
    ma10 = item.get('ma10')
    ma20 = item.get('ma20')
    cp = item.get('current_price', 0)
    if ma5 and ma10 and ma20 and cp:
        if ma5 > ma10 > ma20:  # 多头排列
            trend_score = 28
            # 越接近MA5表现越好
            if cp >= ma5:
                trend_score = 30
        elif ma5 > ma20 and cp > ma20:  # 偏多
            trend_score = 22
        elif ma20 > ma5 > ma10:  # 空头排列
            trend_score = 10
            if cp < ma20 * 0.95:  # 严重跌破
                trend_score = 5
        elif ma5 < ma20 and cp > ma5:  # 偏弱
            trend_score = 14
    scores['trend'] = min(trend_score, 30)
    
    # 2. 动量得分 (20分)
    rsi = item.get('rsi_14', 50)
    if rsi is not None:
        if 40 <= rsi <= 60:  # 中性区间，有上涨空间
            rsi_score = 16
        elif 30 <= rsi < 40:  # 偏弱但可能超卖反转
            rsi_score = 14
        elif 60 < rsi <= 70:  # 偏强但接近超买
            rsi_score = 14
        elif rsi < 30:  # 超卖区，可能反弹（偏好：低ROE弹性机会）
            rsi_score = 12
        elif rsi > 70:  # 超买区，风险高
            rsi_score = 6
        else:
            rsi_score = 10
    else:
        rsi_score = 10
    scores['momentum'] = rsi_score
    
    # 3. 弹性得分 (15分)
    vol = item.get('annual_vol', 0)
    if vol is not None:
        if 30 <= vol <= 70:  # 适中波动率，偏好的弹性范围
            vol_score = 13
        elif 20 <= vol < 30:
            vol_score = 10
        elif 70 < vol <= 100:
            vol_score = 11
        elif vol > 100:  # 波动过大
            vol_score = 7
        elif vol < 20:  # 波动太小，没弹性
            vol_score = 6
        else:
            vol_score = 9
    else:
        vol_score = 8
    scores['volatility'] = vol_score
    
    # 4. 流动性得分 (15分)
    vr = item.get('volume_ratio', 0)
    if vr is not None:
        if vr >= 1.5:  # 放量，活跃
            liq_score = 14
        elif 1.0 <= vr < 1.5:
            liq_score = 12
        elif 0.5 <= vr < 1.0:
            liq_score = 9
        else:  # 缩量
            liq_score = 6
    else:
        liq_score = 8
    scores['liquidity'] = liq_score
    
    # 5. 通胀逻辑得分 (10分)
    sector = item.get('sector', '')
    theme = item.get('theme', '')
    text = f"{sector} {theme}"
    
    # 偏好的科技通胀板块
    inflation_boost = ['半导体', '芯片', 'AI', '人工智能', '机器人', '通信', '光通信', 
                       '算力', '设备', '信息', '科技', '稀土', '永磁']
    # 不感兴趣的
    no_go = ['消费', '银行', '食品', '饮料', '白酒', '地产', '农业', '畜牧']
    
    infl_score = 6  # 默认中性
    if any(k in text for k in inflation_boost):
        infl_score = 9
    if any(k in text for k in no_go):
        infl_score = 4
    # 期货的特殊处理
    if item.get('is_futures'):
        fut_cat = sector.replace('期货-', '')
        if fut_cat in ['有色', '能化']:
            infl_score = 7  # 商品通胀逻辑
        else:
            infl_score = 5
    
    scores['inflation'] = infl_score
    
    # 6. 近期走势 (10分)
    chg5 = item.get('price_change_5d', 0)
    chg20 = item.get('price_change_20d', 0)
    
    if chg5 is not None and chg20 is not None:
        # 5日和20日综合：过快上涨不好（追高风险），过快下跌也不好（趋势坏）
        if -5 < chg5 < 10 and chg20 > 0:
            perf_score = 9
        elif -10 < chg5 < 15 and chg20 > -5:
            perf_score = 7
        elif chg5 > 15 or chg20 > 30:  # 涨太多追高
            perf_score = 5
        elif chg5 < -15 or chg20 < -20:  # 跌太多趋势坏
            perf_score = 3
        else:
            perf_score = 6
    else:
        perf_score = 5
    scores['performance'] = perf_score
    
    total = sum(scores.values())
    scores['total'] = total
    
    return scores


def scan_and_rank(data_path: str) -> Dict:
    """
    全量扫描 + 板块推荐
    
    Returns:
        {板块名: [前3推荐标的]}
    """
    with open(data_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    items = [d for d in data if 'error' not in d]
    
    # 计算每个标的的评分
    for item in items:
        scores = compute_composite_score(item)
        item['_composite_score'] = scores['total']
        item['_score_detail'] = scores
    
    # 按板块分组
    sectors = {}
    for item in items:
        # 确定板块
        if item.get('is_futures'):
            sector = item.get('sector', '期货')
        else:
            # 优先用theme，其次sector
            sector = item.get('theme', item.get('sector', '其他'))
        
        sector = sector or '其他'
        if sector not in sectors:
            sectors[sector] = []
        sectors[sector].append(item)
    
    # 每个板块内按评分排序，取前5
    top_picks = {}
    all_ranked = []
    
    for sector, s_items in sorted(sectors.items()):
        s_items.sort(key=lambda x: x.get('_composite_score', 0), reverse=True)
        top5 = s_items[:5]
        top_picks[sector] = [
            {
                'ticker': item.get('ticker', ''),
                'name': item.get('name', ''),
                'price': item.get('current_price', 0),
                'score': item.get('_composite_score', 0),
                'score_detail': item.get('_score_detail', {}),
                'rsi_14': item.get('rsi_14'),
                'change_5d': item.get('price_change_5d', 0),
                'change_20d': item.get('price_change_20d', 0),
                'volume_ratio': item.get('volume_ratio', 0),
                'is_futures': item.get('is_futures', False),
            }
            for item in top5
        ]
        all_ranked.extend(top_picks[sector])
    
    # 全局排名
    all_ranked.sort(key=lambda x: x['score'], reverse=True)
    
    return {
        'scan_time': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'total_scanned': len(items),
        'sectors': len(sectors),
        'top_by_sector': top_picks,
        'global_top10': all_ranked[:10],
    }


def format_top3_wechat(result: Dict) -> str:
    """格式化微信推送（板块前3推荐）"""
    lines = []
    lines.append(f"📊 ** · 板块推荐**")
    lines.append(f"扫描时间: {result['scan_time']}")
    lines.append(f"扫描标的: {result['total_scanned']}个 | 板块: {result['sectors']}个")
    lines.append("")
    
    for sector, picks in sorted(result['top_by_sector'].items()):
        # 图标
        if any(k in sector for k in ['半导体','芯片','AI','人工智能','机器人','通信']):
            icon = '🏭'
        elif any(k in sector for k in ['有色','稀土','永磁','金属','矿业','煤炭']):
            icon = '🔩'
        elif any(k in sector for k in ['期货']):
            icon = '📉'
        elif any(k in sector for k in ['银行','消费','高股息','红利']):
            icon = '🏦'
        else:
            icon = '📊'
        
        lines.append(f"\n{icon} **{sector}** 推荐 Top 3:")
        for i, p in enumerate(picks[:3], 1):
            emoji = {1: '🥇', 2: '🥈', 3: '🥉'}[i]
            score = p['score']
            rsi = p.get('rsi_14', 'N/A')
            chg5 = p.get('change_5d', 0)
            
            # 评分颜色
            if score >= 70:
                score_str = f"🟢{score}"
            elif score >= 55:
                score_str = f"🟡{score}"
            else:
                score_str = f"🔴{score}"
            
            futures_tag = " [期货]" if p.get('is_futures') else ""
            lines.append(
                f"  {emoji} **{p['name']}**({p['ticker']}){futures_tag} "
                f"@{p['price']} 评分{score_str} "
                f"RSI{rsi} 5d:{chg5:+.1f}%"
            )
    
    lines.append("")
    lines.append("📌 **评分说明：**")
    lines.append("> 趋势30分+动量20分+弹性15分+流动性15分+通胀逻辑10分+近期走势10分")
    lines.append("> 🟢≥70  🟡55-69  🔴<55")
    lines.append("")
    lines.append("*研究辅助，非投资建议*")
    
    return "\n".join(lines)


def build_llm_prompt_for_top3(result: Dict) -> str:
    """为LLM构建深度分析prompt（针对前3标的）"""
    lines = ["请用方法论对以下板块前3推荐做深度分析。每个标的用300字以内说明："]
    lines.append("1. 为什么下看好/不看好")
    lines.append("2. 当前产业周期位置")
    lines.append("3. 看涨vs看跌关键因素")
    lines.append("4. 操作建议（关注/买入/观望/回避）")
    lines.append("")
    
    for sector, picks in sorted(result['top_by_sector'].items()):
        lines.append(f"=== {sector} ===")
        for p in picks[:3]:
            sd = p.get('score_detail', {})
            lines.append(f"  {p['name']}({p['ticker']}) @{p['price']}")
            lines.append(f"    总分{p['score']}/100 | 趋势{sd.get('trend',0)} 动量{sd.get('momentum',0)} 弹性{sd.get('volatility',0)} 流动性{sd.get('liquidity',0)} 通胀{sd.get('inflation',0)} 走势{sd.get('performance',0)}")
            lines.append(f"    RSI14={p.get('rsi_14','N/A')} 5d:{p.get('change_5d',0):+.1f}%")
            lines.append("")
    
    return "\n".join(lines)


# ============================================================
# CLI
# ============================================================
if __name__ == "__main__":
    import pandas as pd
    import numpy as np
    
    parser = argparse.ArgumentParser(description='全量扫描+板块推荐')
    parser.add_argument('--input', type=str, default='/tmp/stock_cron_data.json',
                        help='行情数据JSON路径')
    parser.add_argument('--collect', action='store_true',
                        help='先收集数据再扫描')
    parser.add_argument('--output', type=str, default='/tmp/sector_top3.json',
                        help='推荐结果输出路径')
    
    args = parser.parse_args()
    
    print(f"\n{'='*60}")
    print(f"  🏛️   · 全量板块扫描")
    print(f"{'='*60}")
    
    # 数据收集
    if args.collect or not os.path.exists(args.input):
        print(f"\n📡 收集行情数据...")
        data_file = args.input
        collect_watchlist(output_path=data_file)
    else:
        data_file = args.input
        print(f"\n📂 使用缓存数据: {data_file}")
    
    # 扫描排名
    print(f"\n🔍 扫描全量标的...")
    result = scan_and_rank(data_file)
    
    print(f"   扫描: {result['total_scanned']} 标的, {result['sectors']} 个板块")
    
    # 输出板块推荐
    print(f"\n{'='*60}")
    print(f"  板块推荐 Top 3")
    print(f"{'='*60}")
    
    for sector, picks in sorted(result['top_by_sector'].items()):
        print(f"\n  {sector}:")
        for i, p in enumerate(picks[:3], 1):
            print(f"    {i}. {p['name']}({p['ticker']}) 评分{p['score']} @{p['price']} RSI{p.get('rsi_14','?')}")
    
    # 全局Top10
    print(f"\n{'='*60}")
    print(f"  🏆 全局 Top 10")
    print(f"{'='*60}")
    for i, p in enumerate(result['global_top10'], 1):
        sd = p.get('score_detail', {})
        print(f"  {i:2d}. {p['name']:12s}({p['ticker']:8s}) 评分{p['score']:3d} | "
              f"趋势{sd.get('trend',0):2d} 动量{sd.get('momentum',0):2d} "
              f"弹性{sd.get('volatility',0):2d} 流动性{sd.get('liquidity',0):2d} "
              f"通胀{sd.get('inflation',0):2d}  RSI{p.get('rsi_14',0):5.1f}")
    
    # 保存
    if args.output:
        os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n💾 结果已保存: {args.output}")
    
    # 微信格式
    print(f"\n{'='*60}")
    print(format_top3_wechat(result))
