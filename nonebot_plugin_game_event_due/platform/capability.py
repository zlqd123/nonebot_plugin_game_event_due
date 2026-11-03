"""平台能力探测。

四个来源插件都写死了 AstrBot / Koishi 的能力假设：本地图片直发、合并转发、
超长消息。换成 NoneBot 后要同时支持 OneBot v11 和 QQ 官方 Bot，两者能力差异很
大，所以业务层不能直接调用平台 API，必须先问「这个平台能做什么」。

**QQ 官方 Bot 的约束来自开放平台文档**（``bot.q.qq.com/wiki/develop/api-v2``），
和 OneBot 有本质差别，不能靠经验值糊过去：

- 被动回复有时效与次数上限：群聊 **5 分钟 / 每条消息最多回 5 次**，
  单聊 **60 分钟 / 4 次**。超出返回 ``40034128``。所以「把长文切成 10 段」
  在 QQ 群里会直接失败，必须合并而不是无限拆。
- ``msg_type`` 只有 0 文本 / 2 Markdown / 7 富媒体 / 8 卡片；**没有合并转发**，
  所以 OneBot 的 forward 节点在 QQ 上必须降级成普通文本。
- 长度超限返回 ``40054007``；部分场景发送 URL 会被拒（``40054010``）。
- 富媒体要先上传换 ``file_info``，且单聊与群聊的上传接口**不互通**；
  图片段只接受 URL，本地文件发不出去——这正是本插件纯文字的直接原因。

本模块不做 IO，只根据 Bot / Event 的类型与属性判定能力，便于单元测试。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

#: 没有次数上限时用这个值
UNLIMITED = 0


class Platform(str, Enum):
    """插件认识的平台。"""

    ONEBOT = "onebot"
    QQ = "qq"
    UNKNOWN = "unknown"


def detect_platform(bot: Any) -> Platform:
    """判断 Bot 属于哪个平台。

    **不能对模块路径做子串匹配**：``nonebot.adapters.qq.bot`` 里含 ``onebot``
    （n-*onebot*），会把 QQ Bot 误判成 OneBot。所以按包路径的**段**比较。
    真实 Bot 优先用适配器自己的名字，那是最权威的。
    """
    if bot is None:
        return Platform.UNKNOWN

    adapter = getattr(bot, "adapter", None)
    adapter_name = ""
    if adapter is not None:
        getter = getattr(adapter, "get_name", None)
        if callable(getter):
            try:
                adapter_name = str(getter() or "").lower()
            except Exception:  # noqa: BLE001 - 取名字失败不影响后续判断
                adapter_name = ""

    sections = set((type(bot).__module__ or "").lower().split("."))
    combined = f"{adapter_name} {type(bot).__module__ or ''}".lower()

    if "qqguild" in combined:
        # QQ 频道（旧版 adapter-qqguild），归到 QQ 官方体系
        return Platform.QQ
    if "qq" in sections or "adapter_qq" in sections or "adapter-qq" in adapter_name:
        return Platform.QQ
    if "onebot" in sections or "onebot" in adapter_name or "onebot" in combined.split():
        return Platform.ONEBOT
    return Platform.UNKNOWN


def detect_scope(event: Any) -> str:
    """判断事件发生在群聊、私聊还是频道。

    返回 ``"group"`` / ``"private"`` / ``"channel"``，认不出时给 ``"private"``
    （最保守：单聊的回复预算比群聊宽松，宁可多给也不要在群里撞上限）。
    """
    if event is None:
        return "private"
    name = type(event).__name__.lower()

    if "channel" in name and "c2c" not in name:
        return "channel"
    if "c2c" in name or "private" in name or "friend" in name or "dms" in name:
        return "private"
    if "group" in name:
        return "group"
    return "private"


@dataclass(frozen=True)
class Capability:
    """一个平台的发送能力。

    业务层只关心这几个布尔值与上限，不关心底层是 call_api 还是 send。
    """

    platform: Platform
    #: 能否直接发送本地文件图片（OneBot 可以，QQ 官方只认 URL）
    local_image: bool = False
    #: 能否发送远程 URL 图片
    url_image: bool = False
    #: 能否发送合并转发（QQ 官方没有群合并转发）
    forward_node: bool = False
    #: 单条消息的软上限（字符）。超出的文本需要分段。
    message_limit: int = 3000
    #: 是否支持 @ 到具体用户
    mention_user: bool = True
    #: 是否支持 Markdown
    markdown: bool = False

    #: 同一条用户消息最多能被动回复几次；``UNLIMITED`` 表示不限制。
    #: QQ 群 5 次、单聊 4 次，超过会被平台拒绝，所以分段数必须先被它夹住。
    max_replies: int = UNLIMITED
    #: 被动回复的有效期（秒）。定时任务拿不到 msg_id，走不到这条路，只用于提示。
    passive_window: int = UNLIMITED
    #: 该平台是否允许消息里出现 URL。False 时链接要降级成纯域名文本。
    url_allowed: bool = True

    def can(self, feature: str) -> bool:
        return bool(getattr(self, feature, False))

    def clamp_chunks(self, chunks: list[str]) -> list[str]:
        """把分段数压到 ``max_replies`` 以内。

        超预算时把尾部合并——宁可一条消息长一点（平台会报长度超限），也不要
        静默丢掉内容或撞上「回复次数超限」。
        """
        if self.max_replies == UNLIMITED or len(chunks) <= self.max_replies:
            return chunks

        keep = self.max_replies
        head = chunks[: keep - 1]
        tail = "\n".join(chunks[keep - 1:])
        return [*head, tail]


#: 各平台的默认能力。数字取自各平台公开限制，可在配置里覆盖。
_PROFILES: dict[Platform, Capability] = {
    Platform.ONEBOT: Capability(
        platform=Platform.ONEBOT,
        local_image=True,
        url_image=True,
        forward_node=True,
        message_limit=4000,
        mention_user=True,
        markdown=False,
    ),
    # QQ 官方 Bot：字段按开放平台文档设定，注意 max_replies 是硬限制
    Platform.QQ: Capability(
        platform=Platform.QQ,
        local_image=False,
        url_image=True,
        forward_node=False,
        message_limit=1500,
        mention_user=True,
        markdown=True,
        max_replies=5,
        passive_window=300,
        url_allowed=True,
    ),
    Platform.UNKNOWN: Capability(
        platform=Platform.UNKNOWN,
        local_image=False,
        url_image=True,
        forward_node=False,
        message_limit=1500,
        mention_user=True,
        markdown=False,
        max_replies=3,
        passive_window=300,
        url_allowed=True,
    ),
}


def capability_of(
    bot: Any,
    *,
    overrides: dict[str, Any] | None = None,
    scope: str | None = None,
) -> Capability:
    """拿到某个 Bot 的能力表。

    QQ 官方对群聊和对单聊的限制不同（群 5 次 / 单聊 4 次），所以传 ``scope``
    会拿到更准确的那一档；不传就用群聊的保守值。
    """
    platform = detect_platform(bot)
    profile = _PROFILES.get(platform, _PROFILES[Platform.UNKNOWN])

    data = {field: getattr(profile, field) for field in profile.__dataclass_fields__}

    if platform is Platform.QQ and scope is not None:
        if scope == "private":
            # 单聊：60 分钟、4 次
            data["max_replies"] = 4
            data["passive_window"] = 3600
        elif scope == "channel":
            # 频道：5 分钟，文档未给回复次数上限
            data["max_replies"] = UNLIMITED
            data["passive_window"] = 300
        else:
            data["max_replies"] = 5
            data["passive_window"] = 300

    if overrides:
        for key, value in overrides.items():
            if key in data:
                data[key] = value
    return Capability(**data)


def is_group_message(event: Any) -> bool:
    """判断是不是群聊消息（不同适配器类名不同）。"""
    return detect_scope(event) == "group"


def is_private_message(event: Any) -> bool:
    return detect_scope(event) == "private"
