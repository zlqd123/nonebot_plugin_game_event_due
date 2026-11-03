"""静默时段判定的测试。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from nonebot_plugin_game_event_due.events.polling import in_quiet_hours

TZ = timezone(timedelta(hours=8))


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 4, hour, minute, tzinfo=TZ)


@pytest.mark.parametrize(
    ("hour", "expected"),
    [
        (23, True),
        (0, True),
        (3, True),
        (7, True),
        (8, False),
        (12, False),
        (22, False),
        (22.0, False),
    ],
)
def test_cross_midnight_spec(hour, expected):
    """23-8 表示 23:00 到次日 08:00。"""
    assert in_quiet_hours(at(int(hour)), "23-8") is expected


@pytest.mark.parametrize(
    ("hour", "expected"),
    [(1, True), (5, True), (6, False), (12, False), (23, False)],
)
def test_same_day_spec(hour, expected):
    """1-6 表示凌晨 1 点到 6 点。"""
    assert in_quiet_hours(at(hour), "1-6") is expected


def test_empty_spec_means_always_allowed():
    """留空 = 不设静默时段。"""
    for hour in range(24):
        assert in_quiet_hours(at(hour), "") is False


def test_broken_spec_is_ignored():
    """写错的配置宁可多推，也不要把提醒永久吞掉。"""
    for spec in ("abc", "23", "23-8-1", "-", "25-8", "23-25"):
        assert in_quiet_hours(at(3), spec) is False


def test_equal_bounds_means_disabled():
    assert in_quiet_hours(at(3), "5-5") is False


def test_minute_does_not_matter():
    assert in_quiet_hours(at(23, 1), "23-8") is True
    assert in_quiet_hours(at(7, 59), "23-8") is True
