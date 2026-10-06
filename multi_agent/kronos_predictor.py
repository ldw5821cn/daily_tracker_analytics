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
    
    def __init__(self, model_size="small"):
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
        
        tokenizer = KronosTokenizer.from_pretrained(
            "NeoQuasar/Kronos-Tokenizer-base"
        )
        model = Kronos.from_pretrained(
            f"NeoQuasar/Kronos-{self.model_size}"
        )
        
        self.predictor = KronosPredictor(
            model, 
            tokenizer, 
            max_context=512,
            device=self.device
        )
        print("✅ Model loaded")
    
    def fetch_a_share_data(self, symbol: str, days: int = 120) -> pd.DataFrame:
        """获取A股数据"""
        try:
            import akshare as ak
        except ImportError:
            raise ImportError("请安装 akshare: pip install akshare")
        
        # 转换代码格式
        if symbol.startswith('6'):
            ak_symbol = f"sh{symbol}"
        else:
            ak_symbol = f"sz{symbol}"
        
        print(f"📥 Fetching {symbol} data...")
        
        # 获取日线数据
        end_date = datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now() - timedelta(days=days)).strftime('%Y%m%d')
        
        raw = ak.stock_zh_a_hist(
            symbol=symbol, 
            period="daily", 
            start_date=start_date,
            end_date=end_date,
            adjust="qfq"  # 前复权
        )
        
        df = pd.DataFrame({
            'timestamps': pd.to_datetime(raw['日期']),
            'open': raw['开盘'].astype(float),
            'high': raw['最高'].astype(float),
            'low': raw['最低'].astype(float),
            'close': raw['收盘'].astype(float),
            'volume': raw['成交量'].astype(float),
            'amount': raw['成交额'].astype(float),
        })
        
        print(f"✅ Fetched {len(df)} days")
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
        
        # 准备时间戳
        last_date = df['timestamps'].iloc[-1]
        future_dates = pd.date_range(
            start=last_date + timedelta(days=1),
            periods=pred_days,
            freq='B'  # 工作日
        )
        
        # Kronos 预测
        print(f"🔮 Predicting {pred_days} days with {sample_count} samples...")
        
        pred_df = self.predictor.predict(
            df=df[['open', 'high', 'low', 'close', 'volume', 'amount']],
            x_timestamp=df['timestamps'],
            y_timestamp=future_dates,
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
