"""多游戏提醒合并成一条、并按方向分组的测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from nonebot_plugin_game_event_due.events.services.message_formatter import (
    MessageSettings,
    build_multi_game_reminder_parts,
    parts_to_text,
)
from nonebot_plugin_game_event_due.events.services.reminder_service import ReminderPlan
from nonebot_plugin_game_event_due.events.sources.base import Activity, Game

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=TZ)

AK = Game("ak", "明日方舟", "sra", ("方舟",), "明日方舟")
END = Game("end", "明日方舟：终末地", "sra", ("终末地",), "终末地")


def make(
    game_id: str, name: str, *, starts_in: timedelta, ends_in: timedelta
) -> Activity:
    return Activity(
        game_id=game_id,
        activity_id=name,
        name=name,
        start_time=NOW + starts_in,
        end_time=NOW + ends_in,
    )


def end_plan(activity: Activity, tier: str = "d1") -> ReminderPlan:
    return ReminderPlan(
        activity=activity, reasons=(tier,), tier=tier, remaining=None, start_tier=""
    )


def start_plan(activity: Activity, tier: str = "s1") -> ReminderPlan:
    return ReminderPlan(
        activity=activity, reasons=(tier,), tier="", remaining=None, start_tier=tier
    )


def test_single_game_has_no_top_header():
    """只有一个游戏时不套顶层 ⏰ 标题。"""
    act = make("end", "快结束的", starts_in=timedelta(days=-5), ends_in=timedelta(hours=12))
    text = parts_to_text(
        build_multi_game_reminder_parts([(END, [end_plan(act)])], NOW, MessageSettings())
    )

    assert "⏰" not in text
    assert "🎮 明日方舟：终末地" in text
    assert "24 小时内" not in text, "标题里不再带窗口字样"
    assert "▎即将结束（1）" in text
    assert "快结束的" in text
    assert "· 快结束的　11-05 00:00" in text, "行内以时间收尾，不写「结束」"


def test_two_games_merged_into_one_message():
    """两个游戏合并成一条消息，各占一个小节。"""
    end_act = make("end", "终末地活动", starts_in=timedelta(days=-5), ends_in=timedelta(hours=12))
    ak_act = make("ak", "方舟新活动", starts_in=timedelta(hours=10), ends_in=timedelta(days=14))

    entries = [(AK, [start_plan(ak_act)]), (END, [end_plan(end_act)])]
    text = parts_to_text(build_multi_game_reminder_parts(entries, NOW, MessageSettings()))

    assert text.count("⏰") == 1, "只应有一个顶层标题"
    assert "🎮 明日方舟\n" in text
    assert "🎮 明日方舟：终末地" in text
    assert "24 小时内" not in text
    assert "方舟新活动" in text and "终末地活动" in text


def test_start_line_shows_full_range():
    """「即将开始」把整段时间写全：开始~结束。"""
    act = make("end", "归野", starts_in=timedelta(hours=10), ends_in=timedelta(days=13))
    text = parts_to_text(
        build_multi_game_reminder_parts([(END, [start_plan(act)])], NOW, MessageSettings())
    )

    assert "· 归野　11-04 22:00~11-17 12:00" in text
    assert not text.rstrip().endswith("开始")


def test_end_line_keeps_single_time():
    """「即将结束」只写结束时刻，时间区间没有意义。"""
    act = make("end", "墨分诸彩", starts_in=timedelta(days=-5), ends_in=timedelta(hours=16))
    text = parts_to_text(
        build_multi_game_reminder_parts([(END, [end_plan(act)])], NOW, MessageSettings())
    )

    assert "~" not in text
    assert "· 墨分诸彩　11-05 04:00" in text
    assert not text.rstrip().endswith("结束")


def test_sections_grouped_by_direction():
    """同一游戏里「结束」和「开始」分成两段。"""
    ending = make("end", "要结束的", starts_in=timedelta(days=-5), ends_in=timedelta(hours=10))
    starting = make("end", "要开始的", starts_in=timedelta(hours=8), ends_in=timedelta(days=20))

    text = parts_to_text(
        build_multi_game_reminder_parts(
            [(END, [end_plan(ending), start_plan(starting)])], NOW, MessageSettings()
        )
    )

    assert "▎即将结束（1）" in text
    assert "▎即将开始（1）" in text
    # 结束段在前，开始段在后
    assert text.index("要结束的") < text.index("要开始的")
    assert "10 小时" not in text or "要结束的" in text


def test_line_is_title_plus_time_only():
    """每行只有标题和时间，不再重复「剩余不足24小时」。"""
    act = make(
        "end",
        "「丰碑留名·魇林」限时挑战",
        starts_in=timedelta(days=-5),
        ends_in=timedelta(hours=16),
    )
    text = parts_to_text(
        build_multi_game_reminder_parts([(END, [end_plan(act)])], NOW, MessageSettings())
    )

    assert "剩余不足" not in text, "分组标题已表达方向，行内不再重复"
    assert "开始不足" not in text
    assert "「丰碑留名·魇林」限时挑战" in text
    assert "结束" in text


def test_empty_entries_returns_nothing():
    assert build_multi_game_reminder_parts([], NOW, MessageSettings()) == []
    assert build_multi_game_reminder_parts([(AK, [])], NOW, MessageSettings()) == []


def test_burst_day_is_compact():
    """赛季交接日一次 10 条，也要读得下去。"""
    ending = [
        make(
            "end",
            f"要结束的活动{i}",
            starts_in=timedelta(days=-5),
            ends_in=timedelta(hours=15 + i),
        )
        for i in range(6)
    ]
    starting = [
        make("end", f"要开始的活动{i}", starts_in=timedelta(hours=23), ends_in=timedelta(days=30))
        for i in range(4)
    ]
    plans = [end_plan(a) for a in ending] + [start_plan(a) for a in starting]
    text = parts_to_text(
        build_multi_game_reminder_parts([(END, plans)], NOW, MessageSettings())
    )

    assert "▎即将结束（6）" in text
    assert "▎即将开始（4）" in text
    assert len(text) < 700, f"太长: {len(text)}"
    assert text.count("\n") == 12, "13 行：标题 + 2 个分组 + 10 条活动"
