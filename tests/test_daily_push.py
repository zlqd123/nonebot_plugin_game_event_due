"""每日推送与订阅策略测试。

覆盖本次需求的四条规则：

1. 私聊不能订阅；
2. 每个群每天最多一条消息；
3. 多个游戏、多个活动合并进同一条消息；
4. 预置订阅能正确解析出「游戏 + 提醒档位」。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from nonebot_plugin_game_event_due.events import parse_presets
from nonebot_plugin_game_event_due.events.sources.base import Activity, Game

TZ = timezone(timedelta(hours=8))


# ---- 预置订阅解析 ----


def test_parse_preset_basic():
    """会话key=游戏@结束档位 三段都能解析。"""
    entries = parse_presets("qq:group:ABC123=方舟,终末地@1")

    assert len(entries) == 1
    key, games, end_thresholds, start_thresholds = entries[0]
    assert key == "qq:group:ABC123"
    assert games == ["方舟", "终末地"]
    assert end_thresholds == [1]
    assert start_thresholds is None


def test_parse_preset_start_only():
    """``~1`` 表示开始前不足 1 天提醒。"""
    (entry,) = parse_presets("qq:group:ABC123=方舟,终末地~1")
    key, games, end_thresholds, start_thresholds = entry

    assert games == ["方舟", "终末地"]
    assert end_thresholds is None
    assert start_thresholds == [1]


def test_parse_preset_both_directions():
    """``@3,1~1`` 同时配置结束与开始两个方向。"""
    (entry,) = parse_presets("qq:group:ABC=方舟@3,1~1")
    _, games, end_thresholds, start_thresholds = entry

    assert games == ["方舟"]
    assert end_thresholds == [3, 1]
    assert start_thresholds == [1]


def test_parse_preset_without_threshold():
    """两个标记都省略时返回 None，表示跟随全局默认。"""
    (entry,) = parse_presets("onebot:group:123456=ak")
    _, games, end_thresholds, start_thresholds = entry

    assert games == ["ak"]
    assert end_thresholds is None
    assert start_thresholds is None


def test_parse_preset_at_without_days_means_off():
    """写空的 @ / ~ 表示显式关闭该方向。"""
    (entry,) = parse_presets("qq:group:A=ak@~")
    _, _, end_thresholds, start_thresholds = entry

    assert end_thresholds == []
    assert start_thresholds == []


def test_parse_preset_multiple_entries():
    """多条用换行或分号分隔。"""
    raw = "qq:group:A=ak@1\nonebot:group:1=end@3,1;qq:group:B=sr~1"
    entries = parse_presets(raw)

    assert [e[0] for e in entries] == [
        "qq:group:A",
        "onebot:group:1",
        "qq:group:B",
    ]
    assert entries[2][3] == [1], "第三条是开始档位"


def test_parse_preset_ignores_broken_lines():
    """格式错误的行跳过，不能让插件起不来。"""
    entries = parse_presets("随便写的\nqq:group:A=\n=ak\nqq:group:B=ak")
    assert [e[0] for e in entries] == ["qq:group:B"]


def test_parse_preset_empty():
    assert parse_presets("") == []


def test_parse_preset_games_split_by_space():
    """游戏之间也允许用空格分隔。"""
    (entry,) = parse_presets("qq:group:A=方舟 终末地@1")
    assert entry[1] == ["方舟", "终末地"]


# ---- 合并推送的消息结构 ----


def make_activity(name: str, days_left: float = 5) -> Activity:
    now = datetime(2026, 11, 2, 12, 0, tzinfo=TZ)
    return Activity(
        game_id="ak",
        activity_id=name,
        name=name,
        start_time=now - timedelta(days=3),
        end_time=now + timedelta(days=days_left),
    )


def test_merged_message_contains_every_game():
    """两个游戏的活动合并后，同一条消息里都能看到。"""
    from nonebot_plugin_game_event_due.events.services.message_formatter import (
        MessagePart,
        MessageSettings,
        build_overview_parts,
        parts_to_text,
    )

    now = datetime(2026, 11, 2, 12, 0, tzinfo=TZ)
    ak = Game("ak", "明日方舟", "sra", ("方舟",), "明日方舟")
    end = Game("end", "明日方舟：终末地", "sra", ("终末地",), "终末地")

    merged: list[MessagePart] = [
        MessagePart(kind="text", value=f"📅 今日活动 · {now:%m-%d}")
    ]
    for game, acts in ((ak, [make_activity("方舟活动")]), (end, [make_activity("终末地活动")])):
        merged.append(MessagePart(kind="text", value=""))
        merged.extend(build_overview_parts(game, acts, now, MessageSettings()))

    text = parts_to_text(merged)

    assert text.count("📅") == 1, "只应有一个日期标题"
    assert "方舟活动" in text
    assert "终末地活动" in text
    assert "🎮 明日方舟" in text
    assert "🎮 明日方舟：终末地" in text


def test_merged_message_is_single_part():
    """合并后仍是一个文本片段，发送层只发一条消息。"""
    from nonebot_plugin_game_event_due.events.services.message_formatter import (
        MessagePart,
        parts_to_text,
    )

    parts = [
        MessagePart(kind="text", value="📅 今日活动 · 11-02"),
        MessagePart(kind="text", value=""),
        MessagePart(kind="text", value="🎮 明日方舟"),
    ]
    text = parts_to_text(parts)
    assert text.startswith("📅 今日活动")
    assert "🎮 明日方舟" in text


# ---- 每天最多一次 ----


@pytest.mark.asyncio
async def test_daily_state_roundtrip():
    """每日推送状态能写入并读回，用于判重。"""
    from nonebot_plugin_game_event_due.events.services.subscription_service import (
        SubscriptionService,
    )

    store: dict[str, object] = {}

    async def get_kv(key, default):
        return store.get(key, default)

    async def put_kv(key, value):
        store[key] = value

    service = SubscriptionService(get_kv, put_kv)

    assert await service.get_daily_state() == {}
    await service.mark_daily_pushed("qq:group:ABC", "2026-11-02")
    assert await service.get_daily_state() == {"qq:group:ABC": "2026-11-02"}

    # 再写一次不会丢别的会话
    await service.mark_daily_pushed("onebot:group:1", "2026-11-02")
    state = await service.get_daily_state()
    assert state["qq:group:ABC"] == "2026-11-02"
    assert state["onebot:group:1"] == "2026-11-02"


@pytest.mark.asyncio
async def test_preset_signature_roundtrip():
    """预置订阅签名用于「只在配置变化时重新应用」。"""
    from nonebot_plugin_game_event_due.events.services.subscription_service import (
        SubscriptionService,
    )

    store: dict[str, object] = {}

    async def get_kv(key, default):
        return store.get(key, default)

    async def put_kv(key, value):
        store[key] = value

    service = SubscriptionService(get_kv, put_kv)

    assert await service.get_preset_signature() == ""
    await service.save_preset_signature("abc123")
    assert await service.get_preset_signature() == "abc123"


# ---- 每天最多一次（端到端，用假投递） ----


class _FakeDelivery:
    """记录推送，永远成功。"""

    def __init__(self) -> None:
        self.pushed: list[tuple[str, str]] = []

    async def push(self, session, parts) -> bool:
        from nonebot_plugin_game_event_due.events.services.message_formatter import (
            parts_to_text,
        )

        self.pushed.append((str(session), parts_to_text(parts)))
        return True

    async def push_text(self, session, text) -> bool:
        self.pushed.append((str(session), text))
        return True


class _FakeSource:
    """固定返回两条活动，不联网。"""

    display_name = "SRA 测试"

    def __init__(self) -> None:
        now = datetime.now(TZ)
        self._activities = [
            Activity(
                game_id="ak",
                activity_id="a1",
                name="方舟活动一",
                start_time=now - timedelta(days=1),
                end_time=now + timedelta(days=5),
            ),
            Activity(
                game_id="ak",
                activity_id="a2",
                name="方舟活动二",
                start_time=now - timedelta(days=1),
                end_time=now + timedelta(days=9),
            ),
        ]

    async def fetch_with_policy(self, game_id, *, max_age_seconds=None):
        return list(self._activities)

    def note(self, game_id):
        return ""


@pytest.mark.asyncio
async def test_daily_push_once_per_day_and_merged():
    """第一次推一条合并消息，同一天再跑就不推了。"""
    from nonebot_plugin_game_event_due.events.polling import (
        ActivityPollingService,
        PollingSettings,
    )
    from nonebot_plugin_game_event_due.events.services.message_formatter import (
        MessageSettings,
    )
    from nonebot_plugin_game_event_due.events.services.reminder_service import (
        ReminderPolicy,
    )
    from nonebot_plugin_game_event_due.events.services.subscription_service import (
        SubscriptionService,
    )
    from nonebot_plugin_game_event_due.events.sources.registry import GameRegistry
    from nonebot_plugin_game_event_due.events.sources.sra import SRA_GAMES
    from nonebot_plugin_game_event_due.events.sources.base import Game

    store: dict[str, object] = {}

    async def get_kv(key, default):
        return store.get(key, default)

    async def put_kv(key, value):
        store[key] = value

    service = SubscriptionService(get_kv, put_kv)
    delivery = _FakeDelivery()

    game = Game("ak", "明日方舟", "sra", ("方舟",), "明日方舟")
    registry = GameRegistry()
    fake_source = _FakeSource()
    registry._games = {"ak": game}
    registry._sources = {"ak": fake_source}

    polling = ActivityPollingService(
        registry=registry,
        subscriptions=service,
        delivery=delivery,
        settings=PollingSettings(),
        default_policy=ReminderPolicy(),
        message_settings=MessageSettings(),
    )

    umo = "qq:group:ABC123"
    await service.add_games(umo, ["ak"])

    report1 = await polling.send_daily_push()
    assert report1.messages == 1
    assert len(delivery.pushed) == 1

    text = delivery.pushed[0][1]
    assert "方舟活动一" in text and "方舟活动二" in text, "两个活动应在同一条消息"
    assert text.count("📅") == 1

    # 同一天再跑：一条都不发
    report2 = await polling.send_daily_push()
    assert report2.messages == 0
    assert len(delivery.pushed) == 1


@pytest.mark.asyncio
async def test_daily_push_skips_private_sessions():
    """私聊会话不会收到每日推送。"""
    from nonebot_plugin_game_event_due.events.polling import (
        ActivityPollingService,
        PollingSettings,
    )
    from nonebot_plugin_game_event_due.events.services.message_formatter import (
        MessageSettings,
    )
    from nonebot_plugin_game_event_due.events.services.reminder_service import (
        ReminderPolicy,
    )
    from nonebot_plugin_game_event_due.events.services.subscription_service import (
        SubscriptionService,
    )
    from nonebot_plugin_game_event_due.events.sources.registry import GameRegistry
    from nonebot_plugin_game_event_due.events.sources.base import Game

    store: dict[str, object] = {}

    async def get_kv(key, default):
        return store.get(key, default)

    async def put_kv(key, value):
        store[key] = value

    service = SubscriptionService(get_kv, put_kv)
    delivery = _FakeDelivery()

    registry = GameRegistry()
    fake_source = _FakeSource()
    registry._games = {"ak": Game("ak", "明日方舟", "sra", ("方舟",), "明日方舟")}
    registry._sources = {"ak": fake_source}
    polling = ActivityPollingService(
        registry=registry,
        subscriptions=service,
        delivery=delivery,
        settings=PollingSettings(),
        default_policy=ReminderPolicy(),
        message_settings=MessageSettings(),
    )

    await service.add_games("qq:private:USER1", ["ak"])
    await service.add_games("qq:group:GROUP1", ["ak"])

    report = await polling.send_daily_push()

    assert report.messages == 1
    assert all("private" not in session for session, _ in delivery.pushed)
    assert delivery.pushed[0][0] == "qq:group:GROUP1"
