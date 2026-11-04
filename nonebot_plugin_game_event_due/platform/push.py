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
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from nonebot import logger

from .capability import Platform, capability_of, detect_platform
from .sender import plan_chunks
from .session import SessionKey, parse_session_key


@dataclass(frozen=True, slots=True)
class _Chunk:
    """渲染用的最小单元：一段文字或一张图的地址。

    只依赖 ``is_image`` / ``value`` 两个属性，所以可以直接吃
    ``message_formatter.MessagePart``；platform 层不必反向导入 events 层。
    """

    is_image: bool
    value: str


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


# ---- 图文混排 ----


def _cq_image(url: str) -> str:
    """OneBot v11 的图片 CQ 码。"""
    safe = url.replace("&", "&amp;").replace("[", "&#91;").replace("]", "&#93;")
    return f"[CQ:image,file={safe}]"


def parts_to_markdown(parts: Sequence[Any], width: int = 640) -> str:
    """把消息片段渲染成 QQ 官方 Bot 的 markdown。

    图片按行内图片写；标题与列表行沿用已上线验证过的排版（``###`` / ``**`` /
    ``-``），因为 QQ 的 markdown 渲染器对裸文本换行不敏感，必须用列表项才能
    保证一项一行。认不出的行原样输出，不做猜测。
    """
    lines: list[str] = []
    for part in parts:
        if part.is_image:
            lines.append(f"![封面 #{width}px]({part.value})")
            lines.append("")
            continue
        for raw in part.value.split("\n"):
            line = raw.rstrip()
            if not line:
                lines.append("")
            elif line.startswith("🎮 "):
                lines.append(f"### {line[2:]}")
                lines.append("")
            elif line.startswith("⏰ "):
                lines.append(f"## {line}")
                lines.append("")
            elif line.startswith("▎"):
                # 列表项后面紧跟一行加粗文字，会被 markdown 吞进上一个列表项，
                # 所以这类小标题前必须先空一行
                if lines and lines[-1].startswith("- "):
                    lines.append("")
                lines.append(f"**{line}**")
                lines.append("")
            elif line.startswith("· "):
                body = line[2:]
                title, _, stamp = body.rpartition("　")
                lines.append(f"- {title}　**{stamp}**" if stamp else f"- {body}")
            else:
                lines.append(line)
    # 压掉连续空行，QQ 对多余空行的处理不稳定
    out: list[str] = []
    for line in lines:
        if not line and out and not out[-1]:
            continue
        out.append(line)
    return "\n".join(out).strip()


def parts_to_plain(parts: Sequence[Any]) -> str:
    """纯文本降级：图片退化成可点的链接，不丢信息。"""
    chunks: list[str] = []
    for part in parts:
        if part.is_image:
            chunks.append(f"封面：{part.value}")
        elif part.value:
            chunks.append(part.value)
    return "\n".join(chunks).strip()


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


async def send_parts(
    bot: Any,
    session: SessionKey,
    parts: Sequence[Any],
    *,
    cover_width: int = 640,
    media: Any = None,
) -> bool:
    """发一条可能带图的消息。

    没有图片片段时就是普通文本发送。有图片时按平台走各自的路径：

    * OneBot：图片以 CQ 码内联，和文字同一条消息；
    * QQ 官方：图片先上传素材库换成 ``qqbot-media://`` 地址，再用 markdown
      发送（``msg_type=2``）。频道等不保证支持 markdown 的场景退回纯文本。

    任何一步不成立都退回纯文本，绝不让配图问题导致整条推送丢失。
    """
    if not parts:
        return True

    if not any(part.is_image for part in parts):
        return await send_to_session(bot, session, parts_to_plain(parts))

    platform = detect_platform(bot)
    capability = capability_of(bot, scope=session.scope)
    plain = parts_to_plain(parts)

    if platform is Platform.ONEBOT:
        rendered = parts_to_onebot(parts)
        if len(rendered) > capability.message_limit:
            return await send_to_session(bot, session, plain)
        return await send_chunk(bot, session, rendered)

    if platform is Platform.QQ and session.scope in ("group", "private"):
        if media is None:
            return await send_to_session(bot, session, plain)
        uploaded: dict[str, str] = {}
        resolved: list[_Chunk] = []
        for part in parts:
            if part.is_image:
                url = uploaded.get(part.value)
                if url is None:
                    url = await media.upload_image(bot, part.value)
                    uploaded[part.value] = url
                if not url:
                    # 传不上去就当这张图不存在，文字照发
                    continue
                resolved.append(_Chunk(True, url))
            else:
                resolved.append(_Chunk(False, part.value))
        markdown = parts_to_markdown(resolved, cover_width)
        if markdown and len(markdown) <= capability.message_limit:
            if await _send_markdown(bot, session, markdown):
                return True
        return await send_to_session(bot, session, plain)

    return await send_to_session(bot, session, plain)


async def _send_markdown(bot: Any, session: SessionKey, markdown: str) -> bool:
    """用 markdown（``msg_type=2``）发送。适配器不支持时返回 False。"""
    module = _adapter_message_module(bot)
    segment = getattr(module, "MessageSegment", None) if module else None
    if segment is None or not hasattr(segment, "markdown"):
        return False
    try:
        message = segment.markdown(markdown)
    except Exception:  # 构造失败就直接降级
        return False

    if session.scope == "group":
        return await _call(
            bot, "send_to_group", group_openid=session.target_id, message=message
        )
    if session.scope == "private":
        return await _call(
            bot, "send_to_c2c", openid=session.target_id, message=message
        )
    return False


def _adapter_message_module(bot: Any) -> Any:
    """拿到当前适配器的 ``message`` 模块；取不到返回 ``None``。"""
    try:
        adapter_module = type(bot).__module__.rsplit(".", 1)[0]
        module = sys.modules.get(f"{adapter_module}.message")
        if module is None:
            module = importlib.import_module(f"{adapter_module}.message")
        return module
    except Exception:  # 取不到就不发 markdown
        return None


async def push_text(bot: Any, target: str, text: str) -> bool:
    """便捷函数：``target`` 是配置里的 ``平台:作用域:ID`` 字符串。"""
    session = parse_session_key(target)
    if session is None:
        logger.warning(f"推送目标格式不正确，应为 平台:作用域:ID：{target}")
        return False
    return await send_to_session(bot, session, text)
