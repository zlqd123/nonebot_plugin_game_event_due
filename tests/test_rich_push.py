"""图文混排的渲染与投递测试。

QQ 官方 Bot 的群聊图片只有 ``msg_type=7`` 富媒体一条路（markdown 不渲染
图片，实测连外链也不行），而且一条消息只能带一张图。所以这里的重点是：

* 多游戏时按游戏拆条，每条自带一张封面；
* 图下载失败、适配器不支持富媒体时，那一条退回纯文字，不丢内容；
* OneBot 仍然是一条消息 + CQ 码内联。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from nonebot_plugin_game_event_due.platform import (
    parts_to_onebot,
    parts_to_plain,
    parts_to_text_only,
    send_parts,
    split_game_blocks,
)
from nonebot_plugin_game_event_due.platform.session import Platform, SessionKey

# ---- 测试替身 ----


@dataclass
class FakePart:
    is_image: bool
    value: str


def _text(value: str) -> FakePart:
    return FakePart(False, value)


def _image(url: str) -> FakePart:
    return FakePart(True, url)


class FakeSegment:
    """够用的 MessageSegment：text / file_image，加法返回消息列表。"""

    def __init__(self, kind: str, payload: Any = None, name: str = "") -> None:
        self.kind = kind
        self.payload = payload
        self.name = name

    def __add__(self, other: Any) -> list[Any]:
        return [self, *([other] if not isinstance(other, list) else other)]

    @staticmethod
    def text(value: str) -> FakeSegment:
        return FakeSegment("text", value)

    @staticmethod
    def file_image(data: bytes, file_name: str = "") -> FakeSegment:
        return FakeSegment("image", data, file_name)


class FakeBot:
    """记录发出去的消息。"""

    def __init__(self, *, rich: bool = True) -> None:
        self.sent: list[dict[str, Any]] = []
        self._rich = rich

    async def send_to_group(self, *, group_openid: str, message: Any) -> None:
        self.sent.append({"api": "group", "target": group_openid, "message": message})

    async def send_to_c2c(self, *, openid: str, message: Any) -> None:
        self.sent.append({"api": "c2c", "target": openid, "message": message})

    async def send_group_msg(self, *, group_id: int, message: Any) -> None:
        self.sent.append({"api": "ob_group", "target": group_id, "message": message})

    # 平台探测靠模块名，这里直接给出属性
    @property
    def adapter(self) -> Any:
        return self


def _patch_qq(monkeypatch, *, rich: bool = True, image_bytes: bytes = b"JPEG") -> None:
    """让 platform 层认为当前是 QQ 官方 Bot，并替换下载与适配器模块。"""
    from nonebot_plugin_game_event_due.platform import media, push

    monkeypatch.setattr(push, "detect_platform", lambda bot: Platform.QQ)
    monkeypatch.setitem(
        __import__("sys").modules,
        "fake_adapter.message",
        type("M", (), {"MessageSegment": FakeSegment}),
    )
    monkeypatch.setattr(
        push,
        "_adapter_message_module",
        lambda bot: type("M", (), {"MessageSegment": FakeSegment}) if rich else None,
    )

    async def fake_fetch(bot: Any, url: str) -> bytes:
        return image_bytes

    monkeypatch.setattr(push, "fetch_image", fake_fetch)
    monkeypatch.setattr(media, "fetch_image", fake_fetch)


def _session(scope: str = "group") -> SessionKey:
    return SessionKey(Platform.QQ, scope, "GROUP_OPENID")  # type: ignore[arg-type]


# ---- 渲染 ----


def test_split_game_blocks_attaches_date_header_to_first_game():
    parts = [
        _text("⏰ 11-04"),
        _text(""),
        _text("🎮 终末地"),
        _image("https://a/end.jpg"),
        _text("▎即将结束（2）\n· 甲　11-05 04:00"),
        _text(""),
        _text("🎮 方舟"),
        _image("https://a/ak.jpg"),
        _text("▎即将开始（1）\n· 乙　11-05 12:00"),
    ]
    blocks = split_game_blocks(parts)
    assert len(blocks) == 2
    assert blocks[0][0].value == "⏰ 11-04"
    assert blocks[1][0].value == "🎮 方舟"
    # 每个块各自带自己的图
    assert [p.value for p in blocks[0] if p.is_image] == ["https://a/end.jpg"]
    assert [p.value for p in blocks[1] if p.is_image] == ["https://a/ak.jpg"]


def test_split_game_blocks_without_header_is_one_block():
    parts = [_text("🎮 终末地"), _image("https://a/x.jpg")]
    assert len(split_game_blocks(parts)) == 1


def test_split_game_blocks_keeps_headerless_message_together():
    parts = [_text("普通消息"), _image("https://a/x.jpg")]
    blocks = split_game_blocks(parts)
    assert len(blocks) == 1
    assert blocks[0][0].value == "普通消息"


def test_parts_to_text_only_drops_images():
    parts = [_text("🎮 终末地"), _image("https://a/x.jpg"), _text("正文")]
    assert parts_to_text_only(parts) == "🎮 终末地\n正文"


def test_parts_to_plain_keeps_cover_as_link():
    parts = [_text("🎮 终末地"), _image("https://a/x.jpg")]
    assert parts_to_plain(parts) == "🎮 终末地\n封面：https://a/x.jpg"


def test_parts_to_onebot_inlines_cq_code():
    parts = [_text("🎮 终末地"), _image("https://a/x.jpg"), _text("正文")]
    rendered = parts_to_onebot(parts)
    assert "[CQ:image,file=https://a/x.jpg]" in rendered
    assert rendered.index("[CQ:image") < rendered.index("正文")


# ---- QQ 官方：富媒体 ----


def test_qq_single_game_sends_text_plus_image(monkeypatch):
    _patch_qq(monkeypatch)
    bot = FakeBot()
    parts = [_text("🎮 终末地"), _image("https://a/x.jpg"), _text("正文")]
    assert asyncio.run(send_parts(bot, _session(), parts)) is True

    assert len(bot.sent) == 1
    message = bot.sent[0]["message"]
    assert [seg.kind for seg in message] == ["text", "image"]
    assert message[0].payload == "🎮 终末地\n正文"
    assert message[1].payload == b"JPEG"
    assert message[1].name.endswith(".jpg")


def test_qq_multi_game_sends_one_message_per_game(monkeypatch):
    _patch_qq(monkeypatch)
    bot = FakeBot()
    parts = [
        _text("⏰ 11-04"),
        _text("🎮 终末地"),
        _image("https://a/end.jpg"),
        _text("· 甲"),
        _text("🎮 方舟"),
        _image("https://a/ak.jpg"),
        _text("· 乙"),
    ]
    assert asyncio.run(send_parts(bot, _session(), parts)) is True

    assert len(bot.sent) == 2
    first, second = (item["message"] for item in bot.sent)
    assert first[0].payload == "⏰ 11-04\n🎮 终末地\n· 甲"
    assert first[1].kind == "image"
    assert second[0].payload == "🎮 方舟\n· 乙"
    assert second[1].kind == "image"


def test_qq_falls_back_to_text_when_image_download_fails(monkeypatch):
    _patch_qq(monkeypatch, image_bytes=b"")
    bot = FakeBot()
    parts = [_text("🎮 终末地"), _image("https://a/x.jpg"), _text("正文")]
    assert asyncio.run(send_parts(bot, _session(), parts)) is True

    # 只剩纯文本那一条，内容完整
    assert len(bot.sent) == 1
    assert bot.sent[0]["message"] == "🎮 终末地\n正文"


def test_qq_falls_back_to_text_when_rich_media_unsupported(monkeypatch):
    _patch_qq(monkeypatch, rich=False)
    bot = FakeBot()
    parts = [_text("🎮 终末地"), _image("https://a/x.jpg"), _text("正文")]
    assert asyncio.run(send_parts(bot, _session(), parts)) is True
    assert bot.sent[0]["message"] == "🎮 终末地\n正文"


def test_qq_without_images_goes_plain(monkeypatch):
    _patch_qq(monkeypatch)
    bot = FakeBot()
    assert asyncio.run(send_parts(bot, _session(), [_text("🎮 终末地\n正文")])) is True
    assert bot.sent[0]["message"] == "🎮 终末地\n正文"


# ---- OneBot ----


def test_onebot_keeps_single_message_with_cq_code(monkeypatch):
    from nonebot_plugin_game_event_due.platform import push

    monkeypatch.setattr(push, "detect_platform", lambda bot: Platform.ONEBOT)
    bot = FakeBot()
    parts = [_text("🎮 终末地"), _image("https://a/x.jpg"), _text("正文")]
    assert asyncio.run(send_parts(bot, _session(), parts)) is True

    assert len(bot.sent) == 1
    rendered = bot.sent[0]["message"]
    assert "[CQ:image,file=https://a/x.jpg]" in str(rendered)
