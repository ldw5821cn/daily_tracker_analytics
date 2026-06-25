#!/usr/bin/env python3
"""
大盘复盘生成器
功能：
1. 获取三大指数（上证/深证/创业板）日线数据
2. 北向资金流向
3. 板块涨跌榜（基于 TickFlow/AkShare）
4. 多模型预测验证：今天预测对了吗
5. 成交量/涨跌家数等市场温度指标
"""

import os
import sys
import json
import time
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import warnings
warnings.filterwarnings('ignore')

# 导入已有模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from data_source_manager import DataSourceManager, DataSourceType
    DSM_AVAILABLE = True
except ImportError:
    DSM_AVAILABLE = False
try:
    from international_market import InternationalMarketFetcher
    INT_MARKET_AVAILABLE = True
except ImportError:
    INT_MARKET_AVAILABLE = False
try:
    from analysis_history_tracker import AnalysisHistoryTracker
    HISTORY_AVAILABLE = True
except ImportError:
    HISTORY_AVAILABLE = False


class MarketReviewGenerator:
    """大盘复盘生成器"""
    
    # 三大指数代码映射
    INDEX_MAP = {
        "上证指数": ("000001", "SH"),
        "深证成指": ("399001", "SZ"),
        "创业板指": ("399006", "SZ"),
        "科创50": ("000688", "SH"),
    }
    
    def __init__(self):
        self.fetcher = None
        if DSM_AVAILABLE:
            try:
                self.fetcher = DataSourceManager()
            except Exception:
                pass
    
    def _fetch_kline(self, symbol: str, days: int = 60) -> Optional[pd.DataFrame]:
        """获取指数K线数据"""
        df = None
        if self.fetcher:
            try:
                df = self.fetcher.get_kline(symbol, count=days, period='1d')
            except Exception:
                pass
        if df is None or len(df) < 2:
            try:
                import akshare as ak
                if symbol.endswith(".SH"):
                    raw = ak.stock_zh_index_daily(symbol="sh" + symbol.replace(".SH", ""))
                elif symbol.endswith(".SZ"):
                    raw = ak.stock_zh_index_daily(symbol="sz" + symbol.replace(".SZ", ""))
                else:
                    return None
                raw = raw.rename(columns={"date": "date", "open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume"})
                raw["date"] = pd.to_datetime(raw["date"])
                df = raw.tail(days)
            except Exception:
                pass
        return df
    
    def fetch_index_data(self, days: int = 30) -> Dict:
        """获取三大指数数据"""
        results = {}
        for name, (code, exchange) in self.INDEX_MAP.items():
            try:
                symbol = f"{code}.{exchange}"
                df = self._fetch_kline(symbol, days=60)
                if df is None or len(df) < 2:
                    continue
                latest = df.iloc[-1]
                prev = df.iloc[-2]
                first_30 = df.iloc[-min(30, len(df))] if len(df) >= 30 else df.iloc[0]
                first_5 = df.iloc[-min(5, len(df))] if len(df) >= 5 else df.iloc[0]
                
                change_pct = (latest['close'] - prev['close']) / prev['close'] * 100
                return_5d = (latest['close'] - first_5['close']) / first_5['close'] * 100
                return_30d = (latest['close'] - first_30['close']) / first_30['close'] * 100
                
                # 简单RSI
                closes = df['close'].values
                gains, losses = 0, 0
                for i in range(-14, 0):
                    diff = closes[i] - closes[i-1]
                    if diff > 0: gains += diff
                    else: losses -= diff
                rsi = 50
                if gains + losses > 0:
                    rsi = 100 - 100 / (1 + gains / (losses + 1e-10))
                
                # 成交量变化
                avg_vol_5 = df['volume'].iloc[-5:].mean()
                avg_vol_20 = df['volume'].iloc[-20:].mean() if len(df) >= 20 else avg_vol_5
                vol_ratio = avg_vol_5 / avg_vol_20 if avg_vol_20 > 0 else 1
                
                results[name] = {
                    "price": round(latest['close'], 2),
                    "change_pct": round(change_pct, 2),
                    "return_5d": round(return_5d, 2),
                    "return_30d": round(return_30d, 2),
                    "rsi": round(rsi, 1),
                    "volume_ratio": round(vol_ratio, 2),
                }
            except Exception as e:
                print(f"  获取 {name} 数据失败: {e}")
        return results
    
    def get_northbound_flow(self) -> Dict:
        """获取北向资金数据（沪股通+深股通）"""
        try:
            import akshare as ak
            df = ak.stock_hsgt_north_net_flow_in_em(symbol="北上")
            if df is not None and len(df) > 0:
                df = df.sort_values('date', ascending=False)
                latest = df.iloc[0]
                total = latest.get('value', 0) or latest.get('net_amount', 0) or 0
                # 5日均值
                if len(df) >= 5:
                    avg = df.iloc[:5]['value'].mean() if 'value' in df.columns else df.iloc[:5]['net_amount'].mean()
                else:
                    avg = total
                return {
                    "today_net": float(total),
                    "avg_5d": float(avg),
                    "strength": "流入" if total > 0 else "流出" if total < 0 else "平衡",
                    "above_avg": abs(total) > abs(avg) if avg != 0 else False
                }
        except Exception as e:
            print(f"  北向资金获取失败: {e}")
        return {"today_net": 0, "avg_5d": 0, "strength": "未知", "above_avg": False}
    
    def get_sector_performance(self) -> Dict:
        """获取板块涨跌榜"""
        try:
            import akshare as ak
            # 获取行业板块涨跌幅
            df = ak.stock_board_industry_name_em()
            if df is not None and len(df) > 0:
                df = df.sort_values('涨跌幅', ascending=False)
                top5 = []
                bottom5 = []
                for _, row in df.head(5).iterrows():
                    top5.append({"name": row.get('板块名称', ''), "change": round(row.get('涨跌幅', 0), 2)})
                for _, row in df.tail(5).iterrows():
                    bottom5.append({"name": row.get('板块名称', ''), "change": round(row.get('涨跌幅', 0), 2)})
                return {"top5": top5, "bottom5": bottom5}
        except Exception as e:
            print(f"  板块涨跌获取失败: {e}")
        return {"top5": [], "bottom5": []}
    
    def validate_predictions(self) -> Dict:
        """验证多模型预测：今天预测 vs 实际表现"""
        if not HISTORY_AVAILABLE:
            return {}
        try:
            tracker = AnalysisHistoryTracker()
            yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
            today = datetime.now().strftime('%Y-%m-%d')
            
            # 昨天预测了今天的方向，今天实际收盘后验证
            conn = __import__('sqlite3').connect(tracker.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT etf_code, predict_date, period_days,
                       ensemble_predicted_return, ensemble_trend, actual_return, direction_accuracy
                FROM model_predictions
                WHERE predict_date = ?
                  AND period_days = 1
                  AND direction_accuracy IS NOT NULL
                LIMIT 10
            ''', (yesterday,))
            
            rows = cursor.fetchall()
            conn.close()
            
            if rows:
                correct = sum(1 for r in rows if r[6] == 1)
                return {
                    "total": len(rows),
                    "correct": correct,
                    "accuracy": round(correct / len(rows) * 100, 1),
                    "details": [{
                        "code": r[0],
                        "predicted": round(r[3]*100, 2),
                        "actual": round((r[5] or 0)*100, 2),
                        "correct": r[6] == 1
                    } for r in rows[:5]]
                }
        except Exception as e:
            print(f"  预测验证失败: {e}")
        return {}
    
    def generate_market_review(self, days: int = 30) -> str:
        """生成大盘复盘报告 Markdown"""
        now = datetime.now()
        date_str = now.strftime('%Y-%m-%d')
        is_afternoon = now.hour >= 13
        
        report = "# 大盘复盘\n\n"
        report += f"> **报告时间**: {now.strftime('%Y-%m-%d %H:%M')}\n"
        report += f"> **类型**: {'收盘复盘' if is_afternoon else '早间复盘'}\n\n"
        
        # 1. 三大指数
        print("  获取三大指数数据...")
        indices = self.fetch_index_data(days)
        if indices:
            report += "## 一、三大指数\n\n"
            report += "| 指数 | 最新价 | 涨跌幅 | 5日 | 30日 | RSI | 量比 |\n"
            report += "|------|--------|--------|-----|------|-----|------|\n"
            for name, data in indices.items():
                emoji = "📈" if data['change_pct'] > 0 else "📉" if data['change_pct'] < 0 else "➖"
                report += f"| {emoji} {name} | {data['price']} | {data['change_pct']:+.2f}% | {data['return_5d']:+.2f}% | {data['return_30d']:+.2f}% | {data['rsi']} | {data['volume_ratio']} |\n"
        
        # 2. 北向资金
        print("  获取北向资金数据...")
        north = self.get_northbound_flow()
        if north.get('today_net'):
            emoji = "📈" if north['today_net'] > 0 else "📉"
            report += f"\n## 二、北向资金\n\n"
            report += f"- {emoji} **今日净{'流入' if north['today_net'] > 0 else '流出'}**: {north['today_net']/1e8:.2f} 亿元\n"
            report += f"- 📊 **5日均值**: {north['avg_5d']/1e8:.2f} 亿元\n"
            report += f"- {'🔺 超出近期均值' if north.get('above_avg') else '🔹 低于或持平近期均值'}\n"
        
        # 3. 板块涨跌榜
        print("  获取板块涨跌榜...")
        sectors = self.get_sector_performance()
        if sectors.get('top5'):
            report += f"\n## 三、板块涨跌榜\n\n"
            report += "**🏆 涨幅前5**\n\n"
            for i, s in enumerate(sectors['top5']):
                report += f"  {i+1}. {s['name']} {s['change']:+.2f}%\n"
            report += "\n**📉 跌幅前5**\n\n"
            for i, s in enumerate(sectors['bottom5']):
                report += f"  {i+1}. {s['name']} {s['change']:+.2f}%\n"
        
        # 4. 国际市场联动
        print("  获取国际市场数据...")
        if INT_MARKET_AVAILABLE:
            try:
                im = InternationalMarketFetcher()
                im_data = im.fetch_all(days=30)
                if im_data:
                    report += "\n## 四、国际市场联动\n\n"
                    for key in ['^GSPC', '^IXIC', '^DJI', 'CL=F', 'GC=F']:
                        if key in im_data:
                            d = im_data[key]
                            report += f"- {d['name']}: {d['price']} ({d['change_pct']:+.2f}%)\n"
            except Exception as e:
                print(f"  国际市场获取失败: {e}")
        
        # 5. 预测验证（仅收盘复盘）
        if is_afternoon:
            print("  验证多模型预测...")
            pred = self.validate_predictions()
            if pred and pred.get('total', 0) > 0:
                report += f"\n## 五、今日预测验证\n\n"
                report += f"- 📊 **预测方向准确率**: {pred['total']} 次预测中 {pred['correct']} 次正确 = **{pred['accuracy']}%**\n\n"
                report += "| 代码 | 预测收益 | 实际收益 | 结果 |\n"
                report += "|------|----------|----------|------|\n"
                for d in pred.get('details', []):
                    emoji = "✅" if d['correct'] else "❌"
                    report += f"| {d['code']} | {d['predicted']:+.2f}% | {d['actual']:+.2f}% | {emoji} |\n"
        
        report += "\n---\n"
        report += f"*复盘生成时间: {now.strftime('%Y-%m-%d %H:%M:%S')}*\n"
        report += "*数据来源: TickFlow + AkShare + 东方财富*\n"
        
        return report


def generate_morning_review() -> str:
    """生成早间复盘"""
    gen = MarketReviewGenerator()
    return gen.generate_market_review()


def generate_afternoon_review() -> str:
    """生成收盘复盘"""
    gen = MarketReviewGenerator()
    return gen.generate_market_review()


if __name__ == "__main__":
    report = generate_morning_review()
    print(report)
