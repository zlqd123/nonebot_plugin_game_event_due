"""活动日历的运行时装配。

把配置翻译成一组对象（HTTP 客户端 / 数据源 / 订阅服务 / 投递 / 轮询），
只装配一次，指令层与定时任务共用同一份实例。
"""

from __future__ import annotations

from pathlib import Path

from nonebot import logger

from ..config import Config, DailyPushConfig, DeadlinePushConfig, StartPushConfig
from ..core.storage import store
from ..platform.media import MediaCache
from .delivery import Delivery
from .polling import ActivityPollingService, PollingSettings
from .services.message_formatter import (
    MessageSettings,
    normalize_game_aliases,
    parse_urgency_rules,
)
from .services.reminder_service import ReminderPolicy, normalize_thresholds
from .services.subscription_service import SubscriptionService
from .sources import JsonHttpClient, SraSource
from .sources.registry import GameRegistry

#: 分隔线过长会撑爆消息，这里做一个上限保护
MAX_SEPARATOR_LENGTH = 40


def resolve_cache_dir(config: Config) -> Path | None:
    """确定缓存目录：配置优先，其次 localstore，最后包内 ``.cache``。"""
    if config.cache_dir.strip():
        path = Path(config.cache_dir.strip()).expanduser()
        try:
            path.mkdir(parents=True, exist_ok=True)
            return path
        except OSError as exc:  # pragma: no cover - 依赖文件系统
            logger.warning(f"缓存目录 {path} 不可用，退回默认目录: {exc}")

    try:
        import nonebot_plugin_localstore as localstore

        path = Path(localstore.get_plugin_cache_dir())
        path.mkdir(parents=True, exist_ok=True)
        return path
    except Exception:  # noqa: BLE001 - 没装 localstore 就用包内目录
        path = Path(__file__).resolve().parent.parent / ".cache"
        try:
            path.mkdir(parents=True, exist_ok=True)
            return path
        except OSError:  # pragma: no cover
            return None


def build_message_settings(config: Config) -> MessageSettings:
    """把配置翻译成消息格式设置。

    输出只保留活动标题 + 时间：描述、标签、额外时间段一律不开。
    ``show_deadline=True`` 是必须的——推送的唯一用途就是提醒「什么时候
    开始 / 什么时候结束」，没有时间这条消息就没信息量。
    ``insert_cover`` 打开时，有版本主视觉的游戏会多一张配图，没有图的
    游戏自动只剩文字。
    """
    return MessageSettings(
        include_description=False,
        include_tags=False,
        include_extra_times=False,
        show_deadline=True,
        single_message_per_game=config.single_message_per_game,
        activity_separator=(config.activity_separator or "").strip("\r\n")[
            :MAX_SEPARATOR_LENGTH
        ],
        urgency_marker=config.urgency_marker,
        urgency_rules=parse_urgency_rules(""),
        headline_remaining=False,
        tier_summary=False,
        sort_by_urgency=config.sort_by_urgency,
        insert_cover=config.insert_cover,
        cover_width=config.cover_width,
        game_aliases=normalize_game_aliases(config.game_aliases),
    )


def build_policy(config: Config) -> ReminderPolicy:
    """把配置翻译成提醒策略。

    ``deadline_push.enabled=false`` 等价于清空档位——一条临期提醒都不发，
    但「新活动」提醒不受影响。
    """
    deadline = config.deadline_push
    thresholds = normalize_thresholds(deadline.thresholds) if deadline.enabled else []
    start = config.start_push
    start_thresholds = (
        normalize_thresholds(start.thresholds) if start.enabled else []
    )
    return ReminderPolicy(
        thresholds=thresholds,
        start_thresholds=start_thresholds,
        notify_new=config.notify_new_activity,
        lookahead_days=config.new_activity_lookahead_days,
        remind_only_active=True,
    )


class EventsRuntime:
    """活动日历的全部依赖。"""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.message_settings = build_message_settings(config)
        self.policy = build_policy(config)
        self.daily_push: DailyPushConfig = config.daily_push
        self.deadline_push: DeadlinePushConfig = config.deadline_push
        self.start_push: StartPushConfig = config.start_push

        cache_dir = resolve_cache_dir(config)
        self.http = JsonHttpClient(
            proxy=config.proxy or None,
            timeout=float(config.request_timeout),
            cache_dir=cache_dir,
        )
        self.source = SraSource(self.http, api_base=config.sra_api_base)
        self.registry = GameRegistry([self.source])

        self.subscriptions = SubscriptionService(store.get, store.put)
        # QQ 官方 Bot 的图片素材缓存跟其它缓存放一起，重启后不必重新上传
        media_cache = MediaCache(
            (cache_dir / "material_cache.json") if cache_dir else None
        )
        self.delivery = Delivery(self.message_settings, media_cache)
        self.polling = ActivityPollingService(
            self.registry,
            self.subscriptions,
            self.delivery,
            PollingSettings(
                # 上游数据是人工维护的，8 小时内几乎不会变
                fetch_interval_hours=config.fetch_interval_hours,
                lookahead_days=self.policy.lookahead_days,
                keep_past_days=3,
                max_new_per_cycle=config.max_new_per_cycle,
                notify_on_subscribe=config.notify_on_subscribe,
                retention_days=config.state_retention_days,
                quiet_hours=config.quiet_hours,
            ),
            self.policy,
            self.message_settings,
        )

    async def close(self) -> None:
        await self.polling.stop()
        await self.http.close()


_runtime: EventsRuntime | None = None


def build_runtime(config: Config) -> EventsRuntime:
    """构建（或复用）活动日历运行时。"""
    global _runtime
    if _runtime is None:
        _runtime = EventsRuntime(config)
    return _runtime


def get_runtime() -> EventsRuntime:
    """拿到已构建的运行时；未构建时按默认配置临时构建。"""
    global _runtime
    if _runtime is None:
        _runtime = EventsRuntime(Config())
    return _runtime
