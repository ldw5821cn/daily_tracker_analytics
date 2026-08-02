#!/usr/bin/env python3
"""统一配置注册表。

集中管理环境变量、watchlist、参数文件路径与默认值。
新增配置项时在此注册，避免各模块重复硬编码默认值。
"""
import os
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field

MULTI_AGENT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = MULTI_AGENT.parent
DATA_DIR = MULTI_AGENT / "data"


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name, "")
    if v.lower() in ("1", "true", "yes", "on"):
        return True
    if v.lower() in ("0", "false", "no", "off"):
        return False
    return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except Exception:
        return default


@dataclass(frozen=True)
class Registry:
    # 目录
    multi_agent: Path = MULTI_AGENT
    project_root: Path = PROJECT_ROOT
    data_dir: Path = DATA_DIR

    # 文件路径
    watchlist_path: Path = DATA_DIR / "watchlist.json"
    portfolio_state_path: Path = DATA_DIR / "portfolio_state.json"
    predictor_params_path: Path = MULTI_AGENT / "config" / "predictor_params.json"
    prediction_backtest_path: Path = DATA_DIR / "prediction_backtest.json"
    market_context_cache_path: Path = DATA_DIR / "market_context_cache.json"
    market_context_history_path: Path = DATA_DIR / "market_context_history.jsonl"

    # LLM / API
    llm_api_key: Optional[str] = field(default_factory=lambda: os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY"))
    llm_base_url: Optional[str] = field(default_factory=lambda: os.environ.get("LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL"))
    llm_model: str = field(default_factory=lambda: os.environ.get("LLM_MODEL") or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini")

    # 数据源
    tickflow_api_key: Optional[str] = field(default_factory=lambda: os.environ.get("TICKFLOW_API_KEY"))
    tushare_token: Optional[str] = field(default_factory=lambda: os.environ.get("TUSHARE_TOKEN"))
    newsapi_key: Optional[str] = field(default_factory=lambda: os.environ.get("NEWSAPI_API_KEY"))
    deepseek_api_key: Optional[str] = field(default_factory=lambda: os.environ.get("DEEPSEEK_API_KEY"))
    exa_api_key: Optional[str] = field(default_factory=lambda: os.environ.get("EXA_API_KEY"))

    # 通知
    hermes_weixin_token: Optional[str] = field(default_factory=lambda: os.environ.get("HERMES_WEIXIN_TOKEN"))
    home_channel_weixin: Optional[str] = field(default_factory=lambda: os.environ.get("HOME_CHANNEL_WEIXIN"))
    home_channel_feishu: Optional[str] = field(default_factory=lambda: os.environ.get("HOME_CHANNEL_FEISHU"))
    notify_webhook_url: Optional[str] = field(default_factory=lambda: os.environ.get("NOTIFY_WEBHOOK_URL"))

    # 业务开关
    use_data_loader_registry_v2: bool = field(default_factory=lambda: _env_bool("USE_DATA_LOADER_REGISTRY_V2", True))
    optimize_target: str = field(default_factory=lambda: os.environ.get("OPTIMIZE_TARGET", "forward_return"))
    em_min_interval: int = field(default_factory=lambda: _env_int("EM_MIN_INTERVAL", 600))

    # 雪球
    xueqiu_gid: Optional[str] = field(default_factory=lambda: os.environ.get("XUEQIU_GID"))

    # 默认值
    default_position_cap: float = 1.0
    max_workers: int = 8


# 全局单例，启动时加载
_config: Optional[Registry] = None


def get_config() -> Registry:
    global _config
    if _config is None:
        _config = Registry()
    return _config


def reload_config() -> Registry:
    global _config
    _config = Registry()
    return _config


def load_json(path: Path, default: Optional[Any] = None) -> Any:
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_watchlist(categories: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    cfg = get_config()
    wl = load_json(cfg.watchlist_path, [])
    if categories:
        wl = [w for w in wl if w.get("category") in categories]
    return wl


def load_predictor_params() -> Dict[str, Any]:
    cfg = get_config()
    return load_json(cfg.predictor_params_path, {})


if __name__ == "__main__":
    cfg = get_config()
    print(cfg)
