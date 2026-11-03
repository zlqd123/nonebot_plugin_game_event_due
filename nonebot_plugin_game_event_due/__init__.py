"""游戏活动日历插件入口。

只做三件事：读配置、注册指令、挂定时任务。取数与平台细节都在
``events`` 子包里。

设计要点（与上游 SRA 公共 API 的相处方式）：

* 插件自身最多每 ``fetch_interval_hours`` 小时请求一次上游；
* 机器人指令优先读本地缓存，只有 ``刷新`` 参数才强制走网络；
* 同一条消息内的多个游戏串行取数，不并发打上游。
"""

from __future__ import annotations

import nonebot
from nonebot import require
from nonebot.plugin import PluginMetadata

from .config import Config, load_config

# 定时推送是本插件的核心能力，apscheduler 缺失时只降级为手动查询
try:
    require("nonebot_plugin_apscheduler")
    SCHEDULER_AVAILABLE = True
except Exception:  # noqa: BLE001 - 缺插件不应让整个插件失效
    SCHEDULER_AVAILABLE = False
    nonebot.logger.warning("未安装 nonebot_plugin_apscheduler，定时推送已禁用")

__plugin_meta__ = PluginMetadata(
    name="游戏活动日历",
    description="订阅二游活动日历，支持每日定时推送与临期提醒（数据源：SRA 公共 API）",
    usage=(
        "活动订阅 <游戏...>      订阅游戏活动\n"
        "活动查询 [游戏...] [刷新]  查看当前活动\n"
        "活动列表                查看本会话订阅\n"
        "活动退订 <游戏...|全部>   退订\n"
        "活动阈值 7,3,1          设置临期提醒档位\n"
        "活动推送 开|关           开关本会话提醒\n"
        "活动刷新                强制拉取最新数据（管理员）\n"
        "活动状态                查看运行状态（管理员）"
    ),
    type="application",
    config=Config,
    supported_adapters={"~onebot.v11", "~qq"},
)

config = load_config()

if config.enabled:
    from .events import register as _register_events

    _register_events(config)
