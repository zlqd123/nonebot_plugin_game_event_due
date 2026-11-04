"""主动推送：没有收到事件时也要把消息发到指定会话。

被动回复用 ``bot.send(event, ...)`` 就够了，但活动提醒、每日推送都发生在
定时任务里，此时手上只有配置里的 ``平台:作用域:ID``，没有事件对象。各平台
的能力差异在这里集中抹平：

- OneBot v11：``send_group_msg`` / ``send_private_msg``，图片用 CQ 码内联，
  真正的一条消息里图文混排；
- QQ 官方：``send_to_group`` / ``send_to_c2c`` / ``send_to_channel``。图片只
  能走 ``msg_type=7`` 富媒体，**一条消息一张图**，所以多游戏时按游戏拆成
  多条，每条是「一张封面 + 该游戏的活动清单」。

拿不到对应方法时返回 ``False``，调用方据此决定是否重试，而不是抛异常把整轮
轮询打断。
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Sequence
from typing import Any

from nonebot import logger

from .capability import Platform, capability_of, detect_platform
from .media import fetch_image, guess_suffix
from .sender import plan_chunks
from .session import SessionKey, parse_session_key

#: OneBot 图片消息的软上限。CQ 码会额外占几十个字符，留点余量。
ONE_BOT_IMAGE_LIMIT = 4000


def _wrap_text(bot: Any, text: str) -> Any:
    """把文本包成当前 bot 对应适配器的 Message 对象。

    NoneBot 的 API 同时接受 ``str`` 与 ``Message``，包一层没有副作用，却能
    让「对 message 调 extract_plain_text」的第三方补丁正常工作。取不到
    Message 类时退回原始字符串，不因为这点小事让推送失败。
    """
    try:
        module = _adapter_message_module(bot)
        if module is None:
            return text
        return module.Message(text)
    except Exception:  # 包不上就用 str，平台自己能处理
        return text


async def _call(bot: Any, api: str, **data: Any) -> bool:
    """调用 bot 上的方法或 API；不存在或失败都返回 False。"""
    method = getattr(bot, api, None)
    if method is None:
        return False
    try:
        await method(**data)
        return True
    except Exception as exc:  # 推送失败只记录，不中断调度
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


# ---- 图文混排 ----


def _cq_image(url: str) -> str:
    """OneBot v11 的图片 CQ 码。"""
    safe = url.replace("&", "&amp;").replace("[", "&#91;").replace("]", "&#93;")
    return f"[CQ:image,file={safe}]"


def parts_to_plain(parts: Sequence[Any]) -> str:
    """纯文本渲染：图片退化成可点的链接，不丢信息。"""
    chunks: list[str] = []
    for part in parts:
        if part.is_image:
            chunks.append(f"封面：{part.value}")
        elif part.value:
            chunks.append(part.value)
    return "\n".join(chunks).strip()


def parts_to_text_only(parts: Sequence[Any]) -> str:
    """只要文字，丢掉图片片段（图已经单独作为媒体发出去了）。"""
    lines = [part.value.rstrip() for part in parts if not part.is_image]
    return "\n".join(line for line in lines if line).strip()


def parts_to_onebot(parts: Sequence[Any]) -> str:
    """OneBot v11：图片以 CQ 码内联，图片前后各留一个换行。"""
    out: list[str] = []
    for part in parts:
        if part.is_image:
            if out and not out[-1].endswith("\n"):
                out.append("\n")
            out.append(_cq_image(part.value))
            out.append("\n")
        else:
            out.append(part.value)
    return "".join(out).strip()


def split_game_blocks(parts: Sequence[Any]) -> list[list[Any]]:
    """按游戏标题切开，让 QQ 那边能一个游戏发一条。

    没有 ``🎮`` 标题的消息原样返回一个块；开头的日期标题（``⏰``）会并入
    第一个游戏块，不单独成条。
    """
    preamble: list[Any] = []
    blocks: list[list[Any]] = []
    for part in parts:
        if not part.is_image and part.value.startswith("🎮 "):
            blocks.append([part])
        elif blocks:
            blocks[-1].append(part)
        else:
            preamble.append(part)
    if preamble:
        if blocks:
            blocks[0] = preamble + blocks[0]
        else:
            blocks = [preamble]
    return blocks


async def send_parts(
    bot: Any,
    session: SessionKey,
    parts: Sequence[Any],
    *,
    cover_width: int = 640,
) -> bool:
    """发一条可能带图的消息。

    没有图片片段时就是普通文本发送。有图片时按平台走各自的路径：

    * OneBot：图片以 CQ 码内联，真正的一条消息；
    * QQ 官方：``msg_type=7`` 富媒体，一个游戏一条（一条一张图）。频道等不
      支持富媒体的场景退回纯文本。

    任何一步不成立都退回纯文本，绝不让配图问题导致整条推送丢失。
    ``cover_width`` 保留给未来可能重新支持的 markdown 排版，目前不使用。
    """
    if not parts:
        return True

    if not any(part.is_image for part in parts):
        return await send_to_session(bot, session, parts_to_plain(parts))

    platform = detect_platform(bot)
    plain = parts_to_plain(parts)

    if platform is Platform.ONEBOT:
        rendered = parts_to_onebot(parts)
        if len(rendered) > ONE_BOT_IMAGE_LIMIT:
            return await send_to_session(bot, session, plain)
        return await send_chunk(bot, session, rendered)

    if platform is Platform.QQ and session.scope in ("group", "private"):
        ok = True
        for block in split_game_blocks(parts):
            if not await _send_qq_block(bot, session, block):
                ok = False
        return ok

    return await send_to_session(bot, session, plain)


async def _send_qq_block(bot: Any, session: SessionKey, block: Sequence[Any]) -> bool:
    """QQ 官方：一条「文字 + 一张图」的富媒体消息，失败就只发文字。"""
    text = parts_to_text_only(block)
    image_url = next((part.value for part in block if part.is_image), "")

    if image_url:
        message = await _build_rich_message(bot, text, image_url)
        if message is not None:
            if await _send_message(bot, session, message):
                return True
    if not text:
        return not image_url
    return await send_to_session(bot, session, text)


async def _build_rich_message(bot: Any, text: str, image_url: str) -> Any:
    """拼出「文字 + 本地图片」的消息；任一步失败返回 ``None``。"""
    module = _adapter_message_module(bot)
    segment = getattr(module, "MessageSegment", None) if module else None
    if segment is None or not hasattr(segment, "file_image"):
        return None

    data = await fetch_image(bot, image_url)
    if not data:
        return None
    try:
        image = segment.file_image(data, f"cover{guess_suffix(data)}")
        return (segment.text(text) + image) if text else image
    except Exception as exc:  # 构造失败就退回纯文字
        logger.opt(exception=exc).warning(f"构造图文消息失败: {exc}")
        return None


async def _send_message(bot: Any, session: SessionKey, message: Any) -> bool:
    if session.scope == "group":
        return await _call(
            bot, "send_to_group", group_openid=session.target_id, message=message
        )
    if session.scope == "private":
        return await _call(bot, "send_to_c2c", openid=session.target_id, message=message)
    return False


def _adapter_message_module(bot: Any) -> Any:
    """拿到当前适配器的 ``message`` 模块；取不到返回 ``None``。"""
    try:
        adapter_module = type(bot).__module__.rsplit(".", 1)[0]
        module = sys.modules.get(f"{adapter_module}.message")
        if module is None:
            module = importlib.import_module(f"{adapter_module}.message")
        return module
    except Exception:  # 取不到就发不了富媒体
        return None


async def push_text(bot: Any, target: str, text: str) -> bool:
    """便捷函数：``target`` 是配置里的 ``平台:作用域:ID`` 字符串。"""
    session = parse_session_key(target)
    if session is None:
        logger.warning(f"推送目标格式不正确，应为 平台:作用域:ID：{target}")
        return False
    return await send_to_session(bot, session, text)
