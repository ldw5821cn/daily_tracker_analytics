"""
技术面分析师 - 量化技术指标分析
对应 TradingAgents-CN 的 market_analyst
"""
import sys
sys.path.insert(0, '/home/zhihu/daily_tracker_analytics/etf_tracker')
sys.path.insert(0, '/home/zhihu/daily_tracker_analytics/etf_tracker/multi_agent')

from core.data_layer import get_stock_data, calc_technical_indicators, multi_period_backtest
import pandas as pd
import numpy as np
import json


def analyze(ticker, name="", current_date="2026-07-02"):
    """
    技术面多维度分析
    
    输出: dict 包含技术评分、指标数据、回测结果
    """
    df, info = get_stock_data(ticker)
    df = calc_technical_indicators(df)
    
    latest = df.iloc[-1]
    cp = float(latest['close'])
    
    # ========== 1. 多周期回测 ==========
    backtest_results = multi_period_backtest(df)
    
    # ========== 2. 当前技术指标快照 ==========
    tech_snapshot = {
        'current_price': round(cp, 2),
        'ma5': round(float(latest['ma5']), 2) if pd.notna(latest['ma5']) else None,
        'ma10': round(float(latest['ma10']), 2) if pd.notna(latest['ma10']) else None,
        'ma20': round(float(latest['ma20']), 2) if pd.notna(latest['ma20']) else None,
        'ma60': round(float(latest['ma60']), 2) if pd.notna(latest['ma60']) else None,
        'ma120': round(float(latest['ma120']), 2) if pd.notna(latest['ma120']) else None,
        'macd_dif': round(float(latest['macd_dif']), 4) if pd.notna(latest['macd_dif']) else 0,
        'macd_dea': round(float(latest['macd_dea']), 4) if pd.notna(latest['macd_dea']) else 0,
        'macd_hist': round(float(latest['macd_hist']), 4) if pd.notna(latest['macd_hist']) else 0,
        'rsi_6': round(float(latest['rsi_6']), 1) if pd.notna(latest['rsi_6']) else 0,
        'rsi_14': round(float(latest['rsi_14']), 1) if pd.notna(latest['rsi_14']) else 0,
        'rsi_24': round(float(latest['rsi_24']), 1) if pd.notna(latest['rsi_24']) else 0,
        'kdj_k': round(float(latest['kdj_k']), 1) if pd.notna(latest['kdj_k']) else 0,
        'kdj_d': round(float(latest['kdj_d']), 1) if pd.notna(latest['kdj_d']) else 0,
        'kdj_j': round(float(latest['kdj_j']), 1) if pd.notna(latest['kdj_j']) else 0,
        'boll_up': round(float(latest['boll_up']), 2) if pd.notna(latest['boll_up']) else None,
        'boll_mid': round(float(latest['boll_mid']), 2) if pd.notna(latest['boll_mid']) else None,
        'boll_down': round(float(latest['boll_down']), 2) if pd.notna(latest['boll_down']) else None,
        'vol_ratio': round(float(latest['vol_ratio']), 2) if pd.notna(latest['vol_ratio']) else 0,
        'annual_vol': round(float(latest['annual_vol_20d']), 1) if pd.notna(latest['annual_vol_20d']) else 0,
        'momentum_5d': round(float(latest['momentum_5d']), 2) if pd.notna(latest['momentum_5d']) else 0,
        'momentum_20d': round(float(latest['momentum_20d']), 2) if pd.notna(latest['momentum_20d']) else 0,
    }
    
    # ========== 3. 信号检测 ==========
    signals = []
    
    # 均线排列
    if all(pd.notna(latest[f'ma{x}']) for x in [5, 10, 20]):
        is_bull = (float(latest['ma5']) > float(latest['ma10']) > float(latest['ma20']))
        is_bear = (float(latest['ma5']) < float(latest['ma10']) < float(latest['ma20']))
        if is_bull:
            signals.append(("🟢", "均线多头排列", "MA5>MA10>MA20，趋势向上"))
        elif is_bear:
            signals.append(("🔴", "均线空头排列", "MA5<MA10<MA20，趋势向下"))
        else:
            signals.append(("🟡", "均线交叉", "短期均线缠绕，方向待选择"))
    
    # MACD状态
    if pd.notna(latest['macd_hist']):
        if latest['macd_hist'] > 0:
            signals.append(("🟢", "MACD柱状翻红", "动能转正，短期看多"))
        else:
            signals.append(("🔴", "MACD柱状翻绿", "动能转负，短期看空"))
    
    # 金叉死叉
    if len(df) >= 2 and pd.notna(latest['macd_dif']) and pd.notna(df.iloc[-2]['macd_dif']):
        prev_dif = float(df.iloc[-2]['macd_dif'])
        prev_dea = float(df.iloc[-2]['macd_dea'])
        cur_dif = float(latest['macd_dif'])
        cur_dea = float(latest['macd_dea'])
        if prev_dif <= prev_dea and cur_dif > cur_dea:
            signals.append(("🟢", "MACD金叉", "DIF上穿DEA，趋势转强"))
        elif prev_dif >= prev_dea and cur_dif < cur_dea:
            signals.append(("🔴", "MACD死叉", "DIF下穿DEA，趋势转弱"))
    
    # RSI状态
    if latest['rsi_14'] < 30:
        signals.append(("🟢", "RSI超卖", f"RSI(14)={latest['rsi_14']:.1f}<30，超卖区"))
    elif latest['rsi_14'] > 70:
        signals.append(("🔴", "RSI超买", f"RSI(14)={latest['rsi_14']:.1f}>70，超买区"))
    
    # 布林带位置
    if pd.notna(latest['boll_up']) and pd.notna(latest['boll_down']):
        if latest['close'] >= latest['boll_up'] * 0.99:
            signals.append(("🔴", "触及布林上轨", "价格接近上轨，有压力"))
        elif latest['close'] <= latest['boll_down'] * 1.01:
            signals.append(("🟢", "触及布林下轨", "价格接近下轨，有支撑"))
    
    # 量比
    if latest['vol_ratio'] > 1.5:
        signals.append(("🟡", f"放量{latest['vol_ratio']:.1f}x", "成交量放大"))
    elif latest['vol_ratio'] < 0.5:
        signals.append(("🟡", f"缩量{latest['vol_ratio']:.1f}x", "成交量萎缩"))
    
    # ========== 4. 技术评分 ==========
    score = 50  # 基础分
    reasons = []
    
    # 价格相对于 MA60
    if pd.notna(latest['ma60']):
        ma60_dist = (cp / float(latest['ma60']) - 1) * 100
        if ma60_dist > 0:
            score += 10
            reasons.append(f"价格在MA60上方({ma60_dist:+.1f}%)")
        else:
            score -= 5
            reasons.append(f"价格在MA60下方({ma60_dist:+.1f}%)")
    
    # MACD趋势
    if latest['macd_hist'] > 0:
        score += 8
        reasons.append("MACD柱状为正")
    else:
        score -= 5
        reasons.append("MACD柱状为负")
    
    # RSI
    if 30 < latest['rsi_14'] < 70:
        score += 5
        reasons.append("RSI处于合理区间")
    elif latest['rsi_14'] < 30:
        score += 3
        reasons.append("RSI超卖可能反弹")
    else:
        score -= 3
        reasons.append("RSI超买需警惕")
    
    # 近1月表现
    if backtest_results:
        r30 = next((r for r in backtest_results if r['days'] == 30), None)
        if r30 and r30['total_return'] > 0:
            score += 8
            reasons.append(f"近1月正收益({r30['total_return']:+.1f}%)")
        elif r30:
            score -= 5
            reasons.append(f"近1月负收益({r30['total_return']:.1f}%)")
    
    # 波动率评估
    vol = tech_snapshot['annual_vol']
    if vol < 30:
        score += 5
        reasons.append("波动率较低")
    elif vol > 60:
        score -= 3
        reasons.append("波动率偏高")
    
    score = max(0, min(100, score))
    
    # ===== 多算法趋势预测 + 自适应交易区间 =====
    prediction = None
    try:
        from analysts.adaptive_predictor import AdaptivePredictor
        ap = AdaptivePredictor()
        pred = ap.predict(ticker=ticker, name=name, df=df, horizons=[1, 3, 5, 10])
        if 'error' not in pred:
            prediction = pred
    except Exception as e:
        print(f"  ⚠️ 自适应预测失败: {e}")
        try:
            from analysts.predictor import TrendPredictor
            pred = TrendPredictor.predict(df)
            acc = TrendPredictor.backtest_accuracy(df)
            if 'error' not in pred:
                prediction = {**pred, 'accuracy': acc.get('accuracy', {}) if 'error' not in acc else {},
                              'samples': acc.get('samples', {}) if 'error' not in acc else {}}
        except Exception:
            pass
    
    # 评级
    if score >= 75:
        rating = "偏多"
    elif score >= 60:
        rating = "中性偏多"
    elif score >= 40:
        rating = "中性"
    elif score >= 25:
        rating = "中性偏空"
    else:
        rating = "偏空"
    
    return {
        'analyst': '技术面分析师',
        'ticker': ticker,
        'name': name,
        'current_price': round(cp, 2),
        'score': score,
        'rating': rating,
        'backtest_results': backtest_results,
        'tech_snapshot': tech_snapshot,
        'signals': signals,
        'reasons': reasons,
        'prediction': prediction,
        'summary': _generate_summary(name, cp, rating, score, backtest_results, signals, prediction),
    }


def _generate_summary(name, price, rating, score, backtest_results, signals, prediction=None):
    """生成自然语言技术分析摘要"""
    lines = []
    lines.append(f"# 技术面分析报告")
    lines.append(f"")
    
    # 综合评级
    lines.append(f"## 综合技术评级：{rating}（{score}/100）")
    lines.append(f"")
    
    # 多算法预测 + 交易区间
    if prediction and 'trend' in prediction:
        lines.append(f"### 多算法趋势预测")
        avg_ret = prediction.get('avg_return', 0) * 100
        conf = prediction.get('confidence')
        if conf is None:
            # AdaptivePredictor 没有 confidence，用方向一致性和收益幅度估算
            preds = prediction.get('predictions', [])
            if preds:
                trend = prediction['trend']
                up_votes = sum(1 for p in preds if p['pred_direction'] == '上涨')
                dn_votes = sum(1 for p in preds if p['pred_direction'] == '下跌')
                total = len(preds)
                if trend == '看涨':
                    conf = 50 + min(up_votes / total * 40, 40) + min(abs(avg_ret) * 5, 10)
                elif trend == '看跌':
                    conf = 50 + min(dn_votes / total * 40, 40) + min(abs(avg_ret) * 5, 10)
                else:
                    conf = 50
            else:
                conf = 50
        lines.append(f"**{prediction['trend']}** (置信度{conf:.0f}%) | {avg_ret:+.2f}%")
        acc = prediction.get('accuracy', {})
        if acc:
            lines.append(f"回测准确率: {acc.get('1日','N/A')}% / {acc.get('3日','N/A')}% / {acc.get('5日','N/A')}% (1/3/5日)")
        zones = prediction.get('trading_zones', {})
        if zones and zones.get('buy'):
            lines.append(f"")
            lines.append(f"🎯 交易区间 (ATR={zones.get('atr', 0):.3f})")
            lines.append(f"- 买入区间: {zones['buy']}")
            lines.append(f"- 开仓区间: {zones['open']}")
            lines.append(f"- 卖出区间: {zones['sell']}")
        lines.append(f"")
        for p in prediction.get('predictions', []):
            lines.append(f"- {p['day']}日后: {p['pred_price']} ({p['pred_return']:+.2f}%)")
        lines.append(f"")
    
    # 回测摘要
    if backtest_results:
        lines.append(f"### 多周期回测")
        lines.append(f"| 周期 | 收益率 | 最大回撤 | 夏普比 | 胜率 |")
        lines.append(f"|------|--------|---------|--------|------|")
        for r in backtest_results[:4]:
            lines.append(f"| {r['period_name']} | {r['total_return']:+.1f}% | {r['max_drawdown']:.1f}% | {r['sharpe']:.2f} | {r['win_rate']:.1f}% |")
        lines.append(f"")
    
    # 关键指标
    lines.append(f"### 当前技术指标")
    # 这里用简短格式
    
    # 信号
    if signals:
        lines.append(f"### 检测到的信号")
        for icon, title, desc in signals[:6]:
            lines.append(f"  {icon} **{title}**: {desc}")
        lines.append(f"")
    
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "601991"
    name = sys.argv[2] if len(sys.argv) > 2 else ""
    result = analyze(ticker, name)
    print(result['summary'])
    print(f"\n--- JSON 摘要 ---")
    print(json.dumps({
        'rating': result['rating'],
        'score': result['score'],
        'price': result['current_price'],
        'signals': [(s[0], s[1]) for s in result['signals']],
    }, ensure_ascii=False, indent=2))
