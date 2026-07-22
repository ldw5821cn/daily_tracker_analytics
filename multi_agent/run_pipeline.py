#!/usr/bin/env python3
"""
 · 全A股扫描 + 一键全链路

流程：
  1. 全A股3600+只快速扫描（Sina分页，~30s）
  2. 机械打分 → 全市场Top10
  3. 对Top10下载详情报 + LLM深度分析（辩论引擎）
  4. 保存预测到回测DB
  5. 验证到期预测
  6. 生成Pages + 部署
  7. 输出微信推荐

用法：
  python3 run_pipeline.py                          # 全链路（推荐）
  python3 run_pipeline.py --skip-llm               # 仅机械打分（更快）
  python3 run_pipeline.py --validate-only           # 仅回测验证
  python3 run_pipeline.py --weekly                  # 每周全量扫描+模拟盘初始化
  python3 run_pipeline.py --simulate                # 每日模拟盘更新
  python3 run_pipeline.py --scan-only               # 仅扫描推荐
"""

import sys
import os
import json
import urllib.request
import time
import argparse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = '/home/liudawei/github/daily_tracker_analytics'
sys.path.insert(0, f'{BASE}/multi_agent')

from predictor import collect_market_data, build_prediction_prompt


# ============================================================
# Step 1: 全A股快速扫描
# ============================================================

def get_stock_pool() -> list:
    """获取股票池（带缓存，每日刷新一次；Tushare 频率受限时回退到本地缓存/文件）"""
    cache_file = '/tmp/a_share_stock_pool.json'
    backup_pickle = '/tmp/a_share_pool_20260722.pkl'
    
    today = datetime.now().strftime('%Y-%m-%d')
    
    # 检查 JSON 缓存是否当天
    if os.path.exists(cache_file):
        mtime = os.path.getmtime(cache_file)
        cache_date = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d')
        if cache_date == today:
            with open(cache_file, 'r', encoding='utf-8') as f:
                cached = json.load(f)
            print(f"  ✅ 使用缓存: {len(cached)} 只股票")
            return cached
    
    # 检查 pickle 缓存（外部预取）
    if os.path.exists(backup_pickle):
        import pandas as pd
        try:
            df = pd.read_pickle(backup_pickle)
            if 'market' in df.columns:
                df = df[df['market'].isin(['主板', '创业板', '科创板'])]
                df = df[~df['name'].str.startswith(('ST', '*ST', '退', 'N', 'C'))]
                stocks = df.to_dict('records')
                with open(cache_file, 'w', encoding='utf-8') as f:
                    json.dump(stocks, f, ensure_ascii=False, indent=2)
                print(f"  ✅ 使用 pickle 缓存: {len(stocks)} 只股票")
                return stocks
        except Exception:
            pass
    
    # 从Tushare获取
    import tushare as ts
    print(f"  📡 从Tushare获取股票列表...")
    pro = ts.pro_api()
    df = pro.stock_basic(exchange='', list_status='L',
                         fields='ts_code,symbol,name,market,list_date')
    df = df[df['market'].isin(['主板', '创业板', '科创板'])]
    df = df[~df['name'].str.startswith(('ST', '*ST', '退', 'N', 'C'))]
    stocks = df.to_dict('records')
    
    # 缓存
    with open(cache_file, 'w', encoding='utf-8') as f:
        json.dump(stocks, f, ensure_ascii=False, indent=2)
    print(f"  ✅ 获取到 {len(stocks)} 只股票（已缓存）")
    return stocks


def scan_all_a_shares() -> list:
    """使用Tushare(缓存)获取股票池 + qt.gtimg.cn批量查实时行情"""
    all_stocks = []
    
    stocks = get_stock_pool()
    print(f"\n📡 批量获取实时行情...")
    batch_size = 100
    
    def parse_qt_response(text: str) -> list:
        """解析 qt.gtimg.cn 返回数据"""
        results = []
        for line in text.strip().split('\n'):
            if not line or '=' not in line:
                continue
            try:
                parts = line.split('~')
                if len(parts) < 40:
                    continue
                code = parts[2] if parts[2] else ''
                name = parts[1] if parts[1] else ''
                price = float(parts[3]) if parts[3] else 0
                prev_close = float(parts[4]) if parts[4] else price
                chg_pct = ((price - prev_close) / prev_close * 100) if prev_close > 0 else 0
                high = float(parts[33]) if parts[33] else 0
                low = float(parts[34]) if parts[34] else 0
                volume = float(parts[6]) if parts[6] else 0
                amount = float(parts[37]) if len(parts) > 37 and parts[37] else 0
                
                results.append({
                    'code': code,
                    'name': name,
                    'trade': str(price),
                    'pricechange': round(price - prev_close, 2),
                    'changepercent': round(chg_pct, 3),
                    'high': str(high),
                    'low': str(low),
                    'volume': str(volume),
                    'amount': str(float(parts[37]) * 10000 if parts[37] else 0),  # 万→元
                    'mktcap': str(float(parts[44]) * 1e8 if parts[44] else 0),    # 亿→元
                    'pe': parts[39] if parts[39] else '0',
                    'turnoverrate': parts[38] if parts[38] else '0',
                })
            except:
                pass
        return results
    
    # 先按market分组，再批量查
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import time
    
    def batch_query(batch_codes):
        query = ','.join(batch_codes)
        url = f'http://qt.gtimg.cn/q={query}'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                text = resp.read().decode('gbk')
            return parse_qt_response(text)
        except:
            return []
    
    # 构建qt.gtimg格式的代码前缀
    code_list = []
    for s in stocks:
        sym = s['symbol']
        prefix = 'sh' if sym.startswith(('6', '5')) else 'sz'
        code_list.append(f'{prefix}{sym}')
    
    # 分批并行查询
    batches = [code_list[i:i+batch_size] for i in range(0, len(code_list), batch_size)]
    print(f"  共 {len(batches)} 批查询...")
    
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(batch_query, b): i for i, b in enumerate(batches)}
        for f in as_completed(futures):
            batch_idx = futures[f]
            all_stocks.extend(f.result())
            if (batch_idx + 1) % 5 == 0:
                print(f"  已完成 {min((batch_idx+1)*batch_size, len(code_list))}/{len(code_list)} 只...")
    
    # qt.gtimg不返回市场类型，从代码还原
    code_map = {s['symbol']: s['market'] for s in stocks}
    for s in all_stocks:
        s['market'] = code_map.get(s['code'], '')
    
    print(f"  ✅ 完成: {len(all_stocks)} 只股票实时行情")
    return all_stocks


def score_stock(s: dict) -> dict:
    """ v2 评分引擎（动量分析 + 风控 + 板块分散）"""
    from strategy_scoring import deep_score
    return deep_score(s)


def scan_and_score(stocks: list = None) -> list:
    """全A股扫描+打分"""
    if stocks is None:
        stocks = scan_all_a_shares()
    
    results = []
    for s in stocks:
        r = score_stock(s)
        if r:
            results.append(r)
    
    # 排序取Top
    results.sort(key=lambda x: x['composite_score'], reverse=True)
    return results


# ============================================================
# Step 2: Top10深度分析
# ============================================================

def get_top10_with_technicals(all_scores: list) -> list:
    """对Top10下载详细技术指标（板块分散版）"""
    from strategy_scoring import select_diversified_top10
    top10_selected = select_diversified_top10(all_scores, n=10)
    top10_raw = [s for s in all_scores if s in top10_selected][:15]  # 按评分排序的分散Top
    
    print(f"\n📊 对 Top 15 下载详细技术数据...")
    top10 = []
    
    def fetch_one(s):
        ticker = s['code']
        # 确定交易所前缀
        if ticker.startswith(('6', '5')):
            prefixed = ticker
        else:
            prefixed = ticker
        data = collect_market_data(ticker, s['name'])
        if 'error' not in data:
            data['composite_score'] = s['composite_score']
            data['score_detail'] = s['score_detail']
            return data
        return None
    
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(fetch_one, s): s for s in top10_raw}
        for f in as_completed(futures):
            r = f.result()
            if r:
                top10.append(r)
                print(f"  ✅ {r['name']}({r['ticker']}): {r['current_price']}")
    
    top10.sort(key=lambda x: x['composite_score'], reverse=True)
    return top10[:10]


# ============================================================
# 微信推送格式化
# ============================================================

def format_market_top10(all_scores: list, top10_detailed: list = None) -> str:
    """格式化全A股Top10推荐（板块分散版）"""
    lines = []
    now = datetime.now()
    lines.append(f"📊 ** · 全A股扫描** {now.strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"")
    
    # 取板块分散的Top10
    from strategy_scoring import select_diversified_top10
    top10 = select_diversified_top10(all_scores, n=10)
    
    # 统计
    avg_score = sum(s['composite_score'] for s in top10) / len(top10)
    avg_chg = sum(s['changepercent'] for s in top10) / len(top10)
    lines.append(f"扫描全A股 · Top10平均评分{avg_score:.0f}/100 · 均涨幅{avg_chg:+.2f}%")
    lines.append("")
    
    for i, s in enumerate(top10, 1):
        emoji = '🟢' if s['composite_score'] >= 75 else '🟡'
        name = s['name']
        code = s['code']
        score = s['composite_score']
        chg = s['changepercent']
        price = s['price']
        amount = s.get('amount', 0)
        vol_str = f" {amount/1e8:.1f}亿" if amount else ""
        
        lines.append(f"{emoji} **{i}. {name}**({code}) 评分{score}")
        lines.append(f"> {chg:+.2f}% @{price:.2f}{vol_str}")
    
    lines.append("")
    lines.append("📌 评分维度：涨跌幅25+成交额20+现价15+PE15+市值15+换手10")
    lines.append("*研究辅助，非投资建议*")
    
    return "\n".join(lines)


# ============================================================
# Pages 生成更新
# ============================================================

def update_pages(all_scores: list):
    """更新Pages页面: 1) 通用预测页 2) 持仓组合页"""
    sys.path.insert(0, os.path.join(BASE, 'scripts'))

    # 1) 通用预测页 (generate_pages.py 无 generate_html 接口,直接调主函数)
    try:
        import generate_pages
        if hasattr(generate_pages, 'main'):
            generate_pages.main()
    except Exception as e:
        print(f"  ⚠️ 生成 prediction.html 失败: {e}")

    # 2) 持仓组合页 (generate_portfolio_pages.py 有 generate_html)
    try:
        from generate_portfolio_pages import generate_html
        portfolio_data = build_portfolio_data(all_scores)
        html = generate_html(portfolio_data)
        docs_dir = os.path.join(BASE, 'docs')
        os.makedirs(docs_dir, exist_ok=True)
        with open(os.path.join(docs_dir, 'portfolio.html'), 'w', encoding='utf-8') as f:
            f.write(html)
    except Exception as e:
        print(f"  ⚠️ 生成 portfolio.html 失败: {e}")


def build_portfolio_data(all_scores: list) -> dict:
    """构造组合页数据，取Top10为推荐持仓等权"""
    holdings = []
    total = len(all_scores[:10])
    total_asset = 1000000.0
    stock_value = total_asset * 0.9 if total else 0.0
    for i, s in enumerate(all_scores[:10], 1):
        weight = round(90 / total, 2) if total else 0
        price = s.get('price', 0)
        entry_price = price * (1 - s.get('changepercent', 0) / 100) if price else price
        holdings.append({
            'name': s['name'],
            'symbol': s['code'],
            'code': s['code'],
            'price': price,
            'entry_price': entry_price,
            'chg_pct': s.get('changepercent', 0),
            'weight': weight,
            'market_value': total_asset * (weight / 100),
            'volume': s.get('amount', 0) / 1e8 if s.get('amount') else 0,
            'market': s.get('market', ''),
            'pe': s.get('pe', 0) or 0,
        })
    return {
        'name': 'ZH3650487',
        'net_value': 1.0,
        'daily_gain': 0.0,
        'total_gain': 0.0,
        'annualized_gain': 0.0,
        'monthly_gain': 0.0,
        'holdings': holdings,
        'recommendations': holdings,
        'initial_capital': 1000000,
        'cash_pct': 10.0,
        'cash_ratio': 0.10,
        'cash': total_asset - stock_value,
        'total_asset': total_asset,
        'stock_value': stock_value,
        'rebalance_history': [],
        'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'date': datetime.now().strftime('%Y-%m-%d %H:%M'),
    }

# ============================================================
# 验证到期预测
# ============================================================

def validate():
    """验证到期预测"""
    data_file = '/tmp/stock_cron_data.json'
    if not os.path.exists(data_file):
        # 重新收集
        from predictor import collect_watchlist
        collect_watchlist(output_path=data_file)
    
    with open(data_file) as f:
        data = json.load(f)
    md = {d['ticker']: d['current_price'] for d in data if 'error' not in d}
    
    try:
        from core.llm_prediction_backtest import validate_expired_predictions
        r = validate_expired_predictions(md)
    except ModuleNotFoundError:
        r = {'validated': 0, 'correct': 0, 'accuracy': 0}
    print(f"  🔍 验证: {r.get('validated', 0)} 条 | 正确: {r.get('correct', 0)} | 准确率: {r.get('accuracy', 0)}%")
    return r


# ============================================================
# 主入口
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=' · 一键全链路')
    parser.add_argument('--skip-llm', action='store_true', help='跳过LLM深度分析（仅机械打分）')
    parser.add_argument('--validate-only', action='store_true', help='仅验证到期预测')
    parser.add_argument('--scan-only', action='store_true', help='仅扫描全A股+推荐')
    parser.add_argument('--weekly', action='store_true', help='每周全量扫描+模拟盘初始化')
    parser.add_argument('--simulate', action='store_true', help='每日模拟盘更新')
    parser.add_argument('--capital', type=float, default=100000.0, help='模拟盘初始资金（默认10万）')
    args = parser.parse_args()
    
    print(f"\n{'='*60}")
    print(f"  🏛️   · 一键全链路")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")
    
    start = time.time()
    
    # 每周全量扫描 + 模拟盘初始化
    if args.weekly:
        print(f"\n{'─'*60}")
        print(f"  🏆 每周全量扫描 + 模拟盘初始化")
        print(f"{'─'*60}")
        stocks = scan_all_a_shares()
        all_scores = scan_and_score(stocks)
        top10 = all_scores[:10]
        print(f"\n  📋 Top10 推荐:")
        for i, s in enumerate(top10, 1):
            print(f"  {i:2d}. {s['name']:8s}({s['code']:8s}) 评分{s['composite_score']:3d} {s['changepercent']:+.2f}%")
        
        # 保存Top10供模拟盘使用
        top10_path = '/tmp/weekly_top10.json'
        with open(top10_path, 'w', encoding='utf-8') as f:
            json.dump(top10, f, ensure_ascii=False, indent=2)
        
        # 初始化模拟盘
        sys.path.insert(0, os.path.join(BASE, 'multi_agent'))
        from simulator import init_weekly_portfolio, format_wechat_summary, get_portfolio_summary
        
        result = init_weekly_portfolio(top10, capital=args.capital)
        sim_summary = get_portfolio_summary()
        wechat_text = format_wechat_summary(sim_summary)
        
        elapsed = time.time() - start
        print(f"\n{'='*60}")
        print(f"  ✅ 每周任务完成! 耗时: {elapsed:.0f}s")
        print(f"{'='*60}")
        print(f"\n📱 微信推送:\n{wechat_text}")
        sys.exit(0)
    
    # 每日模拟盘更新
    if args.simulate:
        print(f"\n{'─'*60}")
        print(f"  💰 每日模拟盘更新")
        print(f"{'─'*60}")
        data_file = '/tmp/stock_cron_data.json'
        
        # 先收集最新行情
        from predictor import collect_watchlist
        collect_watchlist(output_path=data_file)
        
        sys.path.insert(0, os.path.join(BASE, 'multi_agent'))
        from simulator import update_daily, check_stop_loss, format_wechat_summary, get_portfolio_summary
        
        with open(data_file, 'r') as f:
            data = json.load(f)
        update_result = update_daily(data)
        
        # 止损检查
        alerts = check_stop_loss()
        
        sim_summary = get_portfolio_summary()
        wechat_text = format_wechat_summary(sim_summary)
        
        elapsed = time.time() - start
        print(f"\n{'='*60}")
        print(f"  ✅ 模拟盘更新完成! 耗时: {elapsed:.0f}s")
        print(f"{'='*60}")
        print(f"\n📱 微信推送:\n{wechat_text}")
        sys.exit(0)
    
    # 验证到期预测
    if args.validate_only:
        validate()
        sys.exit(0)
    
    # Step 1: 全A股扫描
    print(f"\n{'─'*60}")
    print(f"  Step 1/4: 全A股快速扫描")
    print(f"{'─'*60}")
    stocks = scan_all_a_shares()
    
    # TickFlow 批量预取（避免逐个 API 调用）
    from strategy_scoring import prefetch_tf_quotes
    prefetch_tf_quotes(stocks)
    
    all_scores = scan_and_score(stocks)
    print(f"  ✅ {len(all_scores)} 只完成打分")
    
    # Step 2: 推荐
    print(f"\n{'─'*60}")
    print(f"  Step 2/4: Top10 推荐")
    print(f"{'─'*60}")
    top10 = all_scores[:10]
    for i, s in enumerate(top10, 1):
        print(f"  {i:2d}. {s['name']:8s}({s['code']:8s}) 评分{s['composite_score']:3d} "
              f"{s['changepercent']:+.2f}% @{s['price']:.2f}")
    
    # 生成微信推荐文本
    wechat_text = format_market_top10(all_scores)
    
    if not args.scan_only and not args.skip_llm:
        # Step 3: Top10深度分析
        print(f"\n{'─'*60}")
        print(f"  Step 3/4: Top10 深度LLM分析")
        print(f"{'─'*60}")
        top10_detailed = get_top10_with_technicals(all_scores)
    
    # 验证
    print(f"\n{'─'*60}")
    print(f"  Step 4/4: 回测验证 + Pages")
    print(f"{'─'*60}")
    validate()
    
    # 更新Pages
    update_pages(all_scores)
    
    # 部署
    import subprocess
    print(f"\n  🚀 部署Pages...")
    result = subprocess.run(['bash', 'scripts/deploy_reports.sh'], 
                          cwd=BASE, capture_output=True, text=True, timeout=60)
    print(f"  {result.stdout.split(chr(10))[-3] if result.stdout else '✔'}") 
    
    elapsed = time.time() - start
    print(f"\n{'='*60}")
    print(f"  ✅ 全链路完成! 耗时: {elapsed:.0f}s")
    print(f"{'='*60}")
    print(f"\n📱 微信推送文本:")
    print(wechat_text)
