#!/usr/bin/env python3
"""
同花顺 Financial-API 封装器
官方文档: https://fuyao.aicubes.cn/docs/
"""

import os
import time
import urllib.request
import urllib.parse
import json
from datetime import datetime, timedelta
from typing import List, Dict, Optional

# 从 .env 读取 API Key
def _load_api_key():
    """加载 API Key"""
    # 尝试环境变量
    key = os.environ.get('HITHINK_API_KEY')
    if key:
        return key
    
    # 尝试多个 .env 文件路径
    possible_paths = [
        os.path.join(os.path.dirname(__file__), '..', '..', '.env'),  # 项目根目录
        os.path.join(os.path.dirname(__file__), '..', '.env'),        # multi_agent 上级
        os.path.join(os.getcwd(), '.env'),                             # 当前工作目录
        os.path.expanduser('~/.env'),                                  # 用户主目录
    ]
    
    for env_path in possible_paths:
        if os.path.exists(env_path):
            try:
                with open(env_path) as f:
                    for line in f:
                        if line.startswith('HITHINK_API_KEY='):
                            value = line.strip().split('=', 1)[1]
                            # 处理可能的引号
                            value = value.strip('"\'')
                            if value:
                                return value
            except Exception as e:
                print(f"  ⚠️ 读取 {env_path} 失败: {e}")
                continue
    
    return None


class HiThinkFinanceClient:
    """同花顺金融数据客户端"""
    
    BASE_URL = "https://fuyao.aicubes.cn"
    
    def __init__(self, api_key: str = None):
        self.api_key = api_key or _load_api_key()
        if not self.api_key:
            raise ValueError("缺少 HITHINK_API_KEY，请设置环境变量或写入 .env")
        
        self._request_count = 0
        self._last_request_time = 0
    
    def _make_request(self, endpoint: str, params: dict = None) -> dict:
        """发送 API 请求（带限流）"""
        # 简单限流：每秒最多 10 次
        now = time.time()
        if now - self._last_request_time < 0.1:
            time.sleep(0.1 - (now - self._last_request_time))
        self._last_request_time = time.time()
        
        # 构建 URL
        url = f"{self.BASE_URL}{endpoint}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        
        # 发送请求
        req = urllib.request.Request(url, headers={
            'X-api-key': self.api_key,
            'Content-Type': 'application/json'
        })
        
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
                self._request_count += 1
                
                if data.get('code') != 0:
                    raise Exception(f"API 错误: {data.get('message')} (code={data.get('code')})")
                
                return data.get('data', {})
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise Exception("触发限流，请降低请求频率")
            raise Exception(f"HTTP 错误: {e.code}")
    
    # ==================== 行情数据 ====================
    
    def get_quote(self, thscode: str) -> dict:
        """
        获取单只标的情快照
        
        Args:
            thscode: 同花顺代码，如 000001.SZ
        
        Returns:
            dict: 行情数据
        """
        data = self._make_request("/api/a-share/prices/snapshot", {
            'thscodes': thscode
        })
        items = data.get('item', [])
        return items[0] if items else {}
    
    def get_quotes(self, thscodes: List[str]) -> List[dict]:
        """
        批量获取行情快照
        
        Args:
            thscodes: 同花顺代码列表，如 ['000001.SZ', '600519.SH']
        
        Returns:
            list: 行情数据列表
        """
        data = self._make_request("/api/a-share/prices/snapshot", {
            'thscodes': ','.join(thscodes)
        })
        return data.get('item', [])
    
    def get_kline(self, thscode: str, start_date: str, end_date: str, 
                  interval: str = "1d", adjust: str = "forward") -> List[dict]:
        """
        获取历史 K 线
        
        Args:
            thscode: 同花顺代码
            start_date: 开始日期，如 '2024-01-01'
            end_date: 结束日期，如 '2024-12-31'
            interval: K线周期，当前仅支持 '1d'（日线）
            adjust: 复权方式，'none'/'forward'/'backward'
        
        Returns:
            list: K线数据列表
        """
        # 转换日期为毫秒时间戳
        start_ts = int(datetime.strptime(start_date, '%Y-%m-%d').timestamp() * 1000)
        end_ts = int(datetime.strptime(end_date, '%Y-%m-%d').timestamp() * 1000)
        
        data = self._make_request("/api/a-share/prices/historical", {
            'thscode': thscode,
            'interval': interval,
            'start': start_ts,
            'end': end_ts,
            'adjust': adjust
        })
        
        # 转换毫秒时间戳为日期字符串
        items = data.get('item', [])
        for item in items:
            item['date'] = datetime.fromtimestamp(item['date_ms'] / 1000).strftime('%Y-%m-%d')
        
        return items
    
    # ==================== 特色数据 ====================
    
    def get_limit_up_pool(self, date: str = None) -> List[dict]:
        """
        获取涨停股票池
        
        Args:
            date: 日期，如 '2024-09-29'，默认为最新交易日
        
        Returns:
            list: 涨停股票列表
        """
        params = {}
        if date:
            params['date'] = date.replace('-', '')
        
        data = self._make_request("/api/a-share/special-data/limit-up-pool", params)
        return data.get('item', [])
    
    def get_limit_down_pool(self, date: str = None) -> List[dict]:
        """获取跌停股票池"""
        params = {}
        if date:
            params['date'] = date.replace('-', '')
        
        data = self._make_request("/api/a-share/special-data/limit-down-pool", params)
        return data.get('item', [])
    
    def get_break_board_pool(self, date: str = None) -> List[dict]:
        """获取炸板股票池"""
        params = {}
        if date:
            params['date'] = date.replace('-', '')
        
        data = self._make_request("/api/a-share/special-data/break-board-pool", params)
        return data.get('item', [])
    
    def get_streak_board(self, date: str = None) -> List[dict]:
        """获取连板天梯"""
        params = {}
        if date:
            params['date'] = date.replace('-', '')
        
        data = self._make_request("/api/a-share/special-data/streak-board", params)
        return data.get('item', [])
    
    def get_hot_list(self, date: str = None) -> List[dict]:
        """获取同花顺热榜"""
        params = {}
        if date:
            params['date'] = date.replace('-', '')
        
        data = self._make_request("/api/a-share/special-data/hot-list", params)
        return data.get('item', [])
    
    def get_dragon_tiger_list(self, date: str = None) -> List[dict]:
        """获取龙虎榜"""
        params = {}
        if date:
            params['date'] = date.replace('-', '')
        
        data = self._make_request("/api/a-share/special-data/dragon-tiger-list", params)
        return data.get('item', [])
    
    # ==================== 财务数据 ====================
    
    def get_income_statement(self, thscode: str, periods: int = 4) -> List[dict]:
        """
        获取利润表
        
        Args:
            thscode: 同花顺代码
            periods: 期数（最近 N 期）
        
        Returns:
            list: 利润表数据
        """
        data = self._make_request("/api/a-share/financials/income-statement", {
            'thscode': thscode,
            'periods': periods
        })
        return data.get('item', [])
    
    def get_balance_sheet(self, thscode: str, periods: int = 4) -> List[dict]:
        """获取资产负债表"""
        data = self._make_request("/api/a-share/financials/balance-sheet", {
            'thscode': thscode,
            'periods': periods
        })
        return data.get('item', [])
    
    def get_cash_flow(self, thscode: str, periods: int = 4) -> List[dict]:
        """获取现金流量表"""
        data = self._make_request("/api/a-share/financials/cash-flow", {
            'thscode': thscode,
            'periods': periods
        })
        return data.get('item', [])
    
    def get_financial_indicators(self, thscode: str, report: str = None) -> List[dict]:
        """
        获取财务指标
        
        Args:
            thscode: 同花顺代码
            report: 报告期（如 2024-4，默认为最新）
        
        Returns:
            list: 财务指标数据
        """
        params = {'thscode': thscode}
        if report:
            params['report'] = report
        
        data = self._make_request("/api/a-share/financials/indicators", params)
        
        # 转换为标准格式
        indicators = []
        if 'abilities' in data:
            for ability_group in data['abilities']:
                ability = ability_group.get('ability')
                for indicator in ability_group.get('indicators', []):
                    indicators.append({
                        'ability': ability,
                        'index_id': indicator.get('index_id'),
                        'value': float(indicator['value']) if indicator.get('value') else None,
                        'report': data.get('report'),
                        'thscode': data.get('thscode')
                    })
        
        return indicators
    
    # ==================== 估值数据 ====================
    
    def get_valuation(self, thscodes: List[str]) -> List[dict]:
        """
        获取估值数据（PE/PB/PS/PC）
        
        Args:
            thscodes: 同花顺代码列表
        
        Returns:
            list: 估值数据
        """
        data = self._make_request("/api/a-share/valuations/snapshot", {
            'thscodes': ','.join(thscodes)
        })
        return data.get('item', [])
    
    # ==================== 指数数据 ====================
    
    def get_index_list(self, tag: str = None) -> List[dict]:
        """获取同花顺指数列表"""
        params = {}
        if tag:
            params['tag'] = tag
        
        data = self._make_request("/api/a-share/index/list", params)
        return data.get('item', [])
    
    def get_index_constituents(self, thscode: str) -> List[dict]:
        """获取指数成分股"""
        data = self._make_request("/api/a-share/index/constituents", {
            'thscode': thscode
        })
        return data.get('item', [])
    
    # ==================== 交易日历 ====================
    
    def get_trading_days(self, start_date: str = None, end_date: str = None) -> List[str]:
        """
        获取交易日历
        
        Args:
            start_date: 开始日期，如 '2024-01-01'
            end_date: 结束日期，如 '2024-12-31'
        
        Returns:
            list: 交易日列表（YYYY-MM-DD）
        """
        params = {}
        if start_date:
            params['start'] = start_date.replace('-', '')
        if end_date:
            params['end'] = end_date.replace('-', '')
        
        data = self._make_request("/api/a-share/calendar/trading-days", params)
        days = data.get('item', [])
        return [datetime.fromtimestamp(d / 1000).strftime('%Y-%m-%d') for d in days]
    
    # ==================== 工具方法 ====================
    
    @staticmethod
    def to_thscode(symbol: str) -> str:
        """
        转换为同花顺代码格式
        
        Args:
            symbol: 股票代码，如 '000001'
        
        Returns:
            str: 同花顺代码，如 '000001.SZ'
        """
        if '.' in symbol:
            return symbol
        
        # 沪市: 60xxxx, 68xxxx (科创板)
        if symbol.startswith(('60', '68')):
            return f"{symbol}.SH"
        # 深市: 00xxxx, 30xxxx (创业板)
        elif symbol.startswith(('00', '30')):
            return f"{symbol}.SZ"
        # 北交所: 8xxxxx, 4xxxxx
        elif symbol.startswith(('8', '4')):
            return f"{symbol}.BJ"
        else:
            return f"{symbol}.SZ"  # 默认深市
    
    @staticmethod
    def from_thscode(thscode: str) -> str:
        """从同花顺代码提取股票代码"""
        return thscode.split('.')[0]


# 便捷函数
def get_client() -> HiThinkFinanceClient:
    """获取客户端实例"""
    return HiThinkFinanceClient()


# 测试
if __name__ == '__main__':
    print("=" * 60)
    print("🧪 同花顺 Financial-API 测试")
    print("=" * 60)
    
    client = get_client()
    
    # 测试 1: 行情快照
    print("\n1. 测试行情快照（平安银行）...")
    quote = client.get_quote('000001.SZ')
    print(f"   最新价: {quote.get('last_price')}")
    print(f"   涨跌幅: {quote.get('price_change_ratio_pct'):.2f}%")
    print(f"   成交量: {quote.get('volume'):,}")
    
    # 测试 2: 历史 K 线
    print("\n2. 测试历史 K 线（最近 5 天）...")
    kline = client.get_kline('000001.SZ', '2024-09-20', '2024-09-29')
    for bar in kline[-5:]:
        print(f"   {bar['date']}: 开{bar['open_price']:.2f} 高{bar['high_price']:.2f} "
              f"低{bar['low_price']:.2f} 收{bar['close_price']:.2f}")
    
    # 测试 3: 涨停池
    print("\n3. 测试涨停池...")
    limit_up = client.get_limit_up_pool()
    print(f"   涨停股票数: {len(limit_up)}")
    if limit_up:
        print(f"   示例: {limit_up[0].get('name')} ({limit_up[0].get('thscode')})")
    
    print(f"\n✅ 测试完成，共发送 {client._request_count} 次请求")
