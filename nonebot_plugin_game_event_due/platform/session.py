"""会话标识归一。

AstrBot 的 ``unified_msg_origin`` 形如 ``aiocqhttp:GroupMessage:123456``，
OneBot v11 的 ``get_session_id()`` 形如 ``group_123456`` / ``private_789``，
QQ 官方 Bot 又是 ``group_xxx`` / ``c2c_xxx``。

订阅、提醒状态要以会话为单位持久化，所以需要一个跨平台稳定的 key。
这里统一产出 ``<platform>:<scope>:<target_id>``，例如：

- ``onebot:group:123456``
- ``onebot:private:789``
- ``qq:group:abcdef``
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .capability import Platform, detect_platform, detect_scope


@dataclass(frozen=True)
class SessionKey:
    """一个可持久化的会话标识。

    ``platform`` 允许传字符串（配置、JSON 里读出来都是字符串），构造时统一
    转成 ``Platform``，避免 ``__str__`` 里出现 ``'str' object has no attribute
    'value'`` 这种只在运行时才暴露的问题。
    """

    platform: Platform
    scope: str  # group / private / channel
    target_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.platform, Platform):
            try:
                normalized = Platform(str(self.platform).strip().lower())
            except ValueError:
                normalized = Platform.UNKNOWN
            # frozen dataclass 只能用 object.__setattr__ 赋值
            object.__setattr__(self, "platform", normalized)
        object.__setattr__(self, "scope", str(self.scope).strip().lower())
        object.__setattr__(self, "target_id", str(self.target_id).strip())

    def __str__(self) -> str:
        return f"{self.platform.value}:{self.scope}:{self.target_id}"

    @property
    def key(self) -> str:
        return str(self)

    @property
    def is_group(self) -> bool:
        return self.scope == "group"


def _extract_target_id(event: Any) -> str:
    """从事件里尽力取到会话目标 ID。"""
    # OneBot v11：群消息有 group_id，私聊用 user_id
    group_id = getattr(event, "group_id", None)
    if group_id:
        return str(group_id)
    # QQ 官方适配器：群消息的 group_openid / 私聊的 user_openid
    for attr in ("group_openid", "channel_id", "guild_id", "user_openid"):
        value = getattr(event, attr, None)
        if value:
            return str(value)
    # 退回到发送者 ID
    for attr in ("user_id", "author"):
        value = getattr(event, attr, None)
        if value is not None:
            inner = getattr(value, "id", None) or getattr(value, "user_openid", None)
            return str(inner if inner is not None else value)
    return ""


def build_session_key(bot: Any, event: Any) -> SessionKey | None:
    """构造会话 key；拿不到目标 ID 时返回 None（例如心跳事件）。"""
    if event is None:
        return None
    target = _extract_target_id(event)
    if not target:
        return None

    platform = detect_platform(bot)
    scope = detect_scope(event)
    return SessionKey(platform=platform, scope=scope, target_id=target)


def parse_session_key(text: str) -> SessionKey | None:
    """反解 ``platform:scope:id``，用于从配置读取推送目标。"""
    parts = str(text or "").strip().split(":", 2)
    if len(parts) != 3:
        return None
    platform_raw, scope, target = parts
    try:
        platform = Platform(platform_raw)
    except ValueError:
        platform = Platform.UNKNOWN
    if scope not in ("group", "private", "channel") or not target:
        return None
    return SessionKey(platform=platform, scope=scope, target_id=target)
