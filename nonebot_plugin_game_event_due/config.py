"""插件配置。

数据源固定为 SRA 公共 API，所以配置只需要描述「取数节奏」「提醒策略」
「每日推送」三件事。缓存策略是这里最重要的部分：

* 插件自身最多每 ``fetch_interval_hours`` 小时主动请求一次上游；
* 收到机器人指令时优先读本地缓存，只有显式 ``刷新`` 才强制取网络。

环境变量统一加 ``GAME_EVENT_DUE_`` 前缀，嵌套用双下划线，例如
``GAME_EVENT_DUE_DAILY_PUSH__HOUR=8`` 对应 ``daily_push.hour``。

**为什么不用 ``get_plugin_config``**：它会调用
``BaseSettings._settings_build_values``，而这个内部函数的签名在
pydantic-settings 1.x / 2.x 之间变过；NoneBot 2.4 与 2.5 自带的实现也不一致
（2.5 改用 ``DotEnvSettingsSource``，不读真实环境变量，且 field 名需与
去前缀后的键完全一致）。结果是插件配置会静默退回默认值——最难排查的一类
bug。所以这里改为直接读 ``driver.config``：NoneBot 全局 Config 已经用
``extra="allow"`` 把 ``.env`` 与真实环境变量都解析好，并完成双下划线嵌套；
本模块只负责去掉前缀、做类型校验。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

#: 环境变量前缀（小写，NoneBot 的 Config 不区分大小写）
ENV_PREFIX = "game_event_due_"


class DailyPushConfig(BaseModel):
    """每日定时推送。

    每个群每天最多收到一条消息，多个游戏、多个活动合并在一起发，
    避免刷屏。
    """

    #: 是否启用
    enabled: bool = True
    #: 推送时刻（时，0-23，东八区），默认中午 12 点
    hour: int = Field(default=12, ge=0, le=23)
    #: 推送时刻（分，0-59）
    minute: int = Field(default=0, ge=0, le=59)
    #: 推送哪些游戏，逗号或换行分隔（支持别名）；留空表示各会话自己订阅的游戏
    games: str = ""
    #: 推送目标会话，形如 ``onebot:group:123456``，逗号或换行分隔；
    #: 留空则推送给所有已订阅会话
    targets: str = ""
    #: 推送时是否附带活动的结束时间
    include_deadline: bool = True


class StartPushConfig(BaseModel):
    """活动**开始前**的提醒（「明天要开的活动」）。

    和 :class:`DeadlinePushConfig` 的区别：那个盯的是「快结束了」，
    这个盯的是「快开始了」。默认关闭，因为对多数群来说开新活动的提醒
    属于噪音；需要日历前瞻的群可以单独打开。
    """

    #: 是否启用
    enabled: bool = False
    #: 档位，逗号分隔，``1`` 表示开始前不足 24 小时
    thresholds: str | int | list[int] = "1"


class DeadlinePushConfig(BaseModel):
    """临期提醒推送。

    提醒范围跟随各会话自己的订阅（由 ``preset_subscriptions`` 下发），
    所以这里不需要再列游戏。
    """

    #: 是否启用临期提醒；关闭后所有会话都不再收到临期通知
    enabled: bool = True
    #: 提前几天开始提醒，逗号分隔（如 ``3,1`` 表示剩 3 天与剩 1 天各提醒一次）。
    #: 也接受单个整数 ``1``（环境变量里写 ``THRESHOLDS=1`` 会被解析成 int）。
    thresholds: str | int | list[int] = "3,1"


class Config(BaseModel):
    """插件总配置。"""

    #: 未知键忽略：NoneBot 的 .env 里还有大量其它插件的配置
    model_config = {"extra": "ignore"}

    #: 总开关
    enabled: bool = True

    #: 请求超时（秒）
    request_timeout: int = Field(default=20, ge=5, le=120)
    #: 代理，留空不走代理
    proxy: str = ""
    #: 缓存目录，留空则用 localstore（不可用时退化为包内 ``.cache``）
    cache_dir: str = ""
    #: SRA 公共 API 基址
    sra_api_base: str = "https://starrailassistant.top/api/v1/activity"

    # ---- 取数节奏 ----
    #: 插件自身最少多久向上游请求一次（小时）。上游数据是人工维护的，
    #: 几小时内不会变，所以默认 8 小时足够，同时避免给公共 API 添压力。
    fetch_interval_hours: int = Field(default=8, ge=1, le=24)
    #: 启动时是否立即刷新一次缓存
    refresh_on_start: bool = True

    # ---- 提醒策略 ----
    #: 是否在新活动出现时提醒
    notify_new_activity: bool = True
    #: 新活动的提前观察天数
    new_activity_lookahead_days: int = Field(default=45, ge=0)
    #: 每轮最多推送多少条「新活动」提醒，避免刷屏
    max_new_per_cycle: int = Field(default=8, ge=0)
    #: 提醒状态保留天数
    state_retention_days: int = Field(default=30, ge=1)
    #: 订阅成功后是否立即推一条当前活动概览
    notify_on_subscribe: bool = True

    #: 静默时段，形如 ``23-8`` 表示 23:00 到次日 08:00 不推送提醒。
    #: 留空表示全天可推送。静默期内命中的提醒不会丢失，会在时段结束后
    #: 的第一次检查补发。
    quiet_hours: str = "23-8"

    # ---- 消息格式 ----
    #: 活动之间的分隔线
    activity_separator: str = "━━━━━━━━━━━━"
    #: 剩余时间色块标记（仅提醒推送，查询列表不带）
    urgency_marker: bool = True
    #: 按紧急度排序
    sort_by_urgency: bool = True
    #: 一个游戏的多条提醒合并成一条消息
    single_message_per_game: bool = True
    #: 自定义游戏显示名，形如 ``sr=星铁``，换行或逗号分隔
    game_aliases: str = ""

    # ---- 推送 ----
    daily_push: DailyPushConfig = Field(default_factory=DailyPushConfig)
    deadline_push: DeadlinePushConfig = Field(default_factory=DeadlinePushConfig)
    start_push: StartPushConfig = Field(default_factory=StartPushConfig)

    # ---- 预置订阅 ----
    #: 启动时自动写入的订阅，用于「开箱即用」地给某些群配好游戏与提醒。
    #: 格式：``会话key=游戏@结束档位~开始档位``，多个游戏/多条用逗号、
    #: 换行或 ``;`` 分隔，例如：
    #:
    #: * ``qq:group:1D89...=方舟,终末地@1``   → 结束前不足 1 天提醒
    #: * ``qq:group:1D89...=方舟,终末地~1``   → **开始**前不足 1 天提醒
    #: * ``qq:group:1D89...=方舟@3,1~1``      → 两个方向都要
    #:
    #: ``@`` 与 ``~`` 都可省略（省略表示沿用全局默认）。同一份配置只会
    #: 应用一次，之后群里用指令改过的设置不会被重启覆盖；改了这行才会
    #: 重新下发。注意：订阅指令已停用，加订阅改这里。
    preset_subscriptions: str = ""


def collect_env_values(raw: dict[str, Any]) -> dict[str, Any]:
    """从 NoneBot 全局配置里摘出本插件的键，并去掉前缀。

    ``raw`` 通常是 ``driver.config.model_dump()``。NoneBot 已经把
    ``GAME_EVENT_DUE_DAILY_PUSH__HOUR`` 解析成
    ``game_event_due_daily_push={"hour": ...}``，这里只需剥掉前缀。
    """
    values: dict[str, Any] = {}
    for key, value in raw.items():
        lowered = key.lower()
        if not lowered.startswith(ENV_PREFIX):
            continue
        field = lowered[len(ENV_PREFIX) :]
        if field:
            values[field] = value
    return values


def load_config(raw: dict[str, Any] | None = None) -> Config:
    """构造插件配置。

    不传 ``raw`` 时自动从 ``driver.config`` 读取；传入时用于测试。
    """
    if raw is None:
        from nonebot import get_driver

        raw = get_driver().config.model_dump()
    return Config.model_validate(collect_env_values(raw))
