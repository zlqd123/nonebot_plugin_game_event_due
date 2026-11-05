"""24 小时窗口快照：12:00:01 边界、四组分类、渲染顺序、调度接线。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from nonebot_plugin_game_event_due.events.services.message_formatter import (
    PART_IMAGE,
    PART_TEXT,
    MessageSettings,
    build_multi_game_snapshot_parts,
    build_snapshot_parts,
    parts_to_text,
)
from nonebot_plugin_game_event_due.events.services.window_service import (
    WINDOW_GROUPS,
    classify_window,
    normalize_check_time,
)
from nonebot_plugin_game_event_due.events.sources.base import Activity, Game

TZ = timezone(timedelta(hours=8))
#: 用户钉死的检查点：12:00:01
C = datetime(2026, 11, 5, 12, 0, 1, tzinfo=TZ)
W = timedelta(hours=24)
GAME = Game("end", "明日方舟：终末地", "sra", ("终末地",), "终末地")
COVER = "https://example.com/cover.jpg"


def make(
    name: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> Activity:
    return Activity(
        game_id="end",
        activity_id=name,
        name=name,
        start_time=start,
        end_time=end,
    )


# ---- 检查点归一 ----


def test_normalize_check_time_pins_second():
    """真正执行往往比 cron 晚几百毫秒，仍归一到 12:00:01。"""
    actual = datetime(2026, 11, 5, 12, 0, 3, 428000, tzinfo=TZ)
    assert normalize_check_time(actual) == C


def test_normalize_check_time_uses_configured_hour_minute():
    actual = datetime(2026, 11, 5, 9, 30, tzinfo=TZ)
    assert normalize_check_time(actual, hour=8, minute=5, second=1) == datetime(
        2026, 11, 5, 8, 5, 1, tzinfo=TZ
    )


# ---- 四组边界 ----


def test_exact_noon_start_is_already_started():
    """12:00:00 开始 → 已开始，不是即将开始（严格 12:00:01，无整点例外）。"""
    act = make("整点开场", start=C - timedelta(seconds=1), end=C + timedelta(days=10))
    snap = classify_window([act], C)
    assert [item.activity.name for item in snap.groups["已开始"]] == ["整点开场"]
    assert snap.groups["即将开始"] == ()


def test_exact_noon_end_is_already_ended():
    """12:00:00 结束 → 已结束，不是即将结束。"""
    act = make("整点收尾", start=C - timedelta(days=5), end=C - timedelta(seconds=1))
    snap = classify_window([act], C)
    assert [item.activity.name for item in snap.groups["已结束"]] == ["整点收尾"]
    assert snap.groups["即将结束"] == ()


def test_future_window_is_closed_at_24h():
    """恰好 C+24h 开始/结束算进窗口，多一秒不算。"""
    boundary_start = make("边界开", start=C + W, end=C + W + timedelta(days=5))
    boundary_end = make("边界收", start=C - timedelta(days=5), end=C + W)
    outside = make("窗外", start=C + W + timedelta(seconds=1))

    snap = classify_window([boundary_start, boundary_end, outside], C)
    assert [i.activity.name for i in snap.groups["即将开始"]] == ["边界开"]
    assert [i.activity.name for i in snap.groups["即将结束"]] == ["边界收"]
    assert snap.total == 2


def test_past_window_includes_lower_bound():
    """恰好 C-24h 开始的算「已开始」，C-24h-1s 不算。"""
    inside = make("下界", start=C - W, end=C + timedelta(days=5))
    outside = make("窗外", start=C - W - timedelta(seconds=1), end=C + timedelta(days=5))
    snap = classify_window([inside, outside], C)
    assert [i.activity.name for i in snap.groups["已开始"]] == ["下界"]


def test_ended_lower_bound():
    """恰好 C-24h 结束的算「已结束」，再早一秒不算。"""
    inside = make("下界", start=C - timedelta(days=5), end=C - W)
    outside = make("窗外", start=C - timedelta(days=5), end=C - W - timedelta(seconds=1))
    snap = classify_window([inside, outside], C)
    assert [i.activity.name for i in snap.groups["已结束"]] == ["下界"]


def test_one_activity_only_one_group():
    """一个活动同时命中多组时，按固定顺序只取第一个。"""
    act = make("短活动", start=C - timedelta(hours=2), end=C + timedelta(hours=2))
    snap = classify_window([act], C)
    assert snap.total == 1
    # 「已开始」优先于「即将结束」
    assert snap.groups["已开始"] and not snap.groups["即将结束"]


def test_groups_ordered_and_empty_ok():
    acts = [
        make("A 已开始", start=C - timedelta(hours=1), end=C + timedelta(days=5)),
        make("B 即将开始", start=C + timedelta(hours=1), end=C + timedelta(days=5)),
        make("C 即将结束", start=C - timedelta(days=5), end=C + timedelta(hours=1)),
        make("D 已结束", start=C - timedelta(days=5), end=C - timedelta(hours=1)),
    ]
    snap = classify_window(acts, C)
    assert WINDOW_GROUPS == ("已开始", "即将开始", "即将结束", "已结束")
    assert [snap.groups[key][0].activity.name for key in WINDOW_GROUPS] == [
        "A 已开始",
        "B 即将开始",
        "C 即将结束",
        "D 已结束",
    ]
    assert snap.total == 4
    assert not snap.is_empty()


def test_missing_times_are_ignored():
    assert classify_window([make("无时间")], C).is_empty()
    assert classify_window([], C).is_empty()


def test_snapshot_carries_checkpoint_and_width():
    snap = classify_window([], C, window_hours=12)
    assert snap.check_at == C
    assert snap.window == timedelta(hours=12)


# ---- 渲染 ----


def test_render_four_groups_in_order():
    acts = [
        make("A 已开始", start=C - timedelta(hours=1), end=C + timedelta(days=5)),
        make("B 即将开始", start=C + timedelta(hours=1), end=C + timedelta(days=5)),
        make("C 即将结束", start=C - timedelta(days=5), end=C + timedelta(hours=1)),
        make("D 已结束", start=C - timedelta(days=5), end=C - timedelta(hours=1)),
    ]
    snap = classify_window(acts, C)
    text = parts_to_text(build_snapshot_parts(GAME, snap, MessageSettings()))
    order = [text.index(f"▎{key}") for key in WINDOW_GROUPS]
    assert order == sorted(order)
    for key in WINDOW_GROUPS:
        assert f"▎{key}（1）" in text
    assert "· A 已开始　11-05 11:00~11-10 12:00" in text
    assert "· D 已结束　11-05 11:00" in text
    assert not text.rstrip().endswith(("开始", "结束"))


def test_empty_groups_are_not_rendered():
    act = make("仅此一条", start=C + timedelta(hours=1), end=C + timedelta(days=5))
    snap = classify_window([act], C)
    text = parts_to_text(build_snapshot_parts(GAME, snap, MessageSettings()))
    assert "▎即将开始（1）" in text
    assert "▎已开始" not in text
    assert "▎即将结束" not in text
    assert "▎已结束" not in text


def test_cover_inserted_between_header_and_body():
    act = make("活动", start=C + timedelta(hours=1), end=C + timedelta(days=5))
    snap = classify_window([act], C)
    parts = build_snapshot_parts(GAME, snap, MessageSettings(), cover=COVER)
    assert [part.kind for part in parts] == [PART_TEXT, PART_IMAGE, PART_TEXT]
    assert parts[1].value == COVER


def test_no_cover_is_single_text_part():
    act = make("活动", start=C + timedelta(hours=1), end=C + timedelta(days=5))
    snap = classify_window([act], C)
    parts = build_snapshot_parts(GAME, snap, MessageSettings(), cover="")
    assert len(parts) == 1 and parts[0].kind == PART_TEXT
    assert (
        build_snapshot_parts(
            GAME, snap, MessageSettings(insert_cover=False), cover=COVER
        )
        == parts
    )


def test_single_game_has_no_top_header():
    act = make("活动", start=C + timedelta(hours=1), end=C + timedelta(days=5))
    snap = classify_window([act], C)
    text = parts_to_text(build_multi_game_snapshot_parts([(GAME, snap)], MessageSettings()))
    assert "📅" not in text
    assert text.startswith("🎮 明日方舟：终末地")


def test_multi_game_has_one_header_and_per_game_covers():
    ak = Game("ak", "明日方舟", "sra", ("方舟",), "明日方舟")
    act1 = make("终末地活动", start=C + timedelta(hours=1), end=C + timedelta(days=5))
    act2 = Activity(
        game_id="ak",
        activity_id="方舟活动",
        name="方舟活动",
        start_time=C + timedelta(hours=2),
        end_time=C + timedelta(days=5),
    )
    snap1 = classify_window([act1], C)
    snap2 = classify_window([act2], C)
    parts = build_multi_game_snapshot_parts(
        [(GAME, snap1), (ak, snap2)],
        MessageSettings(),
        covers={"end": COVER},
    )
    text = parts_to_text(parts)
    assert text.count("📅 活动窗口 · 11-05") == 1
    assert text.count("🎮") == 2
    assert sum(1 for p in parts if p.kind == PART_IMAGE) == 1


def test_empty_snapshot_produces_nothing():
    snap = classify_window([], C)
    assert build_snapshot_parts(GAME, snap, MessageSettings()) == []
    assert build_multi_game_snapshot_parts([(GAME, snap)], MessageSettings()) == []
