"""Analyst skill registry（分析师技能注册表）。

将 predict_one 中按 category 硬编码的基本面/新闻分析抽象为可注册 skill，
新增 category 或替换策略时只需注册新 skill，无需修改 agentic_predictor.py。

每个 skill 是接受 (ticker, name, category, **kwargs) 并返回
{
    'fundamental': {...},   # 与原有 fundamental 结构兼容
    'news': {...},          # 与原有 news 结构兼容，可选
} 的字典。
"""
import sys
from pathlib import Path
from typing import Callable, Dict, Any, Optional, List
from functools import wraps

# 兼容不同启动路径
MULTI_AGENT = Path(__file__).resolve().parents[1]
if str(MULTI_AGENT) not in sys.path:
    sys.path.insert(0, str(MULTI_AGENT))


SkillFn = Callable[..., Dict[str, Any]]

_REGISTRY: Dict[str, Dict[str, Any]] = {}


def register_skill(
    name: str,
    *,
    categories: List[str],
    markets: Optional[List[str]] = None,
    priority: int = 100,
    description: str = "",
) -> Callable[[SkillFn], SkillFn]:
    """注册一个分析师 skill。

    Args:
        name: skill 唯一标识。
        categories: 匹配的标的 category（如 '个股', 'ETF', 'US', '期货'）。
        markets: 可选的市场过滤（如 'cn', 'us', 'hk', 'futures'）。
        priority: 同一 category 下优先级，越小越优先。
        description: 描述信息。
    """

    def decorator(fn: SkillFn) -> SkillFn:
        _REGISTRY[name] = {
            "fn": fn,
            "categories": set(categories),
            "markets": set(markets or []),
            "priority": priority,
            "description": description,
        }

        @wraps(fn)
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def list_skills() -> List[Dict[str, Any]]:
    """返回所有已注册 skill 的元数据（不含函数对象）。"""
    return [
        {
            "name": name,
            "categories": list(meta["categories"]),
            "markets": list(meta["markets"]),
            "priority": meta["priority"],
            "description": meta["description"],
        }
        for name, meta in sorted(_REGISTRY.items(), key=lambda x: x[1]["priority"])
    ]


def select_skill(category: str, market: Optional[str] = None) -> Optional[SkillFn]:
    """根据 category 和 market 选择最佳 skill。"""
    category = category or "个股"
    candidates = []
    for meta in _REGISTRY.values():
        if category not in meta["categories"]:
            continue
        if meta["markets"] and market and market not in meta["markets"]:
            continue
        candidates.append(meta)
    if not candidates:
        return None
    candidates.sort(key=lambda m: m["priority"])
    return candidates[0]["fn"]


def run_skill(
    category: str,
    ticker: str,
    name: str = "",
    market: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """执行选中的 skill，返回 {'fundamental': ..., 'news': ...}。

    若未找到 skill，返回空 fundamental 和 news（fast 模式等价物）。
    """
    fn = select_skill(category, market=market)
    if fn is None:
        return {
            "fundamental": {"score": 50, "rating": "N/A", "fundamentals": {}, "error": "no_skill"},
            "news": {"sentiment_score": 0, "sentiment": "中性", "keywords": []},
        }
    return fn(ticker, name, category=category, market=market, **kwargs)


# ---------------------------------------------------------------------------
# 内置 skills：对应原 predict_one 中各 category 分支
# ---------------------------------------------------------------------------

def _market_from_category(category: str) -> Optional[str]:
    if category == "US":
        return "us"
    if category == "期货":
        return "futures"
    if category == "ETF":
        return "cn"
    return "cn"


@register_skill(
    "futures_fundamental",
    categories=["期货"],
    markets=["futures"],
    priority=10,
    description="期货基本面：库存、基差、仓单、外盘",
)
def _skill_futures(ticker: str, name: str, **kwargs) -> Dict[str, Any]:
    from analysts import futures_fundamental_analyst
    from analysts.sentiment_analyst import compute_sentiment_score as _get_sent

    ff = futures_fundamental_analyst.analyze(ticker, name)
    fundamental = {
        "score": ff.get("score", 50),
        "rating": ff.get("bias", "N/A"),
        "fundamentals": {
            "inventory": ff.get("data", {}).get("inventory"),
            "basis": ff.get("data", {}).get("basis"),
            "foreign": ff.get("data", {}).get("foreign"),
            "warehouse": ff.get("data", {}).get("warehouse"),
            "reasons": ff.get("reasons", []),
        },
        "error": "ok",
    }
    clean_ticker = ticker.replace(".SH", "").replace(".SZ", "")
    _senti = _get_sent(clean_ticker)
    news = {"sentiment_score": _senti / 50 - 1, "sentiment": "中性", "keywords": []}
    return {"fundamental": fundamental, "news": news}


@register_skill(
    "us_fundamental",
    categories=["US"],
    markets=["us"],
    priority=10,
    description="美股基本面质量：Piotroski F、Altman Z、Beneish M",
)
def _skill_us(ticker: str, name: str, **kwargs) -> Dict[str, Any]:
    from analysts import fundamental_factor_analyst
    from analysts.sentiment_analyst import compute_sentiment_score as _get_sent

    ff = fundamental_factor_analyst.analyze_fundamental_factors(ticker, name, category="US")
    quality_score = 50
    if ff["piotroski_f_score"]["score"] is not None:
        quality_score += (ff["piotroski_f_score"]["score"] - 4.5) * 5
    if ff["altman_z_score"]["score"] is not None:
        z = ff["altman_z_score"]["score"]
        if z > 2.99:
            quality_score += 5
        elif z < 1.81:
            quality_score -= 8
    if ff["beneish_m_score"]["score"] is not None:
        m = ff["beneish_m_score"]["score"]
        if m > -1.78:
            quality_score -= 7
    quality_score = max(0, min(100, quality_score))
    fundamental = {
        "score": round(quality_score, 1),
        "rating": ff["piotroski_f_score"]["signals"],
        "fundamentals": {
            "piotroski_f_score": ff["piotroski_f_score"]["score"],
            "altman_z_score": ff["altman_z_score"]["score"],
            "beneish_m_score": ff["beneish_m_score"]["score"],
            "beneish_flag": ff["beneish_m_score"]["flag"],
            "altman_zone": ff["altman_z_score"]["zone"],
        },
        "error": "ok",
    }
    clean_ticker = ticker.replace(".SH", "").replace(".SZ", "").replace("/US", "")
    _senti = _get_sent(clean_ticker)
    news = {"sentiment_score": _senti / 50 - 1, "sentiment": "中性", "keywords": []}
    return {"fundamental": fundamental, "news": news}


@register_skill(
    "etf_quality",
    categories=["ETF"],
    markets=["cn"],
    priority=10,
    description="A 股 ETF 质量：费率、规模、跟踪误差、集中度",
)
def _skill_etf(ticker: str, name: str, **kwargs) -> Dict[str, Any]:
    from analysts import etf_quality_analyst
    from analysts import news_analyst

    eq = etf_quality_analyst.analyze_etf_quality(ticker, name)
    fundamental = {
        "score": eq.get("quality_score", 50),
        "rating": f"质量评分{eq.get('quality_score', 50)}",
        "fundamentals": {
            "management_fee": eq.get("fee", {}).get("management"),
            "custody_fee": eq.get("fee", {}).get("custody"),
            "total_fee": eq.get("fee", {}).get("total"),
            "scale": eq.get("scale"),
            "tracking_error": eq.get("tracking", {}).get("tracking_error"),
            "tracking_is_proxy": eq.get("tracking", {}).get("is_proxy"),
            "concentration_top10": eq.get("concentration", {}).get("top10"),
            "concentration_top20": eq.get("concentration", {}).get("top20"),
            "years_since_establish": eq.get("years_since_establish"),
            "quality_reasons": eq.get("reasons", []),
        },
        "error": "ok",
    }
    news = news_analyst.analyze(ticker, name)
    return {"fundamental": fundamental, "news": news}


@register_skill(
    "cn_fundamental",
    categories=["个股"],
    markets=["cn"],
    priority=10,
    description="A 股个股基本面 + 新闻情绪",
)
def _skill_cn(ticker: str, name: str, **kwargs) -> Dict[str, Any]:
    from analysts import fundamentals_analyst, news_analyst

    fundamental = fundamentals_analyst.analyze(ticker, name)
    news = news_analyst.analyze(ticker, name)
    return {"fundamental": fundamental, "news": news}


# fallback：当 category 不在上述 skill 中时，保持与原 fast 模式一致
@register_skill(
    "fallback_neutral",
    categories=["个股", "ETF", "US", "期货", "指数"],
    priority=999,
    description="默认兜底：中性基本面 + 空新闻",
)
def _skill_fallback(ticker: str, name: str, **kwargs) -> Dict[str, Any]:
    from analysts.sentiment_analyst import compute_sentiment_score as _get_sent

    clean_ticker = ticker.replace(".SH", "").replace(".SZ", "").replace("/US", "")
    try:
        _senti = _get_sent(clean_ticker)
    except Exception:
        _senti = 50
    news = {"sentiment_score": _senti / 50 - 1, "sentiment": "中性", "keywords": []}
    return {
        "fundamental": {"score": 50, "rating": "N/A", "fundamentals": {}, "error": "fallback"},
        "news": news,
    }


if __name__ == "__main__":
    print("registered skills:")
    for s in list_skills():
        print(f"  {s['name']}: categories={s['categories']} markets={s['markets']} priority={s['priority']}")
    print("\nselect 个股 ->", select_skill("个股").__name__)
    print("select ETF  ->", select_skill("ETF").__name__)
    print("select US   ->", select_skill("US").__name__)
    print("select 期货 ->", select_skill("期货").__name__)
    print("select 指数 ->", select_skill("指数").__name__)
