"""Market rules loader for local paper trading.

Data source: multi_agent/config/market_rules.json (extracted from
Vibe-Trading agent/backtest/engines/china_a.py and china_futures.py).

Public APIs:
    - get_price_limit(symbol, is_st=False) -> float
    - get_futures_params(symbol) -> dict
    - calc_a_share_cost(price, shares, is_buy) -> dict
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "market_rules.json"


def _load_config() -> dict[str, Any]:
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


_CONFIG = _load_config()
_CHINA_A = _CONFIG["china_a"]
_CHINA_FUTURES = _CONFIG["china_futures"]
_FUTURES_PRODUCTS: dict[str, dict[str, Any]] = _CHINA_FUTURES["products"]


def _strip_suffix(symbol: str) -> str:
    return symbol.split(".")[0] if "." in symbol else symbol


def get_price_limit(symbol: str, is_st: bool = False) -> float:
    """Return daily price-limit fraction for an A-share code.

    Rules (consistent with Vibe-Trading china_a._price_limit):
        - ChiNext (300xxx) / STAR (688xxx): ±20%
        - Beijing exchange (8xxxxx, length 6): ±30%
        - ST stocks: ±5% (must set is_st=True; cannot infer from code)
        - Main board: ±10%

    Args:
        symbol: e.g. '000001.SZ', '300001', '688001.SH'.
        is_st: Whether the stock is ST/*ST.

    Returns:
        Price limit as a fraction, e.g. 0.10 for ±10%.
    """
    code = _strip_suffix(symbol)
    if not code or not code.isdigit():
        return _CHINA_A["price_limits"]["main_board"]

    if is_st:
        return _CHINA_A["price_limits"]["st"]
    if code.startswith("300") or code.startswith("688"):
        return _CHINA_A["price_limits"]["chisnext_star"]
    if code.startswith("8") and len(code) == 6:
        return _CHINA_A["price_limits"]["beijing"]
    return _CHINA_A["price_limits"]["main_board"]


def calc_a_share_cost(price: float, shares: int, is_buy: bool) -> dict[str, float]:
    """Estimate A-share trading cost for one side.

    Costs include:
        - commission: max(notional * 0.00025, 5.0)
        - transfer fee: notional * 0.00001 (bilateral)
        - stamp tax: notional * 0.0005 (sell-only)
        - slippage: price * 0.1% applied to notional estimate

    Args:
        price: Execution price.
        shares: Number of shares (should already be a 100-lot multiple).
        is_buy: True for buy, False for sell.

    Returns:
        Dict with keys: notional, commission, transfer_fee, stamp_tax,
        slippage, total_cost.
    """
    notional = price * shares
    commission = max(notional * _CHINA_A["commission"]["rate"], _CHINA_A["commission"]["min"])
    transfer_fee = notional * _CHINA_A["transfer_fee"]["rate"]
    stamp_tax = notional * _CHINA_A["stamp_tax"]["rate"] if (not is_buy and _CHINA_A["stamp_tax"]["sell_only"]) else 0.0
    slippage = notional * _CHINA_A["slippage"]
    total_cost = commission + transfer_fee + stamp_tax + slippage
    return {
        "notional": notional,
        "commission": commission,
        "transfer_fee": transfer_fee,
        "stamp_tax": stamp_tax,
        "slippage": slippage,
        "total_cost": total_cost,
    }


def _extract_product(symbol: str) -> str:
    code = _strip_suffix(symbol)
    m = re.match(r"([A-Za-z]+)", code)
    return m.group(1) if m else code


def get_futures_params(symbol: str) -> dict[str, Any]:
    """Look up futures contract multiplier, margin rate, price limit and commission.

    Missing per-product values fall back to engine defaults:
        - multiplier default: 10
        - margin_rate default: 0.10
        - price_limit default: 0.05
        - commission default: ["fixed", 5.0] (RMB per lot)

    Args:
        symbol: e.g. 'IF2406.CFFEX', 'rb2410.SHFE', 'au2412'.

    Returns:
        Dict with keys: product, multiplier, margin_rate, price_limit,
        commission_mode, commission_value.
    """
    product = _extract_product(symbol)
    data = _FUTURES_PRODUCTS.get(product, {})
    multiplier = data.get("multiplier", 10)
    margin_rate = data.get("margin_rate", 0.10)
    price_limit = data.get("price_limit", _CHINA_FUTURES["default_price_limit"])
    commission = data.get("commission", _CHINA_FUTURES["default_commission"])
    if not isinstance(commission, (list, tuple)) or len(commission) != 2:
        commission = _CHINA_FUTURES["default_commission"]
    return {
        "product": product,
        "multiplier": int(multiplier),
        "margin_rate": float(margin_rate),
        "price_limit": float(price_limit),
        "commission_mode": commission[0],
        "commission_value": float(commission[1]),
    }


def calc_futures_commission(symbol: str, price: float, contracts: int) -> float:
    """Calculate futures commission for a given number of contracts.

    Supports both 'fixed' (per lot) and 'rate' (notional-based) modes.
    """
    p = get_futures_params(symbol)
    notional = contracts * price * p["multiplier"]
    if p["commission_mode"] == "rate":
        return notional * p["commission_value"]
    return contracts * p["commission_value"]


if __name__ == "__main__":
    # Quick sanity checks
    print("000001.SZ limit:", get_price_limit("000001.SZ"))          # 0.10
    print("300001.SZ limit:", get_price_limit("300001.SZ"))          # 0.20
    print("688001.SH limit:", get_price_limit("688001.SH"))          # 0.20
    print("835305.BJ limit:", get_price_limit("835305.BJ"))          # 0.30
    print("ST 000001 limit:", get_price_limit("000001.SZ", is_st=True))  # 0.05
    print("IF params:", get_futures_params("IF2406.CFFEX"))
    print("rb params:", get_futures_params("rb2410.SHFE"))
    print("A-share buy cost:", calc_a_share_cost(10.0, 1000, is_buy=True))
    print("A-share sell cost:", calc_a_share_cost(10.0, 1000, is_buy=False))
    print("IF commission:", calc_futures_commission("IF2406.CFFEX", 3500.0, 1))
