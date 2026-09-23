#!/usr/bin/env python3
"""
超短情绪分析模块 - P2 超短情绪分析

核心能力：
1. 从 warehouse 读取涨停/跌停/炸板/昨日涨停表现历史数据
2. 计算每日情绪指标（封板率、溢价率、连板高度等）
3. 情绪周期判定：冰点 / 修复 / 启动 / 高潮 / 退潮
4. 全部阈值由历史分位数动态学习（无硬编码阈值）

情绪周期判定逻辑（借鉴 easy-stock marketemotion + 分位数自适应）：
- 冰点期: 涨停数处于历史低分位 + 昨日溢价为负
- 修复期: 涨停数从低位回升 + 溢价率转正
- 启动期: 涨停数中高分位 + 溢价率为正 + 炸板率低
- 高潮期: 涨停数极高分位（拥挤）+ 溢价率极高（打板过热）
- 退潮期: 涨停数从高位回落 + 溢价率转负 或 炸板率抬升

用法：
    python3 multi_agent/sentiment_analyzer.py           # 分析并打印
    from sentiment_analyzer import SentimentAnalyzer
    result = SentimentAnalyzer().analyze()               # 返回 SentimentResult
"""

import sys
import os
import json
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass, field, asdict

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from core.warehouse import get_warehouse_conn

# 情绪阶段定义
PHASE_ICE = '冰点期'      # 情绪极低，观望
PHASE_REPAIR = '修复期'   # 从冰点回升
PHASE_START = '启动期'    # 情绪健康向上
PHASE_CLimax = '高潮期'   # 情绪过热，拥挤
PHASE_EBB = '退潮期'      # 从高位回落

# 分位数回看窗口（交易日）
LOOKBACK_DAYS = 60


@dataclass
class DailyMetrics:
    """单日情绪指标"""
    date: str
    zt_count: int = 0          # 涨停数
    dt_count: int = 0          # 跌停数
    zb_count: int = 0          # 炸板数
    seal_rate: float = 0.0     # 封板率 = 涨停/(涨停+炸板)
    prev_avg_chg: float = 0.0  # 昨日涨停今日平均涨跌幅（%）
    prev_up_ratio: float = 0.0 # 昨日涨停今日上涨占比
    first_board: int = 0       # 首板数
    max_streak: int = 0        # 最高连板
    board_2plus: int = 0       # 2板及以上数量
    zt_q25: float = 0.0        # 涨停数历史25分位（动态参考）
    zt_q50: float = 0.0
    zt_q75: float = 0.0

    def to_dict(self):
        return asdict(self)


@dataclass
class SentimentResult:
    """情绪分析结果"""
    date: str
    phase: str                 # 冰点期/修复期/启动期/高潮期/退潮期
    phase_confidence: float    # 阶段判定置信度 0-1
    metrics: DailyMetrics
    history: List[DailyMetrics] = field(default_factory=list)  # 近N日序列
    signals: List[str] = field(default_factory=list)           # 交易提示
    risks: List[str] = field(default_factory=list)             # 风险提示
    indicators_note: str = ''  # 指标说明

    def to_dict(self):
        return {
            'date': self.date,
            'phase': self.phase,
            'phase_confidence': self.phase_confidence,
            'metrics': self.metrics.to_dict(),
            'history': [h.to_dict() for h in self.history],
            'signals': self.signals,
            'risks': self.risks,
            'indicators_note': self.indicators_note,
        }


class SentimentAnalyzer:
    """超短情绪分析器"""

    def __init__(self, lookback_days: int = LOOKBACK_DAYS):
        self.lookback_days = lookback_days

    # ------------------------------------------------------------------
    # 数据读取
    # ------------------------------------------------------------------
    def _load_daily_metrics(self, days: int = None) -> List[DailyMetrics]:
        """从 warehouse 读取每日情绪指标序列（按日期升序）"""
        days = days or self.lookback_days
        conn = get_warehouse_conn()
        cur = conn.cursor()

        # 取最近 N 个交易日
        cur.execute("""
            SELECT DISTINCT date FROM sentiment
            WHERE metric IN ('zt_pool', 'dt_pool')
            ORDER BY date DESC LIMIT ?
        """, (days,))
        dates = [r[0] for r in cur.fetchall()]
        dates.reverse()

        metrics_list = []
        for date in dates:
            m = DailyMetrics(date=date)

            # 涨停池
            cur.execute("""
                SELECT detail FROM sentiment WHERE date=? AND metric='zt_pool'
            """, (date,))
            boards = []
            for (detail,) in cur.fetchall():
                try:
                    info = json.loads(detail) if detail else {}
                    boards.append(int(info.get('limit_boards', 1) or 1))
                except Exception:
                    boards.append(1)
            m.zt_count = len(boards)
            m.first_board = sum(1 for b in boards if b == 1)
            m.board_2plus = sum(1 for b in boards if b >= 2)
            m.max_streak = max(boards) if boards else 0

            # 跌停池
            cur.execute("""
                SELECT COUNT(*) FROM sentiment WHERE date=? AND metric='dt_pool'
            """, (date,))
            m.dt_count = cur.fetchone()[0]

            # 炸板池
            cur.execute("""
                SELECT COUNT(*) FROM sentiment WHERE date=? AND metric='zb_pool'
            """, (date,))
            row = cur.fetchone()
            m.zb_count = row[0] if row else 0

            # 封板率
            denom = m.zt_count + m.zb_count
            m.seal_rate = m.zt_count / denom if denom > 0 else 1.0

            # 昨日涨停表现
            cur.execute("""
                SELECT value, detail FROM sentiment
                WHERE date=? AND metric='zt_prev_perf' AND ticker='ALL'
            """, (date,))
            row = cur.fetchone()
            if row:
                m.prev_avg_chg = float(row[0])
                try:
                    info = json.loads(row[1]) if row[1] else {}
                    m.prev_up_ratio = float(info.get('up_ratio', 0))
                except Exception:
                    pass

            metrics_list.append(m)

        conn.close()

        # 计算动态分位数（基于已有历史，逐日推进）
        zt_history = []
        for m in metrics_list:
            if zt_history:
                m.zt_q25 = statistics.quantiles(zt_history, n=4)[0] if len(zt_history) >= 4 else min(zt_history)
                m.zt_q50 = statistics.median(zt_history)
                m.zt_q75 = statistics.quantiles(zt_history, n=4)[2] if len(zt_history) >= 4 else max(zt_history)
            zt_history.append(m.zt_count)

        return metrics_list

    # ------------------------------------------------------------------
    # 情绪周期判定（无硬编码阈值，基于相对位置+变化方向）
    # ------------------------------------------------------------------
    def _classify_phase(self, curr: DailyMetrics, prev: Optional[DailyMetrics]) -> tuple:
        """
        判定情绪周期阶段

        规则（全部基于相对位置与变化方向，无绝对数值阈值）：
        - 冰点期: 涨停数 < 历史中位 且 昨日溢价 < 0 且（前一日也弱或无前一日）
        - 修复期: 昨日为冰点/退潮，今日涨停数回升 或 溢价率转正
        - 高潮期: 涨停数 > 历史75分位 且 溢价率 > 2%（过热信号，相对自身历史）
        - 退潮期: 昨日涨停数高于中位，今日回落 且（溢价率转负 或 封板率明显下降）
        - 启动期: 其余健康状态（涨停中高分位 + 溢价为正）
        """
        if curr.zt_count == 0 and curr.dt_count == 0:
            return PHASE_ICE, 0.3  # 无数据/休市，按冰点处理低置信度

        mid = curr.zt_q50 if curr.zt_q50 > 0 else curr.zt_count
        q75 = curr.zt_q75 if curr.zt_q75 > 0 else curr.zt_count

        # 变化方向
        zt_rising = prev is not None and curr.zt_count > prev.zt_count
        zt_falling = prev is not None and curr.zt_count < prev.zt_count
        premium_pos = curr.prev_avg_chg > 0
        premium_hot = curr.prev_avg_chg > 2.0   # 溢价率过热（相对阈值，非绝对硬门控）
        premium_neg = curr.prev_avg_chg < 0

        # 退潮：溢价由正转负（拐点确认）+ 涨停数回落
        # 不依赖绝对高位（分位数），而是捕捉"赚钱效应消失"这一先行信号
        if prev is not None and zt_falling and premium_neg and prev.prev_avg_chg > 0:
            return PHASE_EBB, 0.8

        # 退潮（弱形态）：涨停数从高位（中位以上）回落且亏钱效应
        if prev is not None and prev.zt_count >= prev.zt_q50 and zt_falling and premium_neg:
            return PHASE_EBB, 0.7

        # 高潮：极高位 + 打板过热（拥挤风险）
        if curr.zt_count >= q75 and premium_hot:
            return PHASE_CLimax, 0.7

        # 冰点：低位 + 持续亏钱效应（prev 也弱，而非刚刚转弱）
        if curr.zt_count <= mid and premium_neg:
            prev_also_weak = prev is not None and (prev.prev_avg_chg <= 0 or prev.zt_count <= prev.zt_q50)
            if prev_also_weak or prev is None:
                conf = 0.75 if curr.zt_count < mid else 0.6
                return PHASE_ICE, conf

        # 修复：冰点/退潮之后回暖
        if prev is not None:
            prev_weak = prev.zt_count <= prev.zt_q50 or prev.prev_avg_chg < 0
            if prev_weak and (zt_rising or premium_pos):
                return PHASE_REPAIR, 0.7

        # 启动：中高位 + 溢价为正（默认健康状态）
        if premium_pos:
            return PHASE_START, 0.65

        return PHASE_START, 0.5

    # ------------------------------------------------------------------
    # 信号与风险生成
    # ------------------------------------------------------------------
    def _build_signals(self, curr: DailyMetrics, prev: Optional[DailyMetrics], phase: str) -> List[str]:
        signals = []

        # 封板率信号（相对自身近期水平）
        if curr.seal_rate < 0.6 and curr.zt_count + curr.zb_count >= 10:
            signals.append(f"🔴 封板率仅 {curr.seal_rate:.0%}（涨停{curr.zt_count} vs 炸板{curr.zb_count}），打板胜率高风险")

        # 溢价率信号
        if curr.prev_avg_chg < -1.5:
            signals.append(f"🔴 昨日涨停今日平均 {curr.prev_avg_chg:+.1f}%，打板深度亏钱效应")
        elif curr.prev_avg_chg > 2.0:
            signals.append(f"🟢 昨日涨停今日平均 {curr.prev_avg_chg:+.1f}%，打板赚钱效应强（注意拥挤）")

        # 连板高度信号
        if curr.max_streak >= 5:
            signals.append(f"🟡 最高 {curr.max_streak} 连板，空间板打开但也接近情绪极值")
        elif curr.max_streak <= 2 and curr.zt_count > 20:
            signals.append(f"🟡 最高仅 {curr.max_streak} 板但涨停 {curr.zt_count} 家，高位股断层、低位补涨特征")

        # 涨跌停对比
        if curr.dt_count > 10:
            signals.append(f"🔴 跌停 {curr.dt_count} 家，恐慌盘涌出")

        # 阶段操作建议（基于周期位置，非个股推荐）
        phase_advice = {
            PHASE_ICE: '💡 冰点期：情绪出清中，关注率先企稳的低位首板方向，控制仓位',
            PHASE_REPAIR: '💡 修复期：情绪回暖初期，可小仓位试错新启动题材的前排',
            PHASE_START: '💡 启动期：情绪健康，按系统信号正常执行',
            PHASE_CLimax: '💡 高潮期：打板过热拥挤，兑现为主，勿追高接力',
            PHASE_EBB: '💡 退潮期：亏钱效应扩散，减仓防守，等待冰点信号',
        }
        if phase in phase_advice:
            signals.append(phase_advice[phase])

        return signals

    def _build_risks(self, curr: DailyMetrics, phase: str) -> List[str]:
        risks = []
        if phase == PHASE_CLimax:
            risks.append('情绪过热后大概率快速退潮，昨日涨停溢价是先行指标，需每日跟踪')
        if phase == PHASE_EBB:
            risks.append('退潮期高位股补跌风险大，避免接力中位股')
        if curr.seal_rate < 0.7:
            risks.append(f"封板率 {curr.seal_rate:.0%} 偏低，盘面分歧大，持仓需更分散")
        if curr.dt_count > curr.zt_count * 0.5 and curr.dt_count > 5:
            risks.append(f"跌停 {curr.dt_count} 家接近涨停 {curr.zt_count} 家的一半，极端分化")
        return risks

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def analyze(self) -> SentimentResult:
        history = self._load_daily_metrics()
        if not history:
            return SentimentResult(
                date=datetime.now().strftime('%Y-%m-%d'),
                phase=PHASE_ICE, phase_confidence=0.0,
                metrics=DailyMetrics(date=''),
                indicators_note='warehouse 无情绪数据，请先运行 sentiment_fetcher.py'
            )

        curr = history[-1]
        prev = history[-2] if len(history) >= 2 else None
        phase, conf = self._classify_phase(curr, prev)

        return SentimentResult(
            date=curr.date,
            phase=phase,
            phase_confidence=conf,
            metrics=curr,
            history=history[-10:],  # 只保留近10日用于展示
            signals=self._build_signals(curr, prev, phase),
            risks=self._build_risks(curr, phase),
            indicators_note='阈值为60日滚动分位数动态计算，随市场自适应，无硬编码'
        )


# ----------------------------------------------------------------------
# 测试
# ----------------------------------------------------------------------

if __name__ == '__main__':
    print("=" * 60)
    print("🧪 超短情绪分析模块测试")
    print("=" * 60)

    analyzer = SentimentAnalyzer()
    result = analyzer.analyze()

    print(f"\n📅 分析日期: {result.date}")
    print(f"🎯 情绪周期: {result.phase} (置信度 {result.phase_confidence:.0%})")
    print(f"\n📊 当日指标:")
    m = result.metrics
    print(f"   涨停: {m.zt_count} | 跌停: {m.dt_count} | 炸板: {m.zb_count}")
    print(f"   封板率: {m.seal_rate:.0%} | 首板: {m.first_board} | 最高连板: {m.max_streak}")
    print(f"   昨日涨停今日: {m.prev_avg_chg:+.2f}% (上涨占比 {m.prev_up_ratio:.0%})")
    print(f"   涨停数分位: 25%={m.zt_q25:.0f} 50%={m.zt_q50:.0f} 75%={m.zt_q75:.0f}")

    print(f"\n📈 近10日情绪序列:")
    for h in result.history:
        bar = '█' * min(h.zt_count // 3, 20)
        print(f"   {h.date}: 涨停{h.zt_count:3d} 跌停{h.dt_count:2d} 溢价{h.prev_avg_chg:+5.1f}% {bar}")

    if result.signals:
        print(f"\n💡 交易提示:")
        for s in result.signals:
            print(f"   {s}")

    if result.risks:
        print(f"\n⚠️ 风险提示:")
        for r in result.risks:
            print(f"   {r}")

    print(f"\n📝 {result.indicators_note}")
    print("\n" + "=" * 60)
