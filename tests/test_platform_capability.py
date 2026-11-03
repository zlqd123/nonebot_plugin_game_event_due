"""平台能力矩阵的测试。

断言全部来自 QQ 开放平台官方文档（``bot.q.qq.com/wiki/develop/api-v2``）：
- 群聊被动回复 5 分钟 / 5 次，单聊 60 分钟 / 4 次
- 没有合并转发
- 图片段只接受 URL

这些是插件「纯文字 + 分段受次数约束」两个设计的依据，所以必须锁在测试里，
避免以后有人改成「按长度狂切」又把 QQ 群打挂。
"""

from __future__ import annotations

import pytest

from nonebot_plugin_game_event_due.platform import (
    UNLIMITED,
    Platform,
    Sender,
    capability_of,
    detect_platform,
    detect_scope,
    plan_chunks,
    split_text,
)


class FakeOneBot:
    """模块路径带 onebot 即被识别为 OneBot v11。"""


FakeOneBot.__module__ = "nonebot.adapters.onebot.v11.bot"


class FakeQQBot:
    pass


FakeQQBot.__module__ = "nonebot.adapters.qq.bot"


class GroupMessageEvent:
    pass


class PrivateMessageEvent:
    pass


class C2CMessageCreateEvent:
    pass


class GroupAtMessageCreateEvent:
    pass


class GuildMessageEvent:
    def __init__(self):
        self.channel_id = "c1"


# ---- 平台识别 ----


def test_detect_platform_onebot():
    assert detect_platform(FakeOneBot()) is Platform.ONEBOT


def test_detect_platform_qq():
    assert detect_platform(FakeQQBot()) is Platform.QQ


def test_detect_platform_none_is_unknown():
    assert detect_platform(None) is Platform.UNKNOWN


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (GroupMessageEvent(), "group"),
        (PrivateMessageEvent(), "private"),
        (C2CMessageCreateEvent(), "private"),
        (GroupAtMessageCreateEvent(), "group"),
        (None, "private"),
    ],
)
def test_detect_scope(event, expected):
    assert detect_scope(event) == expected


# ---- 能力差异 ----


def test_qq_has_no_forward_but_onebot_does():
    assert capability_of(FakeOneBot()).forward_node is True
    assert capability_of(FakeQQBot()).forward_node is False


def test_qq_cannot_send_local_image():
    """QQ 的图片段只接受 URL，本地文件发不出去——这是纯文字设计的由来。"""
    assert capability_of(FakeQQBot()).local_image is False
    assert capability_of(FakeOneBot()).local_image is True


def test_qq_group_allows_five_replies():
    cap = capability_of(FakeQQBot(), scope="group")
    assert cap.max_replies == 5
    assert cap.passive_window == 300


def test_qq_private_allows_four_replies():
    cap = capability_of(FakeQQBot(), scope="private")
    assert cap.max_replies == 4
    assert cap.passive_window == 3600


def test_onebot_has_no_reply_cap():
    assert capability_of(FakeOneBot()).max_replies == UNLIMITED


def test_channel_scope_has_no_cap():
    assert capability_of(FakeQQBot(), scope="channel").max_replies == UNLIMITED


def test_overrides_win():
    cap = capability_of(FakeQQBot(), overrides={"message_limit": 500})
    assert cap.message_limit == 500


# ---- 分段与回复预算 ----


def test_split_text_breaks_at_newline():
    text = "a" * 10 + "\n" + "b" * 10
    chunks = split_text(text, 12)
    assert chunks[0] == "a" * 10
    assert "".join(chunks).replace("b", "") == "a" * 10


def test_split_text_hard_cut_when_no_break():
    chunks = split_text("x" * 25, 10)
    assert [len(c) for c in chunks] == [10, 10, 5]


def test_split_text_short_text_untouched():
    assert split_text("hello", 100) == ["hello"]


def test_split_text_empty():
    assert split_text("", 100) == []


def test_clamp_chunks_merges_tail():
    """QQ 群只能回 5 次，10 段必须被并成 5 段，且内容不丢。"""
    cap = capability_of(FakeQQBot(), scope="group")
    chunks = [f"part{i}" for i in range(10)]
    clamped = cap.clamp_chunks(chunks)
    assert len(clamped) == 5
    # 头 4 段原样，尾部全部并进第 5 段
    assert clamped[:4] == chunks[:4]
    for chunk in chunks[4:]:
        assert chunk in clamped[4]


def test_clamp_chunks_noop_within_budget():
    cap = capability_of(FakeQQBot(), scope="group")
    chunks = ["a", "b", "c"]
    assert cap.clamp_chunks(chunks) == chunks


def test_plan_chunks_respects_reply_budget():
    """5000 字的文本在 QQ 群（1500 字/条、5 次）里最多发出 5 段。"""
    cap = capability_of(FakeQQBot(), scope="group")
    chunks = plan_chunks("字" * 5000, cap)
    assert len(chunks) <= 5


def test_plan_chunks_onebot_keeps_all_chunks():
    cap = capability_of(FakeOneBot())
    chunks = plan_chunks("字" * 12000, cap)
    assert len(chunks) == 3


# ---- Sender 按事件作用域选档 ----


def test_sender_uses_event_scope_for_qq():
    bot = FakeQQBot()
    sender = Sender(bot, event=GroupAtMessageCreateEvent())
    assert sender.max_replies == 5

    private_sender = Sender(bot, event=C2CMessageCreateEvent())
    assert private_sender.max_replies == 4


def test_sender_onebot_limit():
    sender = Sender(FakeOneBot(), event=GroupMessageEvent())
    assert sender.limit == 4000
