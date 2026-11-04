"""跨平台抽象层：能力探测、会话标识、统一发送。"""

from .capability import (
    UNLIMITED,
    Capability,
    Platform,
    capability_of,
    detect_platform,
    detect_scope,
    is_group_message,
    is_private_message,
)
from .push import (
    parts_to_markdown,
    parts_to_onebot,
    parts_to_plain,
    push_text,
    send_chunk,
    send_parts,
    send_to_session,
)
from .sender import (
    OutgoingBatch,
    OutgoingLink,
    OutgoingText,
    Sender,
    plan_chunks,
    send_long,
    split_text,
)
from .session import SessionKey, build_session_key, parse_session_key

__all__ = [
    "UNLIMITED",
    "Capability",
    "Platform",
    "capability_of",
    "detect_platform",
    "detect_scope",
    "is_group_message",
    "is_private_message",
    "parts_to_markdown",
    "parts_to_onebot",
    "parts_to_plain",
    "push_text",
    "send_chunk",
    "send_parts",
    "send_to_session",
    "OutgoingBatch",
    "OutgoingLink",
    "OutgoingText",
    "Sender",
    "plan_chunks",
    "send_long",
    "split_text",
    "SessionKey",
    "build_session_key",
    "parse_session_key",
]
