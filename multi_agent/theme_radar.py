#!/usr/bin/env python3
"""
题材雷达数据引擎 - P3 题材雷达

功能：
1. 从 warehouse 涨停池数据提取题材聚类（按申万二级行业）
2. 计算题材强度指标：涨停数/连板高度/封单资金/梯队完整性
3. 持续性评估：题材内涨停的家数变化、空间板是否打开
4. 与 P2 情绪周期联动：退潮期回避高位题材

无硬编码：
- 题材活跃阈值（>=N只涨停）由近60日分位数动态计算
- 强度评分基于相对位置

用法：
    python3 multi_agent/theme_radar.py                    # 打印题材雷达
    from theme_radar import ThemeRadar
    result = ThemeRadar().analyze()
"""

import sys
import json
import statistics
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass, field, asdict

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from core.warehouse import get_warehouse_conn
from sentiment_analyzer import SentimentAnalyzer, PHASE_EBB, PHASE_CLimax

# 默认回看交易日数
LOOKBACK_DAYS = 60


@dataclass
class ThemeData:
    """单一题材数据"""
    name: str                    # 行业名
    zt_count: int = 0            # 涨停数
    max_board: int = 0           # 最高连板
    avg_fund: float = 0.0        # 平均封单资金（万元）
    stocks: List[dict] = field(default_factory=list)  # 成分股
    first_board: int = 0         # 首板数（新启动）
    high_board: int = 0          # 3板+数量（高度）
    # 持续性追踪字段
    prev_zt_count: int = 0       # 昨日涨停数
    zt_change: int = 0           # 涨停数变化（+/-）
    consecutive_days: int = 0    # 连续活跃天数
    trend: str = 'new'           # new(新) | rising(升温) | cooling(降温) | stable(持续)
    avg_turnover: float = 0.0    # 平均换手率
    total_amount: float = 0.0    # 总成交额（万元）

    def to_dict(self):
        d = asdict(self)
        d['stocks'] = self.stocks[:5]  # 只保留前5只
        return d


@dataclass
class ThemeRadarResult:
    """题材雷达分析结果"""
    date: str
    phase: str                   # 联动 P2 情绪周期
    themes: List[ThemeData]      # 活跃题材列表（按强度排序）
    watchlist: List[str]         # 观察名单（临界活跃）
    avoid: List[str]             # 回避名单（退潮期高位题材）
    market_summary: str = ''     # 市场题材总结
    ladder: Dict[int, List[dict]] = field(default_factory=dict)  # 连板梯队

    def to_dict(self):
        return {
            'date': self.date,
            'phase': self.phase,
            'themes': [t.to_dict() for t in self.themes],
            'watchlist': self.watchlist,
            'avoid': self.avoid,
            'market_summary': self.market_summary,
            'ladder': {str(k): v for k, v in self.ladder.items()},
        }


class ThemeRadar:
    """题材雷达分析器"""

    def __init__(self, lookback_days: int = LOOKBACK_DAYS):
        self.lookback_days = lookback_days

    # ------------------------------------------------------------------
    # 数据读取
    # ------------------------------------------------------------------
    def _load_zt_with_industry(self, date: str = None) -> List[dict]:
        """读取某日涨停股及其行业/连板数据"""
        conn = get_warehouse_conn()
        cur = conn.cursor()

        if date is None:
            cur.execute("""
                SELECT MAX(date) FROM sentiment WHERE metric='zt_pool'
            """)
            date = cur.fetchone()[0]

        cur.execute("""
            SELECT ticker, detail FROM sentiment 
            WHERE date=? AND metric='zt_pool'
        """, (date,))

        stocks = []
        for ticker, detail in cur.fetchall():
            try:
                info = json.loads(detail) if detail else {}
                stocks.append({
                    'ticker': ticker,
                    'industry': info.get('industry', '未知'),
                    'board': int(info.get('limit_boards', 1)),
                    'fund': float(info.get('seal_amount', 0)),
                    'name': info.get('name', ''),
                    'turnover': float(info.get('turnover', 0)),
                    'amount': float(info.get('amount', 0)),
                })
            except Exception:
                stocks.append({
                    'ticker': ticker,
                    'industry': '未知',
                    'board': 1,
                    'fund': 0,
                    'name': '',
                    'turnover': 0,
                    'amount': 0,
                })
        conn.close()
        return stocks

    def _build_ladder(self, stocks: List[dict]) -> Dict[int, List[dict]]:
        """
        构建连板梯队：按连板数分组
        返回: {board: [stocks]}
        """
        ladder: Dict[int, List[dict]] = {}
        for s in stocks:
            board = s['board']
            if board not in ladder:
                ladder[board] = []
            ladder[board].append({
                'ticker': s['ticker'],
                'name': s.get('name', ''),
                'industry': s['industry'],
                'fund_wan': round(s['fund'], 1),
                'turnover': round(s.get('turnover', 0), 1),
                'amount_yi': round(s.get('amount', 0) / 10000, 2),  # 亿
            })
        # 按连板数降序
        return dict(sorted(ladder.items(), reverse=True))

    def _load_historical_theme_counts(self, date: str, days: int = None) -> Dict[str, List[Dict]]:
        """读取历史每日各行业涨停数及持续性数据"""
        days = days or self.lookback_days
        conn = get_warehouse_conn()
        cur = conn.cursor()

        # 取最近 N 个交易日
        cur.execute("""
            SELECT DISTINCT date FROM sentiment 
            WHERE metric='zt_pool' AND date <= ?
            ORDER BY date DESC LIMIT ?
        """, (date, days))
        dates = [r[0] for r in cur.fetchall()]
        dates.reverse()

        theme_history: Dict[str, List[Dict]] = {}
        for d in dates:
            cur.execute("""
                SELECT detail FROM sentiment WHERE date=? AND metric='zt_pool'
            """, (d,))
            counts: Dict[str, Dict] = {}
            for (detail,) in cur.fetchall():
                try:
                    info = json.loads(detail) if detail else {}
                    ind = info.get('industry', '未知')
                    if ind not in counts:
                        counts[ind] = {'count': 0, 'turnover': [], 'amount': []}
                    counts[ind]['count'] += 1
                    if 'turnover' in info:
                        counts[ind]['turnover'].append(float(info['turnover']))
                    if 'amount' in info:
                        counts[ind]['amount'].append(float(info['amount']))
                except Exception:
                    pass
            for ind, data in counts.items():
                if ind not in theme_history:
                    theme_history[ind] = []
                theme_history[ind].append({
                    'date': d,
                    'count': data['count'],
                    'avg_turnover': sum(data['turnover'])/len(data['turnover']) if data['turnover'] else 0,
                    'total_amount': sum(data['amount']),
                })

        conn.close()
        return theme_history
    # 题材聚类与强度计算
    # ------------------------------------------------------------------
    def _cluster_themes(self, stocks: List[dict]) -> List[ThemeData]:
        """按行业聚类涨停股，构建题材"""
        clusters: Dict[str, ThemeData] = {}
        for s in stocks:
            ind = s['industry']
            if ind not in clusters:
                clusters[ind] = ThemeData(name=ind)
            t = clusters[ind]
            t.zt_count += 1
            t.max_board = max(t.max_board, s['board'])
            t.avg_fund += s['fund']
            if s['board'] == 1:
                t.first_board += 1
            if s['board'] >= 3:
                t.high_board += 1
            t.stocks.append({
                'ticker': s['ticker'],
                'board': s['board'],
                'fund_wan': round(s['fund'], 1),
            })

        # 平均封单
        for t in clusters.values():
            t.avg_fund = t.avg_fund / t.zt_count if t.zt_count > 0 else 0

        return sorted(clusters.values(), key=lambda t: t.zt_count, reverse=True)

    def _score_themes(
        self, 
        themes: List[ThemeData], 
        history: Dict[str, List[Dict]]
    ) -> tuple:
        """
        计算题材强度评分 + 持续性 + 分类
        
        返回: (active_themes, watchlist, avoid_list)
        无硬编码阈值：活跃阈值 = 该行业历史涨停数 75 分位（至少 2）
        """
        active = []
        watchlist = []
        avoid = []

        for t in themes:
            hist = history.get(t.name, [])
            if hist:
                counts = [h['count'] for h in hist]
                # 动态阈值：历史 75 分位，但至少 2 只，至多 10 只
                q75 = statistics.quantiles(counts, n=4)[2] if len(counts) >= 4 else max(counts)
                threshold = max(2, min(q75, 10))
                percentile = sum(1 for h in counts if h <= t.zt_count) / len(counts)
                
                # 持续性分析
                t.prev_zt_count = counts[-1] if counts else 0
                t.zt_change = t.zt_count - t.prev_zt_count
                
                # 连续活跃天数（从最近往前数）
                consecutive = 0
                for h in reversed(hist):
                    if h['count'] >= 2:
                        consecutive += 1
                    else:
                        break
                t.consecutive_days = consecutive
                
                # 趋势判断
                if t.zt_change > 0 and t.prev_zt_count > 0:
                    t.trend = 'rising'      # 升温
                elif t.zt_change < 0:
                    t.trend = 'cooling'     # 降温
                elif t.prev_zt_count > 0:
                    t.trend = 'stable'      # 持续
                else:
                    t.trend = 'new'         # 新启动
            else:
                threshold = 2
                percentile = 0.5
                t.trend = 'new'

            # 强度评分（0-10，相对历史 + 持续性加分）
            cnt_score = min(t.zt_count / threshold, 2.0) * 3 if threshold > 0 else 0
            board_score = min(t.max_board / 3, 1.5) * 3
            fund_score = min(t.avg_fund / 5000, 1.0) * 2
            first_score = min(t.first_board / max(t.zt_count, 1), 1.0) * 2
            
            # 持续性加分（连续活跃 2+ 天、升温趋势）
            persistence_bonus = 0
            if t.consecutive_days >= 2:
                persistence_bonus += 0.5
            if t.trend == 'rising':
                persistence_bonus += 0.5
            
            strength = cnt_score + board_score + fund_score + first_score + persistence_bonus

            # 分类
            if t.zt_count >= threshold and strength >= 5:
                active.append((t, strength, percentile))
            elif t.zt_count >= 2:
                watchlist.append(t.name)

            # 回避：高位连板 + 情绪退潮期风险
            if t.high_board >= 2 and t.first_board == 0:
                avoid.append(t.name)

        active.sort(key=lambda x: x[1], reverse=True)
        return active, watchlist, avoid

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def analyze(self, date: str = None) -> ThemeRadarResult:
        # 联动 P2 情绪周期
        phase_result = SentimentAnalyzer().analyze()
        phase = phase_result.phase

        # 若未指定日期，使用 warehouse 中最新涨停池日期（避免未开盘时取到错误日期）
        if date is None:
            conn = get_warehouse_conn()
            cur = conn.cursor()
            cur.execute("SELECT MAX(date) FROM sentiment WHERE metric='zt_pool'")
            row = cur.fetchone()
            conn.close()
            date = row[0] if row and row[0] else datetime.now().strftime('%Y-%m-%d')

        stocks = self._load_zt_with_industry(date)
        if not stocks:
            return ThemeRadarResult(
                date=date or datetime.now().strftime('%Y-%m-%d'),
                phase=phase, themes=[], watchlist=[], avoid=[],
                market_summary='当日无涨停数据'
            )

        current_date = date or datetime.now().strftime('%Y-%m-%d')
        history = self._load_historical_theme_counts(current_date)
        themes = self._cluster_themes(stocks)
        active, watchlist, avoid = self._score_themes(themes, history)

        # 构建题材列表
        theme_list = []
        for t, strength, pct in active:
            # 把评分附加到 ThemeData
            t_dict = t.to_dict()
            t_dict['strength'] = round(strength, 1)
            t_dict['percentile'] = round(pct, 2)
            theme_list.append(t_dict)

        # 构建连板梯队
        ladder = self._build_ladder(stocks)

        # 生成总结
        if active:
            top = active[0][0]
            summary = f"最强题材: {top.name}（{top.zt_count}只涨停，最高{top.max_board}连板）"
            if len(active) > 1:
                summary += f" | 次强: {active[1][0].name}（{active[1][0].zt_count}只）"
        else:
            summary = "无明确主线题材，市场轮动快"

        # 退潮期调整回避名单
        if phase == PHASE_EBB:
            for t, s, p in active:
                if t.high_board >= 2:
                    avoid.append(t.name)
            avoid = list(set(avoid))  # 去重
            summary += " | ⚠️ 情绪退潮期，回避高位题材"

        return ThemeRadarResult(
            date=current_date,
            phase=phase,
            themes=theme_list,
            watchlist=watchlist[:5],  # 最多5个观察
            avoid=avoid[:5],
            market_summary=summary,
            ladder=ladder,
        )


# ----------------------------------------------------------------------
# 测试
# ----------------------------------------------------------------------
if __name__ == '__main__':
    print("=" * 60)
    print("🧪 题材雷达测试")
    print("=" * 60)

    radar = ThemeRadar()
    result = radar.analyze()

    print(f"\n📅 日期: {result.date}")
    print(f"🎯 情绪周期: {result.phase}")
    print(f"\n📊 {result.market_summary}")

    if result.themes:
        print(f"\n🔥 活跃题材（{len(result.themes)}个）:")
        for t in result.themes[:6]:
            trend_icon = {'new': '🆕', 'rising': '📈', 'cooling': '📉', 'stable': '➡️'}.get(t.get('trend', 'new'), '❓')
            print(f"  {trend_icon} 【{t['name']}】涨停{t['zt_count']}只 | 最高{t['max_board']}板 | "
                  f"首板{t['first_board']} | 封单均值{t['avg_fund']:.0f}万 | "
                  f"持续{t.get('consecutive_days', 0)}天 | 强度{t.get('strength', 0):.1f}")
            for s in t.get('stocks', [])[:3]:
                print(f"    {s['ticker']} {s['board']}板 封单{s['fund_wan']}万")

    if result.watchlist:
        print(f"\n👀 观察名单: {', '.join(result.watchlist)}")

    if result.avoid:
        print(f"\n🚫 回避名单: {', '.join(result.avoid)}")

    # 连板梯队
    if result.ladder:
        print(f"\n📶 连板梯队:")
        for board, stocks in result.ladder.items():
            if board >= 2 and stocks:
                print(f"\n  {board}板（{len(stocks)}只）:")
                for s in stocks[:5]:
                    print(f"    {s['ticker']} {s['industry']} 封单{s['fund_wan']}万 换手{s['turnover']}%")

    print("\n" + "=" * 60)
