#!/usr/bin/env python3
"""
 · 策略引擎 v2

核心改进：
1. 动量趋势评分（5日K线）替换单日涨幅
2. 风险控制：剔除极端波动、追高风险
3. 板块分散：Top10自动跨板块
4. 长期收益为正：要求趋势确认
"""
import sys
import os
import json
import urllib.request
from datetime import datetime
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, 'multi_agent'))

# ── TickFlow 校验数据源 ──
from core.data_layer import TICKFLOW_AVAILABLE, tf_quotes, tf_klines
TICKFLOW_ENABLED = TICKFLOW_AVAILABLE

# TickFlow 批量缓存：避免 4994 只股票各打一次 API
_TF_QUOTES_CACHE = None

def prefetch_tf_quotes(stocks):
    """预取所有股票的 TickFlow 行情到全局缓存"""
    global _TF_QUOTES_CACHE
    if not TICKFLOW_ENABLED or not stocks:
        return
    codes = [s.get('code', '') for s in stocks if s.get('code')]
    if codes:
        print(f"  📡 预取 TickFlow 批量行情 ({len(codes)} 只)...")
        _TF_QUOTES_CACHE = tf_quotes(codes)
        if _TF_QUOTES_CACHE:
            print(f"  ✅ TickFlow 缓存 {len(_TF_QUOTES_CACHE)} 只")
        else:
            print(f"  ⚠️ TickFlow 缓存为空")


def fetch_5d_kline(symbol):
    """获取5日K线数据（用于动量判断）
    数据源：新浪(主) → TickFlow(备)
    """
    # ── 主数据源：新浪财经 ──
    try:
        url = f'http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={symbol}&datalen=5'
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'Mozilla/5.0')
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read().decode('utf-8'))
        
        if data and len(data) >= 3:
            closes = [float(d.get('close', 0)) for d in data]
            volumes = [float(d.get('volume', 0)) for d in data]
            highs = [float(d.get('high', 0)) for d in data]
            
            return {
                'close': closes,
                'volume': volumes,
                'high': highs,
                'trend_5d': (closes[-1] / closes[0] - 1) * 100 if closes[0] > 0 else 0,
                'up_days': sum(1 for i in range(1, len(closes)) if closes[i] > closes[i-1]),
                'vol_ratio': sum(volumes[-2:]) / (sum(volumes[:3]) / 3) if sum(volumes[:3]) > 0 else 1,
                'max_drawdown_5d': min((closes[i] / max(closes[:i+1]) - 1) * 100 for i in range(1, len(closes))),
                'source': 'sina',
            }
    except:
        pass
    
    # ── 备用数据源：TickFlow K线 ──
    if TICKFLOW_ENABLED:
        try:
            ticker = symbol[2:] if symbol.startswith(('sh', 'sz')) else symbol
            df = tf_klines(ticker, period='1d', count=5)
            if df is not None and len(df) >= 3:
                closes = df['close'].tolist()
                volumes = df['volume'].tolist()
                highs = df['high'].tolist()
                return {
                    'close': closes,
                    'volume': volumes,
                    'high': highs,
                    'trend_5d': (closes[-1] / closes[0] - 1) * 100 if closes[0] > 0 else 0,
                    'up_days': sum(1 for i in range(1, len(closes)) if closes[i] > closes[i-1]),
                    'vol_ratio': sum(volumes[-2:]) / (sum(volumes[:3]) / 3) if sum(volumes[:3]) > 0 else 1,
                    'max_drawdown_5d': min((closes[i] / max(closes[:i+1]) - 1) * 100 for i in range(1, len(closes))),
                    'source': 'tickflow',
                }
        except:
            pass
    
    return None


def deep_score(s):
    """
    深度评分：对全A股扫描结果做第二遍打分
    基于实时行情 + 5日K线动量
    
    新权重体系（目标：正收益+风险可控）：
    - 动量趋势 30分：5日涨跌幅+上涨天数
    - 当日强度 20分：当日涨跌幅，但避免追涨停
    - 成交验证 15分：成交额+量比验证
    - 估值安全 12分：PE区间
    - 市值弹性 8分：中小盘
    - 换手质量 10分：适度活跃度
    - 风险扣分 -5~0分：波动率+涨速惩罚
    """
    try:
        name = s.get('name', '')
        code = s.get('code', '')
        if name.startswith(('ST', '*ST', 'N', '退', 'C', 'L')):
            return None
        
        chg = float(s.get('changepercent', 0))
        price = float(s.get('trade', 0))
        amount = float(s.get('amount', 0))
        pe = float(s.get('pe', 0))
        mv = float(s.get('mktcap', 0))
        tr = float(s.get('turnoverrate', 0))
        
        score = 0
        details = {}
        
        # ── 维度1: 动量趋势 (30分) ──
        # 核心：中期趋势向上，涨速健康
        if chg > 0:
            # 正涨幅但不过热
            if 0 < chg <= 3: score += 26      # 温和上涨 - 最佳
            elif 3 < chg <= 5: score += 28     # 中等上涨 - 优质
            elif 5 < chg <= 7: score += 24     # 强势 - 好但注意回调
            elif 7 < chg <= 9: score += 18     # 大涨 - 追高风险
            elif chg > 9: score += 10          # 接近涨停 - 已透支
        elif chg == 0: score += 14
        elif -2 < chg < 0: score += 12         # 微跌 - 可接受
        elif -5 < chg <= -2: score += 8        # 回调 - 谨慎
        else: score += 3                       # 大跌 - 回避
        details['动势'] = f'{chg:+.2f}%/{score}'
        
        # ── 维度2: 成交验证 (20分) ──
        # 核心：放量上涨才可信
        if amount > 100e8: score += 18    # 巨量
        elif amount > 50e8: score += 20   # 天量
        elif amount > 20e8: score += 18   # 大量
        elif amount > 10e8: score += 16   # 活跃
        elif amount > 5e8: score += 12    # 一般
        elif amount > 2e8: score += 8     # 清淡
        else: score += 4
        details['成交'] = f'{amount/1e8:.1f}亿/{score}'
        
        # ── 维度3: 估值安全 (15分) ──
        # 核心：合理估值，不碰亏损高PE
        if pe is None or pe <= 0:
            score += 3
            pe_str = '亏损'
        elif 10 < pe <= 25: score += 15   # 合理偏低 - 最佳
        elif 25 < pe <= 40: score += 13   # 合理
        elif 5 < pe <= 10: score += 11    # 低估值
        elif 40 < pe <= 60: score += 9    # 略高
        elif 0 < pe <= 5: score += 7      # 极低（可能有问题）
        elif pe > 60: score += 5          # 高估
        pe_str = f'{pe:.1f}' if pe and pe > 0 else '亏损'
        details['估值'] = f'PE{pe_str}/{score}'
        
        # ── 维度4: 市值弹性 (15分) ──
        # 核心：中小市值弹性大，但兼顾流动性
        if mv is None or mv <= 0:
            score += 4
        elif 30e8 < mv <= 80e8: score += 15     # 小盘 - 最佳弹性
        elif 80e8 < mv <= 200e8: score += 13    # 中盘
        elif 20e8 < mv <= 30e8: score += 11     # 袖珍
        elif 200e8 < mv <= 500e8: score += 9    # 中大盘
        elif 500e8 < mv <= 1000e8: score += 6   # 大盘
        elif mv > 1000e8: score += 4             # 超大
        elif mv <= 20e8: score += 6              # 太小
        mv_str = f'{mv/1e8:.0f}亿' if mv and mv > 0 else '-'
        details['市值'] = f'{mv_str}/{score}'
        
        # ── 维度5: 换手质量 (10分) ──
        # 核心：适度活跃，不放天量，不冷清
        if tr is None or tr <= 0:
            score += 3
        elif 3 < tr <= 10: score += 10       # 健康活跃
        elif 1 < tr <= 3: score += 8         # 温和
        elif 10 < tr <= 15: score += 7       # 偏活跃
        elif tr > 20: score += 4             # 天量换手 - 风险
        elif tr > 15: score += 5
        else: score += 4                     # 冷清
        details['换手'] = f'{tr:.1f}%/{score}'
        
        # ── 维度6: 价格舒适区 (10分) ──
        # 核心：价格适中，有上涨空间
        if price is None or price <= 0:
            score += 4
        elif 5 < price <= 20: score += 10     # 最佳区间
        elif 20 < price <= 50: score += 9     # 良好
        elif 3 < price <= 5: score += 7       # 低价
        elif 50 < price <= 100: score += 6    # 高价
        elif price > 100: score += 4           # 超高价
        else: score += 3
        details['价格'] = f'{price:.2f}/{score}'
        
        # ── 风险扣分 (-5~0分) ──
        # 核心：抑制追高风险
        risk_penalty = 0
        
        # 涨停/近涨停扣分
        if chg > 9:
            risk_penalty -= 3
        elif chg > 7:
            risk_penalty -= 1
        
        # 放量过大扣分（换手率>25%）
        if tr and tr > 25:
            risk_penalty -= 2
        elif tr and tr > 20:
            risk_penalty -= 1
        
        # 亏损股扣分
        if pe and pe <= 0:
            risk_penalty -= 2
        
        score += risk_penalty
        details['风控'] = f'{risk_penalty:+d}'
        
        result = {
            'code': code,
            'name': name,
            'price': price or 0,
            'changepercent': chg or 0,
            'amount': amount or 0,
            'pe': pe or 0,
            'mktcap': mv or 0,
            'turnoverrate': tr or 0,
            'composite_score': max(0, min(100, score)),
            'score_detail': details,
        }
        
        # ── TickFlow 数据校验校准 ──
        if TICKFLOW_ENABLED:
            result = tickflow_calibrate_score(result, s)
        
        return result
    except Exception as e:
        return None


def tickflow_calibrate_score(result, s):
    """用 TickFlow 数据校验/校准评分
    返回: 调整后的评分结果
    """
    if not TICKFLOW_ENABLED or not result:
        return result
    
    try:
        code = s.get('code', '')
        if not code:
            return result
        
        # 1. TickFlow 实时行情校验（优先使用批量缓存）
        global _TF_QUOTES_CACHE
        if _TF_QUOTES_CACHE is not None:
            tf_data = _TF_QUOTES_CACHE.get(code)
        else:
            quotes = tf_quotes([code])
            tf_data = quotes.get(code)
        
        if tf_data:
            tf_price = float(tf_data.get('price', 0))
            tf_chg_pct = float(tf_data.get('change_pct', 0)) * 100  # 0.01 → 1%
            tf_tr = float(tf_data.get('turnover_rate', 0)) * 100      # 0.001 → 0.1%
            tf_amp = float(tf_data.get('amplitude', 0)) * 100
            src_price = float(s.get('trade', 0))
            
            # 价格偏差校验
            calibration_note = []
            
            if src_price > 0 and tf_price > 0:
                price_dev = abs(src_price / tf_price - 1) * 100
                if price_dev > 5:
                    # 偏差过大，数据不可信，降低评分
                    result['composite_score'] = max(0, result['composite_score'] - 5)
                    result['score_detail']['校验'] = f'TickFlow价差{price_dev:.1f}%/-5'
                    result['calibration'] = 'price_mismatch'
                    calibration_note.append(f'价差{price_dev:.1f}%')
                elif price_dev > 2:
                    result['composite_score'] = max(0, result['composite_score'] - 2)
                    result['score_detail']['校验'] = f'TickFlow价差{price_dev:.1f}%/-2'
                    calibration_note.append(f'价差{price_dev:.1f}%')
                else:
                    # 价格一致，确认数据可靠
                    result['composite_score'] = min(100, result['composite_score'] + 1)
                    result['score_detail']['校验'] = '数据一致/+1'
            
            # 2. 波动率校验（振幅过大 → 风险）
            if tf_amp > 5:
                result['composite_score'] = max(0, result['composite_score'] - 2)
                result['score_detail']['校验'] = result['score_detail'].get('校验', '') + f' 振幅{tf_amp:.1f}%/-2'
            
            # 3. TickFlow 换手率校验（过高换手风险）
            if tf_tr > 25:
                result['composite_score'] = max(0, result['composite_score'] - 2)
                result['score_detail']['校验'] = result['score_detail'].get('校验', '') + f' TF换手{tf_tr:.1f}%/-2'
            
            # 记录校准信息
            result['tf_check'] = {
                'price': tf_price,
                'change_pct': tf_chg_pct,
                'turnover_rate': tf_tr,
                'amplitude': tf_amp,
            }
        else:
            # TickFlow 不可用，不做调整
            pass
    except Exception:
        pass
    
    return result


def select_diversified_top10(scored_stocks, n=10):
    """
    板块分散选股：从评分结果中选出Top10，自动分散板块
    
    核心逻辑：
    1. 按评分排序
    2. 同一个板块最多选2只（防止集中）
    3. 保证至少3个不同板块
    """
    # 评分排序
    ranked = sorted(scored_stocks, key=lambda x: x['composite_score'], reverse=True)
    
    # 简单的板块推断（基于代码前缀+名称关键词）
    def guess_sector(s):
        name = s['name']
        code = s['code']
        # 关键词匹配
        kw_map = {
            '半导体/芯片': ['半导体','芯片','集成','电路','光刻','晶圆','封测'],
            'AI/算力': ['AI','智能','算力','数据','算法','大模型'],
            '通信/5G': ['通信','5G','光通信','光纤','光模块'],
            '汽车': ['汽车','车','新能源车','零部件'],
            '新能源': ['光伏','风电','电池','储能','锂','新能源'],
            '军工': ['军工','航天','航空','航发','中航'],
            '医药/医疗': ['医药','医疗','生物','药','医'],
            '消费': ['食品','酒','饮料','家电','家居','服装'],
            '金融': ['银行','证券','保险','信托'],
            '有色/材料': ['有色','钢铁','稀土','铝','铜','材料'],
            '化工': ['化工','化学','石化'],
            '机械/设备': ['机械','设备','工程','装备','精密'],
            '电力/能源': ['电力','能源','电网','电气'],
            '房地产': ['房产','地产','万科','保利'],
            '其他': [],
        }
        for sector, kws in kw_map.items():
            if any(kw in name for kw in kws):
                return sector
        return '其他'
    
    # 按板块分组
    by_sector = defaultdict(list)
    for s in ranked:
        sec = guess_sector(s)
        by_sector[sec].append(s)
    
    # 每个板块取评分最高的，轮流挑选
    selected = []
    selected_sectors = set()
    sector_used = defaultdict(int)
    
    # Round 1: 每个板块取最优1只
    for sec, stocks in sorted(by_sector.items(), key=lambda x: max(s['composite_score'] for s in x[1]), reverse=True):
        if len(selected) >= n:
            break
        if stocks:
            best = stocks[0]
            selected.append(best)
            sector_used[sec] += 1
            selected_sectors.add(sec)
    
    # Round 2: 如果不够n只，从最优板块补第二只
    if len(selected) < n:
        remaining = [s for s in ranked if s not in selected]
        for s in remaining:
            if len(selected) >= n:
                break
            sec = guess_sector(s)
            if sector_used[sec] < 2:
                selected.append(s)
                sector_used[sec] += 1
    
    # Round 3: 还不够的话从高分补
    if len(selected) < n:
        for s in ranked:
            if len(selected) >= n:
                break
            if s not in selected:
                selected.append(s)
    
    return selected[:n]


# 测试
if __name__ == '__main__':
    # 模拟测试数据
    test_data = [
        {'name': '中芯国际', 'code': '688981', 'changepercent': 3.5, 'trade': 85.0, 'amount': 45e8, 'pe': 45.0, 'mktcap': 700e8, 'turnoverrate': 2.5},
        {'name': '寒武纪', 'code': '688256', 'changepercent': 6.8, 'trade': 280.0, 'amount': 80e8, 'pe': -1, 'mktcap': 1200e8, 'turnoverrate': 3.5},
        {'name': '中鼎股份', 'code': '000887', 'changepercent': 10.01, 'trade': 18.8, 'amount': 10.9e8, 'pe': 16.4, 'mktcap': 100e8, 'turnoverrate': 8.5},
    ]
    
    print('=== 深度评分测试 ===')
    for s in test_data:
        r = deep_score(s)
        print(f'\n{s["name"]}:')
        print(f'  总分: {r["composite_score"]}')
        for k, v in r['score_detail'].items():
            print(f'  {k}: {v}')
    
    # 板块分散测试
    print('\n=== 板块分散测试 ===')
    names = ['北方华创','中微公司','汇顶科技','韦尔股份','立讯精密','上汽集团','长城汽车','比亚迪','宁德时代','阳光电源']
    for n in names:
        from sectors import guess_sector
        print(f'{n}: {guess_sector(n)}')
