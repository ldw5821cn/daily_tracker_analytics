#!/usr/bin/env python3
"""
实时预警模块 - 检测价格突破、异常波动、趋势转折
功能：
1. 价格突破均线预警（突破MA20/MA60）
2. MACD 金叉/死叉预警
3. RSI 超买/超卖预警
4. 单日涨跌幅异常预警
5. 成交量异常放大预警
6. 多周期趋势背离预警
7. 生成预警报告并持久化
"""

import os
import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass
import warnings
warnings.filterwarnings('ignore')


@dataclass
class Alert:
    """预警数据类"""
    code: str
    name: str
    alert_type: str
    level: str  # info/warning/critical
    message: str
    triggered_at: str
    value: float = 0.0
    threshold: float = 0.0
    suggestion: str = ""
    
    def to_dict(self) -> Dict:
        return {
            'code': self.code,
            'name': self.name,
            'alert_type': self.alert_type,
            'level': self.level,
            'message': self.message,
            'triggered_at': self.triggered_at,
            'value': self.value,
            'threshold': self.threshold,
            'suggestion': self.suggestion
        }


class AlertEngine:
    """预警引擎"""
    
    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.thresholds = {
            'daily_change': self.config.get('daily_change_threshold', 5.0),  # 日涨跌幅超过5%
            'volume_spike': self.config.get('volume_spike_threshold', 2.0),  # 成交量放大2倍以上
            'rsi_overbought': self.config.get('rsi_overbought', 70.0),
            'rsi_oversold': self.config.get('rsi_oversold', 30.0),
            'drawdown': self.config.get('drawdown_threshold', 10.0),  # 从高点回撤10%
            'breakout_ma_ratio': self.config.get('breakout_ma_ratio', 1.02)  # 突破均线2%
        }
    
    def analyze(self, code: str, name: str, df: pd.DataFrame) -> List[Alert]:
        """
        分析某只标的的预警信号
        
        Args:
            code: 标的代码
            name: 标的名称
            df: 包含技术指标的 K线数据
        
        Returns:
            预警列表
        """
        alerts = []
        if df is None or len(df) < 30:
            return alerts
        
        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else latest
        today = datetime.now().strftime('%Y-%m-%d')
        
        # 1. 单日涨跌幅异常
        daily_change = (latest['close'] - prev['close']) / prev['close'] * 100
        if abs(daily_change) >= self.thresholds['daily_change']:
            level = 'critical' if abs(daily_change) >= 7 else 'warning'
            direction = "大涨" if daily_change > 0 else "大跌"
            alerts.append(Alert(
                code=code, name=name,
                alert_type='单日异常波动',
                level=level,
                message=f"{direction} {abs(daily_change):.2f}%，超过 {self.thresholds['daily_change']}% 阈值",
                triggered_at=today,
                value=daily_change,
                threshold=self.thresholds['daily_change'],
                suggestion='关注是否有消息驱动，谨慎追高/杀跌' if level == 'critical' else '留意后续量能配合'
            ))
        
        # 2. 成交量异常放大
        if 'vol_ma20' in latest and latest['vol_ma20'] > 0:
            volume_ratio = latest['volume'] / latest['vol_ma20']
            if volume_ratio >= self.thresholds['volume_spike']:
                level = 'critical' if volume_ratio >= 3 else 'warning'
                alerts.append(Alert(
                    code=code, name=name,
                    alert_type='成交量异常',
                    level=level,
                    message=f"成交量 {volume_ratio:.2f} 倍于20日均量，资金异常活跃",
                    triggered_at=today,
                    value=volume_ratio,
                    threshold=self.thresholds['volume_spike'],
                    suggestion='若放量上涨可视为积极信号，放量下跌需警惕'
                ))
        
        # 3. RSI 超买/超卖
        if 'rsi14' in latest:
            rsi = latest['rsi14']
            if rsi >= self.thresholds['rsi_overbought']:
                alerts.append(Alert(
                    code=code, name=name,
                    alert_type='RSI超买',
                    level='warning',
                    message=f"RSI(14) = {rsi:.1f}，进入超买区域",
                    triggered_at=today,
                    value=rsi,
                    threshold=self.thresholds['rsi_overbought'],
                    suggestion='短期或有回调压力，不宜追涨'
                ))
            elif rsi <= self.thresholds['rsi_oversold']:
                alerts.append(Alert(
                    code=code, name=name,
                    alert_type='RSI超卖',
                    level='warning',
                    message=f"RSI(14) = {rsi:.1f}，进入超卖区域",
                    triggered_at=today,
                    value=rsi,
                    threshold=self.thresholds['rsi_oversold'],
                    suggestion='短期或有反弹机会，可观察企稳信号'
                ))
        
        # 4. MACD 金叉/死叉
        if all(k in latest for k in ['macd', 'macd_signal', 'macd_hist']):
            macd_cross = prev['macd'] <= prev['macd_signal'] and latest['macd'] > latest['macd_signal']
            macd_death = prev['macd'] >= prev['macd_signal'] and latest['macd'] < latest['macd_signal']
            
            if macd_cross and latest['macd_hist'] > 0:
                alerts.append(Alert(
                    code=code, name=name,
                    alert_type='MACD金叉',
                    level='info',
                    message=f"MACD 形成金叉，动能转强",
                    triggered_at=today,
                    value=latest['macd'],
                    threshold=latest['macd_signal'],
                    suggestion='关注量能配合，可考虑逢低布局'
                ))
            elif macd_death and latest['macd_hist'] < 0:
                alerts.append(Alert(
                    code=code, name=name,
                    alert_type='MACD死叉',
                    level='warning',
                    message=f"MACD 形成死叉，动能转弱",
                    triggered_at=today,
                    value=latest['macd'],
                    threshold=latest['macd_signal'],
                    suggestion='警惕回调风险，考虑减仓或观望'
                ))
        
        # 5. 均线突破/跌破
        if all(k in latest for k in ['close', 'ma20', 'ma60']):
            close = latest['close']
            ma20 = latest['ma20']
            ma60 = latest['ma60']
            
            # 突破MA20
            if prev['close'] <= prev['ma20'] * self.thresholds['breakout_ma_ratio'] and close > ma20 * self.thresholds['breakout_ma_ratio']:
                alerts.append(Alert(
                    code=code, name=name,
                    alert_type='突破MA20',
                    level='info',
                    message=f"收盘价 {close:.2f} 突破 MA20 {ma20:.2f}",
                    triggered_at=today,
                    value=close,
                    threshold=ma20,
                    suggestion='短期趋势转强，可跟踪确认'
                ))
            # 跌破MA20
            elif prev['close'] >= prev['ma20'] / self.thresholds['breakout_ma_ratio'] and close < ma20 / self.thresholds['breakout_ma_ratio']:
                alerts.append(Alert(
                    code=code, name=name,
                    alert_type='跌破MA20',
                    level='warning',
                    message=f"收盘价 {close:.2f} 跌破 MA20 {ma20:.2f}",
                    triggered_at=today,
                    value=close,
                    threshold=ma20,
                    suggestion='短期趋势走弱，注意风控'
                ))
            
            # 突破MA60（中期趋势）
            if prev['close'] <= prev['ma60'] * self.thresholds['breakout_ma_ratio'] and close > ma60 * self.thresholds['breakout_ma_ratio']:
                alerts.append(Alert(
                    code=code, name=name,
                    alert_type='突破MA60',
                    level='info',
                    message=f"收盘价 {close:.2f} 突破 MA60 {ma60:.2f}",
                    triggered_at=today,
                    value=close,
                    threshold=ma60,
                    suggestion='中期趋势转强，可适当加仓'
                ))
            # 跌破MA60
            elif prev['close'] >= prev['ma60'] / self.thresholds['breakout_ma_ratio'] and close < ma60 / self.thresholds['breakout_ma_ratio']:
                alerts.append(Alert(
                    code=code, name=name,
                    alert_type='跌破MA60',
                    level='critical',
                    message=f"收盘价 {close:.2f} 跌破 MA60 {ma60:.2f}",
                    triggered_at=today,
                    value=close,
                    threshold=ma60,
                    suggestion='中期趋势走弱，建议减仓或止损'
                ))
        
        # 6. 从近期高点回撤
        if 'close' in latest:
            recent_high = df['high'].tail(20).max()
            if recent_high > 0:
                drawdown = (recent_high - latest['close']) / recent_high * 100
                if drawdown >= self.thresholds['drawdown']:
                    alerts.append(Alert(
                        code=code, name=name,
                        alert_type='高位回撤',
                        level='warning',
                        message=f"从近20日高点回撤 {drawdown:.2f}%",
                        triggered_at=today,
                        value=drawdown,
                        threshold=self.thresholds['drawdown'],
                        suggestion='若跌破关键支撑位需考虑止损'
                    ))
        
        # 7. 多周期趋势背离（短周期与长周期收益背离）
        if len(df) >= 60:
            return_5d = (latest['close'] - df.iloc[-6]['close']) / df.iloc[-6]['close'] * 100
            return_30d = (latest['close'] - df.iloc[-31]['close']) / df.iloc[-31]['close'] * 100
            
            if return_5d > 3 and return_30d < -5:
                alerts.append(Alert(
                    code=code, name=name,
                    alert_type='趋势背离',
                    level='warning',
                    message=f"近5天反弹 {return_5d:.2f}%，但近30天仍下跌 {abs(return_30d):.2f}%，疑似超跌反弹",
                    triggered_at=today,
                    value=return_5d,
                    threshold=return_30d,
                    suggestion='反弹持续性待观察，不宜重仓追入'
                ))
        
        return alerts
    
    def analyze_etfs(self, etf_configs: List[Dict], fetcher) -> List[Alert]:
        """批量分析多只 ETF"""
        all_alerts = []
        for etf in etf_configs:
            try:
                df = fetcher.get_kline_data(etf['code'], days=120)
                if df is not None and len(df) >= 30:
                    alerts = self.analyze(etf['code'], etf['name'], df)
                    all_alerts.extend(alerts)
            except Exception as e:
                print(f"  [AlertEngine] {etf['name']} 预警分析失败: {e}")
        return all_alerts
    
    def generate_markdown_report(self, alerts: List[Alert]) -> str:
        """生成预警报告 Markdown"""
        if not alerts:
            return "\n## 实时预警\n\n✅ 今日无异常预警信号。\n"
        
        # 按级别分组
        level_order = {'critical': 0, 'warning': 1, 'info': 2}
        alerts.sort(key=lambda x: (level_order.get(x.level, 3), x.alert_type))
        
        md = "\n## 实时预警\n\n"
        
        # 统计
        critical_count = sum(1 for a in alerts if a.level == 'critical')
        warning_count = sum(1 for a in alerts if a.level == 'warning')
        info_count = sum(1 for a in alerts if a.level == 'info')
        
        md += f"**预警统计**: 🔴 严重 {critical_count} | ⚠️ 警告 {warning_count} | ℹ️ 提示 {info_count}\n\n"
        
        md += "| 标的 | 预警类型 | 级别 | 内容 | 建议 |\n"
        md += "|------|----------|------|------|------|\n"
        
        for alert in alerts:
            level_icon = {"critical": "🔴", "warning": "⚠️", "info": "ℹ️"}.get(alert.level, "")
            md += f"| {alert.name} | {alert.alert_type} | {level_icon} {alert.level} | {alert.message} | {alert.suggestion} |\n"
        
        md += "\n"
        return md
    
    def generate_wechat_summary(self, alerts: List[Alert]) -> str:
        """生成微信预警摘要"""
        if not alerts:
            return ""
        
        critical = [a for a in alerts if a.level == 'critical']
        warning = [a for a in alerts if a.level == 'warning']
        
        summary = f"🚨 实时预警 ({len(alerts)} 条)\n\n"
        
        if critical:
            summary += "🔴 严重:\n"
            for a in critical[:5]:
                summary += f"• {a.name}: {a.message}\n"
            summary += "\n"
        
        if warning:
            summary += "⚠️ 警告:\n"
            for a in warning[:5]:
                summary += f"• {a.name}: {a.message}\n"
            summary += "\n"
        
        if len(summary) > 500:
            summary = summary[:480] + "...\n"
        
        return summary


if __name__ == "__main__":
    # 测试
    import pandas as pd
    import numpy as np
    
    # 构造测试数据
    dates = pd.date_range(end=datetime.now(), periods=60, freq='B')
    np.random.seed(42)
    df = pd.DataFrame({
        'date': dates,
        'open': np.random.randn(60).cumsum() + 100,
        'high': np.random.randn(60).cumsum() + 102,
        'low': np.random.randn(60).cumsum() + 98,
        'close': np.random.randn(60).cumsum() + 100,
        'volume': np.random.randint(1000000, 5000000, 60),
    })
    
    # 添加技术指标
    df['ma5'] = df['close'].rolling(5).mean()
    df['ma10'] = df['close'].rolling(10).mean()
    df['ma20'] = df['close'].rolling(20).mean()
    df['ma60'] = df['close'].rolling(60).mean()
    df['vol_ma20'] = df['volume'].rolling(20).mean()
    df['rsi14'] = 65  # 模拟 RSI
    df['macd'] = 0.5
    df['macd_signal'] = 0.3
    df['macd_hist'] = 0.2
    
    # 模拟大涨
    df.iloc[-1, df.columns.get_loc('close')] = df.iloc[-2]['close'] * 1.06
    
    engine = AlertEngine()
    alerts = engine.analyze('516150', '稀土永磁ETF', df)
    
    print(f"发现 {len(alerts)} 条预警")
    for alert in alerts:
        print(f"[{alert.level}] {alert.alert_type}: {alert.message}")
    
    print("\n--- 微信摘要 ---")
    print(engine.generate_wechat_summary(alerts))
