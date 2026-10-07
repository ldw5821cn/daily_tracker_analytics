#!/usr/bin/env python3
"""
Kronos A股预测器 - 集成到 daily_tracker_analytics
"""

import sys
import os
from pathlib import Path
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# 添加 Kronos 到路径
KRONOS_PATH = Path(__file__).parent.parent / 'vendor' / 'Kronos'
sys.path.insert(0, str(KRONOS_PATH))

from model import Kronos, KronosTokenizer, KronosPredictor


class KronosStockPredictor:
    """Kronos A股预测器"""
    
    def __init__(self, model_size="mini"):  # 改用 mini
        self.model_size = model_size
        self.predictor = None
        self.device = "cuda" if self._check_cuda() else "cpu"
        print(f"🖥️  Device: {self.device}")
    
    def _check_cuda(self):
        try:
            import torch
            return torch.cuda.is_available()
        except:
            return False
    
    def load_model(self):
        """加载模型（惰性加载）"""
        if self.predictor is not None:
            return
        
        print(f"📦 Loading Kronos-{self.model_size}...")
        
        # mini 用 2k tokenizer，small/base 用 base tokenizer
        tokenizer_name = "NeoQuasar/Kronos-Tokenizer-2k" if self.model_size == "mini" else "NeoQuasar/Kronos-Tokenizer-base"
        
        tokenizer = KronosTokenizer.from_pretrained(tokenizer_name)
        model = Kronos.from_pretrained(f"NeoQuasar/Kronos-{self.model_size}")
        
        self.predictor = KronosPredictor(
            model, 
            tokenizer, 
            max_context=2048 if self.model_size == "mini" else 512,  # mini 支持更长上下文
            device=self.device
        )
        print("✅ Model loaded")
    
    def fetch_a_share_data(self, symbol: str, days: int = 120, use_hithink: bool = True) -> pd.DataFrame:
        """
        获取A股数据
        
        Args:
            symbol: A股代码（如 000001）
            days: 历史天数
            use_hithink: 是否优先使用 HiThink API（默认 True）
        
        Returns:
            DataFrame: OHLCV 数据
        """
        print(f"📥 Fetching {symbol} data...")
        
        # 优先使用 HiThink API
        if use_hithink:
            try:
                return self._fetch_from_hithink(symbol, days)
            except Exception as e:
                print(f"  ⚠️ HiThink 失败，降级腾讯接口: {e}")
        
        # 降级：腾讯接口
        return self._fetch_from_tencent(symbol, days)
    
    def _fetch_from_hithink(self, symbol: str, days: int) -> pd.DataFrame:
        """从 HiThink API 获取数据"""
        try:
            from multi_agent.hithink_client import HiThinkFinanceClient
        except ImportError:
            try:
                from hithink_client import HiThinkFinanceClient
            except ImportError:
                raise ImportError("HiThink 客户端不可用")
        
        client = HiThinkFinanceClient()
        thscode = HiThinkFinanceClient.to_thscode(symbol)
        
        # 计算日期范围
        end_date = datetime.now()
        start_date = end_date - timedelta(days=int(days * 1.5))  # 多取一些，避免节假日
        
        # 获取 K 线
        kline = client.get_kline(
            thscode=thscode,
            start_date=start_date.strftime('%Y-%m-%d'),
            end_date=end_date.strftime('%Y-%m-%d'),
            interval='1d',
            adjust='forward'
        )
        
        if not kline:
            raise ValueError(f"HiThink 无数据: {symbol}")
        
        # 转换为 DataFrame
        df = pd.DataFrame(kline)
        df = df.rename(columns={
            'open_price': 'open',
            'high_price': 'high',
            'low_price': 'low',
            'close_price': 'close',
            'volume': 'volume'
        })
        
        # 只保留需要的列
        df = df[['date', 'open', 'high', 'low', 'close', 'volume']].copy()
        df['timestamps'] = pd.to_datetime(df['date'])
        df['open'] = df['open'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        df['close'] = df['close'].astype(float)
        df['volume'] = df['volume'].astype(float)
        df['amount'] = df['volume'] * df['close']  # 估算成交额
        
        # 只取最近 days 天
        df = df.tail(days).reset_index(drop=True)
        
        print(f"✅ HiThink fetched {len(df)} days")
        return df
    
    def _fetch_from_tencent(self, symbol: str, days: int) -> pd.DataFrame:
        """从腾讯接口获取数据（降级方案）"""
        # 腾讯接口
        if symbol.startswith('6'):
            tencent_code = f"sh{symbol}"
        else:
            tencent_code = f"sz{symbol}"
        
        import urllib.request
        import json
        
        url = f'https://web.ifzq.gtimg.cn/appstock/app/kline/kline?param={tencent_code},day,,,{days}'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        
        kline = data['data'][tencent_code].get('day', [])
        if not kline:
            raise ValueError(f"无数据: {symbol}")
        
        # 转换为 DataFrame（腾讯K线格式: date, open, close, high, low, volume, 其他）
        df = pd.DataFrame(kline)
        df = df.iloc[:, :6]  # 只取前6列
        df.columns = ['date', 'open', 'close', 'high', 'low', 'volume']
        df['timestamps'] = pd.to_datetime(df['date'])
        df['open'] = df['open'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        df['close'] = df['close'].astype(float)
        df['volume'] = df['volume'].astype(float)
        df['amount'] = df['volume'] * df['close']  # 估算成交额
        
        print(f"✅ Tencent fetched {len(df)} days")
        return df
    
    def predict(self, symbol: str, pred_days: int = 3, sample_count: int = 100) -> dict:
        """
        预测个股未来走势
        
        Args:
            symbol: A股代码（如 000001）
            pred_days: 预测天数
            sample_count: 采样路径数
        
        Returns:
            dict: 预测结果
        """
        self.load_model()
        
        # 获取数据
        df = self.fetch_a_share_data(symbol, days=120)
        
        if len(df) < 30:
            return {'error': '数据不足'}
        
        # 准备时间戳（转为 Series，Kronos 需要 .dt 访问器）
        last_date = df['timestamps'].iloc[-1]
        future_dates = pd.date_range(
            start=last_date + timedelta(days=1),
            periods=pred_days,
            freq='B'  # 工作日
        )
        # 转为 Series（Kronos 需要 .dt 访问器）
        x_timestamp = pd.Series(df['timestamps'].values)
        y_timestamp = pd.Series(future_dates)
        
        # Kronos 预测
        print(f"🔮 Predicting {pred_days} days with {sample_count} samples...")
        
        pred_df = self.predictor.predict(
            df=df[['open', 'high', 'low', 'close', 'volume', 'amount']],
            x_timestamp=x_timestamp,
            y_timestamp=y_timestamp,
            pred_len=pred_days,
            T=1.0,
            top_p=0.9,
            sample_count=sample_count
        )
        
        # 分析结果
        current_price = df['close'].iloc[-1]
        pred_closes = pred_df['close'].values
        
        analysis = {
            'symbol': symbol,
            'current_price': float(current_price),
            'pred_dates': [d.strftime('%Y-%m-%d') for d in future_dates],
            'pred_prices': pred_closes.tolist(),
            'expected_return': float((np.mean(pred_closes) - current_price) / current_price),
            'volatility': float(np.std(pred_closes) / current_price),
            'bull_prob': float(np.mean(pred_closes > current_price)),
            'bear_prob': float(np.mean(pred_closes < current_price)),
            'upside_potential': float((np.max(pred_closes) - current_price) / current_price),
            'downside_risk': float((np.min(pred_closes) - current_price) / current_price),
            'timestamp': datetime.now().isoformat(),
        }
        
        # 信号判断
        if analysis['bull_prob'] > 0.6 and analysis['expected_return'] > 0.02:
            analysis['signal'] = 'BUY'
        elif analysis['bear_prob'] > 0.6 and analysis['expected_return'] < -0.02:
            analysis['signal'] = 'SELL'
        else:
            analysis['signal'] = 'HOLD'
        
        return analysis
    
    def predict_multiple(self, symbols: list, pred_days: int = 3) -> list:
        """批量预测"""
        results = []
        for symbol in symbols:
            try:
                result = self.predict(symbol, pred_days)
                results.append(result)
            except Exception as e:
                print(f"❌ {symbol}: {e}")
                results.append({'symbol': symbol, 'error': str(e)})
        return results


def main():
    """测试"""
    print("=" * 60)
    print("🧪 Kronos A股预测测试")
    print("=" * 60)
    
    predictor = KronosStockPredictor(model_size="small")
    
    # 测试预测
    result = predictor.predict("000001", pred_days=3, sample_count=50)
    
    print(f"\n📊 预测结果:")
    print(f"  代码: {result['symbol']}")
    print(f"  现价: {result['current_price']:.2f}")
    print(f"  预期收益: {result['expected_return']*100:+.2f}%")
    print(f"  波动率: {result['volatility']*100:.2f}%")
    print(f"  上涨概率: {result['bull_prob']*100:.1f}%")
    print(f"  下跌概率: {result['bear_prob']*100:.1f}%")
    print(f"  信号: {result['signal']}")
    
    print(f"\n📅 预测价格:")
    for date, price in zip(result['pred_dates'], result['pred_prices']):
        print(f"  {date}: {price:.2f}")


if __name__ == '__main__':
    main()
