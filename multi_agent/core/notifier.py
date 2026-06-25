"""微信通知占位模块

实际微信推送由 Hermes cron job 在 pipeline 完成后读取报告并调用 send_message 发送。
本地运行 pipeline 时不直接发送微信, 避免依赖外部 webhook key。
"""


def send_weixin_message(message: str, **kwargs):
    """发送微信消息 (占位实现)"""
    # 实际发送由 Hermes cron 处理, pipeline 仅负责生成报告
    raise NotImplementedError(
        "微信推送请通过 Hermes cron job 完成, 或配置外部 webhook 后重写此函数。"
    )
