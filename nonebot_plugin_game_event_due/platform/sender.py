"""统一发送层：把「意图」翻译成各平台的具体消息。

业务层只说三件事：发一段文字、发一批条目、发一条链接。至于本地图能不能直发、
合并转发能不能用、长文要不要切段，全部在这里按能力矩阵决定。

**纯文字优先**：本插件默认不依赖图片通道，所有图片统一降级为链接文本。

**分段要受回复次数限制**：QQ 官方群聊对一条用户消息最多被动回复 5 次，超了
直接报 ``40034128``。所以切完段还要用 ``Capability.clamp_chunks`` 夹一下，
宁可把尾巴并进最后一条，也不要静默丢内容。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nonebot import logger

from .capability import (
    UNLIMITED,
    Capability,
    capability_of,
    detect_scope,
)

NEWLINE = "\n"


@dataclass
class OutgoingText:
    """一段待发送的纯文本。"""

    text: str
    #: 是否作为「标题行」加粗（纯文本平台用【】包裹）
    title: bool = False

    def render(self) -> str:
        return f"【{self.text}】" if self.title else self.text


@dataclass
class OutgoingLink:
    """一条链接（原插件的图片全部降级成这个）。"""

    label: str
    url: str

    def render(self) -> str:
        return f"{self.label}：{self.url}" if self.label else self.url


@dataclass
class OutgoingBatch:
    """一组要一起发出的内容。"""

    items: list[Any] = field(default_factory=list)

    def add_text(self, text: str, *, title: bool = False) -> "OutgoingBatch":
        self.items.append(OutgoingText(text, title=title))
        return self

    def add_link(self, label: str, url: str) -> "OutgoingBatch":
        self.items.append(OutgoingLink(label, url))
        return self

    def render(self) -> str:
        return NEWLINE.join(item.render() for item in self.items)

    def __bool__(self) -> bool:
        return bool(self.items)


def split_text(text: str, limit: int) -> list[str]:
    """按上限切分文本，尽量在换行处断开，避免把链接或句子切坏。"""
    text = text or ""
    if limit <= 0 or len(text) <= limit:
        return [text] if text else []

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        window = remaining[:limit]
        # 优先在最后一个换行处断开
        cut = window.rfind(NEWLINE)
        if cut < limit // 2:  # 换行太靠前就退回按空格
            cut = window.rfind(" ")
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip(NEWLINE)
    return [chunk for chunk in chunks if chunk]


def plan_chunks(text: str, capability: Capability) -> list[str]:
    """计算最终要发几段：先按长度切，再按回复次数上限夹。"""
    chunks = split_text(text, capability.message_limit)
    clamped = capability.clamp_chunks(chunks)
    if len(clamped) < len(chunks):
        logger.debug(
            f"{capability.platform.value} 被动回复次数上限 {capability.max_replies}，"
            f"分段由 {len(chunks)} 合并为 {len(clamped)}"
        )
    return clamped


class Sender:
    """一个 Bot 的发送器。"""

    def __init__(
        self,
        bot: Any,
        capability: Capability | None = None,
        *,
        event: Any = None,
        overrides: dict[str, Any] | None = None,
    ):
        self.bot = bot
        if capability is not None:
            self.capability = capability
        else:
            scope = detect_scope(event) if event is not None else None
            self.capability = capability_of(bot, overrides=overrides, scope=scope)

    @property
    def limit(self) -> int:
        return self.capability.message_limit

    @property
    def max_replies(self) -> int:
        return self.capability.max_replies

    async def send_text(self, event: Any, text: str) -> list[str]:
        """发送纯文本，按平台上限自动分段。返回实际发出去的分段。"""
        chunks = plan_chunks(text, self.capability)
        for chunk in chunks:
            await self._send(event, chunk)
        return chunks

    async def send_batch(self, event: Any, batch: OutgoingBatch) -> None:
        await self.send_text(event, batch.render())

    async def _send(self, event: Any, text: str) -> None:
        """真正调用平台 API。

        NoneBot 的 ``Bot.send`` 是各适配器统一入口，签名都是 (event, message, **kw)，
        所以这里不需要按平台分支——分段与降级已经在上层做完了。
        """
        if not text:
            return
        try:
            await self.bot.send(event, text)
        except Exception as exc:  # noqa: BLE001 - 发送失败不应打断整个流程
            logger.opt(exception=exc).warning(f"发送消息失败：{exc}")
            raise


async def send_long(bot: Any, event: Any, text: str) -> list[str]:
    """便捷函数：按能力分段发送长文本。"""
    return await Sender(bot, event=event).send_text(event, text)


__all__ = [
    "UNLIMITED",
    "OutgoingBatch",
    "OutgoingLink",
    "OutgoingText",
    "Sender",
    "NEWLINE",
    "plan_chunks",
    "send_long",
    "split_text",
]
