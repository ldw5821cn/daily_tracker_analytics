#!/usr/bin/env python3
"""每日市场上下文服务。

提供统一的市场上下文数据模型、缓存、风险标签提取与 prompt 渲染，
供 predictor / agentic_predictor / daily_tracker 等模块注入 LLM prompt。

设计原则：
- 当日首次运行时生成上下文，后续同 query_id 复用。
- 支持 force_refresh 强制刷新，allow_generate=False 时不触发网络/akshare 调用。
- 历史上下文追加写入 multi_agent/data/market_context_history.jsonl，便于复盘。
- 按 region（cn/hk/us/futures）提供差异化上下文，避免 A 股上下文注入美股标的。
- 上下文段落以 BEGIN_UNTRUSTED_MARKET_SUMMARY 护栏包裹，提醒 LLM 仅作参考。
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MULTI_AGENT = os.path.join(PROJECT_ROOT, "multi_agent")
DATA_DIR = os.path.join(MULTI_AGENT, "data")
HISTORY_PATH = os.path.join(DATA_DIR, "market_context_history.jsonl")
DEFAULT_CACHE_PATH = os.path.join(DATA_DIR, "market_context_cache.json")

# 线程级缓存 + 进程级文件缓存
_query_cache: Dict[str, "DailyMarketContext"] = {}
_cache_lock = threading.Lock()


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _get_query_id() -> str:
    """返回当前运行会话的 query_id。"""
    return os.environ.get("HERMES_QUERY_ID", "default")


@dataclass
class RiskTags:
    """市场上下文风险标签与仓位提示。"""

    high_risk: bool = False
    conservative: bool = False
    market_cooling: bool = False
    low_position_cap: bool = False
    low_liquidity: bool = False

    def to_list(self) -> List[str]:
        tags = []
        if self.high_risk:
            tags.append("high_risk")
        if self.conservative:
            tags.append("conservative")
        if self.market_cooling:
            tags.append("market_cooling")
        if self.low_position_cap:
            tags.append("low_position_cap")
        if self.low_liquidity:
            tags.append("low_liquidity")
        return tags


@dataclass
class DailyMarketContext:
    """统一每日市场上下文。"""

    date: str
    region: str  # cn / hk / us / futures
    query_id: str
    macro_score: float
    macro_signal: str  # bullish / bearish / neutral
    market_phase: str  # strong_bull / bull / neutral / bear / strong_bear
    market_light: str  # red / yellow / green
    risk_state: str = "neutral"  # risk_on / risk_off / neutral
    risk_tags: RiskTags = field(default_factory=RiskTags)
    position_cap: float = 1.0  # 0~1，建议仓位上限
    index_scores: List[Dict[str, Any]] = field(default_factory=list)
    market_breadth: Dict[str, Any] = field(default_factory=dict)
    us_macro: Dict[str, Any] = field(default_factory=dict)
    china_macro: Dict[str, Any] = field(default_factory=dict)
    yield_curve: Dict[str, Any] = field(default_factory=dict)
    vix_proxy: Dict[str, Any] = field(default_factory=dict)
    sector_rotation: Dict[str, Any] = field(default_factory=dict)
    global_semi: Dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    generated_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    ttl_seconds: int = 86400

    def to_dict(self) -> Dict[str, Any]:
        return {
            "date": self.date,
            "region": self.region,
            "query_id": self.query_id,
            "macro_score": self.macro_score,
            "macro_signal": self.macro_signal,
            "market_phase": self.market_phase,
            "market_light": self.market_light,
            "risk_state": self.risk_state,
            "risk_tags": self.risk_tags.to_list(),
            "position_cap": self.position_cap,
            "index_scores": self.index_scores,
            "market_breadth": self.market_breadth,
            "us_macro": self.us_macro,
            "china_macro": self.china_macro,
            "yield_curve": self.yield_curve,
            "vix_proxy": self.vix_proxy,
            "sector_rotation": self.sector_rotation,
            "global_semi": self.global_semi,
            "summary": self.summary,
            "generated_at": self.generated_at,
            "ttl_seconds": self.ttl_seconds,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DailyMarketContext":
        rt = d.get("risk_tags", [])
        risk_tags = RiskTags(
            high_risk="high_risk" in rt,
            conservative="conservative" in rt,
            market_cooling="market_cooling" in rt,
            low_position_cap="low_position_cap" in rt,
            low_liquidity="low_liquidity" in rt,
        )
        ctx = cls(
            date=d.get("date", _today()),
            region=d.get("region", "cn"),
            query_id=d.get("query_id", "default"),
            macro_score=d.get("macro_score", 50.0),
            macro_signal=d.get("macro_signal", "neutral"),
            market_phase=d.get("market_phase", "neutral"),
            market_light=d.get("market_light", "yellow"),
            risk_state=d.get("risk_state", "neutral"),
            risk_tags=risk_tags,
            position_cap=d.get("position_cap", 1.0),
            index_scores=d.get("index_scores", []),
            market_breadth=d.get("market_breadth", {}),
            us_macro=d.get("us_macro", {}),
            china_macro=d.get("china_macro", {}),
            yield_curve=d.get("yield_curve", {}),
            vix_proxy=d.get("vix_proxy", {}),
            sector_rotation=d.get("sector_rotation", {}),
            global_semi=d.get("global_semi", {}),
            summary=d.get("summary", ""),
            generated_at=d.get("generated_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            ttl_seconds=d.get("ttl_seconds", 86400),
        )
        return ctx


def _extract_risk_tags_and_cap(
    macro_signal: str,
    macro_score: float,
    market_phase: str,
    market_light: str,
    vix_proxy: Dict[str, Any],
    breadth: Dict[str, Any],
) -> Tuple[RiskTags, float]:
    """基于市场状态提取风险标签与建议仓位上限。"""
    tags = RiskTags()
    position_cap = 1.0

    if market_light == "red" or market_phase in ("bear", "strong_bear"):
        tags.high_risk = True
        tags.conservative = True
        tags.low_position_cap = True
        position_cap = 0.5

    if macro_signal == "bearish":
        tags.conservative = True
        if market_phase in ("bear", "strong_bear"):
            tags.market_cooling = True
            position_cap = min(position_cap, 0.4)

    vix_level = vix_proxy.get("level", "normal")
    if vix_level in ("high", "extreme"):
        tags.high_risk = True
        tags.low_liquidity = True
        position_cap = min(position_cap, 0.5 if vix_level == "high" else 0.3)

    # 涨停家数骤降视为情绪冷却
    limit_up = breadth.get("limit_up") or 0
    if isinstance(limit_up, (int, float)) and limit_up < 20:
        tags.market_cooling = True

    return tags, round(position_cap, 2)


def _build_context_from_macro_analyst(
    region: str = "cn",
    current_date: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """调用现有 macro_analyst.analyze() 构建原始数据字典。"""
    try:
        import sys
        sys.path.insert(0, MULTI_AGENT)
        from analysts.macro_analyst import analyze
        from core.market_rules import market_phase as _market_phase, market_light as _market_light

        current_date = current_date or _today()
        # macro_analyst 主要覆盖 A 股/中国市场
        raw = analyze(current_date=current_date)
        if not raw:
            return None

        phase = _market_phase(
            raw.get("macro_score", 50),
            raw.get("risk_on_off", {}).get("state", "neutral"),
        )
        light = _market_light(raw.get("index_scores", []))
        risk_tags, cap = _extract_risk_tags_and_cap(
            macro_signal=raw.get("macro_signal", "neutral"),
            macro_score=raw.get("macro_score", 50.0),
            market_phase=phase,
            market_light=light,
            vix_proxy=raw.get("vix_proxy", {}),
            breadth=raw.get("market_breadth", {}),
        )
        return {
            "macro_score": raw.get("macro_score", 50.0),
            "macro_signal": raw.get("macro_signal", "neutral"),
            "market_phase": phase,
            "market_light": light,
            "risk_state": raw.get("risk_on_off", {}).get("state", "neutral"),
            "risk_tags": risk_tags,
            "position_cap": cap,
            "index_scores": raw.get("index_scores", []),
            "market_breadth": raw.get("market_breadth", {}),
            "us_macro": raw.get("us_macro", {}),
            "china_macro": raw.get("china_macro", {}),
            "yield_curve": raw.get("yield_curve", {}),
            "vix_proxy": raw.get("vix_proxy", {}),
            "sector_rotation": raw.get("sector_rotation", {}),
            "global_semi": raw.get("global_semi", {}),
            "summary": raw.get("summary", ""),
        }
    except Exception as e:
        print(f"[daily_market_context] macro analyst failed: {e}")
        return None


def _build_us_context(
    region: str = "us",
    current_date: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """为美股标的构建轻量上下文。当前复用 A 股宏观中的 US 宏观数据。"""
    ctx = _build_context_from_macro_analyst(region="cn", current_date=current_date)
    if ctx is None:
        return None
    ctx["region"] = region
    # 美股更关注美国宏观与全球半导体
    ctx["summary"] = (
        f"# 美股市场环境 ({current_date or _today()})\n\n"
        f"- 美国宏观: 联邦利率 {ctx['us_macro'].get('fed_rate')}% | CPI {ctx['us_macro'].get('cpi_yoy')}%\n"
        f"- 失业率: {ctx['us_macro'].get('unemployment')}% | 初请失业金: {ctx['us_macro'].get('initial_jobless')}万\n"
        f"- 中国10Y-2Y利差: {ctx['yield_curve'].get('spread')}\n"
        f"- VIX代理(A股波动率): {ctx['vix_proxy'].get('vix_proxy')} ({ctx['vix_proxy'].get('level')})\n"
        f"- 全球半导体动量: {ctx['global_semi'].get('composite_signal', 'neutral')}\n"
    )
    return ctx


def _build_futures_context(
    region: str = "futures",
    current_date: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """为期货标的构建轻量上下文。"""
    ctx = _build_context_from_macro_analyst(region="cn", current_date=current_date)
    if ctx is None:
        return None
    ctx["region"] = region
    ctx["summary"] = (
        f"# 期货市场环境 ({current_date or _today()})\n\n"
        f"- 综合宏观评分: {ctx['macro_score']}/100 | 信号: {ctx['macro_signal']}\n"
        f"- Risk-on/off: {ctx['risk_state']}\n"
        f"- 领涨板块: {ctx['sector_rotation'].get('top_sector', 'unknown')}\n"
        f"- 全球半导体动量: {ctx['global_semi'].get('composite_signal', 'neutral')}\n"
    )
    return ctx


def _load_file_cache(cache_path: str, query_id: str, region: str) -> Optional[DailyMarketContext]:
    """从文件缓存加载当日上下文。"""
    if not os.path.exists(cache_path):
        return None
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        if data.get("date") != _today():
            return None
        if data.get("query_id") != query_id:
            return None
        if data.get("region") != region:
            return None
        generated_at = datetime.strptime(data["generated_at"], "%Y-%m-%d %H:%M:%S")
        ttl = data.get("ttl_seconds", 86400)
        if (datetime.now() - generated_at).total_seconds() > ttl:
            return None
        return DailyMarketContext.from_dict(data)
    except Exception:
        return None


def _save_file_cache(ctx: DailyMarketContext, cache_path: str) -> None:
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(ctx.to_dict(), f, ensure_ascii=False, indent=2)


def _append_history(ctx: DailyMarketContext) -> None:
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    with open(HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(ctx.to_dict(), ensure_ascii=False) + "\n")


def get_context(
    region: str = "cn",
    query_id: Optional[str] = None,
    force_refresh: bool = False,
    allow_generate: bool = True,
    cache_path: Optional[str] = None,
    current_date: Optional[str] = None,
) -> DailyMarketContext:
    """获取每日市场上下文。

    Args:
        region: 市场区域，'cn' / 'hk' / 'us' / 'futures'。
        query_id: 运行会话标识，默认从环境变量 HERMES_QUERY_ID 读取。
        force_refresh: 是否强制重新生成。
        allow_generate: 是否允许触发网络/akshare 调用生成新上下文；False 时优先用缓存。
        cache_path: 文件缓存路径，默认 multi_agent/data/market_context_cache.json。
        current_date: 指定日期，默认今天。

    Returns:
        DailyMarketContext 实例。
    """
    today = current_date or _today()
    qid = query_id or _get_query_id()
    cache_path = cache_path or DEFAULT_CACHE_PATH
    cache_key = f"{qid}:{region}:{today}"

    # 1. 内存缓存命中
    if not force_refresh:
        with _cache_lock:
            cached = _query_cache.get(cache_key)
            if cached and cached.date == today:
                return cached

    # 2. 文件缓存命中
    if not force_refresh:
        cached = _load_file_cache(cache_path, qid, region)
        if cached:
            with _cache_lock:
                _query_cache[cache_key] = cached
            return cached

    # 3. 禁止生成且没有缓存：返回一个保守的 fallback 上下文
    if not allow_generate:
        fallback = DailyMarketContext(
            date=today,
            region=region,
            query_id=qid,
            macro_score=50.0,
            macro_signal="neutral",
            market_phase="neutral",
            market_light="yellow",
            summary="[fallback] 未生成市场上下文，因 allow_generate=False 且无缓存。",
        )
        return fallback

    # 4. 生成新上下文
    if region in ("us",):
        raw = _build_us_context(region=region, current_date=today)
    elif region in ("futures",):
        raw = _build_futures_context(region=region, current_date=today)
    else:
        # cn / hk / 默认
        raw = _build_context_from_macro_analyst(region=region, current_date=today)

    if raw is None:
        ctx = DailyMarketContext(
            date=today,
            region=region,
            query_id=qid,
            macro_score=50.0,
            macro_signal="neutral",
            market_phase="neutral",
            market_light="yellow",
            summary="[fallback] 宏观分析师返回空数据。",
        )
    else:
        ctx = DailyMarketContext(
            date=today,
            region=region,
            query_id=qid,
            macro_score=raw.get("macro_score", 50.0),
            macro_signal=raw.get("macro_signal", "neutral"),
            market_phase=raw.get("market_phase", "neutral"),
            market_light=raw.get("market_light", "yellow"),
            risk_state=raw.get("risk_state", "neutral"),
            risk_tags=raw.get("risk_tags", RiskTags()),
            position_cap=raw.get("position_cap", 1.0),
            index_scores=raw.get("index_scores", []),
            market_breadth=raw.get("market_breadth", {}),
            us_macro=raw.get("us_macro", {}),
            china_macro=raw.get("china_macro", {}),
            yield_curve=raw.get("yield_curve", {}),
            vix_proxy=raw.get("vix_proxy", {}),
            sector_rotation=raw.get("sector_rotation", {}),
            global_semi=raw.get("global_semi", {}),
            summary=raw.get("summary", ""),
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

    # 5. 写入内存、文件、历史
    with _cache_lock:
        _query_cache[cache_key] = ctx
    _save_file_cache(ctx, cache_path)
    _append_history(ctx)
    return ctx


def region_from_ticker(ticker: str, category: str = "") -> str:
    """根据标的代码或 category 推断 region。"""
    cat = str(category).lower()
    if "us" in cat or "美股" in cat:
        return "us"
    if "futures" in cat or "期货" in cat:
        return "futures"
    if "hk" in cat or "港股" in cat or (isinstance(ticker, str) and ticker.endswith(".HK")):
        return "hk"
    return "cn"


def format_daily_market_context_prompt_section(
    ctx: DailyMarketContext,
    compact: bool = True,
) -> str:
    """把市场上下文渲染成 prompt 段落。

    Args:
        ctx: DailyMarketContext 实例。
        compact: True 时输出精简版（用于个股预测），False 时输出完整版。

    Returns:
        prompt 字符串，包含 UNTRUSTED 护栏。
    """
    risk_tags = ", ".join(ctx.risk_tags.to_list()) or "无"
    lines = [
        "<!-- BEGIN_UNTRUSTED_MARKET_SUMMARY -->",
        f"【{ctx.region.upper()} 市场环境 ({ctx.date})】",
        f"- 综合宏观评分: {ctx.macro_score}/100 | 信号: {ctx.macro_signal} | 阶段: {ctx.market_phase}",
        f"- 红绿灯: {ctx.market_light} | Risk状态: {ctx.risk_state}",
        f"- 风险标签: {risk_tags}",
        f"- 建议仓位上限: {ctx.position_cap:.0%}",
    ]

    if ctx.index_scores and not compact:
        lines.append("- 大盘指数:")
        for s in ctx.index_scores:
            name = s.get("name", s.get("ticker", "?"))
            lines.append(
                f"  · {name}({s.get('ticker')}): 评分{s.get('score')} "
                f"1日{s.get('return_1d', 0):+.2f}% 5日{s.get('return_5d', 0):+.2f}%"
            )

    if ctx.sector_rotation and not compact:
        top = ctx.sector_rotation.get("top_sector", "unknown")
        lines.append(f"- 领涨板块: {top}")

    if ctx.global_semi:
        lines.append(
            f"- 全球半导体动量: {ctx.global_semi.get('composite_signal', 'neutral')} "
            f"(得分{ctx.global_semi.get('composite_score', 50)})"
        )

    lines.append("<!-- END_UNTRUSTED_MARKET_SUMMARY -->")
    return "\n".join(lines)


def get_default_position_cap(ctx: Optional[DailyMarketContext] = None) -> float:
    """返回当前市场上下文下的默认仓位上限。"""
    if ctx is None:
        try:
            ctx = get_context(region="cn", allow_generate=False)
        except Exception:
            return 1.0
    return ctx.position_cap if ctx else 1.0


if __name__ == "__main__":
    ctx = get_context(region="cn", force_refresh=True)
    print(ctx.to_dict())
    print("\n--- prompt section ---\n")
    print(format_daily_market_context_prompt_section(ctx, compact=False))
