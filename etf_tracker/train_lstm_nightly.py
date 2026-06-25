#!/usr/bin/env python3
"""
LSTM 隔夜训练脚本
在每日收盘后运行，训练所有重点 ETF 的 LSTM 模型并保存到本地缓存
次日早报直接加载使用，无需重新训练
"""

import os
import sys
import warnings
import json
import time
from datetime import datetime

warnings.filterwarnings('ignore')
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from etf_tracker import ETFDataFetcher, Config
from multi_model_predictor import MultiModelPredictor

# 要训练 LSTM 的 ETF 列表（重点板块 + 稀土永磁 516150）
DEFAULT_ETF_CODES = [
    '516150',     # 稀土永磁
    '159928',     # 消费
    '512760',     # 芯片
    '588200',     # 科创50
    '159819',     # 人工智能
    '159847',     # 机器人
    '512480',     # 半导体
    '512170',     # 医疗
    '512980',     # 传媒
    '515880',     # 通信
    '159967',     # 新能源
    '518880',     # 黄金
    '00700.HK',   # 腾讯
    'GRAB',       # 美股
]


def train_single_etf_lstm(code: str, fetcher: ETFDataFetcher, config: Config) -> dict:
    """训练单个 ETF 的 LSTM 模型"""
    print(f"\n{'='*60}")
    print(f"训练 ETF: {code}")
    print(f"{'='*60}")
    
    start = time.time()
    result = {'code': code, 'success': False, 'time_cost': 0, 'error': None}
    
    try:
        # 获取 400 天 K 线数据
        df = fetcher.get_kline_data(code, days=400)
        if df is None or len(df) < 60:
            result['error'] = f'数据不足: {len(df) if df is not None else 0} 条'
            return result
        
        print(f"数据量: {len(df)} 条")
        
        # 训练 LSTM（只训练 LSTM，其他模型第二天早上训练）
        predictor = MultiModelPredictor(etf_code=code)
        training_results = predictor.train_all_models(df, target_col='target_1d')
        
        # LSTM 训练结果检查
        lstm_result = training_results.get('lstm', {})
        if 'error' in lstm_result:
            result['error'] = f"LSTM 训练失败: {lstm_result['error']}"
            return result
        
        # 验证模型是否能预测
        ensemble = predictor.ensemble_predict(df, days=5)
        pred = ensemble['ensemble'].get('return_1d', 0)
        
        result['success'] = True
        result['time_cost'] = time.time() - start
        result['prediction'] = pred
        result['confidence'] = ensemble['ensemble'].get('confidence', 0)
        result['windows'] = lstm_result.get('windows', [])
        
        print(f"✅ {code} 训练完成: 1日预测 {pred*100:+.2f}%, 耗时 {result['time_cost']:.1f}s")
        
    except Exception as e:
        result['error'] = str(e)
        result['time_cost'] = time.time() - start
        print(f"❌ {code} 训练失败: {e}")
    
    return result


def main():
    """主函数：批量训练所有重点 ETF 的 LSTM 模型"""
    print(f"\n{'#'*70}")
    print(f"# LSTM 隔夜训练开始: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"#{'#'*69}")
    
    config = Config()
    fetcher = ETFDataFetcher(config)
    
    # 从配置读取 ETF 列表，如果没有则使用默认列表
    etf_codes = DEFAULT_ETF_CODES
    if config.etfs:
        codes = [e.get('code') for e in config.etfs]
        if codes:
            etf_codes = codes
    
    print(f"本次训练 ETF 数量: {len(etf_codes)}")
    
    results = []
    total_start = time.time()
    
    for code in etf_codes:
        try:
            r = train_single_etf_lstm(code, fetcher, config)
            results.append(r)
        except Exception as e:
            print(f"❌ {code} 训练异常: {e}")
            results.append({'code': code, 'success': False, 'error': str(e)})
    
    total_time = time.time() - total_start
    
    # 统计
    success_count = sum(1 for r in results if r['success'])
    failed_count = len(results) - success_count
    
    print(f"\n{'#'*70}")
    print(f"# LSTM 隔夜训练完成")
    print(f"# 总耗时: {total_time:.1f}s")
    print(f"# 成功: {success_count}/{len(results)}")
    print(f"# 失败: {failed_count}/{len(results)}")
    print(f"#{'#'*69}")
    
    # 失败详情
    if failed_count > 0:
        print("\n失败列表:")
        for r in results:
            if not r['success']:
                print(f"  - {r['code']}: {r.get('error', '未知错误')}")
    
    # 保存训练日志
    log_dir = os.path.expanduser("~/etf_tracker/logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"lstm_training_{datetime.now().strftime('%Y%m%d')}.json")
    with open(log_path, 'w') as f:
        json.dump({
            'train_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'total_time': total_time,
            'success_count': success_count,
            'failed_count': failed_count,
            'results': results
        }, f, indent=2, default=str)
    print(f"\n训练日志已保存: {log_path}")


if __name__ == '__main__':
    main()
