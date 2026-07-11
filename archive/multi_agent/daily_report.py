"""
每日微信推送报告生成器 — 并行分析 + 微信友好版报告
支持 个股/ETF + 期货 混合分析
"""
import sys
import os
import json
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, '/home/zhihu/daily_tracker_analytics/etf_tracker/multi_agent')

from orchestrator import analyze_stock, print_short_summary
from core.data_layer import get_realtime_price, get_stock_data, calc_technical_indicators
from analysts.strategy_scanner import scan_stock
from core.watchlist import get_stocks_as_tuples, load_list
from core.futures import get_futures_quotes, analyze_futures_trend, get_futures_report, CATEGORIES

AGENTIC_ENABLED = os.getenv('AGENTIC_REPORT', '0').lower() not in ('0', 'false', 'off', 'no')
# 默认仅对重点跟踪标的开启 Agentic LLM 增强，避免 100 只标的 × 6 次 LLM 调用
AGENTIC_WHITELIST = set(
    x.strip() for x in os.getenv('AGENTIC_WHITELIST', '516150,515880').split(',') if x.strip()
)


def _load_high_dividend_portfolio():
    """加载高股息组合配置"""
    default_path = os.path.join(os.path.dirname(__file__), 'high_dividend_portfolio.json')
    if not os.path.exists(default_path):
        return None
    try:
        with open(default_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _render_high_dividend_portfolio(current_date, zone_map=None):
    """渲染高股息组合持仓摘要"""
    if zone_map is None:
        zone_map = {}
    pf = _load_high_dividend_portfolio()
    if not pf:
        return ""

    lines = []
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"💰 **高股息组合** ({pf.get('name', '高股息')})")
    lines.append("")

    total_cost = 0
    total_mv = 0
    annual_div = 0

    for h in pf.get('holdings', []):
        ticker = h['ticker']
        rt = get_realtime_price(ticker)
        price = rt['price'] if rt and rt.get('price') else h.get('cost_price', 0)
        shares = h.get('shares', 0)
        cost_price = h.get('cost_price', price)
        cost = shares * cost_price
        mv = shares * price
        total_cost += cost
        total_mv += mv
        # 使用配置中的目标股息率或自动计算的股息率
        div_rate = h.get('dividend_yield', 0) or _get_dividend_yield(ticker, price)
        annual_div += mv * div_rate

    pnl = total_mv - total_cost
    pnl_pct = (pnl / total_cost * 100) if total_cost else 0
    div_yield_pct = (annual_div / total_mv * 100) if total_mv else 0
    icon = "🟢" if pnl >= 0 else "🔴"

    lines.append(f"成本: {total_cost:.0f} | 市值: {total_mv:.0f} | {icon}盈亏: {pnl:+.0f} ({pnl_pct:+.2f}%)")
    lines.append(f"估算年化股息: {annual_div:.0f}元 (股息率约 {div_yield_pct:.2f}%)")
    lines.append("")
    lines.append("| 标的 | 持仓 | 成本价 | 现价 | 盈亏 | 买入区 | 开仓区 | 卖出区 | 股息率 |")
    lines.append("|------|------|--------|------|------|--------|--------|--------|--------|")

    for h in pf.get('holdings', []):
        ticker = h['ticker']
        name = h['name']
        shares = h.get('shares', 0)
        cost_price = h.get('cost_price', 0)
        rt = get_realtime_price(ticker)
        price = rt['price'] if rt and rt.get('price') else cost_price
        pnl_i = (price - cost_price) * shares
        pnl_i_pct = (price / cost_price - 1) * 100 if cost_price else 0

        zones = zone_map.get(ticker, {})
        div_rate = h.get('dividend_yield', 0) or _get_dividend_yield(ticker, price)
        lines.append(
            f"| {name}({ticker}) | {shares}股 | {cost_price:.2f} | {price:.2f} | "
            f"{pnl_i:+.0f}({pnl_i_pct:+.1f}%) | {zones.get('buy', '-')} | {zones.get('open', '-')} | {zones.get('sell', '-')} | {div_rate*100:.2f}% |"
        )

    lines.append("")
    return "\n".join(lines)


DIVIDEND_YIELD_CACHE = {}

def _get_dividend_yield(ticker, price):
    """根据年度分红估算股息率（缓存当天）"""
    if ticker in DIVIDEND_YIELD_CACHE:
        return DIVIDEND_YIELD_CACHE[ticker]
    try:
        import akshare as ak
        import re

        def parse_div(s):
            if not s: return 0.0
            m = re.search(r'10派([\d.]+)元', str(s))
            return float(m.group(1)) / 10 if m else 0.0

        df = ak.stock_dividend_cninfo(symbol=ticker)
        df['每股分红'] = df['实施方案分红说明'].apply(parse_div)
        annual = df[(df['分红类型'] == '年度分红') & (df['每股分红'].astype(float) > 0)].sort_values('报告时间', ascending=False)
        if not annual.empty:
            div = float(annual.iloc[0]['每股分红'])
            rate = div / price if price else 0
            DIVIDEND_YIELD_CACHE[ticker] = rate
            return rate
    except Exception as e:
        print(f"  ⚠️ 股息率计算失败 {ticker}: {e}")
    DIVIDEND_YIELD_CACHE[ticker] = 0
    return 0


def _analyze_one(args):
    """单个标的分析函数（用于并行）"""
    ticker, name, current_date = args
    try:
        # 白名单内且总开关打开时才启用 Agentic
        use_agentic = AGENTIC_ENABLED and ticker in AGENTIC_WHITELIST
        result = analyze_stock(ticker, name, current_date, output_file=None, agentic=use_agentic)
        return result
    except Exception as e:
        return {'error': str(e), 'ticker': ticker, 'name': name}


def _analyze_futures_one(args):
    """单个期货分析（轻量版，只做技术分析）"""
    code, name, current_date = args
    try:
        result = analyze_futures_trend(code, name)
        result['ticker'] = code
        result['name'] = name
        return result
    except Exception as e:
        return {'error': str(e), 'ticker': code, 'name': name}


def generate_wechat_report(stocks, futures_list=None, current_date=None):
    """
    并行分析所有标的，生成微信推送格式报告
    
    Args:
        stocks: list of (ticker, name) — 个股和ETF
        futures_list: list of (code, name) — 期货合约
        current_date: str
    
    Returns:
        report_text: Markdown 微信友好版
    """
    if current_date is None:
        current_date = datetime.now().strftime('%Y-%m-%d')
    
    if futures_list is None:
        futures_list = []
    
    print(f"\n{'='*70}")
    print(f"  🚀 并行多Agent分析启动 ({len(stocks)}个股/ETF + {len(futures_list)}期货)")
    print(f"  日期: {current_date}")
    print(f"{'='*70}")
    
    results = []
    
    # ====== 并行分析个股/ETF ======
    if stocks:
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures_map = {
                executor.submit(_analyze_one, (ticker, name, current_date)): (ticker, name)
                for ticker, name in stocks
            }
            for future in as_completed(futures_map):
                ticker, name = futures_map[future]
                try:
                    result = future.result()
                    results.append(result)
                    if 'error' in result:
                        print(f"  ❌ {name}({ticker}): {result['error']}")
                    else:
                        v = result['verdict']
                        print(f"  ✅ {name}({ticker}): {v['rating']} 评分{v['weighted_score']} Bull{v['bull_score']}vsBear{v['bear_score']}")
                except Exception as e:
                    print(f"  ❌ {name}({ticker}): {e}")
                    results.append({'error': str(e), 'ticker': ticker, 'name': name})
    
    # ====== 并行分析期货 ======
    futures_results = []
    if futures_list:
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures_map = {
                executor.submit(_analyze_futures_one, (code, name, current_date)): (code, name)
                for code, name in futures_list
            }
            for future in as_completed(futures_map):
                code, name = futures_map[future]
                try:
                    result = future.result()
                    futures_results.append(result)
                    if 'error' in result:
                        print(f"  ❌ 期货 {name}({code}): {result['error']}")
                    else:
                        print(f"  ✅ 期货 {name}({code}): {result['signal']} 价格{result.get('price', '?')}")
                except Exception as e:
                    print(f"  ❌ 期货 {name}({code}): {e}")
                    futures_results.append({'error': str(e), 'ticker': code, 'name': name})
    
    # 提取交易区间映射
    zone_map = {}
    for r in results:
        if 'error' in r or not r.get('technical_report'):
            continue
        pred = r['technical_report'].get('prediction') or {}
        zones = pred.get('trading_zones', {})
        if zones and zones.get('buy'):
            zone_map[r['ticker']] = zones
    
    # ====== 生成微信报告 ======
    lines = []
    lines.append(f"📊 **多Agent量化日报**")
    lines.append(f"📅 {current_date}")
    lines.append("")
    
    # --- 高股息组合专区 ---
    lines.append(_render_high_dividend_portfolio(current_date, zone_map))
    lines.append("")
    
    # --- 期货行情概览（置顶） ---
    if futures_results:
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("**⚡ 期货行情**")
        lines.append("")
        # 按板块
        for cat, members in CATEGORIES.items():
            items = [r for r in futures_results if r.get('code') in members and 'error' not in r]
            if items:
                avg_chg = sum(r.get('trend_20d', 0) for r in items) / len(items)
                icon = "🟢" if avg_chg >= 0 else "🔴"
                lines.append(f"{icon} **{cat}**: 近20日平均{avg_chg:+.1f}%")
                for r in items:
                    sig = r.get('signal', '?')
                    sig_icon = "📈" if sig == '看多' else "📉" if sig == '看空' else "📊"
                    lines.append(f"  {sig_icon} {r['name']}: {r.get('price', '?')}  {sig}  ")
                lines.append("")
    
    # --- 个股/ETF 分析 ---
    ordered = []
    for ticker, name in stocks:
        for r in results:
            if r.get('ticker') == ticker:
                ordered.append(r)
                break
    
    for r in ordered:
        if 'error' in r:
            continue
        
        v = r['verdict']
        tech = r['technical_report']
        fund = r.get('fundamental_report')
        risk = r['risk_assessment']
        
        rt = get_realtime_price(r['ticker'])
        price = rt['price'] if rt else r['current_price']
        
        lines.append(f"━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"**{r['name']}({r['ticker']})** | {price}元{' (实时)' if rt else ''}")
        lines.append("")
        lines.append(f"📈 **{v['rating']}** (评分{v['weighted_score']}) | 建议: {v['recommendation']}")
        lines.append(f"🔵看涨{v['bull_score']} vs 🔴看跌{v['bear_score']} | 净信号{v['net_signal']:+d}")
        if fund:
            lines.append(f"技术{tech['score']}/100 | 基本面{fund['score']}/100")
        else:
            lines.append(f"技术{tech['score']}/100")
        lines.append(f"⚠️ {risk['overall_risk']}")
        
        # 交易区间（来自技术面预测）
        prediction = tech.get('tech_snapshot', {}).get('prediction') or tech.get('prediction')
        if prediction and prediction.get('trading_zones'):
            zones = prediction['trading_zones']
            lines.append("")
            lines.append(f"🎯 **买入**: {zones.get('buy', '-')} | **开仓**: {zones.get('open', '-')} | **卖出**: {zones.get('sell', '-')}")
        
        # Agentic 增强裁决
        agentic = r.get('agentic_report')
        if agentic:
            lines.append("")
            lines.append(f"🤖 **Agentic 裁决**: {agentic.get('rating', 'N/A')} | 置信度{agentic.get('confidence', 0):.0%}")
            if agentic.get('stop_loss'):
                lines.append(f"   止损: {agentic['stop_loss']} | 建议仓位: {agentic.get('suggested_position', 'N/A')}")
            if agentic.get('verdict'):
                verdict_short = agentic['verdict']
                if len(verdict_short) > 60:
                    verdict_short = verdict_short[:60] + '...'
                lines.append(f"   {verdict_short}")
        
        lines.append("")
        
        bt = tech.get('backtest_results', [])
        if bt:
            bt_short = [p for p in bt if p['days'] in [30, 60, 365]]
            for p in bt_short:
                lines.append(f"  {p['period_name']}: {p['total_return']:+.1f}%  回撤{p['max_drawdown']:.1f}%")
        
        signals = tech.get('signals', [])
        if signals:
            lines.append(f"  信号: {' | '.join([s[0]+s[1] for s in signals[:4]])}")
        
        lines.append("")
    
    # ========== 多策略技术扫描 ==========
    lines.append(f"━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"🔍 **多策略技术扫描**")
    lines.append("")
    for ticker, name in stocks:
        try:
            sr = scan_stock(ticker, name)
            if 'error' not in sr:
                icon = "🟢" if sr['total_score'] >= 20 else "🟡" if sr['total_score'] >= 10 else "🔴"
                lines.append(f"{icon} {name}: {sr['total_score']}/100 | 最佳: {sr['best_strategy']}")
        except:
            pass
    lines.append("")
    
    lines.append(f"━━━━━━━━━━━━━━━━━━━━")
    lines.append("⚠️ 仅供参考，不构成投资建议")
    
    return "\n".join(lines)


def generate_futures_markdown_reports(futures_list, current_date=None, output_dir=None):
    """为每个期货品种生成独立 Markdown 报告"""
    if current_date is None:
        current_date = datetime.now().strftime('%Y-%m-%d')
    if output_dir is None:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        output_dir = os.path.join(repo_root, 'docs', 'reports', current_date)
    os.makedirs(output_dir, exist_ok=True)
    date_str = current_date.replace('-', '')

    saved_files = []
    for code, name in futures_list:
        try:
            result = analyze_futures_trend(code, name)
            if 'error' in result:
                print(f"  ❌ 期货 {name}({code}): {result['error']}")
                continue

            lines = []
            lines.append(f"# ⚡ 期货分析报告")
            lines.append("")
            lines.append(f"**品种**: {name} ({code})")
            lines.append(f"**分析日期**: {current_date}")
            lines.append(f"**当前价格**: {result.get('price', '-')} 元")
            lines.append("")
            lines.append(f"---")
            lines.append("")
            lines.append(f"## 🏆 最终信号")
            lines.append("")
            lines.append(f"- **信号**: {result.get('signal', '-')}")
            lines.append(f"- **均线趋势**: {result.get('ma_trend', '-')}")
            lines.append(f"- **MACD**: {result.get('macd', '-')}")
            lines.append(f"- **布林带位置**: {result.get('boll_pos', '-')}")
            lines.append(f"- **RSI(14)**: {result.get('rsi', '-')}")
            lines.append("")
            lines.append(f"## 📈 趋势动量")
            lines.append("")
            lines.append(f"- 5日动量: {result.get('trend_5d', 0):+.2f}%")
            lines.append(f"- 20日动量: {result.get('trend_20d', 0):+.2f}%")
            lines.append("")
            lines.append(f"## 📊 多周期回测")
            lines.append("")
            lines.append(f"| 周期 | 收益率 | 最大回撤 | 夏普比 |")
            lines.append(f"|------|--------|----------|--------|")
            for p in result.get('backtest', []):
                lines.append(
                    f"| {p.get('period_name', p.get('days', '?'))} | {p.get('total_return', 0):+.1f}% | {p.get('max_drawdown', 0):.1f}% | {p.get('sharpe', 0):.2f} |"
                )
            lines.append("")
            # 走势预判
            fc = result.get('forecast')
            if fc:
                lines.append(f"## 🔮 走势预判")
                lines.append("")
                lines.append(f"- **目标价**: {fc.get('target_price', '-')}")
                lines.append(f"- **预期收益**: {fc.get('expected_return', '-')}")
                lines.append(f"- **支撑位**: {fc.get('support', '-')}")
                lines.append(f"- **压力位**: {fc.get('resistance', '-')}")
                lines.append("")
                lines.append(f"| 周期(日) | 预测价格 | 预测收益 | 置信下限 | 置信上限 | 信号 |")
                lines.append(f"|----------|----------|----------|----------|----------|------|")
                for row in fc.get('multi_period', []):
                    lines.append(
                        f"| {row['days']} | {row['predicted_price']} | {row['predicted_return']} | {row['lower']} | {row['upper']} | {row['signal']} |"
                    )
                lines.append("")
            lines.append(f"---")
            lines.append("")
            lines.append(f"⚠️ **免责声明**: 本报告由多 Agent 系统自动生成，基于量化模型和历史数据，仅供参考，不构成投资建议。投资有风险，入市须谨慎。")
            lines.append("")

            text = "\n".join(lines)
            filename = f"futures_{code}_{date_str}.md"
            filepath = os.path.join(output_dir, filename)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(text)
            saved_files.append(filepath)
            print(f"  ✅ 期货报告已保存: {filepath}")
        except Exception as e:
            print(f"  ❌ 期货 {name}({code}): {e}")
    return saved_files


def generate_markdown_report(stocks, futures_list=None, current_date=None, output_dir=None):
    """并行生成完整 Markdown 报告，默认保存到 docs/reports/YYYY-MM-DD/
    当 futures_list 为空时，默认生成 FUTURES_MAP 中全部期货品种报告。
    """
    if current_date is None:
        current_date = datetime.now().strftime('%Y-%m-%d')
    if futures_list is None:
        futures_list = []
    # 默认生成全部期货报告
    if not futures_list:
        from core.futures import FUTURES_MAP
        futures_list = [(code, name) for code, name in FUTURES_MAP]
    if output_dir is None:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        output_dir = os.path.join(repo_root, 'docs', 'reports', current_date)
    os.makedirs(output_dir, exist_ok=True)

    wechat_report = generate_wechat_report(stocks, futures_list, current_date)
    # 同时保存微信推送文本，避免重复分析
    wechat_path = f'/tmp/wechat_report_{current_date}.txt'
    try:
        with open(wechat_path, 'w', encoding='utf-8') as f:
            f.write(wechat_report)
        print(f'✅ 微信报告已保存: {wechat_path}')
    except Exception as e:
        print(f'⚠️ 保存微信报告失败: {e}')

    # 生成期货报告
    if futures_list:
        generate_futures_markdown_reports(futures_list, current_date, output_dir)

    # 生成对比汇总报告（ETF/个股）
    if stocks:
        from batch_analyzer import batch_analyze
        batch_analyze(stocks, current_date, output_dir=output_dir, agentic=False)

    return wechat_report


def load_daily_focus_list(path=None):
    """加载每日重点关注列表（核心标的，避免跑全量watchlist超时）"""
    if path is None:
        path = os.path.join(os.path.dirname(__file__), 'daily_focus_list.json')
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            items = json.load(f)
        return [(i['ticker'], i['name'], i.get('category', '个股'), i.get('tags', [])) for i in items]
    except Exception as e:
        print(f"⚠️ 加载 daily_focus_list 失败: {e}")
        return []


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='生成分析报告')
    parser.add_argument('--mode', '-m', choices=['wechat', 'markdown'], default='wechat')
    parser.add_argument('--output', '-o', default=None)
    parser.add_argument('--date', '-d', default=None)
    parser.add_argument('--focus', action='store_true', help='只跑 daily_focus_list.json 核心标的')
    parser.add_argument('--all', action='store_true', help='跑全部 watchlist')
    
    args = parser.parse_args()

    if args.all:
        # 全量模式（仅周末手动，或预留时间充足时）
        all_items = load_list()
    else:
        # 默认使用 focus list，每天早上必须完成的日报
        all_items = [
            {'ticker': t, 'name': n, 'category': c}
            for t, n, c, tags in load_daily_focus_list()
        ]
    
    stocks = []
    futures = []
    for item in all_items:
        if item.get('category') == '期货':
            futures.append((item['ticker'], item['name']))
        else:
            stocks.append((item['ticker'], item['name']))
    
    if args.mode == 'markdown':
        report = generate_markdown_report(stocks, futures, args.date)
    else:
        report = generate_wechat_report(stocks, futures, args.date)
    
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(report)
        print(f"✅ 报告已保存: {args.output}")
    else:
        print(report)
