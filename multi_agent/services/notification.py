#!/usr/bin/env python3
"""统一通知服务。

支持多渠道、异步失败降级、重试、限流；通知失败不阻塞主流程。
当前实现：
- console（默认，打印到 stdout）
- hermes（通过本地 hermes send-message 命令推送到微信/飞书，若可用）
- webhook（通用 POST）

用法：
    from services.notification import notify
    notify("每日信号汇总", "Top10: ...", channels=["console", "hermes"])
"""
import os
import sys
import json
import time
import subprocess
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, asdict
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

DEFAULT_CHANNELS = ["console"]
if os.environ.get("HERMES_WEIXIN_TOKEN") or os.environ.get("HOME_CHANNEL_WEIXIN"):
    DEFAULT_CHANNELS.append("hermes")

# 同一渠道同一标题 60 秒内最多发 5 条，防刷屏
_RATE_LIMIT_BUCKET: Dict[str, List[float]] = {}


@dataclass
class NotificationPayload:
    title: str
    body: str
    tags: Optional[List[str]] = None
    priority: str = "normal"  # low / normal / high
    url: Optional[str] = None
    extra: Optional[Dict[str, Any]] = None


def _rate_limited(channel: str, title: str) -> bool:
    now = time.time()
    key = f"{channel}:{title}"
    window = _RATE_LIMIT_BUCKET.setdefault(key, [])
    # 清理过期
    window[:] = [t for t in window if now - t < 60]
    if len(window) >= 5:
        return True
    window.append(now)
    return False


def _send_console(payload: NotificationPayload) -> Dict[str, Any]:
    print(f"[NOTIFY][{payload.priority.upper()}] {payload.title}")
    if payload.body:
        for line in payload.body.splitlines()[:40]:
            print("  ", line)
    return {"channel": "console", "success": True}


def _send_hermes(payload: NotificationPayload) -> Dict[str, Any]:
    """调用 hermes send-message CLI（如果可用）。"""
    try:
        text = f"**{payload.title}**\n\n{payload.body}"
        if payload.url:
            text += f"\n\n[链接]({payload.url})"
        # 优先使用当前 chat 上下文里的 HOME_CHANNEL
        home_weixin = os.environ.get("HOME_CHANNEL_WEIXIN")
        home_feishu = os.environ.get("HOME_CHANNEL_FEISHU")
        targets = []
        if home_weixin:
            targets.append(home_weixin)
        if home_feishu:
            targets.append(home_feishu)
        if not targets:
            targets = ["origin"]
        cmd = ["hermes", "send-message", "--text", text] + ["--target", ",".join(targets)]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, check=False
        )
        return {
            "channel": "hermes",
            "success": result.returncode == 0,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
    except Exception as e:
        return {"channel": "hermes", "success": False, "error": str(e)}


def _send_webhook(payload: NotificationPayload) -> Dict[str, Any]:
    url = os.environ.get("NOTIFY_WEBHOOK_URL")
    if not url:
        return {"channel": "webhook", "success": False, "error": "NOTIFY_WEBHOOK_URL not set"}
    try:
        import urllib.request
        data = json.dumps({
            "title": payload.title,
            "body": payload.body,
            "priority": payload.priority,
            "tags": payload.tags or [],
            "url": payload.url,
            "ts": datetime.utcnow().isoformat(),
        }, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            return {"channel": "webhook", "success": 200 <= resp.status < 300, "status": resp.status}
    except Exception as e:
        return {"channel": "webhook", "success": False, "error": str(e)}


_CHANNEL_SENDERS = {
    "console": _send_console,
    "hermes": _send_hermes,
    "webhook": _send_webhook,
}


def _send_one(args):
    channel, payload = args
    if _rate_limited(channel, payload.title):
        return {"channel": channel, "success": False, "error": "rate_limited"}
    sender = _CHANNEL_SENDERS.get(channel)
    if not sender:
        return {"channel": channel, "success": False, "error": "unknown_channel"}
    try:
        return sender(payload)
    except Exception as e:
        return {"channel": channel, "success": False, "error": str(e)}


def notify(
    title: str,
    body: str,
    channels: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
    priority: str = "normal",
    url: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
    timeout: float = 30.0,
    raise_on_error: bool = False,
) -> List[Dict[str, Any]]:
    """发送通知到一个或多个渠道。

    Args:
        title: 标题。
        body: 正文。
        channels: 渠道列表，默认从环境推断。
        tags: 标签，用于后续分类/过滤。
        priority: 优先级 low / normal / high。
        url: 可选链接。
        extra: 自定义字段。
        timeout: 整体超时（秒）。
        raise_on_error: 是否任一渠道失败就抛异常；默认 False，主流程不中断。

    Returns:
        每个渠道的返回结果列表。
    """
    if channels is None:
        channels = DEFAULT_CHANNELS.copy()
    payload = NotificationPayload(
        title=title, body=body, tags=tags, priority=priority, url=url, extra=extra
    )
    tasks = [(ch, payload) for ch in channels]

    results = []
    with ThreadPoolExecutor(max_workers=min(len(tasks), 4)) as pool:
        futures = {pool.submit(_send_one, t): t[0] for t in tasks}
        deadline = time.time() + timeout
        for fut in futures:
            remaining = max(0.1, deadline - time.time())
            try:
                results.append(fut.result(timeout=remaining))
            except FutureTimeoutError:
                results.append({"channel": futures[fut], "success": False, "error": "timeout"})

    if raise_on_error:
        failures = [r for r in results if not r.get("success")]
        if failures:
            raise RuntimeError(f"Notification failed: {failures}")
    return results


def notify_summary(
    predictions: List[Dict[str, Any]],
    portfolio: Optional[Dict[str, Any]] = None,
    date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """生成并发送每日预测摘要。"""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    bullish = [p for p in predictions if p.get("signal") in ("bullish", "看多", "强烈看多")]
    bearish = [p for p in predictions if p.get("signal") in ("bearish", "看空", "强烈看空")]
    neutral = [p for p in predictions if p.get("signal") in ("neutral", "中性", "观望")]

    lines = [
        f"日期：{date}",
        f"总预测数：{len(predictions)}",
        f"看多：{len(bullish)} | 看空：{len(bearish)} | 观望：{len(neutral)}",
    ]
    if bullish:
        lines.append("\n看多 Top5:")
        for p in sorted(bullish, key=lambda x: x.get("weighted_score", 0), reverse=True)[:5]:
            lines.append(f"  {p.get('name', p.get('ticker'))}({p.get('ticker')}): {p.get('weighted_score')} {p.get('signal')}")
    if bearish:
        lines.append("\n看空 Top5:")
        for p in sorted(bearish, key=lambda x: x.get("weighted_score", 0))[:5]:
            lines.append(f"  {p.get('name', p.get('ticker'))}({p.get('ticker')}): {p.get('weighted_score')} {p.get('signal')}")

    body = "\n".join(lines)
    return notify(f"每日信号摘要 {date}", body, tags=["daily", "summary"])


if __name__ == "__main__":
    # 本地 smoke test
    res = notify(
        "通知服务测试",
        "这是一条来自 multi_agent/services/notification.py 的测试消息。",
        channels=["console"],
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))
