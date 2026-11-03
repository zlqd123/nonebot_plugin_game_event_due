"""消息格式化测试：确认输出只有活动标题，且可选项开关有效。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from nonebot_plugin_game_event_due.events.services.message_formatter import (
    MessageSettings,
    build_overview_parts,
    parts_to_text,
)
from nonebot_plugin_game_event_due.events.sources.base import Activity, Game

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=TZ)
GAME = Game("ak", "明日方舟", "sra", ("方舟",), "明日方舟")


def make_activity(
    name: str,
    *,
    days_left: float = 5,
    start_days: float = -3,
) -> Activity:
    return Activity(
        game_id="ak",
        activity_id=name,
        name=name,
        start_time=NOW + timedelta(days=start_days),
        end_time=NOW + timedelta(days=days_left),
        description="这是一段很长的活动描述，默认不应该出现在输出里。",
        cover="https://example.com/cover.png",
    )


def render(activities, settings: MessageSettings) -> str:
    return parts_to_text(
        build_overview_parts(GAME, activities, NOW, settings, source_name="SRA")
    )


def test_titles_only_by_default():
    """默认只输出标题，不含描述、标签、封面、时间段。"""
    text = render([make_activity("SideStory「时间啊，向前！」")], MessageSettings())

    assert "SideStory「时间啊，向前！」" in text
    assert "描述" not in text
    assert "cover.png" not in text
    assert "https://" not in text
    assert "标签" not in text


def test_no_deadline_by_default():
    """默认不带截止日期。"""
    text = render([make_activity("测试活动")], MessageSettings())
    assert "至 " not in text
    assert "剩 " not in text


def test_deadline_opt_in():
    """打开 show_deadline 后标注截止日期与剩余天数。"""
    text = render(
        [make_activity("测试活动", days_left=3)],
        MessageSettings(show_deadline=True),
    )
    assert "至 " in text
    assert "剩 " in text


def test_active_and_upcoming_groups():
    """进行中与即将开始分组，且都只列标题。"""
    activities = [
        make_activity("进行中活动", start_days=-2, days_left=5),
        make_activity("未来活动", start_days=10, days_left=30),
    ]
    text = render(activities, MessageSettings())

    assert "▎进行中（1）" in text
    assert "▎即将开始（1）" in text
    assert "进行中活动" in text
    assert "未来活动" in text


def test_empty_overview_message():
    """没有活动时给出明确说明，而不是空消息。"""
    text = render([], MessageSettings())
    assert "没有进行中或即将开始的活动" in text


def test_source_note_rendered():
    """数据来源与缓存说明会附在末尾。"""
    text = parts_to_text(
        build_overview_parts(
            GAME,
            [make_activity("活动")],
            NOW,
            MessageSettings(),
            source_name="SRA 公共 API",
            note="本地缓存 3.0 小时前",
        )
    )
    assert "SRA 公共 API" in text
    assert "本地缓存 3.0 小时前" in text


def test_header_uses_game_display_name():
    """标题用游戏名，便于多游戏一起查询时区分。"""
    text = render([make_activity("活动")], MessageSettings())
    assert text.startswith("🎮 明日方舟")


def test_overview_caps_long_lists():
    """超过上限时补一行省略说明，不无限膨胀。"""
    activities = [
        make_activity(f"活动{i}", start_days=-2, days_left=5) for i in range(20)
    ]
    text = render(activities, MessageSettings())
    assert "……另有" in text


def test_game_alias_override():
    """配置 game_aliases 后标题用自定义名。"""
    settings = MessageSettings(game_aliases=(("ak", "舟游"),))
    text = render([make_activity("活动")], settings)
    assert "舟游" in text
