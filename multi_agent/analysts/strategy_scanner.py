"""
多策略扫描引擎 - 基于技术信号的选股打分系统
参考 TickFlow 的 Screener/策略架构

为每个标的运行多个策略，输出综合评分
"""
import sys
import os
import json
import warnings
import numpy as np
import pandas as pd

sys.path.insert(0, '/home/zhihu/daily_tracker_analytics/etf_tracker/multi_agent')
from core.data_layer import get_stock_data, calc_technical_indicators, multi_period_backtest, is_stock, get_realtime_price

warnings.filterwarnings('ignore')


# ==================== 策略定义 ====================

def strategy_trend_breakout(df, params=None):
    """趋势突破策略：价格突破MA20且放量"""
    if df is None or len(df) < 25:
        return 0, []
    l = df.iloc[-1]
    reasons = []
    score = 0
    if float(l['close']) > float(l['ma20']):
        score += 25
        reasons.append(f"价格{float(l['close']):.2f}突破MA20({float(l['ma20']):.2f})")
    if l['vol_ratio'] > 1.3:
        score += 15
        reasons.append(f"放量{l['vol_ratio']:.1f}x")
    if l['macd_hist'] > 0:
        score += 10
        reasons.append("MACD柱状翻红")
    return score, reasons


def strategy_golden_cross(df, params=None):
    """金叉策略：MACD金叉 + 均线多头"""
    if df is None or len(df) < 30:
        return 0, []
    l, p = df.iloc[-1], df.iloc[-2]
    reasons = []
    score = 0
    # MACD金叉
    if float(p['macd_dif']) <= float(p['macd_dea']) and float(l['macd_dif']) > float(l['macd_dea']):
        score += 25
        reasons.append("MACD金叉")
    # 均线多头
    if all(float(l[f'ma{x}']) > float(l[f'ma{y}']) for x, y in [(5,10),(10,20)] if pd.notna(l[f'ma{x}']) and pd.notna(l[f'ma{y}'])):
        score += 20
        reasons.append("均线多头排列")
    # RSI合理
    if 30 < float(l['rsi_14']) < 60:
        score += 10
        reasons.append(f"RSI({float(l['rsi_14']):.0f})合理区间")
    return score, reasons


def strategy_oversold_reversal(df, params=None):
    """超跌反弹策略：RSI超卖 + 价格接近布林下轨"""
    if df is None or len(df) < 25:
        return 0, []
    l = df.iloc[-1]
    reasons = []
    score = 0
    if float(l['rsi_14']) < 35:
        score += 30
        reasons.append(f"RSI({float(l['rsi_14']):.0f})超卖区")
    if pd.notna(l['boll_down']) and float(l['close']) <= float(l['boll_down']) * 1.03:
        score += 20
        reasons.append(f"价格接近布林下轨({float(l['boll_down']):.2f})")
    if l['vol_ratio'] < 0.7:
        score += 10
        reasons.append("缩量企稳")
    return score, reasons


def strategy_momentum(df, params=None):
    """动量策略：近N日涨幅 + 成交量配合"""
    if df is None or len(df) < 25:
        return 0, []
    l = df.iloc[-1]
    reasons = []
    score = 0
    mom = float(l['momentum_5d']) if pd.notna(l['momentum_5d']) else 0
    if 3 < mom < 15:
        score += 25
        reasons.append(f"5日涨幅{mom:+.1f}%，温和上涨")
    elif mom > 15:
        score += 10
        reasons.append(f"5日涨幅{mom:+.1f}%较大，注意追高风险")
    else:
        score -= 5
    if pd.notna(l['vol_ratio']) and l['vol_ratio'] > 1.0:
        score += 10
        reasons.append("量能配合")
    return max(0, score), reasons


def strategy_ma_support(df, params=None):
    """均线支撑策略：回调至MA60/MA120获得支撑"""
    if df is None or len(df) < 130:
        return 0, []
    l = df.iloc[-1]
    reasons = []
    score = 0
    cp = float(l['close'])
    for ma_key, ma_name in [('ma60','MA60'), ('ma120','MA120'), ('ma250','MA250')]:
        if pd.notna(l[ma_key]):
            mv = float(l[ma_key])
            if mv < cp < mv * 1.08:
                score += 20
                reasons.append(f"价格({cp})在{ma_name}({mv})上方获得支撑")
                break
    if score > 0 and l['vol_ratio'] < 0.8:
        score += 10
        reasons.append("缩量回踩支撑")
    return score, reasons


def strategy_macd_ma20(df, params=None):
    """金叉放量突破 - MACD金叉且放量站上MA20"""
    if df is None or len(df) < 35:
        return 0, []
    
    l = df.iloc[-1]
    reasons = []
    score = 0
    

    # 站上ma20
    if float(l['ma20']) > 0 and float(l['close']) > float(l['ma20']):
        score += 25
        reasons.append(f"价格{float(l['close']):.2f}突破MA20({float(l['ma20']):.2f})")


    # MACD金叉/柱状
    if float(l['macd_hist']) > 0:
        score += 20
        reasons.append("MACD柱状翻红")
    elif len(df) >= 2:
        p = df.iloc[-2]
        if float(p['macd_dif']) <= float(p['macd_dea']) and float(l['macd_dif']) > float(l['macd_dea']):
            score += 15
            reasons.append("MACD金叉")


    # 放量
    if float(l['vol_ratio']) > 1.3:
        score += 15
        reasons.append(f"放量{float(l['vol_ratio']):.1f}x")

    
    return score, reasons


def strategy_5_macd(df, params=None):
    """连涨启动 - 连续5日上涨且MACD柱状翻红"""
    if df is None or len(df) < 35:
        return 0, []
    
    l = df.iloc[-1]
    reasons = []
    score = 0
    

    # MACD金叉/柱状
    if float(l['macd_hist']) > 0:
        score += 20
        reasons.append("MACD柱状翻红")
    elif len(df) >= 2:
        p = df.iloc[-2]
        if float(p['macd_dif']) <= float(p['macd_dea']) and float(l['macd_dif']) > float(l['macd_dea']):
            score += 15
            reasons.append("MACD金叉")


    # 连续5日上涨
    if len(df) >= 6:
        segment = df.iloc[-5:]
        all_up = all(float(segment.iloc[i]['close']) > float(segment.iloc[i-1]['close'])
                                               for i in range(1, len(segment))) if True else                                           all(float(segment.iloc[i]['close']) < float(segment.iloc[i-1]['close'])
                                               for i in range(1, len(segment)))
        if all_up:
            score += 20
            reasons.append(f"连续5日上涨")

    
    return score, reasons


# 所有策略注册表
STRATEGIES = [
    {"id": "trend_breakout", "name": "趋势突破", "fn": strategy_trend_breakout},
    {"id": "golden_cross", "name": "金叉共振", "fn": strategy_golden_cross},
    {"id": "oversold_reversal", "name": "超跌反弹", "fn": strategy_oversold_reversal},
    {"id": "momentum", "name": "动量策略", "fn": strategy_momentum},
    {"id": "ma_support", "name": "均线支撑", "fn": strategy_ma_support},
    {"id": "macd_ma20", "name": "金叉放量突破", "fn": strategy_macd_ma20},
    {"id": "5_macd", "name": "连涨启动", "fn": strategy_5_macd},
]


def scan_stock(ticker, name="", df=None):
    """
    对单个标的运行所有策略
    
    Returns:
        dict: 策略扫描结果
    """
    if df is None:
        try:
            df, _ = get_stock_data(ticker)
            df = calc_technical_indicators(df)
        except Exception as e:
            return {'ticker': ticker, 'name': name, 'error': str(e)}
    
    results = []
    total_score = 0
    max_possible = 0
    all_reasons = []
    
    for s in STRATEGIES:
        try:
            score, reasons = s['fn'](df)
            results.append({
                'id': s['id'],
                'name': s['name'],
                'score': score,
                'reasons': reasons,
            })
            total_score += score
            max_possible += 100
            all_reasons.extend(reasons)
        except Exception as e:
            results.append({'id': s['id'], 'name': s['name'], 'score': 0, 'reasons': [f"错误: {e}"]})
    
    # 归一化总分
    normalized = round(total_score / max(max_possible, 1) * 100)
    
    # 最佳策略
    best = max(results, key=lambda r: r['score'])
    
    return {
        'ticker': ticker,
        'name': name,
        'total_score': normalized,
        'best_strategy': best['name'],
        'best_score': best['score'],
        'strategy_results': results,
        'all_signals': list(set(all_reasons)),
        'signal_count': len(set(all_reasons)),
    }


def batch_scan(stocks):
    """
    批量扫描多个标的
    
    Args:
        stocks: list of (ticker, name)
    
    Returns:
        list, markdown_text
    """
    print(f"\n{'='*70}")
    print(f"  🔍 多策略扫描")
    print(f"{'='*70}")
    
    results = []
    for ticker, name in stocks:
        r = scan_stock(ticker, name)
        results.append(r)
        if 'error' in r:
            print(f"  ❌ {name}({ticker}): {r['error']}")
        else:
            print(f"  ✅ {name}({ticker}): 总分{r['total_score']}/100 | 最佳策略: {r['best_strategy']}({r['best_score']})")
            # 打印触发信号
            for s in r['strategy_results']:
                if s['score'] > 0:
                    print(f"     └ {s['name']}({s['score']}): {'; '.join(s['reasons'][:2])}")
    
    # 生成Markdown报告
    lines = []
    lines.append(f"## 🔍 多策略技术扫描\n")
    lines.append(f"| 标的 | 综合分 | 最佳策略 | 信号数 |\n")
    lines.append(f"|------|:-----:|:--------:|:-----:|\n")
    for r in results:
        if 'error' not in r:
            lines.append(f"| {r['name']}({r['ticker']}) | {r['total_score']}/100 | {r['best_strategy']}({r['best_score']}) | {r['signal_count']} |\n")
    lines.append(f"\n")
    
    for r in results:
        if 'error' in r:
            continue
        lines.append(f"### {r['name']}({r['ticker']})\n")
        lines.append(f"- **综合评分**: {r['total_score']}/100\n")
        lines.append(f"- **最佳策略**: {r['best_strategy']} ({r['best_score']}分)\n")
        lines.append(f"- **检测信号**: {r['signal_count']}个\n")
        if r['all_signals']:
            for sig in r['all_signals'][:8]:
                lines.append(f"  - 📍 {sig}\n")
        lines.append(f"\n**各策略详情:**\n")
        for s in r['strategy_results']:
            bar = "█" * (s['score'] // 10) + "░" * (10 - min(s['score'] // 10, 10))
            lines.append(f"- {s['name']}: [{bar}] {s['score']}/100\n")
            if s['reasons'] and s['reasons'][0] != '':
                for reason in s['reasons'][:2]:
                    lines.append(f"  → {reason}\n")
        lines.append(f"\n---\n")
    
    return results, "".join(lines)


if __name__ == "__main__":
    stocks = [
        ("601991", "大唐发电"),
        ("515880", "通信ETF"),
        ("516150", "稀土ETF"),
    ]
    results, report = batch_scan(stocks)
    print(report)
