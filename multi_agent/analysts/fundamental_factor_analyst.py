#!/usr/bin/env python3
"""基本面质量/风险因子分析：Piotroski F-Score、Altman Z-Score、Beneish M-Score。
支持美股（东方财富）与 A 股（东方财富）。
"""
from __future__ import annotations
import warnings
import pandas as pd
import numpy as np

warnings.filterwarnings("ignore")


_US_REPORT_MAP = {
    # 资产负债表项目
    "total_assets": ["资产总计", "资产合计", "Total Assets"],
    "total_liabilities": ["负债合计", "总负债", "Total Liabilities"],
    "current_assets": ["流动资产合计", "流动资产总计", "Total Current Assets"],
    "current_liabilities": ["流动负债合计", "流动负债总计", "Total Current Liabilities"],
    "working_capital": ["营运资金", "营运资本", "Working Capital"],
    "retained_earnings": ["留存收益", "未分配利润", "Retained Earnings"],
    "short_term_debt": ["短期借款", "Short-Term Borrowings"],
    "long_term_debt": ["长期借款", "Long-Term Borrowings"],
    "total_debt": ["负债合计", "Total Debt"],
    "common_stock": ["普通股", "股本", "Common Stock"],
    "shares_outstanding": ["普通股数", "总股本", "Shares Outstanding"],
    # 利润表项目
    "revenue": ["营业总收入", "营业收入", "总收入", "Total Revenue", "Revenue"],
    "gross_profit": ["毛利", "毛利润", "Gross Profit"],
    "operating_income": ["营业利润", "Operating Income", "Operating Profit"],
    "ebit": ["息税前利润", "EBIT", "Earnings Before Interest and Taxes"],
    "net_income": ["净利润", "归属于母公司股东的净利润", "Net Income", "Net Profit"],
    "operating_expense": ["营业费用", "销售费用", "管理费用", "Operating Expenses", "Selling, General and Administrative"],
    "depreciation": ["折旧", "折旧费用", "Depreciation", "Depreciation and Amortization"],
    # 现金流量表项目
    "operating_cash_flow": ["经营活动产生的现金流量净额", "经营活动现金流量净额", "Net Cash from Operating Activities", "Operating Cash Flow"],
    "capex": ["购建固定资产、无形资产和其他长期资产支付的现金", "资本支出", "Capital Expenditure", "Capital Expenditures"],
}


_US_ANALYSIS_FIELDS = {
    "total_assets": ["TOTAL_ASSETS"],
    "current_assets": ["CURRENT_ASSETS"],
    "current_liabilities": ["CURRENT_LIABILITIES"],
    "total_liabilities": ["TOTAL_LIABILITIES"],
    "total_equity": ["TOTAL_EQUITY", "EQUITY"],
    "revenue": ["OPERATE_INCOME"],
    "gross_profit": ["GROSS_PROFIT"],
    "operating_income": ["OPERATE_PROFIT"],
    "net_income": ["PARENT_HOLDER_NETPROFIT"],
    "operating_cash_flow": ["TOTAL_OPERATE_CASH_FLOW", "OPERATE_CASH_FLOW", "CASH_FROM_OPERATIONS"],
    "retained_earnings": ["RETAINED_EARNINGS", "SURPLUS_RESERVE", "UNDISTRIBUTED_PROFIT"],
    "shares_outstanding": ["SHARES_OUTSTANDING", "TOTAL_SHARES"],
    "market_cap": ["TOTAL_MARKET_CAP"],
}


def _get_us_financial_report(stock: str) -> pd.DataFrame | None:
    try:
        import akshare as ak
        bs = ak.stock_financial_us_report_em(stock=stock, symbol="资产负债表", indicator="年报")
        inc = ak.stock_financial_us_report_em(stock=stock, symbol="综合损益表", indicator="年报")
        cf = ak.stock_financial_us_report_em(stock=stock, symbol="现金流量表", indicator="年报")
    except Exception as e:
        return None
    if bs is None or inc is None or cf is None:
        return None
    for df in (bs, inc, cf):
        df["REPORT_DATE"] = pd.to_datetime(df["REPORT_DATE"])
    merged = pd.concat([bs, inc, cf], ignore_index=True)
    return merged.sort_values(["REPORT_DATE", "ITEM_NAME"]).reset_index(drop=True)


def _get_us_analysis_indicator(stock: str) -> pd.DataFrame | None:
    try:
        import akshare as ak
        df = ak.stock_financial_us_analysis_indicator_em(symbol=stock, indicator="年报")
    except Exception:
        return None
    if df is None or df.empty:
        return None
    df = df.copy()
    df["REPORT_DATE"] = pd.to_datetime(df["REPORT_DATE"])
    df = df.sort_values("REPORT_DATE", ascending=False).reset_index(drop=True)
    return df


def _get_a_analysis_indicator(stock: str) -> pd.DataFrame | None:
    try:
        import akshare as ak
        df = ak.stock_financial_analysis_indicator_em(symbol=stock, indicator="年报")
    except Exception:
        return None
    if df is None or df.empty:
        return None
    df = df.copy()
    df["REPORT_DATE"] = pd.to_datetime(df["REPORT_DATE"])
    df = df.sort_values("REPORT_DATE", ascending=False).reset_index(drop=True)
    return df


def _extract_from_analysis(df: pd.DataFrame | None, key: str, row_index: int = 0):
    """从分析指标宽表中按字段别名提取值。"""
    if df is None or df.empty:
        return None
    for col in _US_ANALYSIS_FIELDS.get(key, [key]):
        if col in df.columns:
            val = df.iloc[row_index].get(col)
            if pd.notna(val) and val != 0:
                return float(val)
    return None


def _extract_from_report(df: pd.DataFrame | None, key: str) -> dict | None:
    """从长表报表中按项目名提取最近两年的值。"""
    if df is None or df.empty:
        return None
    candidates = _US_REPORT_MAP.get(key, [key])
    # 取包含候选名的项目，按日期分组取最新
    mask = df["ITEM_NAME"].apply(lambda x: any(c in str(x) for c in candidates))
    sub = df[mask].copy()
    if sub.empty:
        return None
    sub = sub.sort_values("REPORT_DATE", ascending=False)
    latest_date = sub["REPORT_DATE"].max()
    prev_date = sub[sub["REPORT_DATE"] < latest_date]["REPORT_DATE"].max()
    latest = sub[sub["REPORT_DATE"] == latest_date]["AMOUNT"].values
    prev = sub[sub["REPORT_DATE"] == prev_date]["AMOUNT"].values if pd.notna(prev_date) else []
    return {
        "latest": float(latest[0]) if len(latest) else None,
        "prev": float(prev[0]) if len(prev) else None,
    }


def _val(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return 0.0
    return float(v)


def _get_field(df_report, df_analysis, key: str):
    """优先从分析指标表取，缺失则从长表报表取。"""
    v = _extract_from_analysis(df_analysis, key)
    if v is not None and v != 0:
        return v
    d = _extract_from_report(df_report, key)
    if d and d.get("latest") is not None:
        return d["latest"]
    return None


def _get_prev_field(df_report, df_analysis, key: str):
    """获取上一年的值。"""
    d = _extract_from_report(df_report, key)
    if d and d.get("prev") is not None:
        return d["prev"]
    return None


def compute_piotroski(df_report: pd.DataFrame | None, df_analysis: pd.DataFrame | None) -> dict:
    """计算 Piotroski F-Score (0-9)。"""
    score = 0
    details = []
    
    # 1. ROA > 0
    roa = _extract_from_analysis(df_analysis, "ROA", row_index=0)
    if roa is None:
        # 尝试计算
        net_income = _get_field(df_report, df_analysis, "net_income")
        total_assets = _get_field(df_report, df_analysis, "total_assets")
        if net_income and total_assets:
            roa = net_income / total_assets
    if roa and roa > 0:
        score += 1
        details.append("ROA > 0")
    
    # 2. 经营现金流 > 0
    ocf = _get_field(df_report, df_analysis, "operating_cash_flow")
    if ocf and ocf > 0:
        score += 1
        details.append("经营现金流 > 0")
    
    # 3. ROA 改善
    roa_prev = _extract_from_analysis(df_analysis, "ROA", row_index=1) if df_analysis is not None and len(df_analysis) > 1 else None
    if roa is not None and roa_prev is not None and roa > roa_prev:
        score += 1
        details.append("ROA 改善")
    
    # 4. 经营现金流 > 净利润
    net_income = _get_field(df_report, df_analysis, "net_income")
    if ocf and net_income and ocf > net_income:
        score += 1
        details.append("经营现金流 > 净利润")
    
    # 5. 杠杆下降（长期负债/总资产下降）
    total_liabilities = _get_field(df_report, df_analysis, "total_liabilities")
    total_liabilities_prev = _get_prev_field(df_report, df_analysis, "total_liabilities")
    total_assets = _get_field(df_report, df_analysis, "total_assets")
    total_assets_prev = _get_prev_field(df_report, df_analysis, "total_assets")
    if total_liabilities and total_assets and total_liabilities_prev and total_assets_prev:
        lev_now = total_liabilities / total_assets
        lev_prev = total_liabilities_prev / total_assets_prev
        if lev_now < lev_prev:
            score += 1
            details.append("杠杆率下降")
    
    # 6. 流动比率改善
    current_ratio = _extract_from_analysis(df_analysis, "CURRENT_RATIO", row_index=0)
    current_ratio_prev = _extract_from_analysis(df_analysis, "CURRENT_RATIO", row_index=1) if df_analysis is not None and len(df_analysis) > 1 else None
    if current_ratio is None:
        ca = _get_field(df_report, df_analysis, "current_assets")
        cl = _get_field(df_report, df_analysis, "current_liabilities")
        if ca and cl:
            current_ratio = ca / cl
    if current_ratio is None:
        current_ratio_prev = None
    if current_ratio is not None and current_ratio_prev is not None and current_ratio > current_ratio_prev:
        score += 1
        details.append("流动比率改善")
    
    # 7. 未增发股份（股数未增加）
    shares = _get_field(df_report, df_analysis, "shares_outstanding")
    shares_prev = _get_prev_field(df_report, df_analysis, "shares_outstanding")
    if shares and shares_prev and shares <= shares_prev:
        score += 1
        details.append("未增发股份")
    
    # 8. 毛利率改善
    gross_margin = _extract_from_analysis(df_analysis, "GROSS_PROFIT_RATIO", row_index=0)
    gross_margin_prev = _extract_from_analysis(df_analysis, "GROSS_PROFIT_RATIO", row_index=1) if df_analysis is not None and len(df_analysis) > 1 else None
    if gross_margin is None:
        gp = _get_field(df_report, df_analysis, "gross_profit")
        rev = _get_field(df_report, df_analysis, "revenue")
        gp_prev = _get_prev_field(df_report, df_analysis, "gross_profit")
        rev_prev = _get_prev_field(df_report, df_analysis, "revenue")
        if gp and rev:
            gross_margin = gp / rev
        if gp_prev and rev_prev:
            gross_margin_prev = gp_prev / rev_prev
    if gross_margin is not None and gross_margin_prev is not None and gross_margin > gross_margin_prev:
        score += 1
        details.append("毛利率改善")
    
    # 9. 资产周转率改善
    total_assets = _get_field(df_report, df_analysis, "total_assets")
    total_assets_prev = _get_prev_field(df_report, df_analysis, "total_assets")
    revenue = _get_field(df_report, df_analysis, "revenue")
    revenue_prev = _get_prev_field(df_report, df_analysis, "revenue")
    if revenue and total_assets and revenue_prev and total_assets_prev:
        turnover_now = revenue / total_assets
        turnover_prev = revenue_prev / total_assets_prev
        if turnover_now > turnover_prev:
            score += 1
            details.append("资产周转率改善")
    
    return {
        "score": score,
        "max": 9,
        "details": details,
        "signals": "高质量价值股" if score >= 7 else "普通" if score >= 4 else "财务质量偏弱",
    }


def compute_altman(df_report: pd.DataFrame | None, df_analysis: pd.DataFrame | None, ticker: str = "") -> dict:
    """计算 Altman Z-Score。制造业/非金融适用。"""
    wc = _get_field(df_report, df_analysis, "working_capital")
    if wc is None:
        ca = _get_field(df_report, df_analysis, "current_assets")
        cl = _get_field(df_report, df_analysis, "current_liabilities")
        if ca is not None and cl is not None:
            wc = ca - cl
    re = _get_field(df_report, df_analysis, "retained_earnings")
    ebit = _get_field(df_report, df_analysis, "operating_income")
    if ebit is None:
        ebit = _get_field(df_report, df_analysis, "ebit")
    total_assets = _get_field(df_report, df_analysis, "total_assets")
    total_liabilities = _get_field(df_report, df_analysis, "total_liabilities")
    revenue = _get_field(df_report, df_analysis, "revenue")
    market_cap = _get_field(df_report, df_analysis, "market_cap")
    
    if not all([wc, re, ebit, total_assets, total_liabilities, revenue]):
        return {"score": None, "zone": "数据不足", "missing": True}
    
    # 非制造业 Z-Score (Z'')
    z = (
        6.56 * (wc / total_assets)
        + 3.26 * (re / total_assets)
        + 6.72 * (ebit / total_assets)
        + 1.05 * (revenue / total_assets)
    )
    if market_cap and total_liabilities:
        # 原始 Z-Score
        z = (
            1.2 * (wc / total_assets)
            + 1.4 * (re / total_assets)
            + 3.3 * (ebit / total_assets)
            + 0.6 * (market_cap / total_liabilities)
            + 1.0 * (revenue / total_assets)
        )
    
    zone = "安全" if z > 2.99 else "灰色" if z > 1.81 else " distress"
    return {"score": round(z, 2), "zone": zone, "missing": False}


def compute_beneish(df_report: pd.DataFrame | None, df_analysis: pd.DataFrame | None) -> dict:
    """计算 Beneish M-Score，提示盈余操纵风险。M-Score > -1.78 需警惕。"""
    # 获取连续两年的数据
    rev_now = _get_field(df_report, df_analysis, "revenue")
    rev_prev = _get_prev_field(df_report, df_analysis, "revenue")
    gp_now = _get_field(df_report, df_analysis, "gross_profit")
    gp_prev = _get_prev_field(df_report, df_analysis, "gross_profit")
    opexp_now = _get_field(df_report, df_analysis, "operating_expense")
    opexp_prev = _get_prev_field(df_report, df_analysis, "operating_expense")
    ta_now = _get_field(df_report, df_analysis, "total_assets")
    ta_prev = _get_prev_field(df_report, df_analysis, "total_assets")
    depr_now = _get_field(df_report, df_analysis, "depreciation")
    depr_prev = _get_prev_field(df_report, df_analysis, "depreciation")
    tl_now = _get_field(df_report, df_analysis, "total_liabilities")
    tl_prev = _get_prev_field(df_report, df_analysis, "total_liabilities")
    ar_now = _get_field(df_report, df_analysis, "accounts_receivable")
    ar_prev = _get_prev_field(df_report, df_analysis, "accounts_receivable")
    
    if not all([rev_now, rev_prev, gp_now, gp_prev, ta_now, ta_prev, tl_now, tl_prev]):
        return {"score": None, "flag": "数据不足", "missing": True}
    
    # DSRI: 应收账款周转率指数（应收/营收 比例变化）
    dsri = ((ar_now / rev_now) / (ar_prev / rev_prev)) if ar_prev and ar_prev > 0 and rev_prev > 0 else 1.0
    # GMI: 毛利率指数
    gmi = ((gp_prev / rev_prev) / (gp_now / rev_now)) if gp_now and rev_now > 0 and rev_prev > 0 else 1.0
    # AQI: 资产质量指数
    aqi = ((1 - (current_assets_now + ppe_now) / ta_now) / (1 - (current_assets_prev + ppe_prev) / ta_prev)) if False else 1.0
    # 简化 AQI：非流动资产占比变化
    current_assets_now = _get_field(df_report, df_analysis, "current_assets")
    current_assets_prev = _get_prev_field(df_report, df_analysis, "current_assets")
    ppe_now = _get_field(df_report, df_analysis, "property_plant_equipment")
    ppe_prev = _get_prev_field(df_report, df_analysis, "property_plant_equipment")
    if current_assets_now and ppe_now and current_assets_prev and ppe_prev:
        aqi = ((1 - (current_assets_now + ppe_now) / ta_now) / (1 - (current_assets_prev + ppe_prev) / ta_prev))
    else:
        aqi = 1.0
    # SGI: 销售增长指数
    sgi = rev_now / rev_prev if rev_prev > 0 else 1.0
    # DEPI: 折旧指数
    depi = ((depr_prev / ta_prev) / (depr_now / ta_now)) if depr_prev and depr_now and depr_now > 0 and ta_prev > 0 else 1.0
    # SGAI: 销售管理费用指数
    sgai = ((opexp_now / rev_now) / (opexp_prev / rev_prev)) if opexp_now and opexp_prev and opexp_prev > 0 and rev_prev > 0 else 1.0
    # LVGI: 杠杆指数
    lvgi = ((tl_now / ta_now) / (tl_prev / ta_prev)) if tl_prev and ta_prev > 0 else 1.0
    # TATA: 总应计/总资产 (净利润 - 经营现金流) / 总资产
    ni_now = _get_field(df_report, df_analysis, "net_income")
    ocf_now = _get_field(df_report, df_analysis, "operating_cash_flow")
    tata = ((ni_now - ocf_now) / ta_now) if ni_now and ocf_now and ta_now > 0 else 0.0
    
    m = (
        -4.84
        + 0.920 * dsri
        + 0.528 * gmi
        + 0.404 * aqi
        + 0.892 * sgi
        + 0.115 * depi
        - 0.172 * sgai
        - 0.327 * lvgi
        + 4.679 * tata
        - 0.327 * depr_prev  # 0.327 * DEPI not defined here, use 0
    )
    # correct formula: -4.84 + 0.920*DSRI + 0.528*GMI + 0.404*AQI + 0.892*SGI + 0.115*DEPI - 0.172*SGAI - 0.327*LVGI + 4.679*TATA
    m = (
        -4.84
        + 0.920 * dsri
        + 0.528 * gmi
        + 0.404 * aqi
        + 0.892 * sgi
        + 0.115 * depi
        - 0.172 * sgai
        - 0.327 * lvgi
        + 4.679 * tata
    )
    flag = "操纵风险高" if m > -1.78 else "需关注" if m > -2.22 else "正常"
    return {
        "score": round(m, 2),
        "flag": flag,
        "missing": False,
        "components": {
            "dsri": round(dsri, 3),
            "gmi": round(gmi, 3),
            "aqi": round(aqi, 3),
            "sgi": round(sgi, 3),
            "depi": round(depi, 3),
            "sgai": round(sgai, 3),
            "lvgi": round(lvgi, 3),
            "tata": round(tata, 3),
        },
    }


def analyze_fundamental_factors(ticker: str, name: str = "", category: str = "US") -> dict:
    """统一入口：分析基本面质量与风险。"""
    if category == "US":
        df_report = _get_us_financial_report(ticker)
        df_analysis = _get_us_analysis_indicator(ticker)
    else:
        # A 股：使用分析指标表，报表暂不接入
        df_report = None
        df_analysis = _get_a_analysis_indicator(ticker)
    
    piotroski = compute_piotroski(df_report, df_analysis)
    altman = compute_altman(df_report, df_analysis, ticker)
    beneish = compute_beneish(df_report, df_analysis)
    
    return {
        "ticker": ticker,
        "name": name,
        "category": category,
        "piotroski_f_score": piotroski,
        "altman_z_score": altman,
        "beneish_m_score": beneish,
    }


if __name__ == "__main__":
    import json
    res = analyze_fundamental_factors("TSLA", category="US")
    print(json.dumps(res, ensure_ascii=False, indent=2))
