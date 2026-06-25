"""
老系统融合入口 - 在新框架中调用老系统有价值的功能

融合内容：
1. config.json → watchlist（已完成）
2. llm_report_generator → 新框架的辩论报告增强
3. international_market → 新增海外市场分析
4. market_review → 每日行情回顾
5. data_source_manager → TickFlow数据源（备用）
"""

import sys, os, importlib, json
from datetime import datetime

OLD_DIR = os.path.expanduser('~/daily-_tracker_analytics/etf_tracker')
NEW_DIR = os.path.expanduser('~/daily_tracker_analytics/etf_tracker/multi_agent')


def load_old_module(name):
    """从老系统动态加载模块"""
    if OLD_DIR not in sys.path:
        sys.path.insert(0, OLD_DIR)
    try:
        return importlib.import_module(name)
    except Exception as e:
        return None


# ==================== 市场回顾 ====================

def get_market_review():
    """获取今日A股市场回顾（复用老系统 market_review.py）"""
    mod = load_old_module('market_review')
    if mod:
        try:
            return mod.get_market_overview()
        except:
            pass
    return None


# ==================== 海外市场 ====================

def get_international_markets():
    """获取海外市场行情（复用老系统 international_market.py）"""
    mod = load_old_module('international_market')
    if mod:
        try:
            return mod.get_all_market_data()
        except:
            pass
    return None


# ==================== 研报 ====================

def get_research_reports(ticker):
    """读取缓存的券商研报"""
    reports_dir = os.path.join(OLD_DIR, 'research_reports')
    if not os.path.exists(reports_dir):
        return []
    
    reports = []
    for fname in os.listdir(reports_dir):
        if ticker in fname and fname.endswith('.pdf'):
            reports.append(os.path.join(reports_dir, fname))
    return reports[:5]


# ==================== 整合入口 ====================

def full_market_context():
    """
    获取完整的市场上下文（大盘+海外+行业）
    用于增强日报的宏观部分
    """
    context_parts = []
    
    # A股大盘回顾
    review = get_market_review()
    if review:
        context_parts.append(str(review))
    
    # 海外市场
    intl = get_international_markets()
    if intl:
        context_parts.append(str(intl))
    
    return '\n\n'.join(context_parts)


if __name__ == "__main__":
    print("=== 老系统融合测试 ===")
    print()
    
    review = get_market_review()
    if review:
        print("✅ market_review 可调用")
    else:
        print("❌ market_review 不可用")
    
    intl = get_international_markets()
    if intl:
        print("✅ international_market 可调用")
    else:
        print("❌ international_market 不可用")
    
    reports = get_research_reports('601991')
    print(f"✅ 研报缓存: {len(reports)}篇")
    
    print("\n=== 当前watchlist ===")
    with open(os.path.join(NEW_DIR, 'watchlist.json')) as f:
        wl = json.load(f)
    print(f"共{len(wl)}个标的:")
    for w in wl:
        print(f"  {w['ticker']} {w['name']}")
