"""
AI 策略生成器 - 自然语言 → 可执行策略代码

用法:
  python ai_strategy_generator.py "连续3日放量上涨且RSI<60"
  自动生成策略函数并注册到 strategy_scanner.py
"""
import sys
import os
import re
import inspect
import textwrap

sys.path.insert(0, '/home/zhihu/daily_tracker_analytics/etf_tracker/multi_agent')

STRATEGY_DIR = os.path.dirname(os.path.abspath(__file__))
SCANNER_PATH = os.path.join(STRATEGY_DIR, 'strategy_scanner.py')


# ==================== 策略模板 ====================

STRATEGY_TEMPLATE = '''
def strategy_{id}(df, params=None):
    \"\"\"{name} - {description}\"\"\"
    if df is None or len(df) < {min_days}:
        return 0, []
    
    l = df.iloc[-1]
    reasons = []
    score = 0
    
{business_logic}
    
    return score, reasons
'''


# ==================== 内置策略构建器 ====================

def _build_from_description(description):
    """
    根据自然语言描述生成策略逻辑
    
    支持的模式:
    - "价格突破MA20" → 趋势突破
    - "RSI超卖" → 超卖信号
    - "MACD金叉" → 金叉信号
    - "放量" → 成交量条件
    - "连续N日上涨/下跌" → 动量条件
    - "均线多头排列" → 多头排列
    """
    desc = description.lower()
    conditions = []
    min_days = 25
    
    # 均线突破
    ma_match = re.search(r'(突破|站上|上穿)\s*(ma|ma|均线)?(\d+)', desc)
    if ma_match:
        ma_period = ma_match.group(3)
        conditions.append(f"""
    # {ma_match.group(0)}
    if float(l['ma{ma_period}']) > 0 and float(l['close']) > float(l['ma{ma_period}']):
        score += 25
        reasons.append(f"价格{{float(l['close']):.2f}}突破MA{ma_period}({{float(l['ma{ma_period}']):.2f}})")
""")
    
    # 均线跌破
    break_match = re.search(r'(跌破|下穿|低于)\s*(ma|ma|均线)?(\d+)', desc)
    if break_match:
        ma_period = break_match.group(3)
        conditions.append(f"""
    # {break_match.group(0)}（反向信号）
    if float(l['ma{ma_period}']) > 0 and float(l['close']) < float(l['ma{ma_period}']):
        score -= 15
        reasons.append(f"价格跌破MA{ma_period}({{float(l['ma{ma_period}']):.2f}})")
""")
    
    # RSI条件
    rsi_match = re.search(r'rsi[<_>]?(\d+)', desc)
    if not rsi_match:
        rsi_match = re.search(r'(超卖|超买)', desc)
    if rsi_match:
        if '超卖' in desc or ('rsi' in desc and '<' in desc):
            conditions.append(f"""
    # RSI超卖
    if float(l['rsi_14']) < 35:
        score += 25
        reasons.append(f"RSI({{float(l['rsi_14']):.0f}})超卖区")
""")
        elif '超买' in desc:
            conditions.append(f"""
    # RSI超买（反向信号）
    if float(l['rsi_14']) > 70:
        score -= 15
        reasons.append(f"RSI({{float(l['rsi_14']):.0f}})超买区")
""")
        else:
            conditions.append(f"""
    # RSI合理区间
    if 30 < float(l['rsi_14']) < 60:
        score += 10
        reasons.append(f"RSI({{float(l['rsi_14']):.0f}})合理区间")
""")
    
    # MACD条件
    if 'macd' in desc or '金叉' in desc:
        min_days = max(min_days, 35)
        conditions.append(f"""
    # MACD金叉/柱状
    if float(l['macd_hist']) > 0:
        score += 20
        reasons.append("MACD柱状翻红")
    elif len(df) >= 2:
        p = df.iloc[-2]
        if float(p['macd_dif']) <= float(p['macd_dea']) and float(l['macd_dif']) > float(l['macd_dea']):
            score += 15
            reasons.append("MACD金叉")
""")
    
    # 放量条件
    if '放量' in desc:
        conditions.append(f"""
    # 放量
    if float(l['vol_ratio']) > 1.3:
        score += 15
        reasons.append(f"放量{{float(l['vol_ratio']):.1f}}x")
""")
    elif '缩量' in desc:
        conditions.append(f"""
    # 缩量
    if float(l['vol_ratio']) < 0.7:
        score += 10
        reasons.append(f"缩量{{float(l['vol_ratio']):.1f}}x")
""")
    
    # 连续N日上涨/下跌
    consecutive_match = re.search(r'连续(\d+)日(上涨|下跌|涨|跌)', desc)
    if consecutive_match:
        days = int(consecutive_match.group(1))
        direction = consecutive_match.group(2)
        min_days = max(min_days, days + 10)
        is_up = '涨' in direction
        conditions.append(f"""
    # 连续{days}日{'上涨' if is_up else '下跌'}
    if len(df) >= {days + 1}:
        segment = df.iloc[-{days}:]
        all_{'up' if is_up else 'down'} = all(float(segment.iloc[i]['close']) > float(segment.iloc[i-1]['close'])
                                               for i in range(1, len(segment))) if {is_up} else \
                                          all(float(segment.iloc[i]['close']) < float(segment.iloc[i-1]['close'])
                                               for i in range(1, len(segment)))
        if all_{'up' if is_up else 'down'}:
            score += 20
            reasons.append(f"连续{days}日{'上涨' if is_up else '下跌'}")
""")
    
    # 均线多头排列
    if '多头' in desc or '多头排列' in desc:
        conditions.append(f"""
    # 均线多头排列
    if (pd.notna(l['ma5']) and pd.notna(l['ma10']) and pd.notna(l['ma20']) and
        float(l['ma5']) > float(l['ma10']) > float(l['ma20'])):
        score += 20
        reasons.append("均线多头排列")
""")
    
    # 布林带
    if '布林' in desc or 'boll' in desc:
        boll_pos = None
        if '下轨' in desc:
            conditions.append(f"""
    # 布林下轨支撑
    if float(l['close']) <= float(l['boll_down']) * 1.03:
        score += 20
        reasons.append(f"接近布林下轨({{float(l['boll_down']):.2f}})")
""")
        elif '上轨' in desc:
            conditions.append(f"""
    # 布林上轨压力
    if float(l['close']) >= float(l['boll_up']) * 0.97:
        score -= 10
        reasons.append(f"触及布林上轨({{float(l['boll_up']):.2f}})")
""")
        else:
            conditions.append(f"""
    # 布林中轨支撑
    if float(l['close']) > float(l['boll_mid']):
        score += 10
        reasons.append("价格在布林中轨上方")
""")
    
    # 动量（N日涨幅）
    momentum_match = re.search(r'(\d+)日(?:涨幅|涨|下跌|跌).*?(\d+)%', desc)
    if momentum_match:
        days_m = momentum_match.group(1)
        pct_m = float(momentum_match.group(2))
        conditions.append(f"""
    # {days_m}日动量
    mom = float(l['momentum_{days_m}d']) if pd.notna(l['momentum_{days_m}d']) else 0
    if mom > {pct_m}:
        score += 20
        reasons.append(f"{days_m}日涨幅{{mom:+.1f}}%")
""")
    
    # 默认保底条件（如果什么都没匹配到）
    if not conditions:
        conditions.append(f"""
    # 价格相对MA20位置
    if pd.notna(l['ma20']) and float(l['close']) > float(l['ma20']):
        score += 15
        reasons.append(f"价格在MA20上方")
    if pd.notna(l['rsi_14']) and 30 < float(l['rsi_14']) < 60:
        score += 10
        reasons.append(f"RSI合理")
""")
    
    business_logic = "\n".join(conditions)
    return business_logic, min_days


def generate_strategy(description, name=None):
    """
    从自然语言描述生成策略代码
    
    Args:
        description: 策略描述（如"连续3日放量上涨且RSI<60"）
        name: 策略名称（可选，默认从描述提取）
    
    Returns:
        dict: 生成的代码、策略ID等
    """
    if name is None:
        # 从描述提取简短名称
        name = description[:20].strip()
    
    # 生成ID
    strategy_id = re.sub(r'[^a-z0-9_]', '_', description.lower().replace(' ', '_'))[:40]
    strategy_id = re.sub(r'_+', '_', strategy_id).strip('_')
    
    # 构建业务逻辑
    business_logic, min_days = _build_from_description(description)
    
    # 生成代码
    code = STRATEGY_TEMPLATE.format(
        id=strategy_id,
        name=name,
        description=description,
        min_days=min_days,
        business_logic=business_logic,
    )
    
    return {
        'id': strategy_id,
        'name': name,
        'description': description,
        'min_days': min_days,
        'code': code.strip(),
    }


def register_strategy(strategy_dict, scanner_path=None):
    """
    将生成的策略注册到 strategy_scanner.py
    
    Args:
        strategy_dict: generate_strategy() 的返回值
        scanner_path: strategy_scanner.py 路径（默认自动）
    
    Returns:
        bool: 是否注册成功
    """
    path = scanner_path or SCANNER_PATH
    
    if not os.path.exists(path):
        print(f"❌ 找不到 {path}")
        return False
    
    sid = strategy_dict['id']
    
    # 1. 检查是否已存在同名策略
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    if f"def strategy_{sid}(" in content:
        print(f"⚠️ 策略 '{strategy_dict['name']}' 已存在，跳过注册")
        return True
    
    # 2. 在STRATEGIES = [ 之前插入策略函数
    insert_marker = "# 所有策略注册表"
    if insert_marker not in content:
        print("❌ 找不到注册表标记")
        return False
    
    # 把代码缩进修正
    code_lines = strategy_dict['code'].split('\n')
    code_block = '\n'.join(code_lines)
    
    new_content = content.replace(
        f"\n\n# 所有策略注册表",
        f"\n\n{code_block}\n\n\n# 所有策略注册表"
    )
    
    # 3. 在STRATEGIES列表中添加引用
    register_line = f'    {{"id": "{sid}", "name": "{strategy_dict["name"]}", "fn": strategy_{sid}}},'
    
    # 找到第一个策略注册项的位置，在其前插入
    strategies_marker = 'STRATEGIES = ['
    if strategies_marker in new_content:
        # 在最后一个策略后面插入
        last_strategy = None
        lines = new_content.split('\n')
        new_lines = []
        inserted = False
        for i, line in enumerate(lines):
            new_lines.append(line)
            if not inserted and line.strip().startswith(']') and 'STRATEGIES' not in line:
                # 在 ] 之前插入
                new_lines.insert(-1, register_line)
                inserted = True
        
        if inserted:
            new_content = '\n'.join(new_lines)
    
    # 4. 写回文件
    with open(path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    
    print(f"✅ 策略 '{strategy_dict['name']}' 已注册到策略扫描器")
    return True


def generate_and_register(description, name=None, scanner_path=None):
    """
    一句话生成并注册策略
    
    Args:
        description: 策略描述
        name: 策略名称
    
    Returns:
        dict: 策略信息
    """
    print(f"🧠 AI 策略生成: 「{description}」")
    print(f"{'─'*50}")
    
    strategy = generate_strategy(description, name)
    
    print(f"📋 策略ID: {strategy['id']}")
    print(f"📋 最小数据天数: {strategy['min_days']}")
    print(f"\n📝 生成的代码:")
    print(f"{'─'*50}")
    print(strategy['code'])
    print(f"{'─'*50}")
    
    ok = register_strategy(strategy, scanner_path)
    
    if ok:
        print(f"\n🚀 现在运行 python quant_agent.py scan 即可看到新策略")
    
    return strategy


def list_ai_strategies():
    """列出所有已注册的策略"""
    from analysts.strategy_scanner import STRATEGIES
    print(f"\n📋 已注册策略 ({len(STRATEGIES)}个)")
    print(f"{'─'*50}")
    for s in STRATEGIES:
        fn = s['fn']
        doc = fn.__doc__ or '无描述'
        print(f"  {s['id']:30s} {s['name']}")
        print(f"  {'':30s} {doc[:60]}")
        print()
    return STRATEGIES


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='AI策略生成器')
    parser.add_argument('description', nargs='?', help='策略描述（如"连续3日放量上涨且RSI<60"）')
    parser.add_argument('--name', '-n', help='策略名称')
    parser.add_argument('--list', '-l', action='store_true', help='列出所有策略')
    
    args = parser.parse_args()
    
    if args.list:
        list_ai_strategies()
    elif args.description:
        generate_and_register(args.description, args.name)
    else:
        # 演示模式
        print("🎯 AI 策略生成器 - 演示\n")
        
        examples = [
            ("连续3日放量上涨且RSI<60", "放量三连阳"),
            ("MACD金叉且站上MA20", "金叉突破"),
            ("RSI超卖且接近布林下轨", "超卖反弹"),
        ]
        
        for desc, name in examples:
            print(f"\n{'='*60}")
            generate_and_register(desc, name)
            print(f"{'='*60}")
            print()
