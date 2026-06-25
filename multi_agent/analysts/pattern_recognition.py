"""
技术形态识别模块

识别常见 K 线/价格形态：
  - 头肩顶 / 头肩底
  - 双底 (W 底)
  - 双顶 (M 顶)
  - 杯柄形态
  - 上升/下降三角形
  - 对称三角形
  - 旗形 / 楔形
  - 通道 (平行通道)
  - 圆底 / 圆顶

输出：
  - 每只标的识别的形态列表
  - 形态置信度、方向（看涨/看跌/中性）、目标价/止损位
  - Markdown 汇总报告
"""
import sys
import os
import json
import warnings
import numpy as np
import pandas as pd
from scipy.signal import argrelextrema
from typing import List, Dict, Tuple, Optional

sys.path.insert(0, '/home/zhihu/daily_tracker_analytics/etf_tracker/multi_agent')
from core.data_layer import get_stock_data, calc_technical_indicators
from core.watchlist import load_list

warnings.filterwarnings('ignore')


class PatternRecognition:
    """技术形态识别"""

    def __init__(self, window: int = 120, min_touches: int = 3):
        self.window = window
        self.min_touches = min_touches

    def _local_extrema(self, series: pd.Series, order: int = 5) -> Tuple[np.ndarray, np.ndarray]:
        """获取局部极值点索引"""
        max_idx = argrelextrema(series.values, np.greater_equal, order=order)[0]
        min_idx = argrelextrema(series.values, np.less_equal, order=order)[0]
        return max_idx, min_idx

    def _recent_window(self, df: pd.DataFrame) -> pd.DataFrame:
        """取最近 window 根 K 线"""
        return df.tail(self.window).reset_index(drop=True)

    def detect_head_and_shoulders(self, df: pd.DataFrame) -> List[Dict]:
        """头肩顶/底识别（简化版：基于局部峰值/谷值）"""
        results = []
        highs = df['high']
        lows = df['low']
        max_idx, min_idx = self._local_extrema(highs, order=5)

        if len(max_idx) >= 3:
            # 最近 3 个高点
            recent_max = max_idx[-3:]
            h1, h2, h3 = highs.iloc[recent_max[0]], highs.iloc[recent_max[1]], highs.iloc[recent_max[2]]
            # 头肩顶：中间最高，两边近似
            if h2 > h1 and h2 > h3 and abs(h1 - h3) / max(h1, h3) < 0.15:
                neckline = max(lows.iloc[recent_max[0]:recent_max[2]])
                target = neckline - (h2 - neckline)
                results.append({
                    'pattern': '头肩顶',
                    'direction': '看跌',
                    'confidence': round(70 - abs(h1 - h3) / max(h1, h3) * 100, 1),
                    'neckline': round(neckline, 2),
                    'target': round(target, 2),
                    'stop': round(h2 * 1.02, 2),
                    'description': f'三个高点中间({h2:.2f})最高，颈线{neckline:.2f}，跌破看{target:.2f}'
                })

        # 头肩底：基于低点
        if len(min_idx) >= 3:
            recent_min = min_idx[-3:]
            l1, l2, l3 = lows.iloc[recent_min[0]], lows.iloc[recent_min[1]], lows.iloc[recent_min[2]]
            if l2 < l1 and l2 < l3 and abs(l1 - l3) / max(l1, l3) < 0.15:
                neckline = min(highs.iloc[recent_min[0]:recent_min[2]])
                target = neckline + (neckline - l2)
                results.append({
                    'pattern': '头肩底',
                    'direction': '看涨',
                    'confidence': round(70 - abs(l1 - l3) / max(l1, l3) * 100, 1),
                    'neckline': round(neckline, 2),
                    'target': round(target, 2),
                    'stop': round(l2 * 0.98, 2),
                    'description': f'三个低点中间({l2:.2f})最低，颈线{neckline:.2f}，突破看{target:.2f}'
                })
        return results

    def detect_double_patterns(self, df: pd.DataFrame) -> List[Dict]:
        """双顶 / 双底"""
        results = []
        highs = df['high']
        lows = df['low']
        max_idx, min_idx = self._local_extrema(highs, order=5)

        if len(max_idx) >= 2:
            i1, i2 = max_idx[-2], max_idx[-1]
            h1, h2 = highs.iloc[i1], highs.iloc[i2]
            if abs(h1 - h2) / max(h1, h2) < 0.08 and i2 - i1 > 5:
                trough = lows.iloc[i1:i2].min()
                target = trough - (h1 - trough)
                results.append({
                    'pattern': '双顶(M顶)',
                    'direction': '看跌',
                    'confidence': round(75 - abs(h1 - h2) / max(h1, h2) * 100, 1),
                    'neckline': round(trough, 2),
                    'target': round(target, 2),
                    'stop': round(max(h1, h2) * 1.02, 2),
                    'description': f'两个高点接近({h1:.2f}/{h2:.2f})，颈线{trough:.2f}，跌破看{target:.2f}'
                })

        if len(min_idx) >= 2:
            i1, i2 = min_idx[-2], min_idx[-1]
            l1, l2 = lows.iloc[i1], lows.iloc[i2]
            if abs(l1 - l2) / max(l1, l2) < 0.08 and i2 - i1 > 5:
                peak = highs.iloc[i1:i2].max()
                target = peak + (peak - l1)
                results.append({
                    'pattern': '双底(W底)',
                    'direction': '看涨',
                    'confidence': round(75 - abs(l1 - l2) / max(l1, l2) * 100, 1),
                    'neckline': round(peak, 2),
                    'target': round(target, 2),
                    'stop': round(min(l1, l2) * 0.98, 2),
                    'description': f'两个低点接近({l1:.2f}/{l2:.2f})，颈线{peak:.2f}，突破看{target:.2f}'
                })
        return results

    def detect_triangle(self, df: pd.DataFrame) -> List[Dict]:
        """三角形：上升、下降、对称"""
        results = []
        highs = df['high'].values
        lows = df['low'].values
        n = len(df)
        if n < 30:
            return results

        # 用最后 30 根 K 线做线性拟合
        x = np.arange(30)
        h = highs[-30:]
        l = lows[-30:]
        slope_h, intercept_h = np.polyfit(x, h, 1)
        slope_l, intercept_l = np.polyfit(x, l, 1)

        h_std = np.std(h)
        l_std = np.std(l)

        # 判断收敛：高点下降 / 低点上升
        converging = slope_h < -0.01 and slope_l > 0.01
        flat_top = abs(slope_h) < 0.01 and h_std < np.mean(h) * 0.05
        flat_bottom = abs(slope_l) < 0.01 and l_std < np.mean(l) * 0.05

        if converging:
            apex = (intercept_l - intercept_h) / (slope_h - slope_l)
            if apex > 20:  # 未来还有空间
                results.append({
                    'pattern': '对称三角形',
                    'direction': '中性',
                    'confidence': round(min(80, 50 + abs(slope_h) * 200 + abs(slope_l) * 200), 1),
                    'description': '高点下行、低点上行，即将选择方向，突破上轨看涨，跌破下轨看跌'
                })
        elif flat_top and slope_l > 0.01:
            results.append({
                'pattern': '上升三角形',
                'direction': '看涨',
                'confidence': round(min(80, 55 + slope_l * 200), 1),
                'description': '水平阻力 + 抬高支撑，突破上轨为看涨信号'
            })
        elif flat_bottom and slope_h < -0.01:
            results.append({
                'pattern': '下降三角形',
                'direction': '看跌',
                'confidence': round(min(80, 55 + abs(slope_h) * 200), 1),
                'description': '水平支撑 + 下降阻力，跌破下轨为看跌信号'
            })
        return results

    def detect_channel(self, df: pd.DataFrame) -> List[Dict]:
        """平行通道（上升/下降/横盘）"""
        results = []
        highs = df['high'].values[-40:]
        lows = df['low'].values[-40:]
        x = np.arange(len(highs))
        slope_h, intercept_h = np.polyfit(x, highs, 1)
        slope_l, intercept_l = np.polyfit(x, lows, 1)

        parallel = abs(slope_h - slope_l) < 0.005
        if not parallel:
            return results

        latest_close = df['close'].iloc[-1]
        upper = slope_h * (len(highs) - 1) + intercept_h
        lower = slope_l * (len(lows) - 1) + intercept_l
        mid = (upper + lower) / 2

        # 过滤太窄的震荡
        if (upper - lower) / mid < 0.05:
            return results

        if slope_h > 0.03 and slope_l > 0.03:
            direction = '看涨'
        elif slope_h < -0.03 and slope_l < -0.03:
            direction = '看跌'
        else:
            direction = '中性'

        desc = f"平行通道 上{upper:.2f}/中{mid:.2f}/下{lower:.2f}，"
        if latest_close > upper * 0.99:
            desc += "接近上轨，关注突破或回踩"
        elif latest_close < lower * 1.01:
            desc += "接近下轨，关注反弹"
        else:
            desc += "运行在中轨附近"

        results.append({
            'pattern': '平行通道',
            'direction': direction,
            'confidence': round(min(80, 55 + (1 - abs(slope_h - slope_l) / 0.02) * 25), 1),
            'target': round(upper if direction == '看涨' else lower, 2),
            'stop': round(lower if direction == '看涨' else upper, 2),
            'description': desc
        })
        return results

    def detect_cup_handle(self, df: pd.DataFrame) -> List[Dict]:
        """杯柄形态（简化：U 型底 + 小幅回撤）"""
        results = []
        close = df['close'].values[-60:]
        if len(close) < 40:
            return results

        # 找最低点位置
        mid = len(close) // 2
        left = close[:mid]
        right = close[mid:]
        min_left = left.min()
        min_right = right.min()
        min_idx_left = left.argmin()
        min_idx_right = right.argmin()

        # 杯形：左侧下降到底，右侧回升，左右低点接近
        cup_like = (min_idx_left > len(left) * 0.3 and
                    min_idx_right > len(right) * 0.3 and
                    abs(min_left - min_right) / max(min_left, min_right) < 0.12)

        # 柄部：最近 5-10 天小幅回调
        handle = close[-10:].max() < close[-20:-10].max() and close[-1] > close[-10:].min() * 1.02

        if cup_like and handle:
            target = close[-20:-10].max()
            results.append({
                'pattern': '杯柄形态',
                'direction': '看涨',
                'confidence': 65,
                'target': round(target, 2),
                'stop': round(min_right * 0.97, 2),
                'description': f'杯部低点{min_left:.2f}/{min_right:.2f}，柄部缩量回调，突破{target:.2f}看涨'
            })
        return results

    def detect_rounding(self, df: pd.DataFrame) -> List[Dict]:
        """圆底 / 圆顶"""
        results = []
        close = df['close'].values[-40:]
        if len(close) < 30:
            return results
        mid = len(close) // 2
        left = close[:mid]
        right = close[mid:]

        # 圆底：先跌后涨，中间最低
        if left[-1] < left[0] and right[-1] > right[0] and close[mid] == close.min():
            results.append({
                'pattern': '圆底',
                'direction': '看涨',
                'confidence': 60,
                'description': '价格呈圆弧状触底回升，属于底部反转形态'
            })
        # 圆顶：先涨后跌，中间最高
        elif left[-1] > left[0] and right[-1] < right[0] and close[mid] == close.max():
            results.append({
                'pattern': '圆顶',
                'direction': '看跌',
                'confidence': 60,
                'description': '价格呈圆弧状见顶回落，属于顶部反转形态'
            })
        return results

    def detect_flag_pennant(self, df: pd.DataFrame) -> List[Dict]:
        """旗形 / 楔形：急涨/急跌后的小幅反向倾斜整理"""
        results = []
        close = df['close'].values[-40:]
        if len(close) < 30:
            return results

        # 前期趋势（前15天）
        pre = close[:15]
        post = close[15:]
        pre_ret = (pre[-1] - pre[0]) / pre[0]

        if abs(pre_ret) < 0.10:
            return results

        # 整理区斜率
        x = np.arange(len(post))
        slope, intercept = np.polyfit(x, post, 1)
        # 整理区波动率显著低于前期
        pre_vol = np.std(pre) / np.mean(pre)
        post_vol = np.std(post) / np.mean(post)

        if post_vol > pre_vol * 0.7:
            return results

        direction = '看涨' if pre_ret > 0 else '看跌'
        # 楔形：整理区斜率与前期趋势同向；旗形：反向
        pattern_type = '楔形' if (pre_ret > 0 and slope > 0) or (pre_ret < 0 and slope < 0) else '旗形'
        target = close[-1] * (1 + pre_ret * 0.5)
        stop = close[-1] * (1 - pre_ret * 0.3) if pre_ret > 0 else close[-1] * (1 + abs(pre_ret) * 0.3)

        results.append({
            'pattern': f'{direction}{pattern_type}',
            'direction': direction,
            'confidence': round(min(75, 55 + abs(pre_ret) * 200), 1),
            'target': round(target, 2),
            'stop': round(stop, 2),
            'description': f'前期{"上涨" if pre_ret > 0 else "下跌"}{pre_ret*100:.1f}%后整理，{pattern_type}突破看{target:.2f}'
        })
        return results

    def analyze(self, ticker: str, name: str = '') -> Optional[Dict]:
        """分析单只标的技术形态"""
        try:
            df_raw, _ = get_stock_data(ticker)
            if df_raw is None or len(df_raw) < self.window:
                return None
            df = calc_technical_indicators(df_raw)
            df_win = self._recent_window(df)

            patterns = []
            patterns.extend(self.detect_head_and_shoulders(df_win))
            patterns.extend(self.detect_double_patterns(df_win))
            patterns.extend(self.detect_triangle(df_win))
            patterns.extend(self.detect_channel(df_win))
            patterns.extend(self.detect_cup_handle(df_win))
            patterns.extend(self.detect_rounding(df_win))
            patterns.extend(self.detect_flag_pennant(df_win))

            if not patterns:
                return None

            # 过滤置信度低于 55 的形态
            patterns = [p for p in patterns if p['confidence'] >= 55]
            if not patterns:
                return None

            latest = df.iloc[-1]
            return {
                'ticker': ticker,
                'name': name or ticker,
                'date': str(df.index[-1]) if hasattr(df.index[-1], 'strftime') else str(df.index[-1]),
                'close': round(float(latest['close']), 2),
                'patterns': patterns,
                'top_pattern': max(patterns, key=lambda x: x['confidence']),
            }
        except Exception as e:
            print(f"  ⚠️ {ticker} 形态识别失败: {e}")
            return None

    def scan_watchlist(self, category: str = '个股', top_n: int = 20) -> Tuple[List[Dict], str]:
        """扫描 watchlist 并生成 Markdown 报告"""
        watchlist = load_list()
        candidates = []
        if isinstance(watchlist, list):
            for item in watchlist:
                if item.get('category') == category and item.get('ticker'):
                    candidates.append({'ticker': item['ticker'], 'name': item.get('name', item['ticker'])})
        else:
            for item in watchlist.get(category, []):
                if item.get('ticker'):
                    candidates.append({'ticker': item['ticker'], 'name': item.get('name', item['ticker'])})

        results = []
        print(f"🔍 技术形态扫描: {len(candidates)} 只候选")
        for c in candidates:
            r = self.analyze(c['ticker'], c['name'])
            if r:
                results.append(r)
                print(f"  ✅ {r['name']}({r['ticker']}): {r['top_pattern']['pattern']} | 置信度{r['top_pattern']['confidence']}")

        # 按最高置信度排序
        results.sort(key=lambda x: x['top_pattern']['confidence'], reverse=True)
        top = results[:top_n]

        # 生成报告
        bullish = [r for r in top if r['top_pattern']['direction'] == '看涨']
        bearish = [r for r in top if r['top_pattern']['direction'] == '看跌']
        neutral = [r for r in top if r['top_pattern']['direction'] == '中性']

        lines = ["## 📐 技术形态识别\n"]
        lines.append(f"**扫描标的**: {len(candidates)} 只 | **识别到形态**: {len(results)} 只 | **展示 TOP {len(top)}**\n\n")

        def _section(title, items):
            if not items:
                return []
            sec = [f"### {title}\n"]
            sec.append("| 标的 | 形态 | 方向 | 置信度 | 目标价 | 止损位 | 说明 |\n")
            sec.append("|------|------|------|--------|--------|--------|------|\n")
            for r in items:
                p = r['top_pattern']
                sec.append(
                    f"| {r['name']}({r['ticker']}) | {p['pattern']} | {p['direction']} | "
                    f"{p['confidence']} | {p.get('target', '-')} | {p.get('stop', '-')} | {p['description']} |\n"
                )
            sec.append("\n")
            return sec

        lines.extend(_section('看涨形态', bullish))
        lines.extend(_section('看跌形态', bearish))
        lines.extend(_section('中性/待突破', neutral))

        return top, "".join(lines)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='技术形态识别')
    parser.add_argument('--category', '-c', default='个股', help='watchlist 类别')
    parser.add_argument('--top', '-n', type=int, default=20, help='展示数量')
    args = parser.parse_args()

    pr = PatternRecognition(window=120)
    results, report = pr.scan_watchlist(category=args.category, top_n=args.top)
    print("\n" + "=" * 70)
    print(report)
    if results:
        out_dir = '/home/zhihu/daily_tracker_analytics/etf_tracker/docs/reports'
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, 'pattern_recognition.json')
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"结果已保存: {out_path}")
