"""
交易工具函数

提供 A 股交易约束相关的通用计算函数：
- 100 股整数倍校验
- 补仓金额计算
- 目标股数计算
"""

from typing import Optional, Dict, Any


A_SHARE_LOT_SIZE = 100  # A股最小交易单位


def calculate_buy_shares(amount: float, price: float, min_lot: int = A_SHARE_LOT_SIZE) -> int:
    """
    根据金额和股价计算可买入股数（向下取整到最小交易单位）
    
    Args:
        amount: 可用金额
        price: 股票现价
        min_lot: 最小交易单位，默认100股
    
    Returns:
        可买入股数（min_lot的整数倍）
    """
    if price <= 0 or amount <= 0:
        return 0
    shares = int(amount / price)
    return (shares // min_lot) * min_lot


def calculate_min_amount(price: float, min_lot: int = A_SHARE_LOT_SIZE) -> float:
    """
    计算最小交易金额
    
    Args:
        price: 股票现价
        min_lot: 最小交易单位
    
    Returns:
        最小交易金额
    """
    if price <= 0:
        return 0.0
    return price * min_lot


def validate_buy_amount(amount: float, price: float, min_lot: int = A_SHARE_LOT_SIZE) -> Dict[str, Any]:
    """
    校验买入金额是否满足交易约束
    
    Returns:
        {
            'valid': bool,
            'shares': int,  # 可买股数
            'actual_amount': float,  # 实际所需金额
            'min_amount': float,  # 最小所需金额
            'message': str
        }
    """
    min_amount = calculate_min_amount(price, min_lot)
    
    if amount < min_amount:
        return {
            'valid': False,
            'shares': 0,
            'actual_amount': 0.0,
            'min_amount': min_amount,
            'message': f'金额不足一手，最少需要 ¥{min_amount:.2f}（{min_lot}股 @ ¥{price:.2f}）'
        }
    
    shares = calculate_buy_shares(amount, price, min_lot)
    actual_amount = shares * price
    
    return {
        'valid': True,
        'shares': shares,
        'actual_amount': actual_amount,
        'min_amount': min_amount,
        'message': f'可买入 {shares} 股，实际金额 ¥{actual_amount:.2f}'
    }


def calculate_rebalance(
    current_price: float,
    target_weight: float,
    portfolio_value: float,
    current_shares: int = 0,
    min_lot: int = A_SHARE_LOT_SIZE
) -> Dict[str, Any]:
    """
    计算调仓建议
    
    Args:
        current_price: 现价
        target_weight: 目标权重 (0-1)
        portfolio_value: 组合总市值
        current_shares: 当前持股数
        min_lot: 最小交易单位
    
    Returns:
        调仓建议字典
    """
    if current_price <= 0 or portfolio_value <= 0:
        return {
            'action': '不操作',
            'target_shares': 0,
            'diff_shares': 0,
            'target_amount': 0.0,
            'message': '价格或组合市值无效'
        }
    
    target_amount = portfolio_value * target_weight
    target_shares = int(target_amount / current_price)
    target_shares = (target_shares // min_lot) * min_lot
    
    diff_shares = target_shares - current_shares
    
    # 取整到100股
    if diff_shares > 0:
        action = '买入'
        diff_shares = (diff_shares // min_lot) * min_lot
    elif diff_shares < 0:
        action = '卖出'
        diff_shares = (diff_shares // min_lot) * min_lot
    else:
        action = '持有'
    
    return {
        'action': action,
        'target_shares': target_shares,
        'diff_shares': diff_shares,
        'target_amount': target_shares * current_price,
        'message': f'{action} {abs(diff_shares)} 股至 {target_shares} 股'
    }
