"""SRA 数据源测试：游戏表、解析、缓存优先策略。

不发真实网络请求：用一个假客户端模拟 ``get_json`` / ``cache_age``，
把「30 分钟/8 小时最多一次」的规则锁在测试里。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nonebot_plugin_game_event_due.events.sources.base import HttpResult
from nonebot_plugin_game_event_due.events.sources.sra import SRA_GAMES, SraSource

#: 与文档一致的 18 个 game id
EXPECTED_IDS = {
    "sr",
    "ys",
    "zzz",
    "ww",
    "nte",
    "ak",
    "end",
    "ba-cn",
    "ba-jp",
    "ba-global",
    "xtlr",
    "infinitynikki",
    "gf2",
    "pgr",
    "czn",
    "bd2",
    "nikke",
    "hyrz",
}

SAMPLE = {
    "version": "2026-11",
    "versionName": "2026感谢庆典",
    "activities": [
        {
            "name": "SideStory「时间啊，向前！」限时活动",
            "description": "活动描述",
            "startTime": "2026-11-01T12:00:00",
            "endTime": "2026-11-22T03:59:59",
            "cover": "https://example.com/a.png",
        },
        {
            "name": "2026感谢庆典·限时签到",
            "startTime": "2026-11-01T12:00:00",
            "endTime": "2026-11-15T03:59:59",
        },
        # 没有结束时间应被丢弃
        {"name": "残缺活动", "startTime": "2026-11-01T12:00:00"},
    ],
    "pools": [{"name": "应被忽略的卡池"}],
}


class FakeClient:
    """记录请求次数，模拟缓存年龄。"""

    def __init__(self, *, age: float | None = None, data: dict | None = None) -> None:
        self.calls: list[str] = []
        self.age = age
        self.data = data if data is not None else SAMPLE

    def cache_age(self, cache_key: str) -> float | None:
        return self.age

    def read_cache(self, cache_key: str):
        return self.data

    async def get_json(self, url: str, *, cache_key: str | None = None, **kwargs):
        self.calls.append(url)
        return HttpResult(data=self.data)


def test_game_table_matches_documented_ids():
    """18 个游戏齐全，且 game_id 就是 URL 里的文件名。"""
    ids = {game.game_id for game in SRA_GAMES}
    assert ids == EXPECTED_IDS


@pytest.mark.parametrize(
    ("alias", "expected"),
    [
        ("ak", "ak"),
        ("方舟", "ak"),
        ("舟", "ak"),
        ("终末地", "end"),
        ("end", "end"),
        ("zmd", "end"),
        ("崩铁", "sr"),
        ("战双", "pgr"),
        ("火影", "hyrz"),
    ],
)
def test_aliases_resolve_to_expected_game(alias: str, expected: str):
    """中文别名与英文代号都能解析到正确的 game_id。"""
    from nonebot_plugin_game_event_due.events.sources.registry import GameRegistry

    registry = GameRegistry()
    registry._games = {game.game_id: game for game in SRA_GAMES}

    found = registry.resolve(alias)
    assert found, f"别名 {alias} 未解析出任何游戏"
    assert any(game.game_id == expected for game in found)


def test_parse_drops_activities_without_end_time():
    """没有结束时间的活动无法做倒计时，必须丢弃。"""
    source = SraSource(FakeClient())
    activities = source._parse(SAMPLE, "ak")
    names = [item.name for item in activities]
    assert "残缺活动" not in names
    assert len(activities) == 2


def test_parse_ignores_pools():
    """卡池暂不解析。"""
    source = SraSource(FakeClient())
    activities = source._parse(SAMPLE, "ak")
    assert all("卡池" not in item.name for item in activities)


def test_version_recorded():
    """版本号与版本名会被记下来，供状态展示。"""
    source = SraSource(FakeClient())
    source._record_meta(SAMPLE, "ak")
    assert source.versions["ak"] == "2026-11 2026感谢庆典"


@pytest.mark.asyncio
async def test_fresh_cache_skips_network_entirely():
    """缓存比阈值新时，一个请求都不发。"""
    client = FakeClient(age=3600.0)  # 1 小时
    source = SraSource(client)

    activities = await source.fetch_with_policy("ak", max_age_seconds=8 * 3600)

    assert client.calls == []
    assert len(activities) == 2
    assert "本地缓存" in source.notes["ak"]


@pytest.mark.asyncio
async def test_cache_used_before_first_network_call():
    """内存缓存为空时也能从磁盘缓存恢复，不发请求。"""
    client = FakeClient(age=3600.0)
    source = SraSource(client)

    activities = await source.fetch_with_policy("ak", max_age_seconds=8 * 3600)

    assert source._cache["ak"] == activities


@pytest.mark.asyncio
async def test_stale_cache_triggers_one_request():
    """缓存过期时只发一次请求。"""
    client = FakeClient(age=9 * 3600.0)
    source = SraSource(client)

    await source.fetch_with_policy("ak", max_age_seconds=8 * 3600)

    assert client.calls == ["https://starrailassistant.top/api/v1/activity/ak.json"]


@pytest.mark.asyncio
async def test_force_always_requests():
    """max_age_seconds=None 表示跳过缓存判断，直接取网络。"""
    client = FakeClient(age=1.0)
    source = SraSource(client)

    await source.fetch_with_policy("end", max_age_seconds=None)

    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_second_call_within_window_sends_nothing_more():
    """第一次取数后缓存变新鲜，第二次查询不再发请求。"""
    client = FakeClient(age=9 * 3600.0)  # 首次已过期 → 会请求
    source = SraSource(client)

    await source.fetch_with_policy("ak", max_age_seconds=8 * 3600)
    assert len(client.calls) == 1

    # 取数成功后缓存已被刷新
    client.age = 10.0
    await source.fetch_with_policy("ak", max_age_seconds=8 * 3600)

    assert len(client.calls) == 1, "第二次查询不应再打上游"


def test_live_fixture_shape(tmp_path: Path):
    """真实抓取的 ak.json 结构能被解析（离线样本，不联网）。"""
    payload = json.loads((Path(__file__).parent / "fixtures" / "sra_ak.json").read_text("utf-8"))
    source = SraSource(FakeClient())
    activities = source._parse(payload, "ak")
    assert activities, "真实样本解析结果不应为空"
    assert all(item.end_time is not None for item in activities)
