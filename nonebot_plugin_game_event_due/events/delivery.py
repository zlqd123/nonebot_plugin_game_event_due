"""活动消息的投递层。

原插件用 AstrBot 的 ``MessageChain`` + ``Comp.Image`` 发图；NoneBot 要同时
兼容 OneBot v11 与 QQ 官方 Bot，两者的发图方式完全不同，所以这里把差异交给
:func:`nonebot_plugin_game_event_due.platform.push.send_parts`：

- 消息里没有图片片段时，就是一条纯文本（和以前完全一致）；
- 有图片片段时，OneBot 走 CQ 码、QQ 官方走「上传素材 + markdown」，
  哪条路走不通都退回纯文本，图片换成可点的链接。

投递失败必须返回 ``False``，让轮询层不要把这些提醒标记成已推送。
"""

from __future__ import annotations

from collections.abc import Sequence

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
    send_parts,
    send_to_session,
)
from ..platform.media import MediaCache
from .services.message_formatter import MessagePart, MessageSettings, parts_to_text


def render(parts: Sequence[MessagePart]) -> str:
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

    def __init__(
        self,
        settings: MessageSettings,
        media_cache: MediaCache | None = None,
    ) -> None:
        self.settings = settings
        #: QQ 官方 Bot 的图片素材缓存；没配置就只用内存
        self.media = media_cache or MediaCache()

    # ---- 回复当前事件 ----

    async def reply(
        self,
        bot: Bot,
        event,
        parts: Sequence[MessagePart],
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
        except Exception as exc:  # 发送失败交由调用方决定
            logger.opt(exception=exc).warning(f"回复消息失败: {exc}")
            return False

    # ---- 主动推送 ----

    async def push(
        self,
        session: SessionKey,
        parts: Sequence[MessagePart],
    ) -> bool:
        """主动推送到会话（定时任务里没有事件对象）。

        有配图就尝试图文混排，没有就是纯文本；两种情况都由
        :func:`send_parts` 统一处理，失败自动降级。
        """
        if not parts:
            return True

        bot = resolve_bot(session.platform)
        if bot is None:
            logger.warning(f"没有可用的 {session.platform.value} Bot，无法推送到 {session}")
            return False

        return await send_parts(
            bot,
            session,
            parts,
            cover_width=self.settings.cover_width,
            media=self.media,
        )

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
