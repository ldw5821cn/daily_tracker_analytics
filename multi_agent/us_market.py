"""
美股市场分析模块 — 整合10大常用投研网站的数据入口
数据来源: Finviz / StockAnalysis / Seeking Alpha / ETF Database / CompaniesMarketCap
         Koyfin / MacroMicro / FRED / Reddit / AnalysisSite
"""
import os
import re
import json
import urllib.request
from datetime import datetime

# 10 个核心美股数据源入口
US_SOURCES = {
    'Finviz': {
        'url': 'https://finviz.com',
        'desc': '热力图、筛选器、技术形态、板块强弱',
        'use': '盘前扫盘，先看板块热力图',
    },
    'StockAnalysis': {
        'url': 'https://stockanalysis.com',
        'desc': '个股、财报、估值、分析师预期',
        'use': '基本面初筛和财报日历',
    },
    'Seeking Alpha': {
        'url': 'https://seekingalpha.com',
        'desc': '个股深度、财报点评、多空观点、股息和量化评分',
        'use': '看深度观点、量化评级、股息评分',
    },
    'ETF Database': {
        'url': 'https://etfdb.com',
        'desc': 'ETF 分类、持仓、费用率、规模、分红',
        'use': '主题/行业 ETF 筛选',
    },
    'CompaniesMarketCap': {
        'url': 'https://companiesmarketcap.com',
        'desc': '全球市值、营收、利润、PE 排名',
        'use': '龙头横向对比和估值排名',
    },
    'Koyfin': {
        'url': 'https://app.koyfin.com',
        'desc': '市场结构、板块表现、估值、宏观联动',
        'use': '系统性看盘，宏观-市场联动',
    },
    'MacroMicro': {
        'url': 'https://en.macromicro.me',
        'desc': '宏观数据、利率、通胀、就业、流动性、经济周期',
        'use': '全球宏观和周期判断',
    },
    'FRED': {
        'url': 'https://fred.stlouisfed.org',
        'desc': '美国官方宏观数据',
        'use': '利率、通胀、就业、信贷、货币',
    },
    'Reddit': {
        'url': 'https://www.reddit.com/r/stocks',
        'desc': '散户情绪、热门票、早期线索',
        'use': '蹲 r/stocks, r/investing, r/wallstreetbets, r/ValueInvesting',
    },
    'AnalysisSite': {
        'url': 'https://analysissite.vercel.app',
        'desc': '白毛股神 / Serenity 投研看板，推文和 AI 分析',
        'use': '追线索和 AI 观点',
    },
}

# 重点关注的 ETF/个股（示例，可扩展）
US_WATCHLIST = [
    ('SPY', 'SPDR S&P 500 ETF'),
    ('QQQ', 'Invesco QQQ Trust'),
    ('IWM', 'iShares Russell 2000 ETF'),
    ('VIX', 'CBOE 波动率指数'),
    ('TSLA', 'Tesla'),
    ('AAPL', 'Apple'),
    ('NVDA', 'NVIDIA'),
    ('MSFT', 'Microsoft'),
    ('GOOGL', 'Alphabet'),
    ('META', 'Meta'),
    ('AMZN', 'Amazon'),
    ('BABA', 'Alibaba'),
    ('PDD', 'PDD Holdings'),
]


def _fetch_url(url, timeout=15):
    """通用网页抓取，返回文本"""
    try:
        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
                'Accept-Language': 'en-US,en;q=0.9',
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode('utf-8', errors='ignore')
    except Exception as e:
        return f'抓取失败: {e}'


def get_finviz_heatmap_url():
    """Finviz 热力图直接链接"""
    return 'https://finviz.com/map.ashx'


def get_stockanalysis_ticker_url(ticker):
    """StockAnalysis 个股页面"""
    return f'https://stockanalysis.com/stocks/{ticker.lower()}/'


def get_seekingalpha_ticker_url(ticker):
    """Seeking Alpha 个股页面"""
    return f'https://seekingalpha.com/symbol/{ticker.upper()}'


def get_etfdb_ticker_url(ticker):
    """ETFDB ETF 页面"""
    return f'https://etfdb.com/etf/{ticker.upper()}/'


def get_companiesmarketcap_url():
    """全球市值排名"""
    return 'https://companiesmarketcap.com'


def get_sina_us_quote(ticker):
    """从新浪财经获取美股/ETF 实时行情（gb_ 前缀）"""
    try:
        sina_code = f'gb_{ticker.lower()}'
        url = f'https://hq.sinajs.cn/list={sina_code}'
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://finance.sina.com.cn',
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode('gbk', errors='ignore')
        match = re.search(r'"([^"]+)"', text)
        if match:
            parts = match.group(1).split(',')
            if len(parts) > 5:
                name = parts[0]
                latest = float(parts[1]) if parts[1] else 0
                change_pct = float(parts[2]) if parts[2] else 0
                change = float(parts[4]) if parts[4] else 0
                prev_close = float(parts[5]) if parts[5] else 0
                return {
                    'ticker': ticker.upper(),
                    'price': round(latest, 2),
                    'change': round(change, 2),
                    'change_pct': round(change_pct, 2),
                    'prev_close': round(prev_close, 2),
                    'market_state': '交易中' if change_pct != 0 else '-',
                    'name': name,
                }
    except Exception as e:
        return {'ticker': ticker.upper(), 'error': str(e)}
    return {'ticker': ticker.upper(), 'error': '无数据'}


def fetch_us_watchlist_quotes():
    """批量获取美股关注标的实时行情"""
    results = []
    for ticker, name in US_WATCHLIST:
        q = get_sina_us_quote(ticker)
        q['name'] = name
        results.append(q)
    return results


def generate_us_market_report(current_date=None, output_dir=None):
    """生成美股日报 Markdown + 入口汇总"""
    if current_date is None:
        current_date = datetime.now().strftime('%Y-%m-%d')
    if output_dir is None:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        output_dir = os.path.join(repo_root, 'docs', 'reports', current_date)
    os.makedirs(output_dir, exist_ok=True)

    date_str = current_date.replace('-', '')
    quotes = fetch_us_watchlist_quotes()

    lines = []
    lines.append('# 🇺🇸 美股市场日报')
    lines.append('')
    lines.append(f'**日期**: {current_date}')
    lines.append('')
    lines.append('> 本报告整合 10 个常用美股投研网站入口，自动生成，方便快速跳转到对应工具做深度分析。')
    lines.append('')

    # 实时行情
    lines.append('## 📈 重点标的实时行情')
    lines.append('')
    lines.append('| 标的 | 名称 | 价格 | 涨跌 | 涨跌幅 | 市场状态 |')
    lines.append('|------|------|------|------|--------|----------|')
    for q in quotes:
        if 'error' in q:
            lines.append(f"| {q['ticker']} | {q['name']} | - | - | - | 获取失败 |")
            continue
        price = q.get('price', '-')
        chg = q.get('change', '-')
        chg_pct = q.get('change_pct', '-')
        state = q.get('market_state', '-')
        if isinstance(chg, (int, float)):
            chg = f"{chg:+.2f}"
        if isinstance(chg_pct, (int, float)):
            chg_pct = f"{chg_pct:+.2f}%"
        lines.append(f"| {q['ticker']} | {q['name']} | {price} | {chg} | {chg_pct} | {state} |")
    lines.append('')

    # 10 大网站入口
    lines.append('## 🔗 10 个核心美股投研网站')
    lines.append('')
    for name, info in US_SOURCES.items():
        lines.append(f"### [{name}]({info['url']})")
        lines.append(f"- **用途**: {info['desc']}")
        lines.append(f"- **怎么用**: {info['use']}")
        lines.append('')

    # 重点个股快捷入口
    lines.append('## 🔍 关注标的快捷分析入口')
    lines.append('')
    lines.append('| 标的 | Finviz | StockAnalysis | Seeking Alpha | ETFDB |')
    lines.append('|------|--------|---------------|---------------|-------|')
    for ticker, name in US_WATCHLIST:
        finviz = f'[查看](https://finviz.com/quote.ashx?t={ticker.upper()})'
        sa = f'[查看](https://stockanalysis.com/stocks/{ticker.lower()}/)'
        se = f'[查看](https://seekingalpha.com/symbol/{ticker.upper()})'
        etf = f'[查看](https://etfdb.com/etf/{ticker.upper()}/)' if 'ETF' in name or ticker in ['SPY', 'QQQ', 'IWM'] else '-'
        lines.append(f"| {ticker} ({name}) | {finviz} | {sa} | {se} | {etf} |")
    lines.append('')

    lines.append('---')
    lines.append('')
    lines.append('⚠️ **免责声明**: 本报告由系统自动生成，仅作为数据入口和行情汇总，不构成投资建议。')
    lines.append('')

    text = '\n'.join(lines)
    filepath = os.path.join(output_dir, f'us_market_{date_str}.md')
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(text)
    print(f'✅ 美股日报已保存: {filepath}')
    return filepath


if __name__ == '__main__':
    generate_us_market_report()
