#!/usr/bin/env python3
"""
 LLM 预测器

双模式架构：
  Mode 1 — 数据收集（--collect-only）：采集行情+技术指标，输出 JSON（被 cron 脚本调用）
  Mode 2 — 全量预测：由 Hermes agent 在 cron job 中执行，通过 hermes-invest skill 做 LLM 推理

核心思路：
  传统 ML 模型（LSTM/XGBoost/LightGBM/RF/ARIMA）基于历史数据统计预测，对市场结构变化无感知。
  方法论（易方达基金经理）提供可落地的分析框架：全球视野→找通胀环节→中国比较优势→流动性→低ROE弹性→多维跟踪→周期拼接。
  LLM 运用这套框架 + 实时行情数据做定性+定量分析，输出预测。

用法：
  # 数据收集（被 cron 调用）：
  python predictor.py --collect-only --output /tmp/stock_data.json
  
  # 全量预测（由 Hermes agent 执行）：
  python predictor.py  # 需要可用的 LLM API key
"""

import sys
import os
import json
import re
import argparse
from datetime import datetime
from typing import Dict, Optional, List, Tuple

# 路径设置
BASE = '/home/liudawei/github/daily_tracker_analytics'
sys.path.insert(0, f'{BASE}/etf_tracker')
sys.path.insert(0, f'{BASE}/etf_tracker/multi_agent')
sys.path.insert(0, f'{BASE}/multi_agent')

from core.data_layer import get_stock_data, calc_technical_indicators, get_realtime_price
from core.futures import FUTURES_MAP, CATEGORIES, get_futures_quotes, get_futures_kline_data


# 期货代码白名单（用于识别期货标的）
FUTURES_CODES = {code for code, _ in FUTURES_MAP}


def is_futures(ticker: str) -> bool:
    """判断是否为期货代码"""
    return ticker in FUTURES_CODES


# ============================================================
# 投资方法论（用于 LLM system prompt）
# ============================================================
ZHENGXI_METHODOLOGY = """你是一位运用（易方达权益投资管理部副总经理、基金经理）投资方法论的资深分析师。

投资方法（基于他本人公开表述，2012-2026）：

【核心框架】
从全球视野出发，找正在发生技术/需求变化、并因此"涨价（通胀）"的产业环节；
在该环节里选 ROE 偏低、有修复弹性、且流动性够的标的；
持续多维跟踪、逐步拟合、周期拼接。

【六大分析维度】

1. 景气度/通胀（最重要）
   - 投的是产业景气周期，而非成长性本身
   - 先研究清楚"涨价/通胀从哪来"：传统（供需错配）vs 科技（新技术落地创造需求）
   - 最偏爱新技术落地、供给端创造需求型的科技通胀

2. 技术迭代驱动（科技股本质）
   - 科技股根本驱动力是技术迭代，而非宏观政策或单个公司竞争力
   - "科技公司的本质仍然是周期股"——不对单个公司形成信仰
   - 找产业上升周期里最受益的环节

3. 全球视野 + 中国比较优势
   - 先在全局坐标系判断技术/需求拐点
   - 顺产业链找瓶颈环节
   - 落到中国有比较优势的那一环

4. 选股三要素
   a) 流动性第一：成交量好才能及时退出
   b) 偏好"低ROE→高ROE"修复弹性：景气方向上 ROE 越低，未来向上空间越大
   c) 多维跟踪：从客户/竞争对手/供应链了解公司变化

5. 组合管理
   - 前十大集中度长期<50%
   - 入场即想好退出机制
   - 回撤核心靠选对景气方向，而非仓位管理

6. 周期拼接
   - 回报来自不同产业周期的上升段拼接
   - 用小仓位试探、验证后逐步加仓

【输出格式】
你的分析必须包含辩论式对抗论证（看涨 vs 看跌），输出 JSON 格式如下：
{
  "signal": "bullish/neutral/bearish",
  "confidence": 0.0-1.0,
  "horizon_1d": "看涨/看跌/震荡",
  "horizon_3d": "看涨/看跌/震荡",
  "horizon_5d": "看涨/看跌/震荡",
  "horizon_10d": "看涨/看跌/震荡",
  "key_levels": {"support": "...", "resistance": "..."},
  "investment_framework": {
    "inflation_analysis": "该环节的通胀/涨价逻辑分析",
    "global_perspective": "全球视角下的技术/需求判断",
    "china_advantage": "中国比较优势分析",
    "liquidity": "流动性评估",
    "roe_potential": "ROE修复弹性分析"
  },
  "bull_case": {
    "points": ["看涨理由1", "看涨理由2", "看涨理由3"],
    "score": 0-10
  },
  "bear_case": {
    "points": ["看跌理由1", "看跌理由2", "看跌理由3"],
    "score": 0-10
  },
  "risk_assessment": {
    "level": "低/中/高",
    "max_position": "建议仓位比例",
    "stop_loss_hint": "止损参考"
  },
  "reasoning": "综合辩论后的结论",
  "key_catalysts": ["催化剂1", "催化剂2"],
  "key_risks": ["风险1", "风险2"]
}

【辩论规则】
1. 必须同时列出看涨和看跌理由，不能只写一边
2. bull_case.score 和 bear_case.score 是独立打分的（各0-10），不是互斥的
3. 最终 signal 由 weighted_score = bull_case.score - bear_case.score 决定：
   weighted_score >= 3 → bullish
   weighted_score <= -3 → bearish
   其余 → neutral
4. confidence 反映你对自己的判断有多大把握，不是加权分数本身
5. risk_assessment.max_position 建议："10%-15%" 格式
"""


def collect_futures_data(code: str, name: str = "",
                            category: str = "") -> Dict:
    """
    收集期货品种数据（与 collect_market_data 对应）
    
    Args:
        code: 期货代码如 CU0
        name: 名称如 沪铜
        category: 板块如有色/黑色/能化/农产品
    
    Returns:
        dict: 与 collect_market_data 格式对齐的市场数据
    """
    import pandas as pd

    try:
        # 实时行情
        quotes = get_futures_quotes()
        quote = next((q for q in quotes if q['code'] == code), None)

        # 日K线 + 技术指标
        df = get_futures_kline_data(code)
        if df is None or df.empty:
            return {"error": f"无法获取 {code} 期货K线数据", "ticker": code, "name": name}

        from core.data_layer import calc_technical_indicators
        df = calc_technical_indicators(df)
        latest = df.iloc[-1]
        n = len(df)

        recent_5 = df.tail(min(5, n))
        recent_10 = df.tail(min(10, n))
        recent_20 = df.tail(min(20, n))

        price_change_5d = ((recent_5['close'].iloc[-1] - recent_5['close'].iloc[0])
                          / recent_5['close'].iloc[0] * 100) if len(recent_5) >= 5 else 0
        price_change_10d = ((recent_10['close'].iloc[-1] - recent_10['close'].iloc[0])
                           / recent_10['close'].iloc[0] * 100) if len(recent_10) >= 10 else 0
        price_change_20d = ((recent_20['close'].iloc[-1] - recent_20['close'].iloc[0])
                           / recent_20['close'].iloc[0] * 100) if len(recent_20) >= 20 else 0

        high_20d = float(recent_20['high'].max()) if len(recent_20) >= 5 else 0
        low_20d = float(recent_20['low'].min()) if len(recent_20) >= 5 else 0

        # 板块分类
        cat_name = ""
        for cat_label, members in CATEGORIES.items():
            if code in members:
                cat_name = cat_label
                break

        cp = float(latest['close'])
        market_data = {
            'ticker': code,
            'name': name,
            'sector': f"期货-{cat_name}" if cat_name else "期货",
            'theme': category or cat_name or "期货",
            'is_futures': True,
            'current_price': round(cp, 2),
            'change_pct': round(quote['change_pct'], 2) if quote else 0,
            'price_change_5d': round(price_change_5d, 2),
            'price_change_10d': round(price_change_10d, 2),
            'price_change_20d': round(price_change_20d, 2),
            'high_20d': round(high_20d, 2),
            'low_20d': round(low_20d, 2),
            'volume_ratio': round(float(latest['vol_ratio']), 2) if 'vol_ratio' in df.columns and pd.notna(latest['vol_ratio']) else 0,
            'ma5': round(float(latest['ma5']), 2) if 'ma5' in df.columns and pd.notna(latest['ma5']) else None,
            'ma10': round(float(latest['ma10']), 2) if 'ma10' in df.columns and pd.notna(latest['ma10']) else None,
            'ma20': round(float(latest['ma20']), 2) if 'ma20' in df.columns and pd.notna(latest['ma20']) else None,
            'ma60': round(float(latest['ma60']), 2) if 'ma60' in df.columns and pd.notna(latest['ma60']) else None,
            'rsi_6': round(float(latest['rsi_6']), 1) if 'rsi_6' in df.columns and pd.notna(latest['rsi_6']) else None,
            'rsi_14': round(float(latest['rsi_14']), 1) if 'rsi_14' in df.columns and pd.notna(latest['rsi_14']) else None,
            'macd_hist': round(float(latest['macd_hist']), 4) if 'macd_hist' in df.columns and pd.notna(latest['macd_hist']) else None,
            'boll_up': round(float(latest['boll_up']), 2) if 'boll_up' in df.columns and pd.notna(latest['boll_up']) else None,
            'boll_down': round(float(latest['boll_down']), 2) if 'boll_down' in df.columns and pd.notna(latest['boll_down']) else None,
            'boll_mid': round(float(latest['boll_mid']), 2) if 'boll_mid' in df.columns and pd.notna(latest['boll_mid']) else None,
            'annual_vol': round(float(latest['annual_vol_20d']), 1) if 'annual_vol_20d' in df.columns and pd.notna(latest['annual_vol_20d']) else None,
            'momentum_5d': round(float(latest['momentum_5d']), 4) if 'momentum_5d' in df.columns and pd.notna(latest['momentum_5d']) else None,
            'momentum_20d': round(float(latest['momentum_20d']), 4) if 'momentum_20d' in df.columns and pd.notna(latest['momentum_20d']) else None,
            'kdj_k': round(float(latest['kdj_k']), 1) if 'kdj_k' in df.columns and pd.notna(latest['kdj_k']) else None,
            'kdj_d': round(float(latest['kdj_d']), 1) if 'kdj_d' in df.columns and pd.notna(latest['kdj_d']) else None,
            'collection_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }

        # 额外期货特有数据
        market_data['open_interest'] = round(float(latest.get('volume', 0)), 0)  # 持仓量近似
        market_data['ma_trend'] = ('多头排列' if market_data.get('ma5',0) > market_data.get('ma20',0) > market_data.get('ma60',0)
                                   else '空头排列' if market_data.get('ma5',0) < market_data.get('ma20',0) < market_data.get('ma60',0)
                                   else '震荡整理')

        return market_data

    except Exception as e:
        return {"error": f"期货数据收集失败: {e}", "ticker": code, "name": name}


def collect_market_data(ticker: str, name: str = "",
                        sector: str = "", theme: str = "") -> Dict:
    """
    收集单个标的的市场数据（纯数据采集，无 LLM 调用）
    可被 cron job 调用，输出 JSON 供 Hermes agent 处理。
    """
    import pandas as pd
    import numpy as np

    try:
        # 实时行情
        rt = get_realtime_price(ticker)
        current_price = rt.get('price', 0) if rt else 0
        change_pct = rt.get('change_percent', 0) if rt else 0
        volume = rt.get('volume', 0) if rt else 0
        turnover = rt.get('turnover', 0) if rt else 0

        # 历史数据 + 技术指标
        df, info = get_stock_data(ticker)
        if df is None or df.empty:
            return {"error": f"无法获取 {ticker} 数据", "ticker": ticker, "name": name}

        df = calc_technical_indicators(df)
        latest = df.iloc[-1]

        # 近 N 日价格走势
        n = len(df)
        recent_5 = df.tail(min(5, n))
        recent_10 = df.tail(min(10, n))
        recent_20 = df.tail(min(20, n))

        price_change_5d = ((recent_5['close'].iloc[-1] - recent_5['close'].iloc[0])
                          / recent_5['close'].iloc[0] * 100) if len(recent_5) >= 5 else 0
        price_change_10d = ((recent_10['close'].iloc[-1] - recent_10['close'].iloc[0])
                           / recent_10['close'].iloc[0] * 100) if len(recent_10) >= 10 else 0
        price_change_20d = ((recent_20['close'].iloc[-1] - recent_20['close'].iloc[0])
                           / recent_20['close'].iloc[0] * 100) if len(recent_20) >= 20 else 0

        high_20d = float(recent_20['high'].max()) if len(recent_20) >= 5 else 0
        low_20d = float(recent_20['low'].min()) if len(recent_20) >= 5 else 0

        # 成交额/量趋势
        avg_vol_5d = float(recent_5['volume'].mean()) if len(recent_5) >= 5 else 0
        avg_vol_20d = float(recent_20['volume'].mean()) if len(recent_20) >= 5 else 0

        market_data = {
            'ticker': ticker,
            'name': name,
            'sector': sector,
            'theme': theme,
            'current_price': round(current_price, 3) if current_price else round(float(latest['close']), 3),
            'change_pct': round(change_pct, 2) if change_pct else 0,
            'price_change_5d': round(price_change_5d, 2),
            'price_change_10d': round(price_change_10d, 2),
            'price_change_20d': round(price_change_20d, 2),
            'high_20d': round(high_20d, 3),
            'low_20d': round(low_20d, 3),
            'volume_ratio': round(float(latest['vol_ratio']), 2) if 'vol_ratio' in df.columns and pd.notna(latest['vol_ratio']) else 0,
            'avg_vol_5d': round(float(avg_vol_5d), 0),
            'avg_vol_20d': round(float(avg_vol_20d), 0),
            'ma5': round(float(latest['ma5']), 3) if 'ma5' in df.columns and pd.notna(latest['ma5']) else None,
            'ma10': round(float(latest['ma10']), 3) if 'ma10' in df.columns and pd.notna(latest['ma10']) else None,
            'ma20': round(float(latest['ma20']), 3) if 'ma20' in df.columns and pd.notna(latest['ma20']) else None,
            'ma60': round(float(latest['ma60']), 3) if 'ma60' in df.columns and pd.notna(latest['ma60']) else None,
            'ma120': round(float(latest['ma120']), 3) if 'ma120' in df.columns and pd.notna(latest['ma120']) else None,
            'rsi_6': round(float(latest['rsi_6']), 1) if 'rsi_6' in df.columns and pd.notna(latest['rsi_6']) else None,
            'rsi_14': round(float(latest['rsi_14']), 1) if 'rsi_14' in df.columns and pd.notna(latest['rsi_14']) else None,
            'macd_hist': round(float(latest['macd_hist']), 4) if 'macd_hist' in df.columns and pd.notna(latest['macd_hist']) else None,
            'boll_up': round(float(latest['boll_up']), 3) if 'boll_up' in df.columns and pd.notna(latest['boll_up']) else None,
            'boll_down': round(float(latest['boll_down']), 3) if 'boll_down' in df.columns and pd.notna(latest['boll_down']) else None,
            'boll_mid': round(float(latest['boll_mid']), 3) if 'boll_mid' in df.columns and pd.notna(latest['boll_mid']) else None,
            'annual_vol': round(float(latest['annual_vol_20d']), 1) if 'annual_vol_20d' in df.columns and pd.notna(latest['annual_vol_20d']) else None,
            'momentum_5d': round(float(latest['momentum_5d']), 4) if 'momentum_5d' in df.columns and pd.notna(latest['momentum_5d']) else None,
            'momentum_20d': round(float(latest['momentum_20d']), 4) if 'momentum_20d' in df.columns and pd.notna(latest['momentum_20d']) else None,
            'kdj_k': round(float(latest['kdj_k']), 1) if 'kdj_k' in df.columns and pd.notna(latest['kdj_k']) else None,
            'kdj_d': round(float(latest['kdj_d']), 1) if 'kdj_d' in df.columns and pd.notna(latest['kdj_d']) else None,
            'collection_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }

        if info:
            if isinstance(info, dict):
                market_data['industry'] = info.get('industry', '')
                market_data['sector'] = sector or info.get('sector', '')

        return market_data

    except Exception as e:
        import traceback
        return {"error": f"数据收集失败: {e}", "ticker": ticker, "name": name}


def collect_watchlist(watchlist_path: str = None,
                      output_path: str = None) -> List[Dict]:
    """
    批量收集 watchlist 数据
    
    Args:
        watchlist_path: watchlist.json 路径
        output_path: JSON 输出路径
    
    Returns:
        市场数据列表
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # 加载 watchlist
    if watchlist_path and os.path.exists(watchlist_path):
        with open(watchlist_path, 'r', encoding='utf-8') as f:
            wl = json.load(f)
    else:
        # 默认路径
        default_path = os.path.expanduser(
            '~/github/daily_tracker_analytics/multi_agent/watchlist.json')
        if os.path.exists(default_path):
            with open(default_path, 'r', encoding='utf-8') as f:
                wl = json.load(f)
        else:
            print("❌ 找不到 watchlist.json")
            return []

    items = [(item['ticker'], item.get('name', ''), 
              item.get('sector', ''), item.get('theme', ''))
             for item in wl]

    print(f"📊 开始收集 {len(items)} 个标的的数据...")
    
    # 区分股票/ETF和期货
    stock_items = [(t, n, s, th) for t, n, s, th in items if not is_futures(t)]
    futures_items = [(t, n, s, th) for t, n, s, th in items if is_futures(t)]
    
    print(f"   股票/ETF: {len(stock_items)} | 期货: {len(futures_items)}")

    all_data = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures_map = {}
        # 股票/ETF
        for t, n, s, th in stock_items:
            f = executor.submit(collect_market_data, t, n, s, th)
            futures_map[f] = (t, n, 'stock')
        # 期货
        for t, n, s, th in futures_items:
            f = executor.submit(collect_futures_data, t, n, th)
            futures_map[f] = (t, n, 'futures')
        
        for future in as_completed(futures_map):
            t, n, _type = futures_map[future]
            try:
                result = future.result()
                all_data.append(result)
                if 'error' in result:
                    print(f"  ❌ {n}({t}): {result['error']}")
                else:
                    print(f"  ✅ {n}({t}): {result['current_price']}")
            except Exception as e:
                print(f"  ❌ {n}({t}): {e}")

    # 输出
    if output_path:
        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(all_data, f, ensure_ascii=False, indent=2)
        print(f"\n💾 数据已保存到: {output_path}")

    return all_data


def build_prediction_prompt(item: Dict) -> str:
    """为 Hermes agent 构建预测 prompt"""
    sector_context = f"行业: {item.get('sector', '')}" if item.get('sector') else ""
    theme_context = f"主题: {item.get('theme', '')}" if item.get('theme') else ""

    prompt = f"""请运用投资方法论对以下标的进行 LLM 预测分析。

【标的信息】
代码: {item.get('ticker', '?')}
名称: {item.get('name', '?')}
{sector_context}
{theme_context}

【当前市场数据】
当前价: {item.get('current_price', 'N/A')}
涨跌幅: {item.get('change_pct', 0):.2f}%
近5日涨幅: {item.get('price_change_5d', 0):.2f}%
近10日涨幅: {item.get('price_change_10d', 0):.2f}%
近20日涨幅: {item.get('price_change_20d', 0):.2f}%
20日最高: {item.get('high_20d', 'N/A')}
20日最低: {item.get('low_20d', 'N/A')}
量比: {item.get('volume_ratio', 'N/A')}

【技术指标】
MA5: {item.get('ma5', 'N/A')} | MA10: {item.get('ma10', 'N/A')} | MA20: {item.get('ma20', 'N/A')} | MA60: {item.get('ma60', 'N/A')}
RSI(6): {item.get('rsi_6', 'N/A')} | RSI(14): {item.get('rsi_14', 'N/A')}
MACD柱: {item.get('macd_hist', 'N/A')}
布林上轨: {item.get('boll_up', 'N/A')} | 中轨: {item.get('boll_mid', 'N/A')} | 下轨: {item.get('boll_down', 'N/A')}
年化波动率: {item.get('annual_vol', 'N/A')}%
KDJ_K: {item.get('kdj_k', 'N/A')} | KDJ_D: {item.get('kdj_d', 'N/A')}
5日动量: {item.get('momentum_5d', 'N/A')}
20日动量: {item.get('momentum_20d', 'N/A')}

请按方法论六大维度的标准进行分析预测，并以 JSON 格式输出预测结果。"""
    return prompt


def format_wechat_summary(results: list) -> str:
    """将预测结果格式化为微信推送摘要"""
    lines = []
    lines.append("📊 ** LLM 预测**")
    now = datetime.now()
    lines.append(f"时间: {now.strftime('%Y-%m-%d %H:%M')} ({now.strftime('%A')})")
    lines.append(f"标的数: {len(results)}")
    lines.append("")

    bullish = sum(1 for r in results if r.get('signal') == 'bullish')
    neutral = sum(1 for r in results if r.get('signal') == 'neutral')
    bearish = sum(1 for r in results if r.get('signal') == 'bearish')

    lines.append(f"🟢看涨:{bullish} 🟡震荡:{neutral} 🔴看跌:{bearish}")
    lines.append("")

    for r in results:
        if 'error' in r:
            continue
        name = r.get('name', r.get('ticker', '?'))
        signal = r.get('signal', '?')
        conf = r.get('confidence', 0)

        emoji = {'bullish': '🟢', 'neutral': '🟡', 'bearish': '🔴'}.get(signal, '⚪')

        lines.append(f"{emoji} **{name}** ({r.get('ticker', '')})")
        lines.append(f"> 信号: {signal} | 信心: {conf:.0%} | 5日: {r.get('horizon_5d', '?')}")

        # 辩论：看涨 vs 看跌
        bull = r.get('bull_case', {})
        bear = r.get('bear_case', {})
        if bull and bear:
            b_score = bull.get('score', 0)
            be_score = bear.get('score', 0)
            lines.append(f"> 🗣️ 辩论: 看涨({b_score}) vs 看跌({be_score}) | 净信号{b_score - be_score:+d}")
            b_pts = bull.get('points', [])
            if b_pts:
                lines.append(f">   🟢 {' · '.join(b_pts[:2])}")
            be_pts = bear.get('points', [])
            if be_pts:
                lines.append(f">   🔴 {' · '.join(be_pts[:2])}")

        # 风险评估
        risk = r.get('risk_assessment', {})
        if risk:
            rl = risk.get('level', '')
            pos = risk.get('max_position', '')
            sl = risk.get('stop_loss_hint', '')
            parts = []
            if rl: parts.append(f"风险:{rl}")
            if pos: parts.append(f"仓位:{pos}")
            if sl: parts.append(f"止损:{sl}")
            if parts:
                lines.append(f"> ⚠️ {' | '.join(parts)}")

        # 关键位
        levels = r.get('key_levels', {})
        if levels:
            sup = levels.get('support', '?')
            res = levels.get('resistance', '?')
            lines.append(f"> 📍 支撑{sup} / 阻力{res}")

        reasoning = r.get('reasoning', '')
        if reasoning:
            # 保持简洁，突出核心判断
            short = reasoning[:150].replace('\n', ' ')
            lines.append(f"> 💡 {short}{'...' if len(reasoning) > 150 else ''}")

        catalysts = r.get('key_catalysts', [])
        if catalysts:
            lines.append(f"> 🔥 催化: {' · '.join(catalysts[:3])}")

        risks = r.get('key_risks', [])
        if risks:
            lines.append(f"> ⚠️ 风险: {' · '.join(risks[:3])}")

        lines.append("")

    return "\n".join(lines)


# ============================================================
# 主入口
# ============================================================
if __name__ == "__main__":
    import pandas as pd
    import numpy as np

    parser = argparse.ArgumentParser(description=' LLM 预测器')
    parser.add_argument('--collect-only', action='store_true',
                        help='仅收集数据，不调用 LLM，输出 JSON')
    parser.add_argument('--watchlist', type=str, default=None,
                        help='watchlist.json 路径')
    parser.add_argument('--output', type=str, default='/tmp/stock_data.json',
                        help='数据输出路径（默认: /tmp/stock_data.json）')
    parser.add_argument('--single', type=str, default=None,
                        help='仅分析单个标的，格式: ticker,name,sector,theme')

    args = parser.parse_args()

    print("=" * 60)
    print("  🏛️   LLM 预测器")
    print("=" * 60)

    if args.single:
        # 单个标的
        parts = args.single.split(',')
        ticker = parts[0]
        name = parts[1] if len(parts) > 1 else ''
        sector = parts[2] if len(parts) > 2 else ''
        theme = parts[3] if len(parts) > 3 else ''
        if is_futures(ticker):
            data = collect_futures_data(ticker, name, theme or sector)
        else:
            data = collect_market_data(ticker, name, sector, theme)
        if args.output:
            os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
            with open(args.output, 'w', encoding='utf-8') as f:
                json.dump([data], f, ensure_ascii=False, indent=2)
            print(f"\n💾 数据已保存: {args.output}")
    elif args.collect_only:
        # 批量数据收集
        collect_watchlist(args.watchlist, args.output)
    else:
        # 全量预测模式（需要 LLM API key）
        print("\n⚠️  全量预测模式需要可用的 LLM API key。")
        print("   请设置 LLM_REPORT_API_KEY 或使用 --collect-only 收集数据后由 Hermes agent 处理。")
        print("\n   推荐用法:")
        print("   # 1. 收集数据")
        print("   python predictor.py --collect-only --output /tmp/stock_data.json")
        print("   # 2. Hermes agent cron job 读取数据 + hermes-invest skill 做 LLM 推理\n")
