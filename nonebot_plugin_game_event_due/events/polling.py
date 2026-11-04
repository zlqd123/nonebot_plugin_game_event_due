"""活动日历的取数与推送调度。

职责边界：

* **取数**：:meth:`ActivityPollingService.fetch_activities` 默认读本地缓存，
  只有缓存超过 ``fetch_interval_hours`` 或显式 ``force`` 才请求上游；
* **临期提醒**：:meth:`run_cycle` 找出进入提醒档位的活动并推送；
* **每日推送**：:meth:`send_daily_push` 把指定游戏的全部活动推一遍。

按时跑由 ``events/__init__.py`` 用 apscheduler 驱动，本模块不自己起循环。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from collections.abc import Sequence

from nonebot import logger

from ..platform import parse_session_key
from .delivery import Delivery
from .services.message_formatter import (
    MessagePart,
    MessageSettings,
    build_multi_game_reminder_parts,
    build_overview_parts,
    build_reminder_parts,
    build_single_reminder_parts,
)
from .services.reminder_service import (
    ActivityNotifyState,
    ReminderPlan,
    ReminderPolicy,
    apply_plan,
    collect_plans,
    matches_filters,
)
from .services.subscription_service import (
    SubscriptionService,
    resolve_message_settings,
    resolve_policy,
)
from .sources.base import Activity, GAME_TIMEZONE, Game, filter_relevant
from .sources.registry import GameRegistry


def in_quiet_hours(moment: datetime, spec: str) -> bool:
    """判断某个时刻是否落在静默时段内。

    ``spec`` 形如 ``23-8``（23:00 到次日 08:00）或 ``0-6``；留空或写错
    都视为「没有静默时段」，宁可多推一条也不要把提醒永久吞掉。
    """
    raw = str(spec or "").strip()
    if not raw:
        return False
    parts = raw.replace("~", "-").replace("—", "-").split("-")
    if len(parts) != 2:
        return False
    try:
        start, end = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    if not (0 <= start <= 23 and 0 <= end <= 24):
        return False
    hour = moment.hour
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    # 跨零点，例如 23-8
    return hour >= start or hour < end


@dataclass(frozen=True, slots=True)
class PollingSettings:
    """轮询与推送的运行时设置。"""

    #: 插件自身向上游请求的最小间隔（小时）
    fetch_interval_hours: int = 8
    lookahead_days: int = 45
    keep_past_days: int = 3
    max_new_per_cycle: int = 8
    notify_on_subscribe: bool = True
    retention_days: int = 30
    #: 静默时段，如 ``23-8``；留空表示全天可推送
    quiet_hours: str = ""

    @property
    def fetch_interval_seconds(self) -> float:
        return max(1, self.fetch_interval_hours) * 3600.0


@dataclass(slots=True)
class CycleReport:
    """一轮检查的结果，用于日志与 ``/活动状态``。"""

    started_at: datetime | None = None
    finished_at: datetime | None = None
    sessions: int = 0
    fetched_games: int = 0
    activities: int = 0
    messages: int = 0
    reminders: int = 0
    failures: dict[str, str] = field(default_factory=dict)
    pruned: int = 0

    @property
    def ok(self) -> bool:
        return not self.failures


class ActivityPollingService:
    """把数据源、订阅与投递串起来。"""

    def __init__(
        self,
        registry: GameRegistry,
        subscriptions: SubscriptionService,
        delivery: Delivery,
        settings: PollingSettings,
        default_policy: ReminderPolicy,
        message_settings: MessageSettings,
    ) -> None:
        self.registry = registry
        self.subscriptions = subscriptions
        self.delivery = delivery
        self.settings = settings
        self.default_policy = default_policy
        self.message_settings = message_settings

        self._memo: dict[str, list[Activity]] = {}
        self._cycle_lock = asyncio.Lock()
        self.last_report = CycleReport()

    # ---- 数据获取 ----

    async def fetch_activities(
        self,
        game_id: str,
        *,
        force: bool = False,
    ) -> list[Activity]:
        """获取某个游戏当前相关的活动。

        ``force=False``（机器人指令的默认路径）时优先用本地缓存：只要缓存
        没超过 ``fetch_interval_hours`` 就直接返回，**一个请求都不发**。
        ``force=True``（定时任务 / ``/活动刷新``）才真的打到上游。
        """
        now = datetime.now(GAME_TIMEZONE)
        source = self.registry.source_for(game_id)
        if source is None:
            raise LookupError(f"未注册的游戏: {game_id}")

        raw = await self._fetch_raw(source, game_id, force=force)

        return filter_relevant(
            raw,
            now,
            lookahead_days=self.settings.lookahead_days,
            keep_past_days=self.settings.keep_past_days,
        )

    async def _fetch_raw(self, source, game_id: str, *, force: bool) -> list[Activity]:
        """按缓存新鲜度取原始活动列表。"""
        max_age = None if force else self.settings.fetch_interval_seconds
        fetch_with_policy = getattr(source, "fetch_with_policy", None)
        if fetch_with_policy is not None:
            raw = list(await fetch_with_policy(game_id, max_age_seconds=max_age))
        else:
            raw = list(await source.fetch(game_id))
        self._memo[game_id] = raw
        return raw

    def invalidate_memo(self, game_id: str | None = None) -> None:
        """丢弃内存缓存。磁盘缓存不受影响。"""
        if game_id is None:
            self._memo.clear()
        else:
            self._memo.pop(game_id, None)

    def source_note(self, game_id: str) -> str:
        """数据来源说明（本地缓存 / 网络）。"""
        notes = getattr(self.registry.source_for(game_id), "notes", None) or {}
        return str(notes.get(game_id) or "")

    # ---- 提醒决策 ----

    def _limit_new_plans(
        self,
        plans: list[ReminderPlan],
        limit: int,
    ) -> list[ReminderPlan]:
        """限制每轮「仅因为是新活动」而推送的条数，避免刷屏。

        带有倒计时提醒的活动不受此限制，因为它们具有时效性。
        """
        if limit <= 0:
            return plans
        urgent = [plan for plan in plans if plan.tier]
        new_only = [plan for plan in plans if plan.is_new and not plan.tier]
        if len(new_only) <= limit:
            return plans
        kept = urgent + new_only[:limit]
        kept.sort(key=lambda plan: plan.activity.sort_key())
        return kept

    def plan_for_session(
        self,
        activities: Sequence[Activity],
        now: datetime,
        states: dict[str, dict[str, Any]],
        policy: ReminderPolicy,
        *,
        whitelist: Sequence[str] = (),
        blacklist: Sequence[str] = (),
    ) -> list[ReminderPlan]:
        candidates = [
            activity
            for activity in activities
            if matches_filters(activity, whitelist=whitelist, blacklist=blacklist)
        ]
        plans = collect_plans(candidates, now, states, policy)
        return self._limit_new_plans(plans, self.settings.max_new_per_cycle)

    # ---- 临期提醒 ----

    async def run_cycle(self, *, force: bool = False) -> CycleReport:
        """检查一遍并在必要时推送临期提醒。同一时刻只会有一轮在跑。"""
        async with self._cycle_lock:
            return await self._run_cycle_locked(force=force)

    async def _run_cycle_locked(self, *, force: bool = False) -> CycleReport:
        report = CycleReport(started_at=datetime.now(GAME_TIMEZONE))
        now = report.started_at
        assert now is not None

        sessions = await self.subscriptions.get_all()
        active_sessions = {
            umo: session
            for umo, session in sessions.items()
            if session.get("enabled") and session.get("games")
        }
        if not active_sessions:
            report.finished_at = datetime.now(GAME_TIMEZONE)
            self.last_report = report
            return report

        known_games = self.registry.games
        needed = sorted(
            {
                game_id
                for session in active_sessions.values()
                for game_id in session["games"]
            }
            & set(known_games)
        )

        fetched: dict[str, list[Activity]] = {}
        for game_id in needed:
            try:
                fetched[game_id] = await self.fetch_activities(game_id, force=force)
                report.fetched_games += 1
                report.activities += len(fetched[game_id])
            except Exception as exc:  # noqa: BLE001 - 单个游戏失败不影响其它
                detail = f"{type(exc).__name__}: {exc}"
                report.failures[game_id] = detail
                logger.warning(f"获取 {known_games[game_id].name} 活动数据失败: {detail}")

        report.sessions = len(active_sessions)
        if not fetched:
            report.finished_at = datetime.now(GAME_TIMEZONE)
            self.last_report = report
            return report

        notify_state = await self.subscriptions.get_notify_state()
        dirty = False

        for umo, session in active_sessions.items():
            policy = resolve_policy(session, self.default_policy)
            if not policy.enabled:
                continue
            settings = resolve_message_settings(session, self.message_settings)
            bucket = notify_state.setdefault(umo, {})

            # 先把该会话本轮所有游戏的命中收集起来，合成一条消息再发
            pending: list[tuple[Game, list[ReminderPlan]]] = []
            for game_id in session["games"]:
                activities = fetched.get(game_id)
                game = known_games.get(game_id)
                if activities is None or game is None:
                    continue

                plans = self.plan_for_session(
                    activities,
                    now,
                    bucket,
                    policy,
                    whitelist=session.get("whitelist") or (),
                    blacklist=session.get("blacklist") or (),
                )
                if plans:
                    pending.append((game, plans))

            if not pending:
                continue

            if in_quiet_hours(now, self.settings.quiet_hours):
                deferred = sum(len(plans) for _, plans in pending)
                logger.info(
                    f"静默时段跳过推送 {umo}: {deferred} 条提醒推迟到时段结束后"
                )
                continue

            delivered = await self.deliver_session_plans(
                umo, pending, now, settings
            )
            if delivered:
                report.messages += 1
                report.reminders += len(delivered)
            for plan in delivered:
                state = ActivityNotifyState.from_dict(bucket.get(plan.activity.uid))
                apply_plan(state, plan)
                self.subscriptions.record_plan(
                    notify_state,
                    umo,
                    plan.activity,
                    state,
                )
                dirty = True

        report.pruned = self.subscriptions.prune_notify_state(
            notify_state,
            now,
            self.settings.retention_days,
        )
        if dirty or report.pruned:
            await self.subscriptions.save_notify_state(notify_state)

        report.finished_at = datetime.now(GAME_TIMEZONE)
        self.last_report = report
        if report.reminders:
            logger.info(
                f"活动提醒已推送: 会话 {report.sessions} 个, "
                f"提醒 {report.reminders} 条"
            )
        if report.failures:
            logger.warning(f"本轮数据源异常: {report.failures}")
        return report

    # ---- 投递 ----

    async def deliver_session_plans(
        self,
        umo: str,
        pending: list[tuple[Game, list[ReminderPlan]]],
        now: datetime,
        settings: MessageSettings | None = None,
    ) -> list[ReminderPlan]:
        """把一个会话本轮的全部提醒作为**一条**消息发出。

        合并模式下一个会话一轮只发一条，多个游戏用 ``🎮 游戏名`` 分节；
        关掉合并时退化为逐条发送，语义与 :meth:`deliver_plans` 一致。
        """
        if not pending:
            return []

        session = parse_session_key(umo)
        if session is None:
            logger.warning(f"无法解析会话标识，跳过推送: {umo}")
            return []

        effective = settings or self.message_settings

        if not effective.single_message_per_game:
            delivered: list[ReminderPlan] = []
            for game, plans in pending:
                delivered.extend(
                    await self.deliver_plans(umo, game, plans, now, effective)
                )
            return delivered

        parts = build_multi_game_reminder_parts(
            pending, now, effective, covers=self._covers_for(pending)
        )
        ok = await self.delivery.push(session, parts)
        if not ok:
            return []
        return [plan for _, plans in pending for plan in plans]

    def _covers_for(
        self, pending: Sequence[tuple[Game, list[ReminderPlan]]]
    ) -> dict[str, str]:
        """本轮消息要用的配图：``game_id -> 版本主视觉``。

        只有数据源能给出主视觉时才带图；给不出就返回空串，格式化层会走
        纯文字。这样「有图插图，无图文字」不需要额外开关。
        """
        covers: dict[str, str] = {}
        for game, _ in pending:
            source = self.registry.source_for(game.game_id)
            cover_of = getattr(source, "game_cover", None)
            if not callable(cover_of):
                continue
            cover = str(cover_of(game.game_id) or "").strip()
            if cover:
                covers[game.game_id] = cover
        return covers

    async def deliver_plans(
        self,
        umo: str,
        game: Game,
        plans: list[ReminderPlan],
        now: datetime,
        settings: MessageSettings | None = None,
    ) -> list[ReminderPlan]:
        """推送一个游戏本轮的提醒，返回实际送达的提醒。

        合并发送时要么全部成功要么全部失败；逐条发送时只把成功的那些返回，
        失败的会在下一轮重试且不会与已送达的重复。
        """
        if not plans:
            return []

        session = parse_session_key(umo)
        if session is None:
            logger.warning(f"无法解析会话标识，跳过推送: {umo}")
            return []

        effective = settings or self.message_settings
        if effective.single_message_per_game:
            parts = build_reminder_parts(game, plans, now, effective)
            ok = await self.delivery.push(session, parts)
            return list(plans) if ok else []

        delivered: list[ReminderPlan] = []
        for plan in plans:
            parts = build_single_reminder_parts(game, plan, now, effective)
            if await self.delivery.push(session, parts):
                delivered.append(plan)
        return delivered

    # ---- 每日推送 ----

    async def send_daily_push(
        self,
        *,
        game_tokens: Sequence[Game] = (),
        session_keys: Sequence[str] = (),
        include_deadline: bool = True,
        force: bool = False,
        only_group: bool = True,
    ) -> CycleReport:
        """把每个会话订阅的全部活动合并成**一条**消息推送。

        规则：

        * 每个会话每天最多推一次——用 KV 记录日期，重跑或重启都不会重复发；
        * 多个游戏、多个活动合并进同一条消息，避免刷屏；
        * ``game_tokens`` 为空时用各会话自己订阅的游戏；
        * ``session_keys`` 为空时推给所有已订阅会话；
        * ``only_group=True`` 时跳过私聊会话（私聊订阅已禁用）。
        """
        report = CycleReport(started_at=datetime.now(GAME_TIMEZONE))
        now = report.started_at
        assert now is not None
        today = now.strftime("%Y-%m-%d")

        sessions = await self.subscriptions.get_all()
        targets = {
            umo: session
            for umo, session in sessions.items()
            if session.get("enabled") and session.get("games")
        }
        if session_keys:
            wanted = {str(key).strip() for key in session_keys if str(key).strip()}
            targets = {umo: s for umo, s in targets.items() if umo in wanted}

        if only_group:
            group_targets: dict[str, dict[str, Any]] = {}
            for umo, session in targets.items():
                parsed = parse_session_key(umo)
                if parsed is not None and parsed.scope != "private":
                    group_targets[umo] = session
            targets = group_targets

        if not targets:
            report.finished_at = datetime.now(GAME_TIMEZONE)
            return report

        pushed_dates = await self.subscriptions.get_daily_state()
        known_games = self.registry.games
        configured = [game.game_id for game in game_tokens if game.game_id in known_games]

        fetched: dict[str, list[Activity]] = {}
        for umo, session in targets.items():
            # 今天已经推过就跳过，保证「每群每天最多一次」
            if pushed_dates.get(umo) == today:
                logger.debug(f"每日推送跳过 {umo}: 今天已推送")
                continue

            game_ids = configured or [
                game_id for game_id in session["games"] if game_id in known_games
            ]
            if not game_ids:
                continue

            settings = resolve_message_settings(session, self.message_settings)
            if not include_deadline:
                settings = _without_deadline(settings)

            session_key = parse_session_key(umo)
            if session_key is None:
                continue

            report.sessions += 1
            blocks: list[list[MessagePart]] = []
            for game_id in game_ids:
                if game_id not in fetched:
                    try:
                        fetched[game_id] = await self.fetch_activities(
                            game_id, force=force
                        )
                        report.fetched_games += 1
                        report.activities += len(fetched[game_id])
                    except Exception as exc:  # noqa: BLE001
                        detail = f"{type(exc).__name__}: {exc}"
                        report.failures[game_id] = detail
                        logger.warning(
                            f"每日推送取数失败 {known_games[game_id].name}: {detail}"
                        )
                        continue

                game = known_games[game_id]
                blocks.append(
                    build_overview_parts(
                        game,
                        fetched[game_id],
                        now,
                        settings,
                        source_name=self.registry.source_name(game_id),
                    )
                )

            if not blocks:
                continue

            # 多游戏合并成一条：只用一个开头的日期标题，后面接各游戏的段落
            merged: list[MessagePart] = [
                MessagePart(kind="text", value=f"📅 今日活动 · {now:%m-%d}")
            ]
            for block in blocks:
                merged.append(MessagePart(kind="text", value=""))
                merged.extend(block)

            if await self.delivery.push(session_key, merged):
                report.messages += 1
                pushed_dates[umo] = today
                await self.subscriptions.mark_daily_pushed(umo, today)

        report.finished_at = datetime.now(GAME_TIMEZONE)
        if report.messages:
            logger.info(
                f"每日活动推送完成: {report.messages} 个会话，{report.sessions} 个目标"
            )
        return report

    # ---- 订阅后的初始概览 ----

    async def send_subscription_overview(
        self,
        umo: str,
        games: Sequence[Game],
        *,
        force: bool = False,
    ) -> int:
        """订阅成功后推送当前活动概览，并初始化提醒状态。"""
        session_key = parse_session_key(umo)
        if session_key is None:
            return 0

        now = datetime.now(GAME_TIMEZONE)
        session = await self.subscriptions.get_session(umo) or {}
        policy = resolve_policy(session, self.default_policy)
        settings = resolve_message_settings(session, self.message_settings)
        notify_state = await self.subscriptions.get_notify_state()
        bucket = notify_state.setdefault(umo, {})

        sent = 0
        dirty = False
        for game in games:
            try:
                activities = await self.fetch_activities(game.game_id, force=force)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"订阅 {game.name} 时获取活动失败: {exc}")
                await self.delivery.push_text(
                    session_key,
                    f"⚠️ 暂时无法获取 {game.name} 的活动数据，稍后会自动重试",
                )
                continue

            initialized = SubscriptionService.build_initial_states(
                activities, now, policy
            )
            if not self.settings.notify_on_subscribe:
                bucket.update(initialized)
                dirty = True
                continue

            parts = build_overview_parts(
                game,
                activities,
                now,
                settings,
                source_name=self.registry.source_name(game.game_id),
            )
            if await self.delivery.push(session_key, parts):
                sent += 1
                bucket.update(initialized)
                dirty = True

        if dirty:
            await self.subscriptions.save_notify_state(notify_state)
        return sent

    # ---- 生命周期 ----

    async def stop(self) -> None:
        """占位：轮询由 apscheduler 驱动，这里只清内存缓存。"""
        self._memo.clear()


def _without_deadline(settings: MessageSettings) -> MessageSettings:
    """每日推送时可选地隐藏结束时间。"""
    from dataclasses import replace

    return replace(settings, include_extra_times=False, headline_remaining=False)


__all__ = ["ActivityPollingService", "CycleReport", "PollingSettings"]
