"""pytest 全局夹具：加载插件、注册适配器。

关键顺序问题：测试文件在**收集阶段**就会 ``from nonebot_plugin_game_event_due... import``。
一旦子模块被普通导入，NoneBot 之后调 ``load_plugin`` 会直接抛
``Module ... is not loaded as a plugin``。而 ``nonebot_plugin_game_event_due`` 的
``__init__`` 又需要 driver 才能读配置。

所以这里在模块顶层按顺序做三件事，早于任何测试模块被导入：

1. ``nonebot.init()``——幂等，已有 driver 就早退，后面 nonebug 再调无副作用；
2. 注册 OneBot v11 适配器；
3. ``load_plugin`` 加载被测插件。

夹具只负责确认插件确实加载成功。
"""

from __future__ import annotations

import sys
from pathlib import Path

import nonebot
import pytest

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PLUGIN_NAME = "nonebot_plugin_game_event_due"

# 1) 在导入被测包之前完成初始化
nonebot.init()

# 2) 测试要构造事件、调用 API，需要 OneBot v11 适配器
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter  # noqa: E402

_DRIVER = nonebot.get_driver()
if OneBotV11Adapter.get_name() not in {
    adapter.get_name() for adapter in _DRIVER._adapters.values()
}:
    _DRIVER.register_adapter(OneBotV11Adapter)

# 3) 先加载插件，再让测试模块导入它的子模块
if nonebot.get_plugin(PLUGIN_NAME) is None:
    nonebot.load_plugin(PLUGIN_NAME)


@pytest.fixture(scope="session", autouse=True)
def _load_game_hub(after_nonebot_init):
    """确认插件在 nonebot 里注册成功。"""
    plugin = nonebot.get_plugin(PLUGIN_NAME)
    assert plugin is not None, f"{PLUGIN_NAME} 未加载，检查插件导入错误"
    assert plugin.metadata is not None, "插件缺少 __plugin_meta__"
    return plugin
