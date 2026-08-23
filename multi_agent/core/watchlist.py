"""标的列表管理 - 支持动态添加/删除/查看关注标的"""
import json
import os

DEFAULT_FILE = '/home/liudawei/github/daily_tracker_analytics/multi_agent/watchlist.json'

DEFAULT_STOCKS = [
    {"ticker": "601991", "name": "大唐发电", "category": "个股"},
    {"ticker": "600642", "name": "申能股份", "category": "个股"},
    {"ticker": "600023", "name": "浙能电力", "category": "个股"},
    {"ticker": "600011", "name": "华能国际", "category": "个股"},
    {"ticker": "600027", "name": "华电国际", "category": "个股"},
    {"ticker": "601398", "name": "工商银行", "category": "个股"},
    {"ticker": "600206", "name": "有研新材", "category": "个股"},
    {"ticker": "300054", "name": "鼎龙股份", "category": "个股"},
    {"ticker": "688019", "name": "安集科技", "category": "个股"},
    {"ticker": "688041", "name": "海光信息", "category": "个股"},
    {"ticker": "688256", "name": "寒武纪", "category": "个股"},
    {"ticker": "688981", "name": "中芯国际", "category": "个股"},
    {"ticker": "688012", "name": "中微公司", "category": "个股"},
    {"ticker": "300782", "name": "卓胜微", "category": "个股"},
    {"ticker": "688047", "name": "龙芯中科", "category": "个股"},
    {"ticker": "300474", "name": "景嘉微", "category": "个股"},
    {"ticker": "688072", "name": "拓荆科技", "category": "个股"},
    {"ticker": "688082", "name": "盛美上海", "category": "个股"},
    {"ticker": "000657", "name": "中钨高新", "category": "个股"},
    {"ticker": "600549", "name": "厦门钨业", "category": "个股"},
    {"ticker": "002378", "name": "章源钨业", "category": "个股"},
    {"ticker": "688146", "name": "中船特气", "category": "个股"},
    {"ticker": "000969", "name": "安泰科技", "category": "个股"},
    {"ticker": "601958", "name": "金钼股份", "category": "个股"},
    {"ticker": "603993", "name": "洛阳钼业", "category": "个股"},
    {"ticker": "600392", "name": "盛和资源", "category": "个股"},
    {"ticker": "300263", "name": "隆华科技", "category": "个股"},
    {"ticker": "000960", "name": "锡业股份", "category": "个股"},
    {"ticker": "000426", "name": "兴业银锡", "category": "个股"},
    {"ticker": "600301", "name": "华锡有色", "category": "个股"},
    {"ticker": "301319", "name": "唯特偶", "category": "个股"},
    {"ticker": "600961", "name": "株冶集团", "category": "个股"},
    {"ticker": "688530", "name": "欧莱新材", "category": "个股"},
    {"ticker": "601600", "name": "中国铝业", "category": "个股"},
    {"ticker": "000612", "name": "焦作万方", "category": "个股"},
    {"ticker": "300346", "name": "南大光电", "category": "个股"},
    {"ticker": "600703", "name": "三安光电", "category": "个股"},
    {"ticker": "002428", "name": "云南锗业", "category": "个股"},
    {"ticker": "600497", "name": "驰宏锌锗", "category": "个股"},
    {"ticker": "300489", "name": "光智科技", "category": "个股"},
    {"ticker": "688313", "name": "仕佳光子", "category": "个股"},
    {"ticker": "000962", "name": "东方钽业", "category": "个股"},
    {"ticker": "300726", "name": "宏达电子", "category": "个股"},
    {"ticker": "000733", "name": "振华科技", "category": "个股"},
    {"ticker": "603678", "name": "火炬电子", "category": "个股"},
    {"ticker": "002138", "name": "顺络电子", "category": "个股"},
    {"ticker": "300618", "name": "寒锐钴业", "category": "个股"},
    {"ticker": "301219", "name": "腾远钴业", "category": "个股"},
    {"ticker": "300666", "name": "江丰电子", "category": "个股"},
    {"ticker": "600459", "name": "贵研铂业", "category": "个股"},
    {"ticker": "301026", "name": "浩通科技", "category": "个股"},
    {"ticker": "300706", "name": "阿石创", "category": "个股"},
    {"ticker": "601899", "name": "紫金矿业", "category": "个股"},
    {"ticker": "600362", "name": "江西铜业", "category": "个股"},
    {"ticker": "000630", "name": "铜陵有色", "category": "个股"},
    {"ticker": "002203", "name": "海亮股份", "category": "个股"},
    {"ticker": "000878", "name": "云南铜业", "category": "个股"},
    {"ticker": "300308", "name": "中际旭创", "category": "个股"},
    {"ticker": "300502", "name": "新易盛", "category": "个股"},
    {"ticker": "300394", "name": "天孚通信", "category": "个股"},
    {"ticker": "600547", "name": "山东黄金", "category": "个股"},
    {"ticker": "600489", "name": "中金黄金", "category": "个股"},
    {"ticker": "000975", "name": "银泰黄金", "category": "个股"},
    {"ticker": "600988", "name": "赤峰黄金", "category": "个股"},
    {"ticker": "601088", "name": "中国神华", "category": "个股"},
    {"ticker": "601225", "name": "陕西煤业", "category": "个股"},
    {"ticker": "601898", "name": "中煤能源", "category": "个股"},
    {"ticker": "600188", "name": "兖矿能源", "category": "个股"},
    {"ticker": "000983", "name": "山西焦煤", "category": "个股"},
    {"ticker": "600019", "name": "宝钢股份", "category": "个股"},
    {"ticker": "000898", "name": "鞍钢股份", "category": "个股"},
    {"ticker": "600010", "name": "包钢股份", "category": "个股"},
    {"ticker": "000932", "name": "华菱钢铁", "category": "个股"},
    {"ticker": "000959", "name": "首钢股份", "category": "个股"},
    {"ticker": "601857", "name": "中国石油", "category": "个股"},
    {"ticker": "600028", "name": "中国石化", "category": "个股"},
    {"ticker": "601808", "name": "中海油服", "category": "个股"},
    {"ticker": "600583", "name": "海油工程", "category": "个股"},
    {"ticker": "002353", "name": "杰瑞股份", "category": "个股"},
    {"ticker": "600309", "name": "万华化学", "category": "个股"},
    {"ticker": "600426", "name": "华鲁恒升", "category": "个股"},
    {"ticker": "600989", "name": "宝丰能源", "category": "个股"},
    {"ticker": "600346", "name": "恒力石化", "category": "个股"},
    {"ticker": "000301", "name": "东方盛虹", "category": "个股"},
    {"ticker": "002493", "name": "荣盛石化", "category": "个股"},
    {"ticker": "000807", "name": "云铝股份", "category": "个股"},
    {"ticker": "600219", "name": "南山铝业", "category": "个股"},
    {"ticker": "000923", "name": "河钢资源", "category": "个股"},
    {"ticker": "001203", "name": "大中矿业", "category": "个股"},
    {"ticker": "002385", "name": "大北农", "category": "个股"},
    {"ticker": "000876", "name": "新希望", "category": "个股"},
    {"ticker": "300999", "name": "金龙鱼", "category": "个股"},
    {"ticker": "515880", "name": "通信ETF", "category": "ETF"},
    {"ticker": "516150", "name": "稀土ETF", "category": "ETF"},
]


def load_list(filepath=None):
    """加载关注标的列表"""
    fp = filepath or DEFAULT_FILE
    if os.path.exists(fp):
        try:
            with open(fp, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    # 首次使用：创建默认列表
    save_list(DEFAULT_STOCKS, fp)
    return DEFAULT_STOCKS


def save_list(stocks, filepath=None):
    """保存关注标的列表"""
    fp = filepath or DEFAULT_FILE
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, 'w', encoding='utf-8') as f:
        json.dump(stocks, f, ensure_ascii=False, indent=2)
    return True


def add_stock(ticker, name, category="个股", theme="", sector="", filepath=None):
    """添加关注标的"""
    stocks = load_list(filepath)
    # 去重
    existing = [s for s in stocks if s['ticker'] != ticker]
    existing.append({
        "ticker": ticker,
        "name": name,
        "category": category,
        "theme": theme,
        "sector": sector,
    })
    save_list(existing, filepath)
    return existing


def remove_stock(ticker, filepath=None):
    """移除关注标的"""
    stocks = load_list(filepath)
    stocks = [s for s in stocks if s['ticker'] != ticker]
    save_list(stocks, filepath)
    return stocks


def get_stocks_as_tuples(filepath=None):
    """获取 (ticker, name) 元组列表（供日报/分析器使用）"""
    stocks = load_list(filepath)
    return [(s['ticker'], s['name']) for s in stocks]


def list_stocks(filepath=None):
    """打印关注列表"""
    stocks = load_list(filepath)
    print(f"\n📋 关注标的列表 ({len(stocks)}个)")
    print(f"{'─'*50}")
    for s in stocks:
        print(f"  {s['ticker']}  {s['name']}  ({s['category']})")
    print()
    return stocks


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='关注标的列表管理')
    sub = parser.add_subparsers(dest='cmd')
    
    p_list = sub.add_parser('list', help='查看列表')
    p_add = sub.add_parser('add', help='添加标的')
    p_add.add_argument('ticker', help='股票代码')
    p_add.add_argument('--name', '-n', required=True, help='股票名称')
    p_add.add_argument('--category', '-c', default='个股', help='分类')
    p_add.add_argument('--theme', '-t', default='', help='主题标签')
    p_add.add_argument('--sector', '-s', default='', help='行业板块')
    
    p_rm = sub.add_parser('remove', help='移除标的')
    p_rm.add_argument('ticker', help='股票代码')
    
    args = parser.parse_args()
    
    if args.cmd == 'add':
        add_stock(args.ticker, args.name, args.category, args.theme, args.sector)
        print(f"✅ 已添加 {args.name}({args.ticker})")
    elif args.cmd == 'remove':
        remove_stock(args.ticker)
        print(f"✅ 已移除 {args.ticker}")
    else:
        list_stocks()
