"""主动推送：没有收到事件时也要把消息发到指定会话。

被动回复用 ``bot.send(event, ...)`` 就够了，但活动提醒、每日推送都发生在
定时任务里，此时手上只有配置里的 ``平台:作用域:ID``，没有事件对象。各平台
的能力差异在这里集中抹平：

- OneBot v11：``send_group_msg`` / ``send_private_msg``
- QQ 官方：``send_to_group`` / ``send_to_c2c`` / ``send_to_channel``

拿不到对应方法时返回 ``False``，调用方据此决定是否重试，而不是抛异常把整轮
轮询打断。
"""

from __future__ import annotations

import importlib
import sys
from typing import Any

from nonebot import logger

from .capability import Platform, capability_of, detect_platform
from .sender import plan_chunks
from .session import SessionKey, parse_session_key


def _wrap_text(bot: Any, text: str) -> Any:
    """把文本包成当前 bot 对应适配器的 Message 对象。

    NoneBot 的 API 同时接受 ``str`` 与 ``Message``，包一层没有副作用，却能
    让「对 message 调 extract_plain_text」的第三方补丁正常工作。取不到
    Message 类时退回原始字符串，不因为这点小事让推送失败。
    """
    try:
        adapter_module = type(bot).__module__.rsplit(".", 1)[0]
        module = sys.modules.get(f"{adapter_module}.message")
        if module is None:
            module = importlib.import_module(f"{adapter_module}.message")
        return module.Message(text)
    except Exception:  # noqa: BLE001 - 包不上就用 str，平台自己能处理
        return text


async def _call(bot: Any, api: str, **data: Any) -> bool:
    """调用 bot 上的方法或 API；不存在或失败都返回 False。"""
    method = getattr(bot, api, None)
    if method is None:
        return False
    try:
        await method(**data)
        return True
    except Exception as exc:  # noqa: BLE001 - 推送失败只记录，不中断调度
        logger.opt(exception=exc).warning(f"主动推送失败 {api}: {exc}")
        return False


async def send_to_session(bot: Any, session: SessionKey, text: str) -> bool:
    """把一段文本发到会话，按平台上限自动分段。全部成功才算成功。

    主动推送不受「被动回复次数」限制，所以这里不夹 ``max_replies``——
    那个上限只作用于被动回复。
    """
    if not text:
        return True

    capability = capability_of(bot, scope=session.scope)
    chunks = plan_chunks(text, capability)
    for chunk in chunks:
        if not await send_chunk(bot, session, chunk):
            return False
    return True


async def send_chunk(bot: Any, session: SessionKey, text: str) -> bool:
    """发送单段文本（不再切分）。"""
    platform = detect_platform(bot)

    if platform is Platform.ONEBOT:
        if session.scope == "group":
            return await _call(
                bot,
                "send_group_msg",
                group_id=_as_int(session.target_id),
                message=_wrap_text(bot, text),
            )
        return await _call(
            bot,
            "send_private_msg",
            user_id=_as_int(session.target_id),
            message=_wrap_text(bot, text),
        )

    if platform is Platform.QQ:
        if session.scope == "group":
            return await _call(
                bot,
                "send_to_group",
                group_openid=session.target_id,
                message=_wrap_text(bot, text),
            )
        if session.scope == "channel":
            return await _call(
                bot,
                "send_to_channel",
                channel_id=session.target_id,
                message=_wrap_text(bot, text),
            )
        return await _call(
            bot,
            "send_to_c2c",
            openid=session.target_id,
            message=_wrap_text(bot, text),
        )

    # 未知平台：退回统一 send，但需要事件对象，这里只能放弃
    logger.warning(f"未知平台 {platform}，无法主动推送到 {session}")
    return False


def _as_int(value: str) -> int | str:
    """OneBot 的群号/QQ 号是整数；不是纯数字时原样传，让平台自己报错。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


async def push_text(bot: Any, target: str, text: str) -> bool:
    """便捷函数：``target`` 是配置里的 ``平台:作用域:ID`` 字符串。"""
    session = parse_session_key(target)
    if session is None:
        logger.warning(f"推送目标格式不正确，应为 平台:作用域:ID：{target}")
        return False
    return await send_to_session(bot, session, text)
