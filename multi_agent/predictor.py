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

# 证据链模块（P0 新增）
try:
    from evidence import (
        Evidence, EvidenceType, EvidenceLevel,
        AnalysisResultWithEvidence, Condition, Scenario,
        convert_prediction_to_evidence_result,
        enrich_prompt_with_evidence_rules
    )
    EVIDENCE_CHAIN_AVAILABLE = True
except ImportError:
    EVIDENCE_CHAIN_AVAILABLE = False
    print("⚠️  evidence.py 未找到，证据链功能不可用")


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
# 证据链功能（P0 新增）
# ============================================================

def build_evidence_enriched_prompt(item: Dict) -> str:
    """
    构建带证据规则的预测 prompt
    
    借鉴 easy-stock 的证据链设计
    """
    base_prompt = build_prediction_prompt(item)
    
    if not EVIDENCE_CHAIN_AVAILABLE:
        return base_prompt
    
    # 注入证据规则
    enriched = enrich_prompt_with_evidence_rules(base_prompt)
    
    # 添加证据输出格式要求
    evidence_format = """
【证据链输出要求】
请在 reasoning 字段中包含以下证据信息：
1. support_points: 支持看涨的具体证据（引用市场数据）
2. counter_points: 支持看跌的具体证据（引用市场数据）
3. evidence_level: 证据充分度（sufficient/limited/insufficient）
4. key_conditions: 失效条件（如"若跌破XX则判断失效"）
5. source_refs: 引用的数据来源编号（如 m-price-001, m-tech-001）

示例：
{
  "signal": "bullish",
  "confidence": 0.65,
  ...
  "support_points": ["MACD金叉且放量", "板块资金净流入"],
  "counter_points": ["RSI超买，短期可能回调"],
  "evidence_level": "limited",
  "key_conditions": ["若跌破MA20则看涨失效"],
  "source_refs": ["m-price-001", "m-tech-001", "m-fund-001"]
}
"""
    return enriched + evidence_format


def convert_to_evidence_result(
    prediction: Dict,
    market_data: Dict
) -> Optional[AnalysisResultWithEvidence]:
    """
    将预测结果转换为带证据链的分析结果
    
    Args:
        prediction: LLM 预测结果
        market_data: 市场数据
        
    Returns:
        AnalysisResultWithEvidence 或 None（如果模块不可用）
    """
    if not EVIDENCE_CHAIN_AVAILABLE:
        return None
    
    return convert_prediction_to_evidence_result(prediction, market_data)


def save_evidence_result(
    result: AnalysisResultWithEvidence,
    output_dir: str = None
) -> str:
    """
    保存证据链结果到 JSON 文件
    
    Args:
        result: 分析结果
        output_dir: 输出目录（默认 multi_agent/data/evidence）
        
    Returns:
        保存的文件路径
    """
    if output_dir is None:
        output_dir = os.path.join(BASE, 'multi_agent', 'data', 'evidence')
    
    os.makedirs(output_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"{result.ticker}_{timestamp}.json"
    filepath = os.path.join(output_dir, filename)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(result.to_json())
    
    return filepath


def generate_evidence_report(
    results: List[AnalysisResultWithEvidence],
    output_path: str = None
) -> str:
    """
    生成证据链报告（HTML 格式）
    
    Args:
        results: 分析结果列表
        output_path: 输出路径（可选）
        
    Returns:
        HTML 报告内容
    """
    if not results:
        return "<p>无证据链数据</p>"
    
    html = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>证据链分析报告</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; 
         background: #0f172a; color: #e2e8f0; margin: 0; padding: 24px; }
  h1 { color: #60a5fa; font-size: 24px; margin-bottom: 8px; }
  .subtitle { color: #94a3b8; margin-bottom: 24px; }
  .card { background: #1e293b; border-radius: 12px; padding: 20px; margin-bottom: 16px; }
  .headline { font-size: 18px; font-weight: 600; color: #f1f5f9; margin-bottom: 8px; }
  .evidence-level { display: inline-block; padding: 4px 12px; border-radius: 12px; font-size: 12px; }
  .level-sufficient { background: #166534; color: #86efac; }
  .level-limited { background: #854d0e; color: #fde047; }
  .level-insufficient { background: #991b1b; color: #fca5a5; }
  .section { margin-top: 16px; }
  .section-title { font-size: 14px; font-weight: 600; color: #94a3b8; margin-bottom: 8px; 
                   text-transform: uppercase; letter-spacing: 0.5px; }
  .evidence-item { background: #0f172a; padding: 12px; border-radius: 8px; margin-bottom: 8px;
                   border-left: 3px solid #334155; }
  .evidence-item.support { border-left-color: #22c55e; }
  .evidence-item.counter { border-left-color: #ef4444; }
  .evidence-type { display: inline-block; padding: 2px 8px; border-radius: 8px; font-size: 11px;
                   margin-right: 8px; }
  .type-fact { background: #1e40af; color: #93c5fd; }
  .type-opinion { background: #7c3aed; color: #c4b5fd; }
  .type-inference { background: #0e7490; color: #67e8f9; }
  .source-id { color: #64748b; font-size: 12px; font-family: monospace; }
  .condition { background: #1e293b; padding: 12px; border-radius: 8px; margin-bottom: 8px; }
  .condition-id { color: #f59e0b; font-weight: 600; }
  .scenario { background: #1e293b; padding: 12px; border-radius: 8px; margin-bottom: 8px; }
  .scenario-key { display: inline-block; padding: 2px 8px; border-radius: 8px; font-size: 12px;
                  margin-right: 8px; }
  .key-strong { background: #166534; color: #86efac; }
  .key-base { background: #1e40af; color: #93c5fd; }
  .key-weak { background: #991b1b; color: #fca5a5; }
  .baseline-relation { margin-top: 16px; padding: 12px; background: #1e293b; border-radius: 8px; }
  .timestamp { color: #64748b; font-size: 12px; margin-top: 8px; }
</style>
</head>
<body>
  <h1>📋 证据链分析报告</h1>
  <div class="subtitle">生成时间: """ + datetime.now().strftime('%Y-%m-%d %H:%M:%S') + """</div>
"""
    
    for result in results:
        # 证据充分度样式
        level_class = f"level-{result.evidence_level}"
        
        html += f"""
  <div class="card">
    <div class="headline">{result.headline}</div>
    <div>
      <span class="evidence-level {level_class}">{result.evidence_level}</span>
      <span style="color: #64748b; font-size: 13px; margin-left: 12px;">
        {result.name} ({result.ticker})
      </span>
    </div>
    
    <div class="section">
      <div class="section-title">核心判断</div>
      <p>{result.thesis}</p>
    </div>
"""
        
        # 支持证据
        if result.support:
            html += """
    <div class="section">
      <div class="section-title">✅ 支持证据</div>
"""
            for ev in result.support:
                type_class = f"type-{ev.source_type}"
                html += f"""
      <div class="evidence-item support">
        <span class="evidence-type {type_class}">{ev.source_type}</span>
        <span class="source-id">[{ev.source_id}]</span>
        <div style="margin-top: 4px;">{ev.content}</div>
      </div>
"""
            html += "    </div>\n"
        
        # 反对证据
        if result.counter:
            html += """
    <div class="section">
      <div class="section-title">❌ 反对证据</div>
"""
            for ev in result.counter:
                type_class = f"type-{ev.source_type}"
                html += f"""
      <div class="evidence-item counter">
        <span class="evidence-type {type_class}">{ev.source_type}</span>
        <span class="source-id">[{ev.source_id}]</span>
        <div style="margin-top: 4px;">{ev.content}</div>
      </div>
"""
            html += "    </div>\n"
        
        # 失效条件
        if result.conditions:
            html += """
    <div class="section">
      <div class="section-title">⚠️ 失效条件</div>
"""
            for cond in result.conditions:
                html += f"""
      <div class="condition">
        <span class="condition-id">{cond.condition_id}</span>
        <span style="margin-left: 8px;">{cond.text}</span>
        <div style="color: #64748b; font-size: 12px; margin-top: 4px;">
          指标: {cond.metric} | 窗口: {cond.window} | 状态: {cond.status}
        </div>
      </div>
"""
            html += "    </div>\n"
        
        # 情景分析
        if result.scenarios:
            html += """
    <div class="section">
      <div class="section-title">📊 情景分析</div>
"""
            for scenario in result.scenarios:
                key_class = f"key-{scenario.key}"
                html += f"""
      <div class="scenario">
        <span class="scenario-key {key_class}">{scenario.key}</span>
        <strong>{scenario.name}</strong>
        <div style="margin-top: 4px; color: #94a3b8;">{scenario.description}</div>
        <div style="margin-top: 4px;">应对: {scenario.response}</div>
      </div>
"""
            html += "    </div>\n"
        
        # 基线关系
        if result.baseline_relation != "insufficient":
            relation_emoji = "✅" if result.baseline_relation == "agree" else "⚠️"
            html += f"""
    <div class="baseline-relation">
      {relation_emoji} 基线关系: <strong>{result.baseline_relation}</strong>
      {f" - {result.baseline_reason}" if result.baseline_reason else ""}
    </div>
"""
        
        html += f"""
    <div class="timestamp">分析时间: {result.created_at} | 模型版本: {result.model_version}</div>
  </div>
"""
    
    html += """
</body>
</html>
"""
    
    # 保存到文件
    if output_path:
        os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)
    
    return html


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
