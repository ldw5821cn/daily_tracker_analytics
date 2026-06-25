#!/usr/bin/env python3
"""
中国宏观数据模块
获取 GDP、CPI、PPI、M2、社融、PMI、LPR、SHIBOR、汇率、国债收益率等
为 A 股/ETF 分析提供宏观环境判断
"""
import os
import json
import urllib.request
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import warnings
warnings.filterwarnings('ignore')


class MacroDataFetcher:
    """中国宏观数据获取器"""

    def __init__(self):
        self._cache = {}
        self._cache_time = None

    # ========== AkShare 接口 ==========
    def _try_akshare(self, func_name: str, *args, **kwargs):
        """尝试调用 akshare 接口"""
        try:
            import akshare as ak
            func = getattr(ak, func_name)
            return func(*args, **kwargs)
        except Exception as e:
            return None

    def _standardize_date_value_df(self, df,
                                   date_keywords: list = None,
                                   value_keywords: list = None) -> Optional[pd.DataFrame]:
        """把 akshare 返回的宏观 DF 统一成 date/value 列"""
        if df is None or df.empty:
            return None
        df = df.copy()

        # 找日期列
        date_keywords = date_keywords or ['日期', 'date', '月份', 'trade_date', 'TRADE_DATE']
        date_col = None
        for kw in date_keywords:
            matches = [c for c in df.columns if kw.lower() in str(c).lower()]
            if matches:
                date_col = matches[0]
                break
        if date_col is None:
            date_col = df.columns[0]

        # 找数值列（今值/VALUE/value/RATE 等）
        value_keywords = value_keywords or ['今值', 'value', 'VALUE', 'RATE', '利率', 'LPR1Y', 'LPR5Y', 'usd_cny']
        value_col = None
        for kw in value_keywords:
            matches = [c for c in df.columns if kw.lower() in str(c).lower()]
            if matches:
                value_col = matches[0]
                break
        if value_col is None:
            # 默认用第二列
            value_col = df.columns[1] if len(df.columns) > 1 else df.columns[0]

        df = df.rename(columns={date_col: 'date', value_col: 'value'})
        df = df[['date', 'value']]
        df['date'] = pd.to_datetime(df['date'], errors='coerce')
        df['value'] = pd.to_numeric(df['value'], errors='coerce')
        df = df.dropna()
        return df.sort_values('date').reset_index(drop=True)

    def get_cpi(self) -> Optional[pd.DataFrame]:
        """CPI 同比"""
        df = self._try_akshare("macro_china_cpi_yearly")
        # 过滤掉未来 NaN 行，并确保日期列名为 '日期'
        if df is not None and '今值' in df.columns:
            df = df[df['今值'].notna()]
        return self._standardize_date_value_df(df, value_keywords=['今值'])

    def get_ppi(self) -> Optional[pd.DataFrame]:
        """PPI 同比"""
        df = self._try_akshare("macro_china_ppi_yearly")
        if df is not None and '今值' in df.columns:
            df = df[df['今值'].notna()]
        return self._standardize_date_value_df(df, value_keywords=['今值'])

    def get_m2(self) -> Optional[pd.DataFrame]:
        """M2 同比"""
        df = self._try_akshare("macro_china_m2_yearly")
        if df is not None and '今值' in df.columns:
            df = df[df['今值'].notna()]
        return self._standardize_date_value_df(df, value_keywords=['今值'])

    def get_pmi(self) -> Optional[pd.DataFrame]:
        """官方制造业 PMI"""
        df = self._try_akshare("macro_china_pmi_yearly")
        if df is not None and '今值' in df.columns:
            df = df[df['今值'].notna()]
        return self._standardize_date_value_df(df, value_keywords=['今值'])

    def get_gdp(self) -> Optional[pd.DataFrame]:
        """GDP 季度同比"""
        df = self._try_akshare("macro_china_gdp_yearly")
        if df is not None and '今值' in df.columns:
            df = df[df['今值'].notna()]
        return self._standardize_date_value_df(df, value_keywords=['今值'])

    def get_czf(self) -> Optional[pd.DataFrame]:
        """社会融资规模增量"""
        df = self._try_akshare("macro_china_shrzgm")
        if df is None or df.empty:
            return None
        # 月份 '202604' 转日期
        df = df.copy()
        df = df.rename(columns={df.columns[0]: 'date'})
        df['date'] = pd.to_datetime(df['date'], format='%Y%m', errors='coerce')
        df = df[df['date'].notna()]
        value_col = [c for c in df.columns if '社会融资' in str(c) or '人民币贷款' in str(c)]
        value_col = value_col[0] if value_col else df.columns[1]
        df = df.rename(columns={value_col: 'value'})
        df['value'] = pd.to_numeric(df['value'], errors='coerce')
        return df[['date', 'value']].dropna().sort_values('date')

    def get_lpr(self) -> Optional[pd.DataFrame]:
        """LPR 1年期"""
        df = self._try_akshare("macro_china_lpr")
        return self._standardize_date_value_df(df, value_keywords=['LPR1Y', 'RATE_1', '1Y'])

    def get_shibor(self, tenor: str = '1Y') -> Optional[pd.DataFrame]:
        """SHIBOR 利率（使用东方财富 ak.rate_interbank）"""
        indicator_map = {
            'ON': '隔夜',
            '1W': '1周',
            '1M': '1月',
            '3M': '3月',
            '1Y': '1年',
        }
        if tenor not in indicator_map:
            return None
        df = self._try_akshare("rate_interbank",
                               market="上海银行同业拆借市场",
                               symbol="Shibor人民币",
                               indicator=indicator_map[tenor])
        if df is not None and not df.empty:
            # 列名通常是 ['日期', '利率']
            df = df.copy()
            rename_map = {'日期': 'date', '利率': 'value'}
            for old, new in rename_map.items():
                if old in df.columns:
                    df = df.rename(columns={old: new})
            if 'date' not in df.columns:
                df = df.rename(columns={df.columns[0]: 'date'})
            if 'value' not in df.columns:
                df = df.rename(columns={df.columns[1]: 'value'})
            df['date'] = pd.to_datetime(df['date'], errors='coerce')
            df['value'] = pd.to_numeric(df['value'], errors='coerce')
            return df[['date', 'value']].dropna().sort_values('date')
        return None

    def get_treasury_yield(self) -> Optional[pd.DataFrame]:
        """中国国债收益率（10年期）"""
        df = self._try_akshare("bond_zh_us_rate")
        if df is None or df.empty:
            return None
        df = df.copy()
        date_col = [c for c in df.columns if '日期' in str(c) or 'date' in str(c).lower()][0]
        cn_col = [c for c in df.columns if '中国国债收益率10年' in str(c)]
        cn_col = cn_col[0] if cn_col else None
        if cn_col is None:
            return None
        df = df.rename(columns={date_col: 'date', cn_col: 'value'})
        df['date'] = pd.to_datetime(df['date'], errors='coerce')
        df['value'] = pd.to_numeric(df['value'], errors='coerce')
        return df[['date', 'value']].dropna().sort_values('date')

    def get_rmb_exchange(self) -> Optional[pd.DataFrame]:
        """人民币汇率中间价（美元/人民币）"""
        df = self._try_akshare("currency_boc_safe")
        if df is None or df.empty:
            return None
        df = df.copy()
        # 列名可能是 '美元', 'USD'
        date_col = [c for c in df.columns if '日期' in str(c) or 'date' in str(c).lower()][0]
        usd_col = [c for c in df.columns if '美元' in str(c) or 'USD' in str(c)][0]
        df = df.rename(columns={date_col: 'date', usd_col: 'value'})
        df['date'] = pd.to_datetime(df['date'], errors='coerce')
        df['value'] = pd.to_numeric(df['value'], errors='coerce')
        return df[['date', 'value']].dropna().sort_values('date')

    # ========== 汇总获取 ==========
    def fetch_all(self) -> Dict[str, Dict]:
        """获取主要宏观指标最新值和趋势"""
        indicators = {
            'gdp': {'name': 'GDP同比', 'unit': '%', 'df': self.get_gdp(), 'higher_is': 'positive'},
            'cpi': {'name': 'CPI同比', 'unit': '%', 'df': self.get_cpi(), 'higher_is': 'mixed'},
            'ppi': {'name': 'PPI同比', 'unit': '%', 'df': self.get_ppi(), 'higher_is': 'mixed'},
            'm2': {'name': 'M2同比', 'unit': '%', 'df': self.get_m2(), 'higher_is': 'positive'},
            'pmi': {'name': '制造业PMI', 'unit': '', 'df': self.get_pmi(), 'higher_is': 'positive'},
            'czf': {'name': '社融增量', 'unit': '亿元', 'df': self.get_czf(), 'higher_is': 'positive'},
            'cn_10y': {'name': '中债10Y收益率', 'unit': '%', 'df': self.get_treasury_yield(), 'higher_is': 'mixed'},
            'lpr_1y': {'name': 'LPR 1年期', 'unit': '%', 'df': self.get_lpr(), 'higher_is': 'negative'},
            'shibor_1y': {'name': 'SHIBOR 1年期', 'unit': '%', 'df': self.get_shibor('1Y'), 'higher_is': 'negative'},
            'rmb': {'name': 'USD/CNY中间价', 'unit': '', 'df': self.get_rmb_exchange(), 'higher_is': 'negative'},
        }

        results = {}
        for key, cfg in indicators.items():
            df = cfg.get('df')
            if df is None or df.empty:
                results[key] = {'name': cfg['name'], 'status': 'unavailable'}
                continue
            latest = df.iloc[-1]
            prev = df.iloc[-2] if len(df) >= 2 else latest
            val = latest['value']
            prev_val = prev['value']
            change = val - prev_val if pd.notna(val) and pd.notna(prev_val) else None
            yoy = None
            if len(df) >= 13:
                yoy_val = df.iloc[-13]['value']
                if pd.notna(val) and pd.notna(yoy_val):
                    yoy = round(val - yoy_val, 2)

            results[key] = {
                'name': cfg['name'],
                'value': round(val, 2) if pd.notna(val) else None,
                'prev_value': round(prev_val, 2) if pd.notna(prev_val) else None,
                'change': round(change, 2) if change is not None else None,
                'yoy_change': yoy,
                'date': str(latest['date'].strftime('%Y-%m-%d') if hasattr(latest['date'], 'strftime') else latest['date']),
                'unit': cfg['unit'],
                'higher_is': cfg['higher_is'],
                'status': 'ok',
            }

        return results

    # ========== 生成报告 ==========
    def generate_summary(self) -> str:
        """生成宏观数据摘要 Markdown"""
        data = self.fetch_all()
        lines = []
        lines.append("### 国内宏观指标")
        lines.append("")
        lines.append("| 指标 | 最新值 | 环比变化 | 同比变化 | 日期 | 解读 |")
        lines.append("|------|--------|----------|----------|------|------|")

        for key in ['gdp', 'cpi', 'ppi', 'm2', 'pmi', 'czf', 'cn_10y', 'lpr_1y', 'shibor_1y', 'rmb']:
            d = data.get(key, {})
            if d.get('status') != 'ok':
                continue
            val = d.get('value')
            change = d.get('change')
            yoy = d.get('yoy_change')
            unit = d.get('unit', '')
            if val is None:
                continue
            change_str = f"{change:+.2f}{unit}" if change is not None else '-'
            yoy_str = f"{yoy:+.2f}{unit}" if yoy is not None else '-'
            interpret = self._interpret_indicator(key, d)
            lines.append(f"| {d['name']} | {val}{unit} | {change_str} | {yoy_str} | {d['date']} | {interpret} |")

        lines.append("")
        lines.append(f"**综合判断**: {self._overall_assessment(data)}")
        return "\n".join(lines)

    def _interpret_indicator(self, key: str, d: Dict) -> str:
        val = d.get('value')
        change = d.get('change') or 0
        if val is None:
            return "数据缺失"

        if key == 'pmi':
            if val > 50:
                return "扩张区间"
            elif val > 49:
                return "荣枯线附近"
            else:
                return "收缩区间"
        elif key == 'cpi':
            if val < 1:
                return "通缩压力"
            elif val < 3:
                return "温和通胀"
            else:
                return "通胀偏高"
        elif key == 'ppi':
            if val < 0:
                return "工业品通缩"
            elif val < 3:
                return "价格温和"
            else:
                return "工业品通胀"
        elif key == 'm2':
            if val > 10:
                return "流动性宽松"
            elif val > 8:
                return "流动性适中"
            else:
                return "流动性偏紧"
        elif key in ['lpr_1y', 'shibor_1y']:
            return "利率下行利好" if change < 0 else "利率上行偏紧"
        elif key == 'rmb':
            return "人民币升值利好" if change < 0 else "人民币贬值压力"
        elif key == 'cn_10y':
            if change < -0.05:
                return "债市走强/流动性宽松"
            elif change > 0.05:
                return "债市走弱/预期收紧"
            else:
                return "利率平稳"
        elif key == 'czf':
            return "融资活跃" if change > 0 else "融资收缩"
        return "-"

    def _overall_assessment(self, data: Dict) -> str:
        """根据多个指标给出综合判断"""
        def _val(d):
            return d.get('value') if d and d.get('status') == 'ok' else None

        scores = []
        if _val(data.get('pmi')) is not None and _val(data.get('pmi')) > 50:
            scores.append(1)
        if _val(data.get('m2')) is not None and _val(data.get('m2')) > 9:
            scores.append(1)
        if _val(data.get('cpi')) is not None and 0 < _val(data.get('cpi')) < 3:
            scores.append(1)
        if data.get('lpr_1y', {}).get('change', 0) <= 0:
            scores.append(1)
        if data.get('rmb', {}).get('change', 0) <= 0:
            scores.append(1)

        total = 5
        ratio = len(scores) / total
        if ratio >= 0.7:
            return "宏观环境偏暖，有利于风险资产"
        elif ratio >= 0.4:
            return "宏观环境中性，结构性机会为主"
        else:
            return "宏观环境偏冷，注意控制仓位"

    def generate_context_for_llm(self) -> str:
        """生成给 LLM 的宏观环境文本"""
        data = self.fetch_all()
        lines = ["【国内宏观环境】"]
        for key in ['gdp', 'cpi', 'ppi', 'm2', 'pmi', 'czf', 'cn_10y', 'lpr_1y', 'rmb']:
            d = data.get(key, {})
            if d.get('status') != 'ok':
                continue
            val = d.get('value')
            change = d.get('change')
            unit = d.get('unit', '')
            if val is None:
                continue
            change_str = f"环比{change:+.2f}{unit}" if change is not None else ''
            lines.append(f"- {d['name']}: {val}{unit} ({change_str}) - {self._interpret_indicator(key, d)}")
        lines.append(self._overall_assessment(data))
        return "\n".join(lines)


if __name__ == "__main__":
    fetcher = MacroDataFetcher()
    print("=" * 60)
    print("国内宏观数据测试")
    print("=" * 60)
    print(fetcher.generate_summary())
    print("\n--- LLM 上下文 ---")
    print(fetcher.generate_context_for_llm())
