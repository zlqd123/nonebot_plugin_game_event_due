"""「活动开始前不足一天」提醒的测试。

和「快结束了」是两条独立的线：一个活动可以只在开始前提醒，
完全不管结束时间。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from nonebot_plugin_game_event_due.events.services.reminder_service import (
    ActivityNotifyState,
    ReminderPolicy,
    apply_plan,
    collect_plans,
    current_start_tier,
    reason_label,
)
from nonebot_plugin_game_event_due.events.sources.base import Activity

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=TZ)


def make(name: str, *, starts_in: timedelta, lasts: timedelta = timedelta(days=10)):
    return Activity(
        game_id="ak",
        activity_id=name,
        name=name,
        start_time=NOW + starts_in,
        end_time=NOW + starts_in + lasts,
    )


# ---- 档位计算 ----


def test_start_tier_hit_within_one_day():
    """20 小时后开始 → 命中 s1。"""
    assert current_start_tier(NOW + timedelta(hours=20), NOW, [1]) == "s1"


def test_start_tier_miss_beyond_one_day():
    """3 天后才开始 → 不命中。"""
    assert current_start_tier(NOW + timedelta(days=3), NOW, [1]) == ""


def test_start_tier_already_started():
    """已经开始的活动不再有「开始前」提醒。"""
    assert current_start_tier(NOW - timedelta(hours=1), NOW, [1]) == ""


def test_start_tier_thresholds_ordering():
    """多个档位取最紧急的那个。"""
    assert current_start_tier(NOW + timedelta(hours=20), NOW, [3, 1]) == "s1"
    assert current_start_tier(NOW + timedelta(days=2), NOW, [3, 1]) == "s3"


def test_reason_label_start_vs_end():
    """两种档位的文案要能区分开。"""
    assert reason_label("s1") == "开始不足24小时"
    assert reason_label("s3") == "开始不足3天"
    assert reason_label("d1") == "剩余不足24小时"
    assert reason_label("d3") == "剩余不足3天"


# ---- 决策 ----


def test_upcoming_activity_planned_for_start_reminder():
    """只有「快开始」的活动进计划。"""
    policy = ReminderPolicy(
        thresholds=(),
        start_thresholds=(1,),
        notify_new=False,
    )
    soon = make("即将开始", starts_in=timedelta(hours=20))
    later = make("还早", starts_in=timedelta(days=5))

    plans = collect_plans([soon, later], NOW, {}, policy)

    assert [p.activity.name for p in plans] == ["即将开始"]
    assert plans[0].start_tier == "s1"
    assert plans[0].tier == "", "不应同时产生结束档位"


def test_start_reminder_ignores_end_thresholds():
    """只配开始档位时，快结束的活动不会被推。"""
    policy = ReminderPolicy(thresholds=(), start_thresholds=(1,), notify_new=False)
    ending = Activity(
        game_id="ak",
        activity_id="ending",
        name="快结束的",
        start_time=NOW - timedelta(days=9),
        end_time=NOW + timedelta(hours=5),
    )

    assert collect_plans([ending], NOW, {}, policy) == []


def test_start_reminder_deduped_after_push():
    """推过一次后，同档位不会再推。"""
    policy = ReminderPolicy(thresholds=(), start_thresholds=(1,), notify_new=False)
    soon = make("即将开始", starts_in=timedelta(hours=20))

    first = collect_plans([soon], NOW, {}, policy)
    assert len(first) == 1

    state = ActivityNotifyState()
    apply_plan(state, first[0])
    states = {soon.uid: state.to_dict()}

    assert collect_plans([soon], NOW, states, policy) == []


def test_state_persists_start_tier():
    """开始档位要能存进 KV 再读回来。"""
    state = ActivityNotifyState(start_tier="s1")
    payload = state.to_dict()

    assert payload["start_tier"] == "s1"
    assert ActivityNotifyState.from_dict(payload).start_tier == "s1"


def test_both_directions_in_one_plan():
    """同时配两个方向时，一个活动可以带两条理由。"""
    policy = ReminderPolicy(
        thresholds=(7, 3, 1),
        start_thresholds=(1,),
        notify_new=False,
        remind_only_active=False,
    )
    # 20 小时后开始、2 天后结束 → 两个方向同时命中，合并成一条推送
    act = make("双向", starts_in=timedelta(hours=20), lasts=timedelta(days=2))
    plans = collect_plans([act], NOW, {}, policy)

    assert len(plans) == 1, "同一个活动只出一条消息"
    assert plans[0].start_tier == "s1"
    assert plans[0].tier == "d3"
    assert set(plans[0].reasons) == {"s1", "d3"}


def test_disabled_policy_produces_nothing():
    policy = ReminderPolicy(thresholds=(), start_thresholds=(), notify_new=False)
    assert collect_plans([make("x", starts_in=timedelta(hours=2))], NOW, {}, policy) == []


def test_start_reminder_shows_start_time_not_deadline():
    """「开始前」提醒必须显示开始时间，不能显示结束时间。"""
    from nonebot_plugin_game_event_due.events.services.message_formatter import (
        MessageSettings,
        build_reminder_parts,
        parts_to_text,
    )
    from nonebot_plugin_game_event_due.events.sources.base import Game

    policy = ReminderPolicy(thresholds=(), start_thresholds=(1,), notify_new=False)
    act = make("开赛倒计时", starts_in=timedelta(hours=18), lasts=timedelta(days=14))
    plans = collect_plans([act], NOW, {}, policy)
    assert len(plans) == 1

    game = Game("ak", "明日方舟", "sra", ("方舟",), "明日方舟")
    text = parts_to_text(
        build_reminder_parts(game, plans, NOW, MessageSettings(show_deadline=True))
    )

    assert "开始不足24小时" in text
    assert " 开始" in text, "应显示开始时间"
    assert "剩 " not in text, "不应显示「还剩多少天结束」"


def test_deadline_reminder_still_shows_end_time():
    """结束前提醒保持原样：显示截止时间。"""
    from nonebot_plugin_game_event_due.events.services.message_formatter import (
        MessageSettings,
        build_reminder_parts,
        parts_to_text,
    )
    from nonebot_plugin_game_event_due.events.sources.base import Game

    policy = ReminderPolicy(thresholds=(1,), start_thresholds=(), notify_new=False)
    ending = Activity(
        game_id="ak",
        activity_id="ending",
        name="快结束的",
        start_time=NOW - timedelta(days=9),
        end_time=NOW + timedelta(hours=5),
    )
    plans = collect_plans([ending], NOW, {}, policy)
    game = Game("ak", "明日方舟", "sra", ("方舟",), "明日方舟")
    text = parts_to_text(
        build_reminder_parts(game, plans, NOW, MessageSettings(show_deadline=True))
    )

    assert "剩余不足24小时" in text
    assert "剩 " in text, "结束提醒应带「还剩多少」"
    assert " 开始" not in text
