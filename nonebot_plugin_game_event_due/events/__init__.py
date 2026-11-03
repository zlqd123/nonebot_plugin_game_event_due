"""活动日历域。

注册内容包括：

* 临期提醒任务：每天 ``daily_push.hour`` 跑一轮，把 24 小时内开始/结束的
  活动汇总成一条推出去（同时刷新缓存）；
* 可选的每日摘要任务：把指定游戏的全部活动推一遍（默认关闭）；
* 插件卸载时关闭 HTTP 客户端。

两个任务都由 ``nonebot_plugin_apscheduler`` 驱动，本模块不自己起循环。
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime

from nonebot import get_driver, logger

from ..config import Config
from ..platform import parse_session_key
from .runtime import build_runtime
from .services.reminder_service import normalize_thresholds
from .sources.base import GAME_TIMEZONE

__all__ = ["register"]

#: 预置订阅多条之间的分隔符：分号（半角/全角）或换行
PRESET_SPLIT = "[;；" + chr(10) + "]+"


def register(config: Config) -> None:
    runtime = build_runtime(config)

    from . import commands

    commands.register(config)

    _schedule(config, runtime)
    _register_shutdown(runtime)
    _apply_presets(config, runtime)


def _schedule(config: Config, runtime) -> None:
    """挂上临期检查与每日推送两个任务。"""
    try:
        from nonebot_plugin_apscheduler import scheduler
    except Exception:  # noqa: BLE001 - 没装就只跑手动查询
        logger.warning("未安装 nonebot_plugin_apscheduler，定时推送未启动")
        return

    _schedule_fetch(config, runtime, scheduler)
    _schedule_daily_push(config, runtime, scheduler)


def _schedule_fetch(config: Config, runtime, scheduler) -> None:
    """每天固定时刻跑一轮临期检查（同时强制刷新缓存）。"""
    daily = config.daily_push
    scheduler.add_job(
        runtime.polling.run_cycle,
        "cron",
        hour=daily.hour,
        minute=daily.minute,
        id="game_event_due_check",
        replace_existing=True,
        # 一天只有这一轮，缓存必然过期，直接强制取数
        kwargs={"force": True},
        # 一轮没跑完就跳过本次，不要堆积
        max_instances=1,
        coalesce=True,
    )
    logger.info(f"活动临期检查已注册: 每天 {daily.hour:02d}:{daily.minute:02d}")


def _schedule_daily_push(config: Config, runtime, scheduler) -> None:
    """按配置时刻推送每日活动。"""
    daily = config.daily_push
    if not daily.enabled:
        return

    games, unknown = runtime.registry.resolve_many(_tokens(daily.games))
    if unknown:
        logger.warning(f"每日推送配置里有无法识别的游戏，已忽略: {'、'.join(unknown)}")
    targets = _tokens(daily.targets)

    async def _job() -> None:
        await runtime.polling.send_daily_push(
            game_tokens=games,
            session_keys=targets,
            include_deadline=daily.include_deadline,
        )

    scheduler.add_job(
        _job,
        "cron",
        hour=daily.hour,
        minute=daily.minute,
        id="game_event_due_daily_push",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scope = "、".join(game.name for game in games) or "各会话订阅的游戏"
    logger.info(
        f"每日活动推送已注册: {daily.hour:02d}:{daily.minute:02d} → {scope}"
    )


def _tokens(value: str) -> list[str]:
    """把逗号/空白/换行分隔的配置字符串拆成 token 列表。"""
    return [token for token in re.split(r"[,，\s]+", str(value or "")) if token]


def parse_presets(
    value: str,
) -> list[tuple[str, list[str], list[int] | None, list[int] | None]]:
    """解析预置订阅配置。

    格式：``会话key=游戏1,游戏2@结束档位~开始档位``，多条用换行或 ``;`` 分隔。

    * ``会话key`` 是 ``qq:group:xxx`` / ``onebot:group:123`` 这类标识；
    * 游戏支持代号与中文别名；
    * ``@1`` 表示「结束前不足 1 天提醒」；
    * ``~1`` 表示「**开始**前不足 1 天提醒」；
    * 两个标记都可省略，省略时返回 ``None`` 表示沿用全局配置。

    解析不出的项直接跳过，并交给调用方记录告警——配置写错不应该让插件起不来。
    """
    entries: list[tuple[str, list[str], list[int] | None, list[int] | None]] = []
    for raw in re.split(PRESET_SPLIT, str(value or "")):
        line = raw.strip()
        if not line or "=" not in line:
            continue
        key, _, rest = line.partition("=")
        key = key.strip()
        rest = rest.strip()

        end_thresholds: list[int] | None = None
        start_thresholds: list[int] | None = None

        if "@" in rest:
            rest, _, end_text = rest.partition("@")
            end_text, tilde, start_text = end_text.partition("~")
            end_thresholds = list(normalize_thresholds(_tokens(end_text)))
            if tilde:
                start_thresholds = list(normalize_thresholds(_tokens(start_text)))
        elif "~" in rest:
            rest, _, start_text = rest.partition("~")
            start_thresholds = list(normalize_thresholds(_tokens(start_text)))

        games = _tokens(rest)
        if key and games:
            entries.append((key, games, end_thresholds, start_thresholds))
    return entries


async def _prime_states(runtime, umo: str, games) -> None:
    """把当前已有的活动标记为「已知」，避免预置后立刻刷一屏提醒。

    和 ``/活动订阅`` 做的事一样，只是不发概览消息——定时任务启动时不需要
    给群里补一条历史活动。
    """
    from .services.subscription_service import resolve_policy

    now = datetime.now(GAME_TIMEZONE)
    session = await runtime.subscriptions.get_session(umo) or {}
    policy = resolve_policy(session, runtime.policy)
    notify_state = await runtime.subscriptions.get_notify_state()
    bucket = notify_state.setdefault(umo, {})
    changed = False

    for game in games:
        try:
            activities = await runtime.polling.fetch_activities(game.game_id)
        except Exception as exc:  # noqa: BLE001 - 取不到就下次再说
            logger.warning(f"预置订阅初始化 {game.name} 失败: {exc}")
            continue
        initialized = runtime.subscriptions.build_initial_states(activities, now, policy)
        if initialized:
            bucket.update(initialized)
            changed = True

    if changed:
        await runtime.subscriptions.save_notify_state(notify_state)


def _apply_presets(config: Config, runtime) -> None:
    """按配置给指定会话写入订阅。

    只在配置内容变化时应用一次：这样用户之后用 ``/活动阈值``、``/活动订阅``
    改过的东西不会被每次重启覆盖，但改了 ``preset_subscriptions`` 就会重新
    下发。
    """
    raw = (config.preset_subscriptions or "").strip()
    if not raw:
        return

    entries = parse_presets(raw)
    if not entries:
        logger.warning("preset_subscriptions 解析为空，请检查格式：会话key=游戏@档位")
        return

    async def _job() -> None:
        signature = hashlib.sha1(raw.encode("utf-8")).hexdigest()
        if await runtime.subscriptions.get_preset_signature() == signature:
            return

        for key, game_tokens, thresholds, start_thresholds in entries:
            parsed = parse_session_key(key)
            if parsed is None:
                logger.warning(f"预置订阅的会话标识无法解析，已跳过: {key}")
                continue
            if parsed.scope == "private":
                logger.warning(f"预置订阅跳过私聊会话（私聊订阅已禁用）: {key}")
                continue

            games, unknown = runtime.registry.resolve_many(game_tokens)
            if unknown:
                logger.warning(f"预置订阅 {key} 里有无法识别的游戏: {'、'.join(unknown)}")
            if not games:
                continue

            umo = str(parsed)
            await runtime.subscriptions.add_games(umo, [g.game_id for g in games])
            # 预置订阅用于「安静地盯到期」，所以默认关掉新活动提醒：
            # 每日推送已经覆盖「今天有哪些活动」，再加新活动提醒只会重复。
            changes: dict[str, object] = {"enabled": True, "notify_new": False}
            if thresholds is not None:
                changes["thresholds"] = list(thresholds)
            if start_thresholds is not None:
                changes["start_thresholds"] = list(start_thresholds)
            await runtime.subscriptions.update_session(umo, changes)
            await _prime_states(runtime, umo, games)
            names = "、".join(g.name for g in games)
            bits: list[str] = []
            if thresholds:
                bits.append(f"结束前 {'/'.join(str(t) for t in thresholds)} 天")
            if start_thresholds:
                bits.append(f"开始前 {'/'.join(str(t) for t in start_thresholds)} 天")
            scope = f"（{'，'.join(bits)}）" if bits else ""
            logger.info(f"预置订阅已应用: {key} → {names}{scope}")

        await runtime.subscriptions.save_preset_signature(signature)

    try:
        get_driver().on_startup(_job)
    except Exception:  # noqa: BLE001 - 没驱动时（测试）不阻塞注册
        logger.debug("无法挂载预置订阅任务：driver 未就绪")


def _register_shutdown(runtime) -> None:
    """插件停止时关闭 HTTP 连接池。"""
    driver = get_driver()

    @driver.on_shutdown
    async def _close() -> None:  # pragma: no cover - 只在进程退出时跑
        await runtime.close()
