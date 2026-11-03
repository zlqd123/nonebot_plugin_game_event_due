"""活动消息的投递层。

原插件用 AstrBot 的 ``MessageChain`` + ``Comp.Image`` 发图，NoneBot 要同时兼容
OneBot v11 与 QQ 官方 Bot，能力差异很大。本插件只发文字，所以这里只做三件事：

1. 把 ``MessagePart`` 渲染成纯文本（封面降级为链接）；
2. 长文本按平台上限分段，OneBot 能合并转发时用合并转发；
3. 区分「回复当前事件」与「主动推送到会话」两条路径。

投递失败必须返回 ``False``，让轮询层不要把这些提醒标记成已推送。
"""

from __future__ import annotations

import nonebot
from nonebot import logger
from nonebot.adapters import Bot

from ..platform import (
    Capability,
    Platform,
    Sender,
    SessionKey,
    capability_of,
    detect_platform,
    send_to_session,
)
from .services.message_formatter import MessagePart, MessageSettings, parts_to_text


def render(parts: list[MessagePart]) -> str:
    """把消息片段渲染成纯文本；封面渲染成「封面：URL」。"""
    return parts_to_text(parts)


def resolve_bot(platform: Platform) -> Bot | None:
    """从已连接的 Bot 里挑一个匹配平台的，用于主动推送。"""
    for bot in nonebot.get_bots().values():
        if platform is Platform.UNKNOWN or detect_platform(bot) is platform:
            return bot
    return None


class Delivery:
    """把消息送到目标会话。"""

    def __init__(self, settings: MessageSettings) -> None:
        self.settings = settings

    # ---- 回复当前事件 ----

    async def reply(
        self,
        bot: Bot,
        event,
        parts: list[MessagePart],
        *,
        title: str = "",
    ) -> bool:
        """回复触发指令的会话。"""
        text = render(parts)
        if not text:
            return True

        capability = capability_of(bot)
        try:
            await Sender(bot, capability).send_text(event, text)
            return True
        except Exception as exc:  # noqa: BLE001 - 发送失败交由调用方决定
            logger.opt(exception=exc).warning(f"回复消息失败: {exc}")
            return False

    # ---- 主动推送 ----

    async def push(
        self,
        session: SessionKey,
        parts: list[MessagePart],
    ) -> bool:
        """主动推送到会话（定时任务里没有事件对象）。"""
        text = render(parts)
        if not text:
            return True

        bot = resolve_bot(session.platform)
        if bot is None:
            logger.warning(f"没有可用的 {session.platform.value} Bot，无法推送到 {session}")
            return False

        return await send_to_session(bot, session, text)

    async def push_text(self, session: SessionKey, text: str) -> bool:
        """推一段纯文本（用于错误提示）。"""
        if not text:
            return True
        bot = resolve_bot(session.platform)
        if bot is None:
            logger.warning(f"没有可用的 {session.platform.value} Bot，无法推送到 {session}")
            return False
        return await send_to_session(bot, session, text)


def capability_for(bot: Bot) -> Capability:
    return capability_of(bot)
