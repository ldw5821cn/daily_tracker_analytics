"""
智能选股器 - 基于多策略信号打分 + 基本面过滤

候选池来源：
  1. watchlist 个股
  2. 热门 ETF 成分股（可选）
  3. 全市场 akshare 实时行情 TOP（可选）

评分维度：
  - 18+ 技术策略信号
  - 趋势强度（价格 vs 多均线）
  - 成交量/换手活跃度
  - 波动率适中
  - 近期涨幅不过高（避免追高）

输出：
  - TOP N 推荐列表
  - 每只标的：综合分、最佳策略、关键信号、风险提示
"""
import sys
import os
import json
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Tuple, Optional
import pandas as pd
import numpy as np

sys.path.insert(0, '/home/zhihu/daily_tracker_analytics/etf_tracker/multi_agent')
from core.data_layer import get_stock_data, calc_technical_indicators, is_stock
from core.strategy_library import STRATEGIES, scan_signal_score
from core.watchlist import load_list

warnings.filterwarnings('ignore')


class SmartStockPicker:
    """智能选股器"""

    def __init__(self, min_data_days: int = 60,
                 min_annual_vol: float = 10.0,
                 max_annual_vol: float = 100.0,
                 max_momentum_20d: float = 60.0,
                 max_price_distance_ma60: float = 50.0,
                 min_score: int = 5):
        self.min_data_days = min_data_days
        self.min_annual_vol = min_annual_vol
        self.max_annual_vol = max_annual_vol
        self.max_momentum_20d = max_momentum_20d
        self.max_price_distance_ma60 = max_price_distance_ma60
        self.min_score = min_score

    def _oversold_bonus(self, latest) -> Tuple[float, List[str]]:
        """超跌反弹加分：RSI/KDJ/布林带下轨"""
        bonus = 0
        reasons = []
        rsi = float(latest['rsi_14']) if pd.notna(latest['rsi_14']) else 50
        kdj_j = float(latest['kdj_j']) if pd.notna(latest['kdj_j']) else 50
        cp = float(latest['close'])
        boll_down = float(latest['boll_down']) if pd.notna(latest['boll_down']) else 0
        if rsi < 30:
            bonus += 8
            reasons.append(f"RSI超卖({rsi:.0f})")
        if kdj_j < 0:
            bonus += 8
            reasons.append(f"KDJ_J负值({kdj_j:.0f})")
        if boll_down > 0 and cp < boll_down * 1.05:
            bonus += 6
            reasons.append("接近布林下轨")
        return bonus, reasons

    def _load_candidates_from_watchlist(self, category: str = "个股") -> List[Dict]:
        """从 watchlist 加载候选"""
        items = load_list()
        return [item for item in items if item.get('category') == category]

    def _load_candidates_from_etf_components(self, etf_tickers: List[str] = None,
                                             top_n: int = 30) -> List[Dict]:
        """从 ETF 成分股加载候选"""
        try:
            import akshare as ak
            candidates = []
            if etf_tickers is None:
                etf_tickers = ['515880', '516150', '512760', '159819', '562500']
            for etf in etf_tickers:
                try:
                    df = ak.fund_etf_component_em(symbol=etf)
                    if df is None or df.empty:
                        continue
                    for _, row in df.head(top_n).iterrows():
                        code = str(row.get('股票代码', '')).zfill(6)
                        name = row.get('股票名称', '')
                        if code and name:
                            candidates.append({
                                'ticker': code,
                                'name': name,
                                'category': '个股',
                                'source_etf': etf,
                            })
                except Exception:
                    continue
            # 去重
            seen = set()
            unique = []
            for c in candidates:
                if c['ticker'] not in seen:
                    seen.add(c['ticker'])
                    unique.append(c)
            return unique
        except Exception:
            return []

    def _load_market_wide_candidates(self, limit: int = 200) -> List[Dict]:
        """从全市场 A 股当日行情加载候选（按成交额排序）"""
        try:
            import akshare as ak
            df = ak.stock_zh_a_spot_em()
            if df is None or df.empty:
                return []
            # 过滤 ST、退市、北交所、科创板/创业板可保留，但剔除异常停牌
            df = df[df['名称'].notna()]
            df = df[~df['名称'].str.contains('ST|退|N', na=False)]
            df = df[df['成交量'].notna()]
            df['成交额'] = pd.to_numeric(df['成交额'], errors='coerce')
            df = df[df['成交额'] > 0]
            df = df.sort_values('成交额', ascending=False).head(limit)

            candidates = []
            for _, row in df.iterrows():
                code = str(row.get('代码', '')).zfill(6)
                name = row.get('名称', '')
                if code and name:
                    candidates.append({
                        'ticker': code,
                        'name': name,
                        'category': '个股',
                        'source': 'market',
                    })
            return candidates
        except Exception:
            return []

    def _analyze_one(self, candidate: Dict) -> Optional[Dict]:
        """分析单个候选标的"""
        ticker = candidate['ticker']
        name = candidate.get('name', '')
        try:
            df_raw, _ = get_stock_data(ticker)
            if df_raw is None or len(df_raw) < self.min_data_days:
                return None
            df = calc_technical_indicators(df_raw)
            latest = df.iloc[-1]
            cp = float(latest['close'])

            # 基础过滤
            annual_vol = float(latest['annual_vol_20d']) if pd.notna(latest['annual_vol_20d']) else 0
            if not (self.min_annual_vol <= annual_vol <= self.max_annual_vol):
                return None

            mom20 = float(latest['momentum_20d']) if pd.notna(latest['momentum_20d']) else 0
            if mom20 > self.max_momentum_20d:
                return None

            ma60 = float(latest['ma60']) if pd.notna(latest['ma60']) else 0
            if ma60 > 0 and abs(cp / ma60 - 1) * 100 > self.max_price_distance_ma60:
                return None

            vol_ratio = float(latest['vol_ratio']) if pd.notna(latest['vol_ratio']) else 1.0
            avg_volume = float(df['volume'].tail(20).mean()) if len(df) >= 20 else 0

            # 多策略打分
            strategy_scores = []
            total_score = 0
            best_strategy = None
            best_score = 0
            all_reasons = []

            for sid, sinfo in STRATEGIES.items():
                score, reasons = scan_signal_score(df, sinfo['fn'], max_score=100)
                strategy_scores.append({
                    'id': sid,
                    'name': sinfo['name'],
                    'category': sinfo.get('category', '其他'),
                    'score': score,
                    'reasons': reasons,
                })
                total_score += score
                if score > best_score:
                    best_score = score
                    best_strategy = sinfo['name']
                if reasons:
                    all_reasons.extend(reasons)

            # 超跌反弹额外加分
            oversold_bonus, oversold_reasons = self._oversold_bonus(latest)
            total_score += oversold_bonus
            all_reasons.extend(oversold_reasons)

            normalized_score = round(total_score / (len(STRATEGIES) * 100) * 100, 1)
            if normalized_score < self.min_score:
                return None

            # 趋势强度：价格相对多条均线的位置
            ma_scores = []
            for ma in [5, 10, 20, 60]:
                mval = latest.get(f'ma{ma}')
                if pd.notna(mval) and mval > 0:
                    ma_scores.append(1 if cp > mval else -1)
            trend_score = round(sum(ma_scores) / len(ma_scores) * 25 + 50, 1) if ma_scores else 50

            # 综合分 = 策略分*0.5 + 趋势分*0.2 + 量能分*0.1 + 波动率适中分*0.1 + 超跌分*0.1
            volume_score = min(100, max(0, (vol_ratio - 0.5) / 2.5 * 100))
            vol_mid_score = 100 - min(100, abs(annual_vol - 40) / 40 * 100)
            composite = round(normalized_score * 0.5 + trend_score * 0.2 + volume_score * 0.1 + vol_mid_score * 0.1 + oversold_bonus * 0.1, 1)

            # 关键信号摘要
            key_signals = []
            if latest['macd_hist'] > 0:
                key_signals.append('MACD红柱')
            if cp > ma60:
                key_signals.append('站上MA60')
            if vol_ratio > 1.5:
                key_signals.append(f'放量{vol_ratio:.1f}x')
            if mom20 > 0:
                key_signals.append(f'20日涨{mom20:.1f}%')
            rsi = float(latest['rsi_14']) if pd.notna(latest['rsi_14']) else 50
            if rsi < 30:
                key_signals.append('RSI超卖')
            elif rsi > 70:
                key_signals.append('RSI超买')

            return {
                'ticker': ticker,
                'name': name,
                'composite_score': composite,
                'strategy_score': normalized_score,
                'trend_score': trend_score,
                'best_strategy': best_strategy or '-',
                'best_strategy_score': best_score,
                'current_price': round(cp, 2),
                'ma60_dist': round((cp / ma60 - 1) * 100, 2) if ma60 > 0 else 0,
                'momentum_20d': round(mom20, 2),
                'vol_ratio': round(vol_ratio, 2),
                'annual_vol': round(annual_vol, 2),
                'rsi_14': round(rsi, 1),
                'avg_volume': int(avg_volume),
                'key_signals': key_signals,
                'strategy_scores': strategy_scores,
                'source': candidate.get('source_etf') or candidate.get('source') or 'watchlist',
            }
        except Exception:
            return None

    def pick(self, source: str = 'watchlist',
             etf_tickers: List[str] = None,
             top_n: int = 15,
             max_workers: int = 8) -> Tuple[List[Dict], str]:
        """
        执行选股

        Args:
            source: 'watchlist' | 'etf' | 'market' | 'all'
            etf_tickers: ETF 代码列表（source=etf/all 时使用）
            top_n: 返回前 N 个
            max_workers: 并发数

        Returns:
            results, markdown_report
        """
        candidates = []
        if source in ('watchlist', 'all'):
            candidates.extend(self._load_candidates_from_watchlist(category='个股'))
        if source in ('etf', 'all'):
            candidates.extend(self._load_candidates_from_etf_components(etf_tickers))
        if source in ('market', 'all'):
            candidates.extend(self._load_market_wide_candidates(limit=200))

        # 去重
        seen = set()
        unique_candidates = []
        for c in candidates:
            if c['ticker'] not in seen:
                seen.add(c['ticker'])
                unique_candidates.append(c)

        print(f"\n🔍 智能选股器: 候选池 {len(unique_candidates)} 只，来源={source}")
        print(f"{'='*70}")

        results = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(self._analyze_one, c): c for c in unique_candidates}
            for future in as_completed(futures):
                r = future.result()
                if r:
                    results.append(r)
                    print(f"  ✅ {r['name']}({r['ticker']}): 综合{r['composite_score']} | 最佳策略:{r['best_strategy']}({r['best_strategy_score']})")

        results.sort(key=lambda x: x['composite_score'], reverse=True)
        top = results[:top_n]

        # 生成 Markdown 报告
        lines = []
        lines.append("## 🤖 智能选股器推荐\n")
        lines.append(f"**候选池**: {len(unique_candidates)} 只 | **有效通过**: {len(results)} 只 | **推荐 TOP {len(top)}**\n\n")
        lines.append("| 排名 | 标的 | 综合分 | 策略分 | 最佳策略 | 价格 | 20日涨幅 | 量比 | 关键信号 |\n")
        lines.append("|------|------|--------|--------|----------|------|----------|------|----------|\n")
        for i, r in enumerate(top, 1):
            signals_str = ' / '.join(r['key_signals'][:4])
            lines.append(
                f"| {i} | {r['name']}({r['ticker']}) | {r['composite_score']} | "
                f"{r['strategy_score']} | {r['best_strategy']} | {r['current_price']} | "
                f"{r['momentum_20d']:+.1f}% | {r['vol_ratio']:.1f}x | {signals_str} |\n"
            )

        lines.append("\n### 详细分析\n")
        for i, r in enumerate(top, 1):
            lines.append(f"#### {i}. {r['name']} ({r['ticker']})\n")
            lines.append(f"- **综合评分**: {r['composite_score']}/100 | **策略评分**: {r['strategy_score']}/100 | **趋势评分**: {r['trend_score']}/100\n")
            lines.append(f"- **最新价**: {r['current_price']} | **MA60偏离**: {r['ma60_dist']:+.1f}% | **20日涨幅**: {r['momentum_20d']:+.1f}%\n")
            lines.append(f"- **RSI(14)**: {r['rsi_14']} | **量比**: {r['vol_ratio']:.1f}x | **年化波动**: {r['annual_vol']:.1f}%\n")
            lines.append(f"- **最佳策略**: {r['best_strategy']} ({r['best_strategy_score']}分)\n")
            lines.append(f"- **关键信号**: {' / '.join(r['key_signals'])}\n")
            # 策略分数条
            lines.append("- **各策略得分**: \n")
            for s in sorted(r['strategy_scores'], key=lambda x: x['score'], reverse=True)[:5]:
                bar = "█" * (s['score'] // 10) + "░" * (10 - min(s['score'] // 10, 10))
                lines.append(f"  - {s['name']}: [{bar}] {s['score']}/100\n")
            lines.append("\n")

        return top, "".join(lines)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='智能选股器')
    parser.add_argument('--source', '-s', default='watchlist',
                        choices=['watchlist', 'etf', 'market', 'all'],
                        help='候选池来源')
    parser.add_argument('--top', '-n', type=int, default=15, help='推荐数量')
    parser.add_argument('--min-score', type=int, default=5, help='最低综合分')
    args = parser.parse_args()

    picker = SmartStockPicker(min_score=args.min_score)
    results, report = picker.pick(source=args.source, top_n=args.top)
    print("\n" + "=" * 70)
    print(report)
    if results:
        # 保存 JSON 结果供报告生成使用
        out_dir = '/home/zhihu/daily_tracker_analytics/etf_tracker/docs/reports'
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, 'smart_stock_picks.json')
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"结果已保存: {out_path}")
