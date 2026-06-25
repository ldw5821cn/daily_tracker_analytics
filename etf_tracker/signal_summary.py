#!/usr/bin/env python3
"""
择时信号统一输出模块
综合 5 个维度的信号，输出结构化决策信号：

1. 趋势信号 (Trend) — 均线排列 + MACD
2. 动量信号 (Momentum) — RSI + 布林带位置
3. 资金信号 (Flow) — 量比 + 机构建议仓位
4. 多模型信号 (Model) — 集成预测 + 置信度 + 各模型一致性
5. 新闻情感 (News) — 行业新闻情感得分
"""

from datetime import datetime
from typing import Dict, List, Optional
import warnings
warnings.filterwarnings('ignore')


class SignalAggregator:
    """五维信号融合器"""
    
    # 信号权重配置
    WEIGHTS = {
        "trend": 0.30,
        "momentum": 0.20,
        "flow": 0.15,
        "model": 0.25,
        "news": 0.10,
    }
    
    @staticmethod
    def _safe_float(value, default=50.0) -> float:
        """安全转换浮点值（处理 % 后缀等）"""
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(str(value).replace('%', '').strip())
        except (ValueError, TypeError):
            return default
    
    @staticmethod
    def analyze_trend(signals: Dict) -> Dict:
        """趋势信号：基于均线排列和 MACD"""
        ma_status = signals.get('ma_status', '') or signals.get('ma_trend_30', '')
        macd = signals.get('macd_status', '') or signals.get('macd_signal', '')
        
        score = 50  # 中性基准
        signal = "neutral"
        
        if '多头排列' in ma_status:
            score += 20
        elif '空头排列' in ma_status:
            score -= 20
        elif '金叉' in ma_status:
            score += 10
        elif '死叉' in ma_status:
            score -= 10
        
        if '金叉' in macd:
            score += 10
        elif '死叉' in macd:
            score -= 10
        elif '多头' in macd:
            score += 5
        elif '空头' in macd:
            score -= 5
        
        score = max(0, min(100, score))
        if score >= 65:
            signal = "bullish"
        elif score <= 35:
            signal = "bearish"
        
        return {"score": score, "signal": signal, "detail": f"均线{ma_status}, MACD{macd}"}
    
    @staticmethod
    def analyze_momentum(signals: Dict) -> Dict:
        """动量信号：基于 RSI 和布林带位置"""
        rsi = SignalAggregator._safe_float(signals.get('rsi_30', signals.get('rsi14', 50)), 50)
        boll_pos = SignalAggregator._safe_float(signals.get('boll_position_30', None), 0.5)
        
        score = 50
        signal = "neutral"
        
        # RSI 信号
        if rsi > 70:
            score -= 20  # 超买区
            signal_override = "bearish"
        elif rsi > 60:
            score += 5
        elif rsi > 40:
            score += 0
        elif rsi > 30:
            score -= 5
        elif rsi < 30:
            score += 20  # 超卖反弹机会
            signal_override = "bullish"
        
        # 布林带位置信号
        if boll_pos > 0.9:
            score -= 10
        elif boll_pos > 0.7:
            score += 0
        elif boll_pos > 0.3:
            score += 5
        elif boll_pos < 0.1:
            score += 10  # 布林带下轨支撑
        
        score = max(0, min(100, score))
        if score >= 65:
            signal = "bullish"
        elif score <= 35:
            signal = "bearish"
        
        return {"score": score, "signal": signal, "detail": f"RSI={rsi:.1f}, 布林带位置={boll_pos:.0%}"}
    
    @staticmethod
    def analyze_flow(signals: Dict, position: Dict = None) -> Dict:
        """资金信号：基于量比和仓位建议"""
        vol_ratio = SignalAggregator._safe_float(signals.get('volume_ratio_30', 1.0), 1.0)
        
        score = 50
        signal = "neutral"
        
        if vol_ratio > 1.5:
            score += 15  # 显著放量
        elif vol_ratio > 1.2:
            score += 8
        elif vol_ratio > 1.0:
            score += 3
        elif vol_ratio > 0.8:
            score -= 3
        elif vol_ratio > 0.5:
            score -= 8
        else:
            score -= 15  # 极度缩量
        
        # 仓位建议
        if isinstance(position, dict):
            pos_size = float(position.get('position_size', 0) or position.get('suggested_position', 0) or 0)
            if pos_size > 0.5:
                score += 5
            elif pos_size > 0.3:
                score += 2
            elif pos_size > 0:
                score += 0
            elif pos_size < -0.3:
                score -= 5
        
        score = max(0, min(100, score))
        if score >= 65:
            signal = "bullish"
        elif score <= 35:
            signal = "bearish"
        
        return {"score": score, "signal": signal, "detail": f"量比={vol_ratio:.2f}"}
    
    @staticmethod
    def analyze_model(multi_model: Dict) -> Dict:
        """多模型信号：基于集成预测和置信度"""
        if not multi_model:
            return {"score": 50, "signal": "neutral", "detail": "无多模型数据"}
        
        ensemble = multi_model.get('ensemble', {})
        individual = multi_model.get('individual', {})
        
        ret_1d = float(ensemble.get('return_1d', 0) or 0)
        confidence = float(ensemble.get('confidence', 0) or 0)
        
        score = 50
        signal = "neutral"
        
        # 预测收益率
        if ret_1d > 0.01:
            score += 15
        elif ret_1d > 0.005:
            score += 10
        elif ret_1d > 0.001:
            score += 5
        elif ret_1d < -0.01:
            score -= 15
        elif ret_1d < -0.005:
            score -= 10
        elif ret_1d < -0.001:
            score -= 5
        
        # 置信度修正
        if confidence > 0.7:
            weight = 1.3
        elif confidence > 0.5:
            weight = 1.0
        else:
            weight = 0.7
        
        # 各模型一致性
        if isinstance(individual, dict):
            directions = []
            for m_name, m_result in individual.items():
                if isinstance(m_result, dict):
                    m_ret = m_result.get('return_1d')
                    if m_ret:
                        directions.append(1 if m_ret > 0 else -1)
            if directions:
                if all(d > 0 for d in directions):
                    score += 10  # 全部看多
                elif all(d < 0 for d in directions):
                    score -= 10  # 全部看空
                elif sum(directions) > 0:
                    score += 3  # 多数看多
                elif sum(directions) < 0:
                    score -= 3  # 多数看空
        
        score = max(0, min(100, score))
        adjusted_score = min(100, max(0, 50 + (score - 50) * weight))
        
        if adjusted_score >= 65:
            signal = "bullish"
        elif adjusted_score <= 35:
            signal = "bearish"
        
        return {"score": round(adjusted_score), "signal": signal, 
                "detail": f"1日预测{ret_1d*100:+.2f}%, 置信度{confidence*100:.0f}%"}
    
    @staticmethod
    def analyze_news(news_data: Dict = None, industry: str = "") -> Dict:
        """新闻情感信号"""
        if not news_data:
            return {"score": 50, "signal": "neutral", "detail": "无行业新闻"}
        
        # 从新闻数据中找到对应行业的新闻
        industry_news = None
        for k, v in news_data.items():
            if industry in k or k in industry:
                industry_news = v
                break
        
        if not industry_news or not isinstance(industry_news, list) or len(industry_news) == 0:
            return {"score": 50, "signal": "neutral", "detail": "暂无相关新闻"}
        
        sentiment_scores = []
        for item in industry_news:
            s = item.get('sentiment_score')
            if s is not None:
                sentiment_scores.append(float(s))
        
        if not sentiment_scores:
            return {"score": 50, "signal": "neutral", "detail": f"共{len(industry_news)}条新闻"}
        
        avg = sum(sentiment_scores) / len(sentiment_scores)
        score = 50 + avg * 50  # score在[-1,1]区间映射到[0,100]
        score = max(0, min(100, int(score)))
        
        if score >= 65:
            signal = "bullish"
        elif score <= 35:
            signal = "bearish"
        else:
            signal = "neutral"
        
        return {"score": score, "signal": signal, 
                "detail": f"情感得分{avg:+.2f}, {len(sentiment_scores)}条"}
    
    @classmethod
    def aggregate(cls, etf_result: Dict, news_data: Dict = None) -> Dict:
        """融合五维信号生成综合评分"""
        signals = etf_result.get('signals', {})
        position = etf_result.get('position', {})
        multi_model = etf_result.get('multi_model')
        theme = etf_result.get('theme', '') or etf_result.get('sector', '')
        
        dims = {
            "trend": cls.analyze_trend(signals),
            "momentum": cls.analyze_momentum(signals),
            "flow": cls.analyze_flow(signals, position),
            "model": cls.analyze_model(multi_model),
            "news": cls.analyze_news(news_data, theme),
        }
        
        # 加权总分
        total_score = 0
        for dim, result in dims.items():
            total_score += result['score'] * cls.WEIGHTS[dim]
        
        total_score = round(total_score)
        
        if total_score >= 65:
            overall = "bullish"
        elif total_score <= 35:
            overall = "bearish"
        else:
            overall = "neutral"
        
        return {
            "overall_score": total_score,
            "overall_signal": overall,
            "dimensions": dims,
            "weights": cls.WEIGHTS,
            "summary": f"综合评分 {total_score}/100"
        }


def format_signal_table(signal_result: Dict) -> str:
    """将信号结果格式化为 Markdown 表格"""
    if not signal_result:
        return ""
    
    emoji = "📈" if signal_result['overall_signal'] == 'bullish' else \
            "📉" if signal_result['overall_signal'] == 'bearish' else "📊"
    
    lines = [
        f"\n### 择时信号汇总\n",
        f"\n**{emoji} 综合评级**: {signal_result['overall_score']}/100 ({signal_result['overall_signal']})\n\n",
        "| 维度 | 权重 | 评分 | 信号 | 依据 |\n",
        "|------|------|------|------|------|\n",
    ]
    
    for dim, data in signal_result['dimensions'].items():
        dim_name = {"trend": "趋势", "momentum": "动量", "flow": "资金", "model": "多模型", "news": "新闻情感"}
        emoji_d = "📈" if data['signal'] == 'bullish' else "📉" if data['signal'] == 'bearish' else "📊"
        lines.append(f"| {dim_name.get(dim, dim)} | {signal_result['weights'][dim]*100:.0f}% | {data['score']} | {emoji_d} {data['signal']} | {data['detail']} |\n")
    
    return "".join(lines)


if __name__ == "__main__":
    # 测试
    test_result = {
        "signals": {
            "score": 41.7, "overall_signal": "neutral",
            "ma_status": "多头排列", "macd_status": "金叉",
            "rsi_30": 62.5, "boll_position_30": 0.65,
            "volume_ratio_30": 1.20
        },
        "position": {"position_size": 0.237},
        "multi_model": {
            "ensemble": {"return_1d": 0.0053, "confidence": 0.533},
            "individual": {
                "lightgbm": {"return_1d": 0.004},
                "xgboost": {"return_1d": 0.006},
                "random_forest": {"return_1d": 0.003},
                "arima": {"return_1d": 0.005},
                "lstm": {"return_1d": 0.007}
            }
        },
        "theme": "人工智能",
        "sector": "AI"
    }
    
    agg = SignalAggregator.aggregate(test_result)
    print(format_signal_table(agg))
