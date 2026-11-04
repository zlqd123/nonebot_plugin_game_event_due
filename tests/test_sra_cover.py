"""SRA 版本主视觉的提取测试。

SRA 的活动级 ``cover`` 大多是同一张图重复挂，所以插件只按游戏取一张。
这里的重点是：
* 根级有 ``cover`` 就用它；
* 根级没有时，退而用活动里出现次数最多的那张；
* 一张都没有时返回空串（投递层据此降级成纯文字）。
"""

from __future__ import annotations

import pytest

from nonebot_plugin_game_event_due.events.sources.sra import _pick_cover


def test_root_cover_wins():
    data = {
        "cover": "https://a/root.jpg",
        "activities": [{"cover": "https://a/other.jpg"}],
    }
    assert _pick_cover(data) == "https://a/root.jpg"


def test_falls_back_to_most_common_activity_cover():
    """14 个活动里 9 个挂同一张图时，取那张。"""
    data = {
        "activities": [
            {"cover": "https://a/reused.jpg"},
            {"cover": "https://a/reused.jpg"},
            {"cover": "https://a/reused.jpg"},
            {"cover": "https://a/one-off.jpg"},
            {"cover": ""},
            {"name": "没有 cover 的活动"},
        ]
    }
    assert _pick_cover(data) == "https://a/reused.jpg"


def test_no_cover_anywhere_returns_empty():
    data = {"activities": [{"name": "甲"}, {"name": "乙", "cover": ""}]}
    assert _pick_cover(data) == ""


def test_blank_root_cover_is_treated_as_missing():
    data = {"cover": "   ", "activities": [{"cover": "https://a/x.jpg"}]}
    assert _pick_cover(data) == "https://a/x.jpg"


def test_activities_not_a_list_is_tolerated():
    assert _pick_cover({"cover": "", "activities": None}) == ""
    assert _pick_cover({"activities": [None, 1, "x"]}) == ""


def test_pick_cover_ignores_non_dict_entries():
    data = {"activities": [None, {"cover": "https://a/ok.jpg"}, "junk"]}
    assert _pick_cover(data) == "https://a/ok.jpg"
