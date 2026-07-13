#!/usr/bin/env python3
import urllib.request, json, re, os
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

codes = [
    ('sh516150', '稀土ETF嘉实', '稀土永磁'),
    ('sz512800', '银行ETF', '银行'),
    ('sh159928', '消费ETF', '消费'),
    ('sz512760', '芯片ETF', '半导体'),
    ('sz159819', '人工智能ETF', '人工智能'),
    ('sh513180', '恒生科技ETF华夏', '港股科技'),
    ('sz512480', '半导体ETF', '半导体'),
    ('sh159516', '半导体设备ETF国泰', '半导体设备'),
    ('sh588200', '科创芯片ETF嘉实', '芯片制造'),
    ('sz512000', '券商ETF华宝', '证券'),
    ('sh512880', '证券ETF国泰', '证券'),
    ('sz512170', '医疗ETF华宝', '医疗器械'),
    ('sz510300', '沪深300ETF华泰柏瑞', '大盘蓝筹'),
    ('sh562500', '机器人ETF华夏', '机器人'),
    ('sz159530', '机器人ETF易方达', '机器人'),
    ('sz159792', '港股通互联网ETF富国', '互联网'),
    ('sz159755', '电池ETF广发', '动力电池'),
    ('sz512890', '红利低波ETF华泰柏瑞', '高股息'),
    ('sh563180', '高股息ETF', '高股息'),
    ('sz512400', '有色金属ETF南方', '有色金属'),
    ('sh513120', '港股创新药ETF广发', '创新药'),
    ('sh159995', '芯片ETF华夏', '芯片设计'),
    ('sz515880', '通信ETF国泰', '通信')
]

def fetch_history(code, days=120):
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,{days},qfq"
    req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
    data = urllib.request.urlopen(req, timeout=20).read().decode('utf-8')
    j = json.loads(data)
    data_key = code
    if data_key not in j['data']:
        for k in j['data'].keys():
            if k.endswith('sh') or k.endswith('sz'):
                data_key = k
                break
    arr = j['data'][data_key].get('qfqday') or j['data'][data_key].get('day', [])
    df = []
    for d in arr:
        try:
            v = int(float(d[5]))
        except:
            v = 0
        df.append({'date':d[0], 'open':float(d[1]), 'close':float(d[2]), 'low':float(d[3]), 'high':float(d[4]), 'volume':v})
    return pd.DataFrame(df)

def fetch_spot(codes_list):
    url = 'https://qt.gtimg.cn/q=' + ','.join(codes_list)
    req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
    data = urllib.request.urlopen(req, timeout=15).read().decode('gbk', errors='ignore')
    rows = {}
    for line in data.strip().split(';'):
        line = line.strip()
        if not line:
            continue
        m = re.match(r'v_(\w+)="(.*)"', line)
        if not m:
            continue
        code, fields = m.group(1), m.group(2).split('~')
        rows[code] = {
            'name': fields[1],
            'price': float(fields[3]),
            'pre_close': float(fields[4]),
            'open': float(fields[5]),
            'high': float(fields[33]) if fields[33] else float(fields[3]),
            'low': float(fields[34]) if fields[34] else float(fields[3]),
            'vol': int(fields[36]) if fields[36] else 0,
            'change_pct': float(fields[32]) if fields[32] else 0
        }
    return rows

def main():
    histories = {}
    for code, name, sector in codes:
        try:
            df = fetch_history(code, 120)
            if len(df) >= 20:
                histories[code] = df
                print(f"OK {code} {name}: {len(df)} days")
            else:
                print(f"LOW {code} {name}: {len(df)} days")
        except Exception as e:
            print(f"ERR {code} {name}: {e}")

    print(f"\n成功获取 {len(histories)}/{len(codes)} 只ETF历史数据")

    code_list = [c[0] for c in codes]
    spots = fetch_spot(code_list)
    print(f"成功获取 {len(spots)}/{len(codes)} 只ETF实时数据")

    results = []
    for code, name, sector in codes:
        if code not in histories:
            continue
        df = histories[code].copy()
        last_hist_date = df['date'].iloc[-1]
        today_str = datetime.now().strftime('%Y-%m-%d')
        spot = spots.get(code, {})
        if spot and last_hist_date < today_str:
            df = pd.concat([df, pd.DataFrame([{
                'date': today_str,
                'open': spot['open'],
                'close': spot['price'],
                'high': spot['high'],
                'low': spot['low'],
                'volume': spot['vol']
            }])], ignore_index=True)
        df['ma20'] = df['close'].rolling(20).mean()
        df['ma60'] = df['close'].rolling(60).mean()
        last = df.iloc[-1]
        prev = df.iloc[-2]
        chg_1d = (last['close'] - prev['close']) / prev['close'] * 100
        chg_5d = (last['close'] - df.iloc[-6]['close']) / df.iloc[-6]['close'] * 100 if len(df) >= 6 else 0
        chg_20d = (last['close'] - df.iloc[-21]['close']) / df.iloc[-21]['close'] * 100 if len(df) >= 21 else 0
        high_120 = df['high'].tail(120).max()
        low_120 = df['low'].tail(120).min()
        results.append({
            'code': code, 'name': name, 'sector': sector,
            'close': last['close'], 'date': last['date'],
            'chg_1d': chg_1d, 'chg_5d': chg_5d, 'chg_20d': chg_20d,
            'ma20': last['ma20'], 'ma20_pct': (last['close']-last['ma20'])/last['ma20']*100,
            'ma60': last['ma60'], 'ma60_pct': (last['close']-last['ma60'])/last['ma60']*100,
            'high_120d': high_120, 'low_120d': low_120,
            'volume': int(last['volume'])
        })

    results_df = pd.DataFrame(results)
    results_df = results_df.sort_values('chg_20d', ascending=False).reset_index(drop=True)
    print(results_df[['name','close','chg_1d','chg_5d','chg_20d','ma20_pct']].to_string(index=False))

    reports_dir = '/home/liudawei/github/daily_tracker_analytics/etf_tracker/reports'
    out_json = f"etf_tencent_data_{datetime.now().strftime('%Y%m%d')}.json"
    out_md = f"multi_etf_report_{datetime.now().strftime('%Y%m%d')}.md"
    out_summary = f"wechat_summary_{datetime.now().strftime('%Y%m%d')}.txt"

    sector_groups = results_df.groupby('sector').agg({
        'chg_20d': 'mean',
        'chg_5d': 'mean',
        'chg_1d': 'mean',
        'name': lambda x: ', '.join(x)
    }).reset_index()
    sector_groups.columns = ['sector','avg_20d','avg_5d','avg_1d','etfs']
    sector_groups = sector_groups.sort_values('avg_20d', ascending=False).reset_index(drop=True)
    print('\n板块排名')
    print(sector_groups.to_string(index=False))

    lines = []
    lines.append("# 多板块 ETF 每日投资规划报告")
    lines.append(f"**日期**: {datetime.now().strftime('%Y-%m-%d')}")
    lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("**数据来源**: 腾讯财经 (Tencent Finance API)")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 一、市场概览")
    lines.append("")
    lines.append(f"成功获取 {len(results_df)}/{len(codes)} 只 ETF 数据。")
    lines.append("")
    lines.append("### 近20日板块排名（按收益率排序）")
    lines.append("")
    lines.append("| 排名 | ETF名称 | 代码 | 收盘价 | 1日涨幅 | 5日涨幅 | 20日涨幅 | 距MA20 |")
    lines.append("|:---:|:---------|:---:|:------:|:------:|:------:|:-------:|:------:|")
    for i, r in results_df.iterrows():
        lines.append(f"| {i+1} | {r['name']} | {r['code']} | {r['close']:.3f} | {r['chg_1d']:+.2f}% | {r['chg_5d']:+.2f}% | {r['chg_20d']:+.2f}% | {r['ma20_pct']:+.2f}% |")
    lines.append("")
    lines.append("### 板块综合分析")
    lines.append("")
    lines.append("| 板块 | 平均20日收益 | ETF数量 | 包含ETF |")
    lines.append("|:----:|:----------:|:-------:|:---------|")
    for i, r in sector_groups.iterrows():
        count = len(r['etfs'].split(','))
        lines.append(f"| {r['sector']} | {r['avg_20d']:+.2f}% | {count} | {r['etfs']} |")
    lines.append("")
    lines.append("## 二、各ETF详情")
    lines.append("")
    for i, r in results_df.iterrows():
        lines.append(f"### {r['name']} ({r['code']})")
        lines.append("")
        lines.append(f"- **板块**: {r['sector']}")
        lines.append(f"- **最新收盘**: {r['close']:.3f}（日期: {r['date']}）")
        lines.append(f"- **1日涨幅**: {r['chg_1d']:+.2f}%")
        lines.append(f"- **5日涨幅**: {r['chg_5d']:+.2f}%")
        lines.append(f"- **20日涨幅**: {r['chg_20d']:+.2f}%")
        lines.append(f"- **20日均线**: {r['ma20']:.3f}（偏离: {r['ma20_pct']:+.2f}%）")
        lines.append(f"- **60日均线**: {r['ma60']:.3f}（偏离: {r['ma60_pct']:+.2f}%）")
        lines.append(f"- **120日最高**: {r['high_120d']:.3f}")
        lines.append(f"- **120日最低**: {r['low_120d']:.3f}")
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"*报告由 Hermes Agent 自动生成 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")

    md_content = '\n'.join(lines)
    with open(os.path.join(reports_dir, out_md), 'w', encoding='utf-8') as f:
        f.write(md_content)
    print(f"\n报告已保存: reports/{out_md}")

    json_data = {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'date': datetime.now().strftime('%Y%m%d'),
        'total_etfs': len(codes),
        'success_count': len(results_df),
        'etfs': {r['code'][2:]: r for r in results}
    }
    with open(os.path.join(reports_dir, out_json), 'w', encoding='utf-8') as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)
    print(f"数据已保存: reports/{out_json}")

    up_count_1d = (results_df['chg_1d'] > 0).sum()
    down_count_1d = (results_df['chg_1d'] < 0).sum()
    up_count_20d = (results_df['chg_20d'] > 0).sum()
    strong_sectors = sector_groups.head(3)['sector'].tolist()
    weak_sectors = sector_groups.tail(3)['sector'].tolist()
    top3 = results_df.head(3)
    bottom3 = results_df.tail(3)

    weekday_cn = ['周一','周二','周三','周四','周五','周六','周日'][datetime.now().weekday()]
    summary = f"""📊 多板块 ETF 每日投资规划报告

📅 {datetime.now().strftime('%Y-%m-%d')}（{weekday_cn}）| ↗️ 震荡

━━━━━━━━━━━━━━━━━━

【大盘温度计】
• 覆盖 {len(results_df)}/{len(codes)} 只ETF
• 近20日均涨跌: {results_df['chg_20d'].mean():+.2f}%（上涨{up_count_20d}家/下跌{len(results_df)-up_count_20d}家）
• 近1日均涨跌: {results_df['chg_1d'].mean():+.2f}%（上涨{up_count_1d}家/下跌{down_count_1d}家）

━━━━━━━━━━━━━━━━━━

【板块排名（近20日）】
"""
    for i, r in sector_groups.iterrows():
        if r['avg_20d'] >= 0:
            bar = '  ' + 'G' * max(1, int(min(abs(r['avg_20d']), 50)/5))
        else:
            bar = 'R' * max(1, int(min(abs(r['avg_20d']), 50)/5))
        summary += f"  {bar} {r['sector']}: {r['avg_20d']:+.2f}%\n"

    summary += """
━━━━━━━━━━━━━━━━━━

【TOP 3 强势ETF】
"""
    for i, r in top3.iterrows():
        summary += f"  {i+1}. {r['name']}  {r['close']:.3f}  20d:{r['chg_20d']:+.2f}%  1d:{r['chg_1d']:+.2f}%\n"

    summary += """
【BOTTOM 3 弱势ETF】
"""
    for i, r in bottom3.iterrows():
        summary += f"  {i+1}. {r['name']}  {r['close']:.3f}  20d:{r['chg_20d']:+.2f}%  1d:{r['chg_1d']:+.2f}%\n"

    summary += """
━━━━━━━━━━━━━━━━━━

【盘面观察】
"""
    if '半导体设备' in strong_sectors or '芯片制造' in strong_sectors or '半导体' in strong_sectors:
        summary += "  • 半导体/芯片板块持续强势，资金聚焦硬科技主线\n"
    if '银行' in weak_sectors or '高股息' in weak_sectors:
        summary += "  • 防御板块（银行/高股息）走弱，市场风险偏好回升\n"
    if '稀土永磁' in weak_sectors or '有色金属' in weak_sectors:
        summary += "  • 周期资源板块（稀土/有色）承压，需关注上游价格\n"

    summary += f"""
━━━━━━━━━━━━━━━━━━

⚡ 操作建议：结构性行情延续，重点关注{strong_sectors[0]}、{strong_sectors[1]}等强势板块；对{weak_sectors[-1]}、{weak_sectors[-2]}等弱势板块保持谨慎。仓位建议5-6成。

📈 数据日期: {results_df['date'].iloc[0]}
🤖 Hermes Agent 自动生成 | {datetime.now().strftime('%m/%d %H:%M')}
"""

    with open(os.path.join(reports_dir, out_summary), 'w', encoding='utf-8') as f:
        f.write(summary)
    print(f"摘要已保存: reports/{out_summary}")
    print(f"摘要字数: {len(summary)}")
    print('\n摘要内容：')
    print(summary)

if __name__ == '__main__':
    main()
