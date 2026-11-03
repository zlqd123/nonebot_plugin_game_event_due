"""主动推送与 QQ 官方会话映射的测试。

``platform/push.py`` 是定时任务唯一的出口，一旦映射错（比如把群 openid 传给
``send_to_c2c``），只会表现为「线上收不到消息」，很难在联调时发现。所以这里
用假 Bot 记录实际调用参数，把映射关系锁死。
"""

from __future__ import annotations

import pytest

from nonebot_plugin_game_event_due.platform import SessionKey, push_text, send_to_session
from nonebot_plugin_game_event_due.platform.session import (
    build_session_key,
    parse_session_key,
)


class RecordingBot:
    """记录所有被调用的「API 名 + 参数」。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def send_group_msg(self, **kwargs):
        self.calls.append(("send_group_msg", kwargs))

    async def send_private_msg(self, **kwargs):
        self.calls.append(("send_private_msg", kwargs))

    async def send_to_group(self, **kwargs):
        self.calls.append(("send_to_group", kwargs))

    async def send_to_channel(self, **kwargs):
        self.calls.append(("send_to_channel", kwargs))

    async def send_to_c2c(self, **kwargs):
        self.calls.append(("send_to_c2c", kwargs))


class OneBotRecordingBot(RecordingBot):
    pass


OneBotRecordingBot.__module__ = "nonebot.adapters.onebot.v11.bot"


class QQRecordingBot(RecordingBot):
    pass


QQRecordingBot.__module__ = "nonebot.adapters.qq.bot"


# ---- 会话键 ----


def test_session_key_str_roundtrip():
    key = SessionKey("onebot", "group", "123456")
    assert str(key) == "onebot:group:123456"
    assert parse_session_key(str(key)) == key


def test_parse_session_key_rejects_bad_scope():
    assert parse_session_key("onebot:guild:1") is None


def test_parse_session_key_rejects_missing_parts():
    assert parse_session_key("onebot:group") is None
    assert parse_session_key("just-a-string") is None


def test_build_session_key_needs_event():
    assert build_session_key(None, None) is None


# ---- OneBot 推送映射 ----


@pytest.mark.asyncio
async def test_onebot_group_push_uses_send_group_msg():
    bot = OneBotRecordingBot()
    ok = await send_to_session(bot, SessionKey("onebot", "group", "123456"), "hi")
    assert ok is True
    name, kwargs = bot.calls[0]
    assert name == "send_group_msg"
    # OneBot 的群号必须是整数，不能是字符串
    assert kwargs["group_id"] == 123456
    # 消息被包成 Message 对象，兼容会对 message 调 extract_plain_text 的第三方补丁
    message = kwargs["message"]
    assert getattr(message, "extract_plain_text", None) is not None
    assert message.extract_plain_text() == "hi"


@pytest.mark.asyncio
async def test_onebot_private_push_uses_send_private_msg():
    bot = OneBotRecordingBot()
    ok = await send_to_session(bot, SessionKey("onebot", "private", "10001"), "hi")
    assert ok is True
    name, kwargs = bot.calls[0]
    assert name == "send_private_msg"
    assert kwargs["user_id"] == 10001


# ---- QQ 官方推送映射 ----


@pytest.mark.asyncio
async def test_qq_group_push_uses_send_to_group():
    bot = QQRecordingBot()
    ok = await send_to_session(bot, SessionKey("qq", "group", "OPENID123"), "hi")
    assert ok is True
    name, kwargs = bot.calls[0]
    assert name == "send_to_group"
    # QQ 官方用的是 group_openid 字符串，不能转成 int
    assert kwargs["group_openid"] == "OPENID123"


@pytest.mark.asyncio
async def test_qq_private_push_uses_send_to_c2c():
    bot = QQRecordingBot()
    ok = await send_to_session(bot, SessionKey("qq", "private", "USER_OPENID"), "hi")
    assert ok is True
    name, kwargs = bot.calls[0]
    assert name == "send_to_c2c"
    assert kwargs["openid"] == "USER_OPENID"


@pytest.mark.asyncio
async def test_qq_channel_push_uses_send_to_channel():
    bot = QQRecordingBot()
    ok = await send_to_session(bot, SessionKey("qq", "channel", "CH1"), "hi")
    assert ok is True
    name, kwargs = bot.calls[0]
    assert name == "send_to_channel"
    assert kwargs["channel_id"] == "CH1"


# ---- 长文本与失败处理 ----


@pytest.mark.asyncio
async def test_long_push_is_chunked():
    bot = QQRecordingBot()
    ok = await send_to_session(bot, SessionKey("qq", "group", "G"), "字" * 4000)
    assert ok is True
    assert len(bot.calls) > 1


@pytest.mark.asyncio
async def test_push_to_unknown_platform_returns_false():
    class PlainBot(RecordingBot):
        pass

    ok = await send_to_session(PlainBot(), SessionKey("unknown", "group", "1"), "hi")
    assert ok is False


@pytest.mark.asyncio
async def test_push_text_rejects_bad_target():
    bot = OneBotRecordingBot()
    assert await push_text(bot, "not-a-session", "hi") is False
    assert bot.calls == []


@pytest.mark.asyncio
async def test_push_text_accepts_string_session():
    bot = OneBotRecordingBot()
    assert await push_text(bot, "onebot:group:123456", "hi") is True
    assert bot.calls[0][0] == "send_group_msg"


@pytest.mark.asyncio
async def test_missing_api_returns_false_not_raise():
    class NoApiBot:
        """只有 QQ 的模块路径，但没有任何发送方法。"""

    NoApiBot.__module__ = "nonebot.adapters.qq.bot"
    ok = await send_to_session(NoApiBot(), SessionKey("qq", "group", "G"), "hi")
    assert ok is False


@pytest.mark.asyncio
async def test_api_exception_is_swallowed():
    class BrokenBot:
        async def send_to_group(self, **kwargs):
            raise RuntimeError("boom")

    BrokenBot.__module__ = "nonebot.adapters.qq.bot"
    ok = await send_to_session(BrokenBot(), SessionKey("qq", "group", "G"), "hi")
    assert ok is False
