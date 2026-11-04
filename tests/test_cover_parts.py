"""「有图插图，无图文字」的测试。

配图来自 SRA 的版本主视觉（JSON 根级 ``cover``），活动级 cover 大多是同一张
图重复挂，所以只按游戏取一张。这里的重点是：有图时图插在游戏标题之后、
条目之前；没有图时输出跟以前完全一致，不留空行也不留占位。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from nonebot_plugin_game_event_due.events.services.message_formatter import (
    PART_IMAGE,
    PART_TEXT,
    MessageSettings,
    build_multi_game_reminder_parts,
    build_overview_parts,
    build_window_parts,
    parts_to_text,
)
from nonebot_plugin_game_event_due.events.services.reminder_service import ReminderPlan
from nonebot_plugin_game_event_due.events.sources.base import Activity, Game

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=TZ)
GAME = Game("end", "明日方舟：终末地", "sra", ("终末地",), "终末地")
COVER = "https://web.hycdn.cn/upload/image/20261014/abc.jpg"


def make_plan(
    name: str,
    *,
    game_id: str = "end",
    end_in_hours: float = 16,
    start_in_hours: float | None = None,
) -> ReminderPlan:
    """构造一个已命中 24 小时窗口的提醒。"""
    end_time = NOW + timedelta(hours=end_in_hours)
    start_time = (
        NOW + timedelta(hours=start_in_hours) if start_in_hours is not None else None
    )
    activity = Activity(
        game_id=game_id,
        activity_id=name,
        name=name,
        start_time=start_time,
        end_time=end_time,
    )
    return ReminderPlan(
        activity=activity,
        reasons=("deadline_1d",),
        tier="d1",
        remaining=timedelta(hours=end_in_hours),
        start_tier="s1" if start_in_hours is not None else "",
    )


# ---- 有图 ----


def test_cover_sits_between_header_and_items():
    parts = build_window_parts(
        GAME, [make_plan("「墨分诸彩」引入活动")], NOW, MessageSettings(), cover=COVER
    )

    assert [part.kind for part in parts] == [PART_TEXT, PART_IMAGE, PART_TEXT]
    assert parts[0].value == "🎮 明日方舟：终末地"
    assert parts[1].value == COVER
    assert parts[2].value.startswith("▎即将结束（1）")


def test_cover_only_adds_one_part_regardless_of_item_count():
    plans = [make_plan(f"活动{i}", end_in_hours=10 + i) for i in range(5)]
    parts = build_window_parts(GAME, plans, NOW, MessageSettings(), cover=COVER)
    assert sum(1 for part in parts if part.kind == PART_IMAGE) == 1


# ---- 无图 ----


def test_no_cover_means_exactly_one_text_part():
    parts = build_window_parts(
        GAME, [make_plan("「墨分诸彩」引入活动")], NOW, MessageSettings()
    )
    assert [part.kind for part in parts] == [PART_TEXT]
    assert parts[0].value.startswith("🎮 明日方舟：终末地")


def test_empty_cover_string_is_same_as_no_cover():
    with_blank = build_window_parts(
        GAME, [make_plan("活动")], NOW, MessageSettings(), cover=""
    )
    without = build_window_parts(GAME, [make_plan("活动")], NOW, MessageSettings())
    assert with_blank == without


def test_insert_cover_off_ignores_given_cover():
    settings = MessageSettings(insert_cover=False)
    parts = build_window_parts(GAME, [make_plan("活动")], NOW, settings, cover=COVER)
    assert [part.kind for part in parts] == [PART_TEXT]
    assert COVER not in parts[0].value


# ---- 多游戏合并 ----


def test_multi_game_only_gives_covers_to_games_that_have_one():
    ak = Game("ak", "明日方舟", "sra", ("方舟",), "明日方舟")
    ak_plan = make_plan("方舟活动", game_id="ak")

    entries = [(GAME, [make_plan("终末地活动")]), (ak, [ak_plan])]
    parts = build_multi_game_reminder_parts(
        entries, NOW, MessageSettings(), covers={GAME.game_id: COVER}
    )

    images = [part.value for part in parts if part.kind == PART_IMAGE]
    assert images == [COVER]
    text = parts_to_text(parts)
    assert "🎮 明日方舟：终末地" in text
    assert "🎮 明日方舟" in text


def test_multi_game_without_any_cover_is_pure_text():
    ak = Game("ak", "明日方舟", "sra", ("方舟",), "明日方舟")
    ak_plan = make_plan("方舟活动", game_id="ak")

    entries = [(GAME, [make_plan("终末地活动")]), (ak, [ak_plan])]
    parts = build_multi_game_reminder_parts(entries, NOW, MessageSettings())
    assert all(part.kind == PART_TEXT for part in parts)


def test_single_game_collapses_to_one_block():
    parts = build_multi_game_reminder_parts(
        [(GAME, [make_plan("活动")])], NOW, MessageSettings(), covers={"end": COVER}
    )
    # 只有一个游戏时不加顶层日期标题
    assert parts[0].value == "🎮 明日方舟：终末地"


# ---- 每日全量摘要 ----


def make_activity(name: str, *, days_left: float = 10) -> Activity:
    return Activity(
        game_id="end",
        activity_id=name,
        name=name,
        start_time=NOW - timedelta(days=3),
        end_time=NOW + timedelta(days=days_left),
    )


def test_overview_gets_cover_between_header_and_list():
    parts = build_overview_parts(
        GAME,
        [make_activity("甲"), make_activity("乙", days_left=20)],
        NOW,
        MessageSettings(show_deadline=True),
        source_name="SRA 公共 API",
        cover=COVER,
    )
    assert [part.kind for part in parts] == [PART_TEXT, PART_IMAGE, PART_TEXT]
    assert parts[0].value == "🎮 明日方舟：终末地"
    assert parts[1].value == COVER
    assert "▎进行中（2）" in parts[2].value
    assert "数据来源：SRA 公共 API" in parts[2].value


def test_overview_without_cover_is_unchanged():
    with_blank = build_overview_parts(
        GAME, [make_activity("甲")], NOW, MessageSettings(), cover=""
    )
    without = build_overview_parts(GAME, [make_activity("甲")], NOW, MessageSettings())
    assert with_blank == without
    assert [part.kind for part in with_blank] == [PART_TEXT]


def test_overview_cover_can_be_switched_off():
    settings = MessageSettings(insert_cover=False)
    parts = build_overview_parts(
        GAME, [make_activity("甲")], NOW, settings, cover=COVER
    )
    assert [part.kind for part in parts] == [PART_TEXT]
