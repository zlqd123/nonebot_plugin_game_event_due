"""配置测试：前缀剥离、嵌套解析、默认值。

这几条断言是为了钉住一个曾出过问题的行为：插件配置**不能**依赖
``get_plugin_config``，否则在不同 NoneBot / pydantic-settings 组合下会
静默退回默认值。现在改为读 ``driver.config``，这里用字典模拟它。
"""

from __future__ import annotations

import pytest

from nonebot_plugin_game_event_due.config import (
    ENV_PREFIX,
    Config,
    collect_env_values,
    load_config,
)


def test_defaults():
    """不配置任何环境变量时的默认值。"""
    config = load_config({})

    assert config.enabled is True
    assert config.fetch_interval_hours == 8
    # 每日推送默认开启，默认中午 12 点
    assert config.daily_push.enabled is True
    assert config.daily_push.hour == 12
    assert config.deadline_push.enabled is True
    assert config.deadline_push.thresholds == "3,1"


def test_plain_field_override():
    """扁平字段能覆盖默认值。"""
    config = load_config({f"{ENV_PREFIX}fetch_interval_hours": 3})
    assert config.fetch_interval_hours == 3


def test_nested_field_override():
    """双下划线嵌套配置能落到子模型。"""
    config = load_config(
        {
            f"{ENV_PREFIX}daily_push": {
                "enabled": True,
                "hour": 21,
                "minute": 30,
                "games": "ak,end",
            }
        }
    )

    assert config.daily_push.enabled is True
    assert config.daily_push.hour == 21
    assert config.daily_push.minute == 30
    assert config.daily_push.games == "ak,end"


def test_unknown_keys_ignored():
    """.env 里有大量其它插件的配置，必须忽略而不是报错。"""
    config = load_config(
        {
            "bison_config_path": "/data",
            "superusers": {"123"},
            "command_start": {"/"},
            f"{ENV_PREFIX}fetch_interval_hours": 6,
        }
    )
    assert config.fetch_interval_hours == 6


def test_prefix_required():
    """没有前缀的同名字段不能被误读。"""
    config = load_config({"fetch_interval_hours": 2, "daily_push": {"hour": 3}})
    assert config.fetch_interval_hours == 8
    assert config.daily_push.hour == 12


def test_prefix_is_case_insensitive():
    """NoneBot 的 Config 不区分大小写，键可能是原始大小写。"""
    values = collect_env_values({"GAME_EVENT_DUE_FETCH_INTERVAL_HOURS": 4})
    assert values == {"fetch_interval_hours": 4}


def test_bare_prefix_key_ignored():
    """只有前缀、没有字段名的键应被跳过，而不是产生空字段名。"""
    values = collect_env_values({ENV_PREFIX.rstrip("_"): "x"})
    assert values == {}


def test_hour_range_validation():
    """非法时间会被 pydantic 拒绝，避免注册出永不触发的定时任务。"""
    with pytest.raises(Exception):
        load_config({f"{ENV_PREFIX}daily_push": {"hour": 25}})


def test_fetch_interval_range_validation():
    with pytest.raises(Exception):
        load_config({f"{ENV_PREFIX}fetch_interval_hours": 0})


def test_config_is_frozen_against_extra():
    """多余字段不进入模型，保持属性访问确定。"""
    config = load_config({f"{ENV_PREFIX}nonexistent": 1})
    assert not hasattr(config, "nonexistent")
    assert isinstance(config, Config)

def test_deadline_disabled_clears_thresholds():
    """关掉临期提醒等价于清空档位，但新活动提醒不受影响。"""
    from nonebot_plugin_game_event_due.events.runtime import build_policy

    config = load_config(
        {
            f"{ENV_PREFIX}deadline_push": {"enabled": False, "thresholds": "3,1"},
            f"{ENV_PREFIX}notify_new_activity": True,
        }
    )
    policy = build_policy(config)

    assert policy.thresholds == []
    assert policy.notify_new is True


def test_deadline_enabled_keeps_thresholds():
    """开启时保留配置档位，并按从大到小排序。"""
    from nonebot_plugin_game_event_due.events.runtime import build_policy

    config = load_config({f"{ENV_PREFIX}deadline_push": {"thresholds": "1,7,3"}})
    assert tuple(build_policy(config).thresholds) == (7, 3, 1)


def test_token_splitting_accepts_cn_and_ascii_separators():
    """每日推送的游戏/目标列表用中英文逗号与空白都能分隔。"""
    from nonebot_plugin_game_event_due.events import _tokens

    assert _tokens("方舟,终末地 end") == ["方舟", "终末地", "end"]
    assert _tokens("ak，end") == ["ak", "end"]
    assert _tokens("") == []
