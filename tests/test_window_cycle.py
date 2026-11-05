"""窗口快照接进 run_cycle 的集成测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from nonebot_plugin_game_event_due.events.polling import (
    ActivityPollingService,
    DailyCheckSettings,
    PollingSettings,
)
from nonebot_plugin_game_event_due.events.services.message_formatter import MessageSettings
from nonebot_plugin_game_event_due.events.services.reminder_service import ReminderPolicy
from nonebot_plugin_game_event_due.events.services.subscription_service import (
    SubscriptionService,
)
from nonebot_plugin_game_event_due.events.sources.base import Activity, Game
from nonebot_plugin_game_event_due.events.sources.registry import GameRegistry

TZ = timezone(timedelta(hours=8))
C = datetime(2026, 11, 5, 12, 0, 1, tzinfo=TZ)
GAME = Game("end", "明日方舟：终末地", "sra", ("终末地",), "终末地")


class FakeSource:
    def __init__(self, activities):
        self._activities = activities
        self.fetch_calls = 0

    async def fetch(self, game_id):
        return list(self._activities)

    async def fetch_with_policy(self, game_id, *, max_age_seconds=None):
        self.fetch_calls += 1
        return list(self._activities)

    def note(self, game_id):
        return ""

    def games(self):
        return [GAME]


class FakeDelivery:
    def __init__(self):
        self.pushed: list[list] = []

    async def push(self, session, parts):
        self.pushed.append(list(parts))
        return True

    async def push_text(self, session, text):
        return True


@pytest.mark.asyncio
async def test_run_cycle_sends_one_snapshot_per_day(monkeypatch):
    """一轮 = 一条消息；同一天再跑不再发；不写 notify_state。"""
    from nonebot_plugin_game_event_due.events import polling as polling_module

    monkeypatch.setattr(
        polling_module,
        "datetime",
        type(
            "FakeDatetime",
            (),
            {"now": staticmethod(lambda tz: C)},
        ),
    )

    activities = [
        Activity(
            game_id="end",
            activity_id="a",
            name="已开始的活动",
            start_time=C - timedelta(hours=1),
            end_time=C + timedelta(days=5),
        ),
        Activity(
            game_id="end",
            activity_id="b",
            name="已结束的活动",
            start_time=C - timedelta(days=5),
            end_time=C - timedelta(hours=1),
        ),
    ]

    store: dict[str, object] = {}

    async def get_kv(key, default):
        return store.get(key, default)

    async def put_kv(key, value):
        store[key] = value

    subs = SubscriptionService(get_kv, put_kv)
    delivery = FakeDelivery()
    registry = GameRegistry()
    registry._games = {"end": GAME}
    registry._sources = {"end": FakeSource(activities)}

    polling = ActivityPollingService(
        registry=registry,
        subscriptions=subs,
        delivery=delivery,
        settings=PollingSettings(
            quiet_hours="",
            window_hours=24,
            daily_check=DailyCheckSettings(hour=12, minute=0, second=1),
        ),
        default_policy=ReminderPolicy(),
        message_settings=MessageSettings(insert_cover=False),
    )

    umo = "qq:group:ABC"
    await subs.add_games(umo, ["end"])

    report1 = await polling.run_cycle(force=True)
    assert report1.messages == 1
    assert report1.reminders == 2
    assert len(delivery.pushed) == 1

    text = "\n".join(str(part.value) for part in delivery.pushed[0])
    assert "🎮 明日方舟：终末地" in text
    assert "▎已开始（1）" in text
    assert "▎已结束（1）" in text
    assert "已开始的活动" in text and "已结束的活动" in text

    # 没有写档位状态：窗口快照不依赖 notify_state 去重
    assert await subs.get_notify_state() == {}

    report2 = await polling.run_cycle(force=True)
    assert report2.messages == 0
    assert len(delivery.pushed) == 1


@pytest.mark.asyncio
async def test_run_cycle_skips_empty_window(monkeypatch):
    """窗口里一条活动都没有时不发消息。"""
    from nonebot_plugin_game_event_due.events import polling as polling_module

    monkeypatch.setattr(
        polling_module,
        "datetime",
        type("FakeDatetime", (), {"now": staticmethod(lambda tz: C)}),
    )

    store: dict[str, object] = {}

    async def get_kv(key, default):
        return store.get(key, default)

    async def put_kv(key, value):
        store[key] = value

    subs = SubscriptionService(get_kv, put_kv)
    delivery = FakeDelivery()
    registry = GameRegistry()
    registry._games = {"end": GAME}
    registry._sources = {"end": FakeSource([])}

    polling = ActivityPollingService(
        registry=registry,
        subscriptions=subs,
        delivery=delivery,
        settings=PollingSettings(quiet_hours=""),
        default_policy=ReminderPolicy(),
        message_settings=MessageSettings(),
    )
    await subs.add_games("qq:group:ABC", ["end"])

    report = await polling.run_cycle(force=True)
    assert report.messages == 0
    assert delivery.pushed == []
