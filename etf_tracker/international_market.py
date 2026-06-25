#!/usr/bin/env python3
"""
国际市场数据模块
获取美股指数、商品、汇率、VIX、恒生指数等数据，为A股开盘提供国际环境参考
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Optional


class InternationalMarketFetcher:
    """国际市场数据获取器"""
    
    # 需要获取的国际指数和商品
    TICKERS = {
        # 美股指数
        '^DJI': {'name': '道琼斯工业指数', 'symbol': 'DJI', 'category': 'us_index'},
        '^GSPC': {'name': '标普500指数', 'symbol': 'SPX', 'category': 'us_index'},
        '^IXIC': {'name': '纳斯达克综合指数', 'symbol': 'IXIC', 'category': 'us_index'},
        '^RUT': {'name': '罗素2000指数', 'symbol': 'RUT', 'category': 'us_index'},
        # 波动率
        '^VIX': {'name': 'VIX恐慌指数', 'symbol': 'VIX', 'category': 'risk'},
        # 商品 - 能源
        'CL=F': {'name': 'WTI原油', 'symbol': 'CL', 'category': 'commodity'},
        'BZ=F': {'name': '布伦特原油', 'symbol': 'BZ', 'category': 'commodity'},
        'NG=F': {'name': '天然气', 'symbol': 'NG', 'category': 'commodity'},
        # 商品 - 贵金属
        'GC=F': {'name': 'COMEX黄金', 'symbol': 'GC', 'category': 'commodity'},
        'SI=F': {'name': 'COMEX白银', 'symbol': 'SI', 'category': 'commodity'},
        'PL=F': {'name': '铂金', 'symbol': 'PL', 'category': 'commodity'},
        # 商品 - 工业金属
        'HG=F': {'name': 'COMEX铜', 'symbol': 'HG', 'category': 'commodity'},
        'ALI=F': {'name': '伦铝', 'symbol': 'ALI', 'category': 'commodity'},
        # 商品 - 农产品/软商品
        'ZW=F': {'name': '小麦', 'symbol': 'ZW', 'category': 'commodity'},
        'ZS=F': {'name': '大豆', 'symbol': 'ZS', 'category': 'commodity'},
        'CC=F': {'name': '可可', 'symbol': 'CC', 'category': 'commodity'},
        # 外汇
        'EURUSD=X': {'name': '欧元/美元', 'symbol': 'EURUSD', 'category': 'forex'},
        'USDJPY=X': {'name': '美元/日元', 'symbol': 'USDJPY', 'category': 'forex'},
        'GBPUSD=X': {'name': '英镑/美元', 'symbol': 'GBPUSD', 'category': 'forex'},
        'CNY=X': {'name': '美元/人民币', 'symbol': 'CNY', 'category': 'forex'},
        'CNH=F': {'name': '美元/离岸人民币', 'symbol': 'CNH', 'category': 'forex'},
        'DX-Y.NYB': {'name': '美元指数', 'symbol': 'DXY', 'category': 'forex'},
        # 港股
        '^HSI': {'name': '恒生指数', 'symbol': 'HSI', 'category': 'hk_index'},
        '513180': {'name': '恒生科技ETF华夏', 'symbol': 'HSTECH', 'category': 'hk_index'},
    }
    
    def __init__(self):
        self._cache = {}
        self._cache_time = None
    
    def _fetch_yf(self, ticker_key: str, days: int) -> Optional[pd.DataFrame]:
        """使用 yfinance 获取数据"""
        import yfinance as yf
        ticker = yf.Ticker(ticker_key)
        return ticker.history(period=f"{days * 2}d")
    
    def _fetch_tickflow(self, code: str, days: int) -> Optional[pd.DataFrame]:
        """使用 TickFlow 获取 A股/ETF 数据"""
        try:
            from data_source_manager import DataSourceManager
            import os, json
            # 优先使用环境变量或配置文件中的 key
            cfg_path = os.path.join(os.path.dirname(__file__), 'config.json')
            cfg = {}
            if os.path.exists(cfg_path):
                with open(cfg_path, 'r', encoding='utf-8') as f:
                    cfg = json.load(f) or {}
            dsm = DataSourceManager(config_dict=cfg)
            return dsm.get_etf_kline(code, days=days)
        except Exception:
            return None
    
    def _normalize_df(self, df: pd.DataFrame, source: str) -> pd.DataFrame:
        """标准化为统一列名"""
        df = df.copy()
        if source == 'yf':
            df = df.reset_index()
            df = df.rename(columns={
                'Date': 'date', 'Open': 'open', 'Close': 'close',
                'High': 'high', 'Low': 'low', 'Volume': 'volume'
            })
            df['date'] = pd.to_datetime(df['date']).dt.tz_localize(None)
        df = df.sort_values('date').reset_index(drop=True)
        return df
    
    def fetch_all(self, days: int = 30) -> Dict[str, Dict]:
        """获取所有国际市场数据"""
        results = {}
        failures = []
        
        # 批量获取以提高效率 - 分组按category
        for category in ['us_index', 'risk', 'commodity', 'forex', 'hk_index']:
            cat_tickers = {k: v for k, v in self.TICKERS.items() if v['category'] == category}
            for ticker_key, info in cat_tickers.items():
                try:
                    # A股/ETF 代码优先走 TickFlow
                    if ticker_key.isdigit() or (len(ticker_key) == 6 and ticker_key.isdigit()):
                        df = self._fetch_tickflow(ticker_key, days)
                        source = 'tf'
                    else:
                        df = None
                        source = 'yf'
                    
                    # TickFlow 失败降级到 yfinance
                    if df is None or len(df) == 0:
                        df = self._fetch_yf(ticker_key, days)
                        source = 'yf'
                    
                    if df is None or len(df) == 0:
                        failures.append(f"{info['name']}({ticker_key}): 空数据")
                        continue
                    
                    df = self._normalize_df(df, source)
                    
                    latest = df.iloc[-1]
                    prev = df.iloc[-2] if len(df) >= 2 else latest
                    
                    # 计算涨跌幅
                    change_pct = ((latest['close'] - prev['close']) / prev['close']) * 100
                    
                    # 获取近30天收益
                    first_30 = df.iloc[-min(22, len(df))]
                    return_30d = ((latest['close'] - first_30['close']) / first_30['close']) * 100
                    
                    # 近5天收益(周)
                    first_5 = df.iloc[-min(5, len(df))]
                    return_5d = ((latest['close'] - first_5['close']) / first_5['close']) * 100
                    
                    result = {
                        'name': info['name'],
                        'symbol': info['symbol'],
                        'category': info['category'],
                        'price': round(float(latest['close']), 2),
                        'open': round(float(latest['open']), 2),
                        'high': round(float(latest['high']), 2),
                        'low': round(float(latest['low']), 2),
                        'change_pct': round(float(change_pct), 2),
                        'return_5d': round(float(return_5d), 2),
                        'return_30d': round(float(return_30d), 2),
                        'volume': int(latest['volume']),
                        'latest_date': str(latest['date'].strftime('%Y-%m-%d') if hasattr(latest['date'], 'strftime') else latest['date']),
                    }
                    
                    # 特殊处理 VIX
                    if ticker_key == '^VIX':
                        result['level'] = '高恐慌' if result['price'] > 25 else '中等' if result['price'] > 15 else '低恐慌'
                    
                    # 特殊处理人民币汇率
                    if ticker_key == 'CNY=X':
                        result['note'] = '升值' if result['change_pct'] < 0 else '贬值'
                    
                    results[ticker_key] = result
                    
                except Exception as e:
                    failures.append(f"{info['name']}({ticker_key}): {e}")
        
        if failures:
            print(f"  [InternationalMarket] 部分数据获取失败:")
            for f in failures:
                print(f"    ⚠️ {f}")
        
        return results
    
    def generate_market_summary(self) -> str:
        """生成国际市场摘要文本"""
        data = self.fetch_all(days=30)
        
        lines = []
        date = datetime.now().strftime('%Y-%m-%d')
        lines.append(f"> **数据时间**: {date}  |  **数据来源**: Yahoo Finance")
        lines.append("")
        
        # 美股三大指数
        lines.append("### 美股指数")
        lines.append("")
        lines.append("| 指数 | 最新价 | 涨跌幅 | 近5天 | 近30天 |")
        lines.append("|------|--------|--------|-------|--------|")
        for key in ['^DJI', '^GSPC', '^IXIC', '^RUT']:
            if key in data:
                d = data[key]
                lines.append(f"| {d['name']} | {d['price']:,.2f} | {d['change_pct']:+.2f}% | {d['return_5d']:+.2f}% | {d['return_30d']:+.2f}% |")
        lines.append("")
        
        # VIX
        if '^VIX' in data:
            v = data['^VIX']
            lines.append(f"**VIX恐慌指数**: {v['price']:.2f} ({v['level']})  ")
        lines.append("")
        
        # 大宗商品
        lines.append("### 大宗商品")
        lines.append("")
        lines.append("| 品种 | 最新价 | 涨跌幅 | 近5天 | 近30天 |")
        lines.append("|------|--------|--------|-------|--------|")
        for key in ['CL=F', 'BZ=F', 'GC=F', 'SI=F', 'HG=F', 'NG=F']:
            if key in data:
                d = data[key]
                unit = "美元/桶" if key in ['CL=F', 'BZ=F'] else "美元/盎司" if key == 'GC=F' else "美元/盎司" if key == 'SI=F' else "美元/磅" if key == 'HG=F' else "美元/百万英热"
                lines.append(f"| {d['name']} | {d['price']:.2f}{unit} | {d['change_pct']:+.2f}% | {d['return_5d']:+.2f}% | {d['return_30d']:+.2f}% |")
        lines.append("")
        
        # 汇率
        lines.append("### 汇率与美元")
        lines.append("")
        lines.append("| 品种 | 最新价 | 涨跌幅 | 近30天 |")
        lines.append("|------|--------|--------|--------|")
        for key in ['DX-Y.NYB', 'CNY=X', 'CNH=F', 'EURUSD=X', 'USDJPY=X', 'GBPUSD=X']:
            if key in data:
                d = data[key]
                note = f" ({d.get('note', '')})" if d.get('note') else ''
                lines.append(f"| {d['name']} | {d['price']:.4f} | {d['change_pct']:+.2f}% | {d['return_30d']:+.2f}%{note} |")
        lines.append("")
        
        # 港股
        lines.append("### 香港市场")
        lines.append("")
        for key in ['^HSI', '^HSTECH']:
            if key in data:
                h = data[key]
                lines.append(f"**{h['name']}**: {h['price']:,.2f} ({h['change_pct']:+.2f}%), 近30天 {h['return_30d']:+.2f}%  ")
        lines.append("")
        lines.append("---")
        
        return "\n".join(lines)
    
    def generate_market_analysis(self) -> str:
        """生成国际市场对A股的影响分析"""
        data = self.fetch_all(days=30)
        if not data:
            return ""
        
        insights = []
        
        # 美股影响
        for key, name in [('^DJI', '道指'), ('^GSPC', '标普'), ('^IXIC', '纳指'), ('^RUT', '罗素2000')]:
            if key in data:
                change = data[key]['change_pct']
                emoji = "📈" if change > 0 else "📉"
                insights.append(f"{emoji} **{name}**: {data[key]['price']:,.2f} ({change:+.2f}%)")
        
        # VIX
        if '^VIX' in data:
            vix = data['^VIX']
            vix_note = "⚠️ 市场恐慌情绪较高" if vix['price'] > 20 else "✅ 市场情绪稳定"
            insights.append(f"{vix_note} (VIX={vix['price']:.2f})")
        
        # 商品 - 对稀土永磁板块有影响
        commodity_notes = []
        if 'HG=F' in data:
            cu = data['HG=F']
            commodity_notes.append(f"铜价{cu['change_pct']:+.2f}%")
        if 'GC=F' in data:
            au = data['GC=F']
            commodity_notes.append(f"金价{au['change_pct']:+.2f}%")
        if 'CL=F' in data:
            oil = data['CL=F']
            commodity_notes.append(f"原油{oil['change_pct']:+.2f}%")
        if 'SI=F' in data:
            ag = data['SI=F']
            commodity_notes.append(f"银价{ag['change_pct']:+.2f}%")
        if commodity_notes:
            insights.append(f"🛢️ 商品: {' | '.join(commodity_notes)}")
        
        # 美元/人民币 - 对港股和外贸板块影响
        if 'CNY=X' in data:
            cny = data['CNY=X']
            cny_note = "人民币升值" if cny['change_pct'] < 0 else "人民币贬值"
            insights.append(f"💱 {cny_note} (USD/CNY={cny['price']:.4f})")
        
        if 'DX-Y.NYB' in data:
            dxy = data['DX-Y.NYB']
            insights.append(f"💵 美元指数: {dxy['price']:.2f} ({dxy['change_pct']:+.2f}%)")
        
        # 港股
        if '^HSI' in data:
            hsi = data['^HSI']
            hsi_change = hsi['change_pct']
            hsi_30 = hsi['return_30d']
            hsi_emoji = "📈" if hsi_change > 0 else "📉"
            insights.append(f"{hsi_emoji} **恒生指数**: {hsi['price']:,.2f} ({hsi_change:+.2f}%), 近30天{hsi_30:+.2f}%")
        
        # 综合判断
        if '^VIX' in data and data['^VIX']['price'] > 20:
            risk_note = "⚠️ **国际市场风险提示**: VIX高于20，建议控制仓位，关注避险资产。"
        elif '^VIX' in data and data['^VIX']['price'] < 15:
            risk_note = "✅ **国际市场环境**: 风险偏好良好，有利于A股开盘情绪。"
        else:
            risk_note = "📊 **国际市场环境**: 市场情绪中性，A股开盘需关注结构性机会。"
        
        insights.append(risk_note)
        
        return "\n".join([f"- {line}" for line in insights])


# ============ 测试 ============
if __name__ == "__main__":
    print("=" * 60)
    print("国际市场数据测试")
    print("=" * 60)
    
    fetcher = InternationalMarketFetcher()
    
    print("\n--- 国际市场表格 ---")
    print(fetcher.generate_market_summary())
    
    print("\n--- 国际环境分析 ---")
    print(fetcher.generate_market_analysis())
    
    print("\n测试完成!")
