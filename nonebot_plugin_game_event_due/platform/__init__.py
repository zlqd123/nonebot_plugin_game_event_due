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
    parts_to_onebot,
    parts_to_plain,
    parts_to_text_only,
    push_text,
    send_chunk,
    send_parts,
    send_to_session,
    split_game_blocks,
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
    "OutgoingBatch",
    "OutgoingLink",
    "OutgoingText",
    "Platform",
    "Sender",
    "SessionKey",
    "build_session_key",
    "capability_of",
    "detect_platform",
    "detect_scope",
    "is_group_message",
    "is_private_message",
    "parse_session_key",
    "parts_to_onebot",
    "parts_to_plain",
    "parts_to_text_only",
    "plan_chunks",
    "push_text",
    "send_chunk",
    "send_long",
    "send_parts",
    "send_to_session",
    "split_game_blocks",
    "split_text",
]
