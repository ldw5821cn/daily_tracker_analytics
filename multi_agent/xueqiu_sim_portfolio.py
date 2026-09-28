#!/usr/bin/env python3
"""
雪球模拟仓 - 高股息防御组合
创建日期: 2026-09-28
初始资金: 50,000元 (模拟)

跟踪标的: 用户当前真实持仓 + 优化建议
"""

# 当前真实持仓（2026-09-28 截图）
CURRENT_HOLDINGS = [
    # 深市
    {'name': '平安银行', 'code': 'SZ000001', 'shares': 100, 'cost': 11.40, 'market': '深市'},
    {'name': '深圳能源', 'code': 'SZ000027', 'shares': 200, 'cost': 6.185, 'market': '深市'},
    {'name': '兴蓉环境', 'code': 'SZ000598', 'shares': 200, 'cost': 6.995, 'market': '深市'},
    {'name': '深红利',   'code': 'SZ159905', 'shares': 1100, 'cost': 1.532, 'market': '深市ETF'},
    # 沪市
    {'name': '华能国际', 'code': 'SH600011', 'shares': 300, 'cost': 6.920, 'market': '沪市'},
    {'name': '华电国际', 'code': 'SH600027', 'shares': 400, 'cost': 4.725, 'market': '沪市'},
    {'name': '浙能电力', 'code': 'SH600023', 'shares': 500, 'cost': 5.058, 'market': '沪市'},
    {'name': '申能股份', 'code': 'SH600642', 'shares': 200, 'cost': 8.505, 'market': '沪市'},
    {'name': '工商银行', 'code': 'SH601398', 'shares': 300, 'cost': 7.843, 'market': '沪市'},
]

# 优化建议加仓（3.4万增量资金）
OPTIMIZATION_PLAN = [
    {'name': '工商银行', 'code': 'SH601398', 'add_amount': 10000, 'reason': '已持仓盈利，股息率~5%'},
    {'name': '平安银行', 'code': 'SZ000001', 'add_amount': 10000, 'reason': 'PE 5倍破净，股息率~6%'},
    {'name': '格力电器', 'code': 'SZ000651', 'add_amount': 10000, 'reason': '家电白马，股息率~6%，分散电力风险'},
    {'name': '现金', 'code': 'CASH', 'add_amount': 4000, 'reason': '灵活补仓'},
]

# 雪球模拟仓配置
XUEQIU_SIM_CONFIG = {
    '组合名称': '高股息防御-长期跟踪',
    '初始资金': 50000,
    '创建日期': '2026-09-28',
    '策略': '高股息+低估值+行业分散',
    '跟踪频率': '每周',
    
    # 目标配置（优化后）
    'target_allocation': {
        '银行': {'weight': 0.30, 'stocks': ['工商银行', '平安银行']},
        '电力': {'weight': 0.25, 'stocks': ['华能国际', '华电国际', '浙能电力', '申能股份', '深圳能源']},
        '家电': {'weight': 0.15, 'stocks': ['格力电器']},
        '公用': {'weight': 0.10, 'stocks': ['兴蓉环境']},
        'ETF':  {'weight': 0.10, 'stocks': ['深红利']},
        '现金': {'weight': 0.10, 'stocks': []},
    },
    
    # 买入节奏
    'execution_plan': [
        {'date': '2026-09-28', 'action': '建仓', 'amount': 17000, 'note': '现有持仓平移到模拟仓'},
        {'date': '2026-10-第一周', 'action': '加仓工行', 'amount': 5000, 'note': '银行核心'},
        {'date': '2026-10-第二周', 'action': '加仓平安', 'amount': 5000, 'note': '破净修复'},
        {'date': '2026-10-第三周', 'action': '买格力', 'amount': 5000, 'note': '分散电力风险'},
        {'date': '2026-10-第四周', 'action': '买格力', 'amount': 5000, 'note': '完成目标配置'},
    ],
}

# 关键指标跟踪
TRACKING_METRICS = {
    '股息率': '每年分红/当前股价',
    '除息日': '确保持股拿到分红',
    'PE/PB': '估值监控',
    '行业分布': '电力不超过30%',
    '最大回撤': '控制在15%以内',
    '年度分红': '目标年化4-6%',
}


def generate_report():
    """生成跟踪报告"""
    print("=" * 70)
    print("📊 雪球模拟仓配置方案")
    print("=" * 70)
    
    print(f"\n【组合信息】")
    for k, v in XUEQIU_SIM_CONFIG.items():
        if isinstance(v, (str, int)):
            print(f"  {k}: {v}")
    
    print(f"\n【目标配置】")
    for sector, cfg in XUEQIU_SIM_CONFIG['target_allocation'].items():
        stocks = ', '.join(cfg['stocks']) if cfg['stocks'] else '无'
        print(f"  {sector}: {cfg['weight']*100:.0f}% ({stocks})")
    
    print(f"\n【执行计划】")
    for step in XUEQIU_SIM_CONFIG['execution_plan']:
        print(f"  {step['date']}: {step['action']} ({step['amount']}元) - {step['note']}")
    
    print(f"\n【跟踪指标】")
    for k, v in TRACKING_METRICS.items():
        print(f"  {k}: {v}")
    
    print("\n" + "=" * 70)
    print("💡 雪球操作步骤")
    print("=" * 70)
    print("""
1. 打开雪球APP → 模拟 → 创建组合
2. 组合名: 高股息防御-长期跟踪
3. 初始资金: 50,000元
4. 添加持仓（按当前真实持仓）:
   - 平安银行 100股 @11.40
   - 深圳能源 200股 @6.185
   - 兴蓉环境 200股 @6.995
   - 深红利   1100股 @1.532
   - 华能国际 300股 @6.92
   - 华电国际 400股 @4.725
   - 浙能电力 500股 @5.058
   - 申能股份 200股 @8.505
   - 工商银行 300股 @7.843
5. 按执行计划分批加仓
    """)
    
    print("=" * 70)


if __name__ == '__main__':
    generate_report()
