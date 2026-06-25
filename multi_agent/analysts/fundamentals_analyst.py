"""
基本面分析师 - 财务数据分析
对应 TradingAgents-CN 的 fundamentals_analyst
使用 Tushare + akshare 作为 A 股基本面数据源，避免 yfinance 限流和不准
"""
import sys
import os

# 自动定位项目根目录，避免硬编码路径
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'etf_tracker'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'etf_tracker', 'multi_agent'))

import json
import warnings
import time
import pandas as pd

warnings.filterwarnings('ignore')


def _safe_float(val, default=0.0):
    """安全转 float"""
    if pd.isna(val) or val is None or val == '':
        return default
    try:
        return float(val)
    except Exception:
        return default


def _ts_code(ticker):
    """ticker 转 tushare 的 ts_code"""
    if ticker.startswith(('6', '5')):
        return f'{ticker}.SH'
    return f'{ticker}.SZ'


def _get_em_fundamentals(ticker):
    """使用腾讯财经接口获取 A 股基本面数据（PE/PB/股息率/总市值/行业）
    腾讯接口稳定、不限频，字段格式固定
    """
    import requests

    secid = 'sh' + ticker if ticker.startswith(('6', '5', '68', '88')) else 'sz' + ticker
    url = f'http://qt.gtimg.cn/q={secid}'
    try:
        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
        resp.encoding = 'gb2312'
        text = resp.text
        if not text or 'v_' not in text:
            return None

        # 提取引号内的值
        start = text.find('"')
        end = text.find('"', start + 1)
        if start == -1 or end == -1:
            return None
        parts = text[start + 1:end].split('~')
        if len(parts) < 58:
            return None

        def idx(i, default='0'):
            return parts[i] if i < len(parts) and parts[i] else default

        # 腾讯字段索引（常见映射，基于实际返回校准）
        # 44=流通市值, 45=总市值, 46=市净率, 52=市盈率TTM, 53=市盈率(静), 56=股息率
        close = _safe_float(idx(3))
        pe_ttm = _safe_float(idx(52))
        pe_static = _safe_float(idx(53))
        # 市净率：46 或 57，取非零且合理的那个（剔除异常高值）
        pb_46 = _safe_float(idx(46))
        pb_57 = _safe_float(idx(57))
        pb = pb_46 if 0 < pb_46 < 50 else pb_57
        total_mv = _safe_float(idx(45))  # 单位：亿元
        div = _safe_float(idx(56))  # 股息率%

        # 如果 TTM 为 0，用静态 PE
        pe = pe_ttm if pe_ttm > 0 else pe_static

        return {
            'market_cap': round(total_mv, 2) if total_mv else 0,
            'pe_ratio': round(pe, 2) if pe else 0,
            'forward_pe': round(pe_static, 2) if pe_static else 0,
            'pb_ratio': round(pb, 2) if pb else 0,
            'peg_ratio': 0.0,
            'dividend_yield': round(div, 2) if div else 0,
            'payout_ratio': 0.0,
            'eps': 0.0,
            'revenue': 0.0,
            'revenue_growth': 0.0,
            'gross_margins': 0.0,
            'profit_margins': 0.0,
            'operating_margins': 0.0,
            'roe': 0.0,
            'roa': 0.0,
            'debt_to_equity': 0.0,
            'current_ratio': 0.0,
            'free_cash_flow': 0.0,
            'sector': 'A股',
            'industry': '',
            'full_time_employees': 0,
            'beta': 0.0,
            'fifty_two_week_high': 0.0,
            'fifty_two_week_low': 0.0,
            'close': round(close, 2) if close else 0,
        }
    except Exception as e:
        return None


def _em_secid(ticker):
    """ticker 转东方财富 secid（保留备用）"""
    if ticker.startswith('6'):
        return f'1.{ticker}'
    if ticker.startswith(('0', '3')):
        return f'0.{ticker}'
    if ticker.startswith('688'):
        return f'1.{ticker}'
    if ticker.startswith(('8', '4')):
        return f'0.{ticker}'
    return f'0.{ticker}'


def analyze(ticker, name="", current_date="2026-07-02"):
    """
    基本面分析 - 使用腾讯财经 A 股数据
    """
    fundamentals = _get_em_fundamentals(ticker)

    if fundamentals is None:
        return {
            'analyst': '基本面分析师',
            'ticker': ticker,
            'name': name,
            'error': '腾讯财经基本面数据获取失败',
            'summary': f"# 基本面分析报告\n\n## 数据获取失败\n腾讯财经无法获取 {ticker} 基本面数据\n",
            'fundamentals': {},
        }

    # EPS 估算
    if fundamentals['pe_ratio'] and fundamentals['pe_ratio'] > 0 and fundamentals.get('close'):
        fundamentals['eps'] = round(fundamentals['close'] / fundamentals['pe_ratio'], 2)
    fundamentals.pop('close', None)

    # ========== 基本面评分 ==========
    score = 50
    reasons = []
    
    pe = fundamentals['pe_ratio']
    pb = fundamentals['pb_ratio']
    revenue_growth = fundamentals['revenue_growth']
    profit_margins = fundamentals['profit_margins']
    de = fundamentals['debt_to_equity']
    
    if pe and 0 < pe < 15:
        score += 12
        reasons.append(f"PE({pe})较低，估值偏低")
    elif pe and 15 <= pe < 30:
        score += 8
        reasons.append(f"PE({pe})合理")
    elif pe and pe > 50:
        score -= 5
        reasons.append(f"PE({pe})偏高")
    
    if pb and pb < 1.5:
        score += 8
        reasons.append(f"PB({pb})较低，有安全边际")
    elif pb and 1.5 <= pb <= 5:
        score += 4
        reasons.append(f"PB({pb})合理")
    elif pb and pb > 5:
        score -= 3
        reasons.append(f"PB({pb})偏高")
    
    if revenue_growth and revenue_growth > 10:
        score += 10
        reasons.append(f"营收增长{revenue_growth:+.1f}%，成长性良好")
    elif revenue_growth and 0 <= revenue_growth <= 10:
        score += 6
        reasons.append(f"营收维持正增长{revenue_growth:+.1f}%")
    elif revenue_growth and revenue_growth < 0:
        score -= 5
        reasons.append(f"营收负增长{revenue_growth:.1f}%")
    else:
        score += 3
        reasons.append(f"营收增长数据不可用，按平稳处理")
    
    if profit_margins and profit_margins > 15:
        score += 8
        reasons.append(f"净利率{profit_margins:.1f}%较高")
    elif profit_margins and 5 <= profit_margins <= 15:
        score += 3
        reasons.append(f"净利率{profit_margins:.1f}%适中")
    elif profit_margins and profit_margins < 5:
        score -= 3
        reasons.append(f"净利率{profit_margins:.1f}%偏低，盈利能力较弱")
    
    if de and de > 150:
        score -= 8
        reasons.append(f"负债权益比{de:.0f}%过高，财务风险大")
    elif de and de > 100:
        score -= 3
        reasons.append(f"负债权益比{de:.0f}%偏高")
    elif de and de < 30:
        score += 5
        reasons.append(f"负债权益比{de:.0f}%较低，财务稳健")
    
    score = max(0, min(100, score))
    
    if score >= 75:
        rating = "优秀"
    elif score >= 60:
        rating = "良好"
    elif score >= 40:
        rating = "一般"
    elif score >= 25:
        rating = "较差"
    else:
        rating = "差"
    
    return {
        'analyst': '基本面分析师',
        'ticker': ticker,
        'name': name,
        'score': score,
        'rating': rating,
        'fundamentals': fundamentals,
        'reasons': reasons,
        'summary': _generate_summary(name, fundamentals, score, rating, reasons),
    }


def _generate_summary(name, f, score, rating, reasons):
    lines = []
    lines.append(f"# 基本面分析报告")
    lines.append(f"")
    lines.append(f"## 综合评级：{rating}（{score}/100）")
    lines.append(f"")
    
    if f.get('industry'):
        lines.append(f"**所属行业**: {f.get('industry', '')}")
        lines.append(f"")
    
    lines.append(f"### 估值指标")
    lines.append(f"| 指标 | 数值 | 说明 |")
    lines.append(f"|------|------|------|")
    lines.append(f"| PE (TTM) | {f['pe_ratio']:.2f} | {'偏低' if 0 < f['pe_ratio'] < 15 else '合理' if f['pe_ratio'] < 30 else '偏高'} |")
    lines.append(f"| PE (静态) | {f['forward_pe']:.2f} | |")
    lines.append(f"| PB | {f['pb_ratio']:.2f} | {'偏低' if f['pb_ratio'] < 1.5 else '偏高' if f['pb_ratio'] > 5 else '合理'} |")
    lines.append(f"| PEG | {f['peg_ratio']:.2f} | {'<1 低估' if 0 < f['peg_ratio'] < 1 else '>1 估值合理' if f['peg_ratio'] > 1 else 'N/A'} |")
    lines.append(f"| 股息率 | {f['dividend_yield']:.2f}% | |")
    lines.append(f"| 总市值 | {f['market_cap']:.1f}亿 | |")
    lines.append(f"")
    
    lines.append(f"### 盈利能力")
    lines.append(f"| 指标 | 数值 |")
    lines.append(f"|------|------|")
    lines.append(f"| EPS | {f['eps']:.2f} |")
    lines.append(f"| 营收 | {f['revenue']:.1f}亿 |")
    lines.append(f"| 营收增长 | {f['revenue_growth']:+.2f}% |")
    lines.append(f"| 毛利率 | {f['gross_margins']:.1f}% |")
    lines.append(f"| 净利率 | {f['profit_margins']:.1f}% |")
    lines.append(f"| ROE | {f['roe']:.1f}% |")
    lines.append(f"| ROA | {f['roa']:.1f}% |")
    lines.append(f"")
    
    lines.append(f"### 财务健康")
    lines.append(f"| 指标 | 数值 |")
    lines.append(f"|------|------|")
    lines.append(f"| 负债权益比 | {f['debt_to_equity']:.1f}% |")
    lines.append(f"| 流动比率 | {f['current_ratio']:.2f} |")
    lines.append(f"| 自由现金流 | {f['free_cash_flow']:.1f}亿 |")
    lines.append(f"| Beta | {f['beta']:.2f} |")
    lines.append(f"")
    
    if reasons:
        lines.append(f"### 核心观点")
        for r in reasons:
            lines.append(f"- {r}")
        lines.append(f"")
    
    lines.append(f"### 52周区间")
    lines.append(f"最高: {f['fifty_two_week_high']:.2f}  最低: {f['fifty_two_week_low']:.2f}")
    lines.append(f"")
    
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "601991"
    name = sys.argv[2] if len(sys.argv) > 2 else ""
    result = analyze(ticker, name)
    print(result['summary'])
