"""24 小时边界的取舍。

规则是「未来 24 小时内要开始或要结束」，边界取**闭区间**：
恰好还剩 24:00:00 算进入窗口，多一秒就不算。

因为一天只在 12:00 检查一次，边界必须闭，否则「明天 12:00 开始」的活动
会落在今天和明天两轮的缝隙里，永远提醒不到。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from nonebot_plugin_game_event_due.events.services.reminder_service import (
    ActivityNotifyState,
    ReminderPolicy,
    collect_plans,
    current_start_tier,
    current_tier,
    evaluate_activity,
)
from nonebot_plugin_game_event_due.events.sources.base import Activity

TZ = timezone(timedelta(hours=8))
#: 今天的检查时刻
TODAY_NOON = datetime(2026, 11, 4, 12, 0, tzinfo=TZ)
#: 明天的检查时刻
TOMORROW_NOON = datetime(2026, 11, 5, 12, 0, tzinfo=TZ)

POLICY = ReminderPolicy(
    thresholds=(1,),
    start_thresholds=(1,),
    notify_new=False,
)


def make(name: str, start: datetime, end: datetime) -> Activity:
    return Activity(
        game_id="ak", activity_id=name, name=name, start_time=start, end_time=end
    )


def test_start_exactly_24h_away_is_reminded():
    """恰好明天 12:00 开始 → 今天 12:00 就推。"""
    tier = current_start_tier(TOMORROW_NOON, TODAY_NOON, (1,))
    assert tier == "s1"


def test_start_after_24h_is_silent():
    """明天 12:00:01 开始 → 今天不推，留到明天那轮。"""
    start = TOMORROW_NOON + timedelta(seconds=1)
    assert current_start_tier(start, TODAY_NOON, (1,)) == ""


def test_end_exactly_24h_away_is_reminded():
    """恰好还剩 24 小时结束 → 今天推。"""
    assert current_tier(TOMORROW_NOON, TODAY_NOON, (1,)) == "d1"


def test_end_after_24h_is_silent():
    end = TOMORROW_NOON + timedelta(seconds=1)
    assert current_tier(end, TODAY_NOON, (1,)) == ""


def test_same_day_noon_does_not_push_again():
    """当天 12:00 开始的活动，在当天这轮不再推第二次。

    任务在 12:00:00 之后几百毫秒才真正跑，所以此刻活动已经开始，
    ``is_upcoming`` 不再成立，开始倒计时链路直接跳过。
    """
    activity = make("准时开场", TOMORROW_NOON, TOMORROW_NOON + timedelta(days=10))
    run_at = TOMORROW_NOON + timedelta(milliseconds=400)

    assert activity.is_upcoming(run_at) is False
    assert collect_plans([activity], run_at, {}, POLICY) == []


def test_dedupe_blocks_repeat_even_if_window_still_open():
    """活动在 12:00:59 开始、任务 12:00:00 跑：窗口还开着，但档位已记过。"""
    start = TOMORROW_NOON + timedelta(seconds=59)
    activity = make("差一分钟", start, start + timedelta(days=10))
    run_at = TOMORROW_NOON + timedelta(milliseconds=400)

    state = {"start_tier": "s1"}
    assert evaluate_activity(activity, run_at, state, POLICY) is None
    # 没有状态时才会推
    assert evaluate_activity(activity, run_at, ActivityNotifyState(), POLICY) is not None


def test_boundary_is_closed_at_subsecond_scale():
    """任务实际跑在 12:00:00.4，昨天那轮依然把 24h 整点的活动算进来。"""
    run_at = TODAY_NOON + timedelta(milliseconds=400)
    assert current_start_tier(TOMORROW_NOON, run_at, (1,)) == "s1"
