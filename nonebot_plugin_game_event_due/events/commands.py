"""活动日历指令。

所有长文本都走 ``delivery``，指令层不直接拼消息。

「缓存优先」的体现在 :func:`_resolve_force`：只有显式写 ``刷新`` 才会打到
上游，其余情况一律读本地缓存，避免用户随手一查就给公共 API 添压力。

**当前全部指令已停用**：``register`` 不再注册任何 matcher，指令实现整块
保留为注释，模块级常量与纯函数辅助器仍在。恢复时取消对应注释即可。
"""

# 指令全部注释后，下面这些导入暂时没人使用；恢复指令时需要它们
# ruff: noqa: F401

from __future__ import annotations

from datetime import datetime
from typing import Any
from collections.abc import Sequence

from nonebot import logger, on_command  # noqa: F401 - on_command 供注释掉的指令恢复用
from nonebot.adapters import Bot, Event, Message
from nonebot.matcher import Matcher  # noqa: F401
from nonebot.params import CommandArg  # noqa: F401
from nonebot.permission import SUPERUSER  # noqa: F401

from ..config import Config
from ..platform import build_session_key
from .runtime import EventsRuntime, get_runtime
from .services.message_formatter import build_overview_parts, display_game_name
from .services.reminder_service import ReminderPolicy, normalize_thresholds
from .services.subscription_service import resolve_policy
from .sources.base import GAME_TIMEZONE

USAGE_SUBSCRIBE = (
    "订阅指令已停用，活动订阅由管理员在配置文件里设置：\n"
    "GAME_EVENT_DUE_PRESET_SUBSCRIPTIONS=会话key=游戏@结束档位~开始档位\n"
    "可用游戏请查看 /活动游戏"
)
USAGE_QUERY = (
    "用法: /活动查询 [游戏名...] [刷新]\n"
    "默认只读本地缓存；加「刷新」会强制向上游取一次最新数据。"
)
USAGE_THRESHOLD = (
    "用法: /活动阈值 <天数...>\n"
    "例如 /活动阈值 7 3 1 表示剩余不足 7 天 / 3 天 / 24 小时各提醒一次；\n"
    "/活动阈值 关闭 关闭临期提醒；/活动阈值 默认 恢复全局默认。"
)
USAGE_FILTER = (
    "用法:\n"
    "/活动过滤 黑名单 <关键词...>  排除命中关键词的活动\n"
    "/活动过滤 白名单 <关键词...>  只推送命中关键词的活动\n"
    "/活动过滤 清空                清空全部关键词"
)

BLACKLIST_TOKENS = {"黑名单", "blacklist", "black"}
WHITELIST_TOKENS = {"白名单", "whitelist", "white"}
RESET_TOKENS = {"默认", "default", "重置", "reset"}
CLEAR_TOKENS = {"清空", "clear", "none"}
ON_TOKENS = {"开启", "开", "on", "true", "1"}
OFF_TOKENS = {"关闭", "关", "off", "false", "0"}
REFRESH_TOKENS = {"刷新", "refresh", "更新", "force"}


def _umo(bot: Bot, event: Event) -> str:
    """会话 key 的字符串形式，直接当订阅主键。"""
    key = build_session_key(bot, event)
    if key is not None:
        return str(key)
    # 兜底：极少数事件拿不到目标 ID，用发送者 ID 凑一个稳定的 key
    fallback = getattr(event, "user_id", None) or getattr(event, "user_openid", None)
    return f"unknown:private:{fallback or '0'}"


PRIVATE_DISABLED = (
    "活动订阅只支持群聊，私聊订阅已禁用。\n"
    "请在群里使用 /活动订阅；查询功能仍可用 /活动查询。"
)


def _is_private(bot: Bot, event: Event) -> bool:
    """判断当前是否私聊会话。

    只拦「订阅管理」类指令，纯查询（/活动查询、/活动游戏）在私聊里照常可用。
    """
    key = build_session_key(bot, event)
    return key is not None and key.scope == "private"


def _tokens(args: Message) -> list[str]:
    return args.extract_plain_text().strip().split()


def _resolve_force(tokens: Sequence[str]) -> tuple[list[str], bool]:
    """从参数里摘出「刷新」开关，返回 (剩余 token, 是否强制刷新)。"""
    rest = [token for token in tokens if token.lower() not in REFRESH_TOKENS]
    return rest, len(rest) != len(tokens)


def _format_policy(policy: ReminderPolicy) -> str:
    if policy.thresholds:
        threshold_text = "、".join(f"{days}天" for days in policy.thresholds)
    else:
        threshold_text = "已关闭"
    new_text = "开启" if policy.notify_new else "关闭"
    return f"临期提醒: {threshold_text}\n新活动提醒: {new_text}"


def _format_filters(session: dict[str, Any]) -> str:
    whitelist = session.get("whitelist") or []
    blacklist = session.get("blacklist") or []
    white_text = "、".join(whitelist) if whitelist else "（未设置）"
    black_text = "、".join(blacklist) if blacklist else "（未设置）"
    return f"白名单: {white_text}\n黑名单: {black_text}"


def _render_session_summary(
    runtime: EventsRuntime,
    session: dict[str, Any],
    *,
    title: str = "当前活动订阅",
) -> str:
    names = []
    for game_id in session.get("games") or []:
        game = runtime.registry.get(game_id)
        names.append(game.name if game else game_id)
    games_text = "、".join(names) if names else "（无）"

    policy = resolve_policy(session, runtime.policy)
    status = "开启" if session.get("enabled") else "关闭"
    return (
        f"📋 {title}\n"
        f"推送状态: {status}\n"
        f"订阅游戏: {games_text}\n"
        f"{_format_policy(policy)}\n"
        f"{_format_filters(session)}"
    )


def _session_games(
    runtime: EventsRuntime,
    session: dict[str, Any] | None,
    tokens: Sequence[str],
) -> tuple[list[Any], list[str]]:
    """解析目标游戏；未给出参数时使用当前会话已订阅的游戏。"""
    if tokens:
        return runtime.registry.resolve_many(tokens)

    games = []
    for game_id in (session or {}).get("games") or []:
        game = runtime.registry.get(game_id)
        if game is not None:
            games.append(game)
    return games, []


def register(config: Config) -> None:
    """注册活动日历的全部指令。"""
    # ---- 全部指令已停用 ----
    # 活动日历现在只做「定时推送」，不响应任何群内指令。
    # 原始实现完整保留在下方注释里：要恢复某条指令，解开对应的
    # on_command 与 handle 两段；要全部恢复就整块取消注释。
    logger.info("活动日历指令已全部停用，仅保留定时推送")


    # ---- 订阅指令已停用 ----
    # 订阅现在只能通过配置下发（``GAME_EVENT_DUE_PRESET_SUBSCRIPTIONS``），
    # 避免群里任何人改动能影响全群的推送范围。需要恢复时把下面两段
    # 注释解开即可，处理函数同样保留着。
    #
    # subscribe = on_command(
    #     "活动订阅", aliases={"game_sub", "活动关注"}, priority=20, block=True
    # )
    # unsubscribe = on_command(
    #     "活动退订", aliases={"game_unsub", "活动取消订阅"}, priority=20, block=True
    # )
    # list_subs = on_command(
        # "活动列表", aliases={"game_list", "活动订阅列表"}, priority=20, block=True
    # )
    # query = on_command("活动查询", aliases={"game_query"}, priority=20, block=True)
    # filter_cmd = on_command(
        # "活动过滤", aliases={"game_filter", "活动关键词"}, priority=20, block=True
    # )
    # toggle = on_command(
        # "活动开关", aliases={"game_toggle", "活动推送"}, priority=20, block=True
    # )
    # thresholds = on_command(
        # "活动阈值", aliases={"game_threshold", "活动提醒时间"}, priority=20, block=True
    # )
    # games_cmd = on_command(
        # "活动游戏", aliases={"game_games", "活动支持"}, priority=20, block=True
    # )
    # status = on_command(
        # "活动状态", aliases={"game_status"}, priority=20, block=True, permission=SUPERUSER
    # )
    # refresh = on_command(
        # "活动刷新", aliases={"game_refresh"}, priority=20, block=True, permission=SUPERUSER
    # )

    # ---- 订阅处理函数（已停用，保留以便恢复）----
    #
    # @subscribe.handle()
    # async def _subscribe(
    #     matcher: Matcher, bot: Bot, event: Event, args: Message = CommandArg()
    # ):
    #     if _is_private(bot, event):
    #         await matcher.finish(PRIVATE_DISABLED)
    #     runtime = get_runtime()
    #     tokens = _tokens(args)
    #     if not tokens:
    #         await matcher.finish(USAGE_SUBSCRIBE)
    #
    #     games, unknown = runtime.registry.resolve_many(tokens)
    #     if not games:
    #         await matcher.finish(
    #             f"没有识别到游戏: {'、'.join(unknown)}\n{USAGE_SUBSCRIBE}"
    #         )
    #
    #     umo = _umo(bot, event)
    #     await runtime.subscriptions.add_games(umo, [game.game_id for game in games])
    #     names = "、".join(game.name for game in games)
    #     message = f"✅ 已订阅: {names}"
    #     if unknown:
    #         message += f"\n⚠️ 未识别: {'、'.join(unknown)}"
    #
    #     # 先回确认，再推概览（概览可能较长，单独发）
    #     await matcher.send(message)
    #     await runtime.polling.send_subscription_overview(umo, games)
    #     await matcher.finish()
    #
    # @unsubscribe.handle()
    # async def _unsubscribe(
    #     matcher: Matcher, bot: Bot, event: Event, args: Message = CommandArg()
    # ):
    #     if _is_private(bot, event):
    #         await matcher.finish(PRIVATE_DISABLED)
    #     runtime = get_runtime()
    #     tokens = _tokens(args)
    #     umo = _umo(bot, event)
    #     session = await runtime.subscriptions.get_session(umo)
    #     if not session:
    #         await matcher.finish("当前会话没有订阅任何游戏")
    #
    #     # 清空是破坏性操作，必须显式写出「全部」，避免裸指令误删订阅
    #     if any(token in ("全部", "all") for token in tokens):
    #         await runtime.subscriptions.remove_session(umo)
    #         notify_state = await runtime.subscriptions.get_notify_state()
    #         runtime.subscriptions.drop_session_state(notify_state, umo)
    #         await runtime.subscriptions.save_notify_state(notify_state)
    #         await matcher.finish("✅ 已清空当前会话的全部活动订阅")
    #
    #     if not tokens:
    #         await matcher.finish(
    #             "用法: /活动退订 <游戏名...>\n"
    #             "如需清空全部订阅请使用 /活动退订 全部"
    #         )
    #
    #     games, unknown = runtime.registry.resolve_many(tokens)
    #     if not games:
    #         await matcher.finish(f"没有识别到游戏: {'、'.join(unknown)}")
    #
    #     await runtime.subscriptions.remove_games(umo, [game.game_id for game in games])
    #     names = "、".join(game.name for game in games)
    #     await matcher.finish(f"✅ 已退订: {names}")
    #

    # @list_subs.handle()
    # async def _list_subs(matcher: Matcher, bot: Bot, event: Event):
        # if _is_private(bot, event):
            # await matcher.finish(PRIVATE_DISABLED)
        # runtime = get_runtime()
        # session = await runtime.subscriptions.get_session(_umo(bot, event))
        # if not session:
            # await matcher.finish("当前会话没有配置订阅\n" + USAGE_SUBSCRIBE)
        # await matcher.finish(_render_session_summary(runtime, session))

    # ---- 查询 ----

    # @query.handle()
    # async def _query(
        # matcher: Matcher, bot: Bot, event: Event, args: Message = CommandArg()
    # ):
        # runtime = get_runtime()
        # umo = _umo(bot, event)
        # tokens, force = _resolve_force(_tokens(args))
        # session = await runtime.subscriptions.get_session(umo)
        # games, unknown = _session_games(runtime, session, tokens)

        # if not games:
            # if unknown:
                # await matcher.finish(
                    # f"没有识别到游戏: {'、'.join(unknown)}\n{USAGE_QUERY}"
                # )
            # await matcher.finish("当前会话没有订阅任何游戏\n" + USAGE_SUBSCRIBE)

        # now = datetime.now(GAME_TIMEZONE)
        # settings = runtime.message_settings
        # for game in games:
            # try:
                # activities = await runtime.polling.fetch_activities(
                    # game.game_id, force=force
                # )
            # except Exception as exc:  # noqa: BLE001 - 单个游戏失败不影响其它
                # await matcher.send(f"⚠️ 暂时无法获取 {game.name} 的活动数据: {exc}")
                # continue
            # parts = build_overview_parts(
                # game,
                # activities,
                # now,
                # settings,
                # source_name=runtime.registry.source_name(game.game_id),
                # note=runtime.polling.source_note(game.game_id),
            # )
            # await runtime.delivery.reply(
                # bot,
                # event,
                # parts,
                # title=f"🎮 {display_game_name(game, settings)} · 当前活动",
            # )
        # await matcher.finish()

    # ---- 设置 ----

    # @filter_cmd.handle()
    # async def _filter(
        # matcher: Matcher, bot: Bot, event: Event, args: Message = CommandArg()
    # ):
        # if _is_private(bot, event):
            # await matcher.finish(PRIVATE_DISABLED)
        # runtime = get_runtime()
        # umo = _umo(bot, event)
        # session = await runtime.subscriptions.get_session(umo)
        # if not session:
            # await matcher.finish("当前会话没有订阅任何游戏\n" + USAGE_SUBSCRIBE)

        # tokens = _tokens(args)
        # if not tokens:
            # await matcher.finish(_format_filters(session) + "\n\n" + USAGE_FILTER)

        # action = tokens[0]
        # if action in CLEAR_TOKENS:
            # session = (
                # await runtime.subscriptions.update_session(
                    # umo, {"whitelist": [], "blacklist": []}
                # )
                # or session
            # )
            # await matcher.finish("✅ 已清空关键词过滤\n" + _format_filters(session))

        # if action in BLACKLIST_TOKENS:
            # field = "blacklist"
        # elif action in WHITELIST_TOKENS:
            # field = "whitelist"
        # else:
            # await matcher.finish(USAGE_FILTER)

        # keywords = tokens[1:]
        # if not keywords:
            # await matcher.finish(
                # f"请提供关键词，例如: /活动过滤 {action} 双倍掉落\n\n{USAGE_FILTER}"
            # )

        # session = (
            # await runtime.subscriptions.update_session(umo, {field: keywords})
            # or session
        # )
        # label = "黑名单" if field == "blacklist" else "白名单"
        # await matcher.finish(
            # f"✅ 已更新{label}: {'、'.join(keywords)}\n" + _format_filters(session)
        # )

    # @toggle.handle()
    # async def _toggle(
        # matcher: Matcher, bot: Bot, event: Event, args: Message = CommandArg()
    # ):
        # if _is_private(bot, event):
            # await matcher.finish(PRIVATE_DISABLED)
        # runtime = get_runtime()
        # tokens = _tokens(args)
        # if not tokens:
            # await matcher.finish("用法: /活动开关 开启 或 /活动开关 关闭")

        # action = tokens[0].lower()
        # if action in ON_TOKENS:
            # enabled = True
        # elif action in OFF_TOKENS:
            # enabled = False
        # else:
            # await matcher.finish("用法: /活动开关 开启 或 /活动开关 关闭")

        # session = await runtime.subscriptions.update_session(
            # _umo(bot, event), {"enabled": enabled}
        # )
        # if session is None:
            # await matcher.finish("当前会话没有订阅任何游戏\n" + USAGE_SUBSCRIBE)
        # await matcher.finish(f"✅ 活动推送已{'开启' if enabled else '关闭'}")

    # @thresholds.handle()
    # async def _thresholds(
        # matcher: Matcher, bot: Bot, event: Event, args: Message = CommandArg()
    # ):
        # if _is_private(bot, event):
            # await matcher.finish(PRIVATE_DISABLED)
        # runtime = get_runtime()
        # umo = _umo(bot, event)
        # session = await runtime.subscriptions.get_session(umo)
        # if not session:
            # await matcher.finish("当前会话没有订阅任何游戏\n" + USAGE_SUBSCRIBE)

        # tokens = _tokens(args)
        # if not tokens:
            # policy = resolve_policy(session, runtime.policy)
            # await matcher.finish(_format_policy(policy) + "\n\n" + USAGE_THRESHOLD)

        # if tokens[0] in RESET_TOKENS:
            # session = (
                # await runtime.subscriptions.update_session(umo, {"thresholds": None})
                # or session
            # )
            # policy = resolve_policy(session, runtime.policy)
            # await matcher.finish("✅ 已恢复全局默认档位\n" + _format_policy(policy))

        # if tokens[0] in CLEAR_TOKENS or tokens[0] in OFF_TOKENS:
            # session = (
                # await runtime.subscriptions.update_session(umo, {"thresholds": []})
                # or session
            # )
            # policy = resolve_policy(session, runtime.policy)
            # await matcher.finish("✅ 已关闭临期提醒\n" + _format_policy(policy))

        # parsed = normalize_thresholds(tokens)
        # if not parsed:
            # await matcher.finish(USAGE_THRESHOLD)

        # session = (
            # await runtime.subscriptions.update_session(
                # umo, {"thresholds": list(parsed)}
            # )
            # or session
        # )
        # policy = resolve_policy(session, runtime.policy)
        # await matcher.finish("✅ 已更新提醒档位\n" + _format_policy(policy))

    # @games_cmd.handle()
    # async def _games(matcher: Matcher):
        # runtime = get_runtime()
        # games = runtime.registry.ordered()
        # if not games:
            # await matcher.finish("当前没有可用的游戏，请检查 sra_api_base 配置")

        # lines = [f"📋 支持的游戏（{len(games)}）", ""]
        # for game in games:
            # shown = display_game_name(game, runtime.message_settings)
            # aliases = "/".join(str(alias) for alias in game.aliases[:5])
            # suffix = f"，别名：{aliases}" if aliases else ""
            # lines.append(f"• {shown}（{game.game_id}{suffix}）")
        # lines.append("")
        # lines.append("用法: /活动订阅 <游戏名或代号>")
        # await matcher.finish("\n".join(lines).rstrip())

    # ---- 管理 ----

    # @status.handle()
    # async def _status(matcher: Matcher):
        # runtime = get_runtime()
        # report = runtime.polling.last_report
        # sessions = await runtime.subscriptions.get_all()
        # daily = runtime.daily_push
        # lines = [
            # "📊 活动日历状态",
            # f"数据源游戏: {len(runtime.registry.games)}",
            # f"订阅会话: {len(sessions)}",
            # f"取数间隔: {runtime.config.fetch_interval_hours} 小时",
            # f"每日推送: {'开启 ' + f'{daily.hour:02d}:{daily.minute:02d}' if daily.enabled else '关闭'}",
        # ]
        # if report.started_at:
            # finished = report.finished_at or datetime.now(GAME_TIMEZONE)
            # elapsed = (finished - report.started_at).total_seconds()
            # lines += [
                # "",
                # "最近一轮:",
                # f"  开始: {report.started_at:%Y-%m-%d %H:%M:%S}",
                # f"  耗时: {elapsed:.1f}s",
                # f"  拉取游戏: {report.fetched_games}",
                # f"  活动数: {report.activities}",
                # f"  推送消息: {report.messages}",
                # f"  提醒条数: {report.reminders}",
            # ]
            # if report.failures:
                # lines.append("  异常:")
                # lines += [
                    # f"    {gid}: {detail}" for gid, detail in report.failures.items()
                # ]
        # await matcher.finish("\n".join(lines))

    # @refresh.handle()
    # async def _refresh(matcher: Matcher):
        # runtime = get_runtime()
        # runtime.polling.invalidate_memo()
        # report = await runtime.polling.run_cycle(force=True)
        # await matcher.finish(
            # f"✅ 已刷新：游戏 {report.fetched_games} 个，活动 {report.activities} 条，"
            # f"推送 {report.reminders} 条"
            # + (f"\n⚠️ 异常: {report.failures}" if report.failures else "")
        # )
