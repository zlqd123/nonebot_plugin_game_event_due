"""图文混排推送的测试。

配图是「锦上添花」，但它一旦出错绝不能让整条推送消失。这里把三条路径都
锁死：有图走各自平台的正规发法、图片上传失败退回文字、没有图完全不受影响。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import pytest

from nonebot_plugin_game_event_due.platform import (
    SessionKey,
    parts_to_markdown,
    parts_to_onebot,
    parts_to_plain,
    send_parts,
)


@dataclass(frozen=True)
class Chunk:
    """和 ``message_formatter.MessagePart`` 结构一致的最小片段。"""

    kind: str
    value: str

    @property
    def is_image(self) -> bool:
        return self.kind == "image"


def _text(value: str) -> Chunk:
    return Chunk("text", value)


def _image(url: str) -> Chunk:
    return Chunk("image", url)


class RecordingBot:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def send_group_msg(self, **kwargs):
        self.calls.append(("send_group_msg", kwargs))

    async def send_private_msg(self, **kwargs):
        self.calls.append(("send_private_msg", kwargs))

    async def send_to_group(self, **kwargs):
        self.calls.append(("send_to_group", kwargs))

    async def send_to_c2c(self, **kwargs):
        self.calls.append(("send_to_c2c", kwargs))


class OneBotBot(RecordingBot):
    pass


OneBotBot.__module__ = "nonebot.adapters.onebot.v11.bot"


class QQBot(RecordingBot):
    pass


QQBot.__module__ = "nonebot.adapters.qq.bot"


class FakeMedia:
    """假的素材上传器：可以装作成功，也可以装作失败。"""

    def __init__(self, url: str = "qqbot-media://image/v1/abc") -> None:
        self.url = url
        self.uploaded: list[str] = []

    async def upload_image(self, bot, image_url: str) -> str:  # noqa: ANN001
        self.uploaded.append(image_url)
        return self.url


class BrokenMedia(FakeMedia):
    async def upload_image(self, bot, image_url: str) -> str:  # noqa: ANN001
        self.uploaded.append(image_url)
        return ""


class FakeSegment:
    """假的 markdown 段，字段形状和真实适配器一致。"""

    def __init__(self, markdown: str) -> None:
        self.type = "markdown"
        self.data = {"markdown": type("MD", (), {"content": markdown})()}


class FakeMessage(str):
    """假的 Message：只要能被当成字符串发出去就够了。"""


def _install_fake_qq_message(monkeypatch) -> None:
    """本地环境通常没装 adapter-qq，这里塞一个最小实现顶上。

    被测代码是按 ``type(bot).__module__`` 动态找 ``<adapter>.message`` 模块
    的，所以只要往 ``sys.modules`` 里放一个同名模块，就能在纯 Python 环境里
    验证 markdown 分支；真实适配器在容器里始终存在。
    """
    import sys
    import types

    module = types.ModuleType("nonebot.adapters.qq.message")
    segment = types.SimpleNamespace(markdown=lambda content: FakeSegment(content))
    module.MessageSegment = segment  # type: ignore[attr-defined]
    module.Message = FakeMessage  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "nonebot.adapters.qq.message", module)


SAMPLE: list[Chunk] = [
    _text("🎮 终末地"),
    _image("https://example.com/cover.jpg"),
    _text("▎即将结束（1）\n· 「墨分诸彩」引入活动　明天 12:00"),
]


# ---- 纯文本渲染 ----


def test_parts_to_plain_keeps_cover_link():
    text = parts_to_plain(SAMPLE)
    assert "🎮 终末地" in text
    assert "封面：https://example.com/cover.jpg" in text
    assert "明天 12:00" in text


def test_parts_to_plain_without_image_is_unchanged():
    parts = [_text("🎮 终末地"), _text("▎即将结束（0）")]
    assert parts_to_plain(parts) == "🎮 终末地\n▎即将结束（0）"


# ---- 各平台渲染 ----


def test_parts_to_onebot_inlines_cq_image():
    rendered = parts_to_onebot(SAMPLE)
    assert "[CQ:image,file=https://example.com/cover.jpg]" in rendered
    # 图片前后要有换行，否则会粘在标题上
    assert "🎮 终末地\n[CQ:image" in rendered
    assert rendered.index("[CQ:image") < rendered.index("▎即将结束")


def test_parts_to_onebot_escapes_cq_special_chars():
    rendered = parts_to_onebot([_image("https://a/b.jpg?x=1&y=2")])
    assert "&amp;" in rendered
    assert "[CQ:image,file=https://a/b.jpg?x=1&amp;y=2]" in rendered


def test_parts_to_markdown_uses_headings_and_bullets():
    markdown = parts_to_markdown(SAMPLE)
    assert "### 终末地" in markdown
    assert "![封面 #640px](https://example.com/cover.jpg)" in markdown
    assert "**▎即将结束（1）**" in markdown
    assert "- 「墨分诸彩」引入活动　**明天 12:00**" in markdown


def test_parts_to_markdown_respects_width():
    markdown = parts_to_markdown([_image("https://a/b.jpg")], 480)
    assert "#480px" in markdown


def test_parts_to_markdown_collapses_blank_runs():
    parts = [_text("🎮 甲"), _text(""), _text(""), _text("▎即将开始（1）")]
    markdown = parts_to_markdown(parts)
    assert "\n\n\n" not in markdown


def test_markdown_puts_blank_line_before_section_after_bullets():
    """列表项后紧跟小标题会被 markdown 吞进列表项，必须空一行。"""
    parts = [
        _text("🎮 甲"),
        _text("▎即将结束（1）\n· 活动A　11-05 04:00\n▎即将开始（1）\n· 活动B　11-06 12:00"),
    ]
    markdown = parts_to_markdown(parts)
    assert "- 活动A　**11-05 04:00**\n\n**▎即将开始（1）**" in markdown


# ---- 发送路径 ----


@pytest.mark.asyncio
async def test_onebot_image_goes_out_as_cq_code():
    bot = OneBotBot()
    session = SessionKey("onebot", "group", "123456")
    assert await send_parts(bot, session, SAMPLE) is True
    assert bot.calls[0][0] == "send_group_msg"
    message = str(bot.calls[0][1]["message"])
    assert "[CQ:image" in message


@pytest.mark.asyncio
async def test_qq_image_is_uploaded_then_sent_as_markdown(monkeypatch):
    _install_fake_qq_message(monkeypatch)
    bot = QQBot()
    media = FakeMedia()
    session = SessionKey("qq", "group", "OPENID")
    assert await send_parts(bot, session, SAMPLE, media=media) is True
    assert media.uploaded == ["https://example.com/cover.jpg"]
    api, kwargs = bot.calls[0]
    assert api == "send_to_group"
    assert kwargs["group_openid"] == "OPENID"
    segment = kwargs["message"]
    assert segment.type == "markdown"
    assert "qqbot-media://image/v1/abc" in segment.data["markdown"].content


@pytest.mark.asyncio
async def test_qq_upload_failure_falls_back_to_plain_text():
    bot = QQBot()
    session = SessionKey("qq", "group", "OPENID")
    assert await send_parts(bot, session, SAMPLE, media=BrokenMedia()) is True
    api, kwargs = bot.calls[0]
    assert api == "send_to_group"
    text = str(kwargs["message"])
    assert "封面：https://example.com/cover.jpg" in text
    assert "明天 12:00" in text


@pytest.mark.asyncio
async def test_qq_without_media_sender_sends_plain_text():
    bot = QQBot()
    session = SessionKey("qq", "group", "OPENID")
    assert await send_parts(bot, session, SAMPLE) is True
    api, _ = bot.calls[0]
    assert api == "send_to_group"


@pytest.mark.asyncio
async def test_no_image_parts_take_the_plain_path_everywhere():
    bot = QQBot()
    session = SessionKey("qq", "group", "OPENID")
    parts: Sequence[Chunk] = [_text("🎮 终末地"), _text("▎即将结束（1）")]
    assert await send_parts(bot, session, parts) is True
    assert str(bot.calls[0][1]["message"]) == "🎮 终末地\n▎即将结束（1）"


@pytest.mark.asyncio
async def test_empty_parts_are_a_noop():
    bot = OneBotBot()
    session = SessionKey("onebot", "group", "123456")
    assert await send_parts(bot, session, []) is True
    assert bot.calls == []


# ---- 素材缓存 ----


def test_media_cache_put_get_roundtrip(tmp_path):
    from nonebot_plugin_game_event_due.platform.media import MediaCache

    cache = MediaCache(tmp_path / "material_cache.json")
    assert cache.get("abc") == ""
    cache.put("abc", "qqbot-media://image/v1/x", "img-1")
    assert cache.get("abc") == "qqbot-media://image/v1/x"

    # 换一个实例重新读盘，确认真的落盘了
    reloaded = MediaCache(tmp_path / "material_cache.json")
    assert reloaded.get("abc") == "qqbot-media://image/v1/x"


def test_media_cache_survives_broken_file(tmp_path):
    from nonebot_plugin_game_event_due.platform.media import MediaCache

    path = tmp_path / "material_cache.json"
    path.write_text("not json", encoding="utf-8")
    assert MediaCache(path).get("abc") == ""


@pytest.mark.asyncio
async def test_media_cache_upload_delegates(monkeypatch, tmp_path):
    """``Delivery`` 持有的是 MediaCache，所以它必须自己有 upload_image。"""
    from nonebot_plugin_game_event_due.platform import media as media_module

    async def fake(bot, image_url, cache):  # noqa: ANN001
        return f"uploaded:{image_url}"

    monkeypatch.setattr(media_module, "upload_image", fake)
    cache = media_module.MediaCache(tmp_path / "c.json")
    assert await cache.upload_image(None, "https://a/b.jpg") == "uploaded:https://a/b.jpg"
