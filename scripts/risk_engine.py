"""
#4 风控规则引擎 — 基于ATR波动率的止损+Kelly仓位+风险评级
"""
import json, math
from datetime import datetime


class RiskEngine:
    """风控规则引擎"""

    # 风险参数
    DEFAULT_STOP_LOSS_ATR = 2.0       # 止损 = ATR * 2
    DEFAULT_TAKE_PROFIT_ATR = 4.0     # 止盈 = ATR * 4
    MAX_PORTFOLIO_RISK = 0.15         # 组合最大风险敞口 15%
    MAX_SECTOR_EXPOSURE = 0.40        # 单板块最大暴露 40%
    CORRELATION_THRESHOLD = 0.70      # 相关性阈值

    @staticmethod
    def calc_atr_stop_loss(price, atr_14, multiplier=2.0):
        """基于ATR的止损价"""
        if not atr_14 or atr_14 <= 0:
            return round(price * 0.95, 2)  # 无ATR时默认5%止损
        return round(price - atr_14 * multiplier, 2)

    @staticmethod
    def calc_atr_take_profit(price, atr_14, multiplier=4.0):
        """基于ATR的止盈价"""
        if not atr_14 or atr_14 <= 0:
            return round(price * 1.10, 2)
        return round(price + atr_14 * multiplier, 2)

    @staticmethod
    def kelly_position_size(cash, win_rate, avg_win, avg_loss, kelly_fraction=0.25):
        """Kelly公式计算最优仓位"""
        if avg_loss <= 0 or win_rate <= 0 or win_rate >= 1:
            return cash * 0.1  # 默认10%
        r = avg_win / avg_loss if avg_loss > 0 else 1
        kelly = win_rate - (1 - win_rate) / r if r > 0 else 0
        kelly = max(0, kelly * kelly_fraction)
        return cash * min(kelly, 0.35)  # 单只上限35%

    @staticmethod
    def assess_risk(price, atr_14, rsi, macd_hist, ma_trend, vol_ratio):
        """综合风险评估 — 返回风险等级和建议"""
        risk_score = 0
        risk_factors = []

        # 波动率风险
        atr_pct = (atr_14 / price * 100) if price > 0 else 0
        if atr_pct > 5:
            risk_score += 30
            risk_factors.append(f'高波动(ATR={atr_pct:.1f}%)')
        elif atr_pct > 3:
            risk_score += 15
            risk_factors.append(f'中波动(ATR={atr_pct:.1f}%)')

        # RSI 风险
        if rsi > 80:
            risk_score += 25
            risk_factors.append('RSI超买')
        elif rsi < 20:
            risk_score += 20
            risk_factors.append('RSI超卖(可能反弹)')
        elif rsi < 30:
            risk_score += 10
            risk_factors.append('RSI接近超卖')

        # MACD 风险
        if macd_hist < 0:
            risk_score += 15
            risk_factors.append('MACD空头')
        else:
            risk_score -= 5

        # 均线风险
        if '空头排列' in ma_trend:
            risk_score += 20
            risk_factors.append('均线空头')
        elif '多头排列' in ma_trend:
            risk_score -= 5

        # 量能风险
        if vol_ratio > 2:
            risk_score += 10
            risk_factors.append('异常放量')
        elif vol_ratio < 0.3:
            risk_score += 5
            risk_factors.append('严重缩量')

        # 最终评级
        if risk_score >= 60:
            level = '高风险'
            action = '不建议介入或减仓'
        elif risk_score >= 35:
            level = '中风险'
            action = '控制仓位，设置止损'
        elif risk_score >= 15:
            level = '低风险'
            action = '可适度参与'
        else:
            level = '低风险'
            action = '积极关注'

        return {
            'risk_score': risk_score,
            'risk_level': level,
            'action': action,
            'factors': risk_factors,
            'suggested_stop_loss_pct': min(max(atr_pct * 2, 3), 15),
            'suggested_position_pct': max(5, min(35, 35 - risk_score * 0.5)),
        }

    @staticmethod
    def portfolio_risk_check(positions_with_risk, sector_map=None):
        """组合层面风控检查"""
        total_value = sum(p.get('market_value', 0) for p in positions_with_risk)
        if total_value <= 0:
            return {'status': '空仓', 'alerts': []}

        alerts = []
        # 检查单只集中度
        for p in positions_with_risk:
            ratio = p.get('market_value', 0) / total_value
            if ratio > 0.35:
                alerts.append(f"⚠️ 集中度风险: {p['name']}占比{ratio*100:.0f}%")

        # 检查板块集中度
        if sector_map:
            sector_value = {}
            for p in positions_with_risk:
                sector = sector_map.get(p.get('ticker', ''), '其他')
                sector_value[sector] = sector_value.get(sector, 0) + p.get('market_value', 0)
            for sector, val in sector_value.items():
                ratio = val / total_value
                if ratio > RiskEngine.MAX_SECTOR_EXPOSURE:
                    alerts.append(f"⚠️ 板块集中度: {sector}占比{ratio*100:.0f}%")

        # 整体风险敞口
        avg_risk = sum(p.get('risk_score', 0) for p in positions_with_risk) / max(len(positions_with_risk), 1)
        if avg_risk > 40:
            alerts.append(f"⚠️ 组合平均风险{avg_risk:.0f}，建议降低仓位")

        return {
            'status': '正常' if not alerts else '需关注',
            'total_value': round(total_value, 2),
            'total_risk_score': round(avg_risk, 1),
            'position_count': len(positions_with_risk),
            'alerts': alerts,
        }


def generate_risk_report(stock_results):
    """生成风控报告"""
    lines = [f"# 风控报告\n> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"]
    lines.append("| 股票 | 价格 | 风险等级 | 风险分 | 建议仓位 | 建议止损 | 风险因素 |\n")
    lines.append("|------|------|----------|--------|----------|----------|----------|\n")

    re = RiskEngine()
    all_risks = []
    for r in stock_results:
        if 'error' in r or not r.get('atr_14'):
            continue
        risk = re.assess_risk(
            r['price'], r['atr_14'], r.get('rsi', 50),
            r.get('macd_hist', 0), r.get('ma_trend', '震荡'),
            r.get('vol_ratio', 1)
        )
        all_risks.append({**risk, 'name': r['name'], 'ticker': r['ticker']})
        factors = ' '.join(risk['factors'][:3])
        lines.append(f"| {r['name']} | {r.get('price','?')} | {risk['risk_level']} | {risk['risk_score']} | {risk['suggested_position_pct']:.0f}% | {risk['suggested_stop_loss_pct']:.1f}% | {factors} |\n")

    lines.append(f"\n---\n## 组合检查\n")
    for r in all_risks:
        lines.append(f"- {r['name']}: {r['risk_level']}({r['risk_score']}) → {r['action']}\n")

    return ''.join(lines)
