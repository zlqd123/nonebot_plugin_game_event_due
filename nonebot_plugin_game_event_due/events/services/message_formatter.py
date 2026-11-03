"""活动推送消息的构建。

纯字符串与数据结构处理，不依赖任何适配器，便于单独测试与复用。

输出形态刻意保持极简：**只给活动标题**（可选附带结束时间），因为一个游戏
的全部活动通常只占几百字，一条消息就能发完，不需要分片、不需要图片。
``MessagePart`` 仍保留「片段」抽象，是为了让发送层能统一处理多段文本。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..sources.base import Activity, Game, format_datetime, format_remaining
from .reminder_service import (
    TIER_END,
    TIER_START,
    ReminderPlan,
    reason_label,
    tier_kind,
)

MAX_ACTIVE_IN_OVERVIEW = 15
MAX_UPCOMING_IN_OVERVIEW = 8
MAX_DESCRIPTION_LENGTH = 180

PART_TEXT = "text"
PART_IMAGE = "image"

# 一条消息里同时推送多个活动时，插在相邻活动之间的视觉分隔线
DEFAULT_ACTIVITY_SEPARATOR = "━━━━━━━━━━━━"

# ---- 剩余时间视觉标记 ----

# 配置里可以直接写 emoji，也可以写颜色名，这里做一层映射
URGENCY_MARKER_COLORS = {
    "红": "🟥",
    "red": "🟥",
    "橙": "🟧",
    "orange": "🟧",
    "黄": "🟨",
    "yellow": "🟨",
    "绿": "🟩",
    "green": "🟩",
    "蓝": "🟦",
    "blue": "🟦",
    "紫": "🟪",
    "purple": "🟪",
    "黑": "⬛",
    "black": "⬛",
    "白": "⬜",
    "white": "⬜",
}

DEFAULT_URGENCY_RULES = "1:🟥,3:🟧,7:🟩,*:🟦"


@dataclass(frozen=True, slots=True)
class UrgencyLevel:
    """一个剩余时间档位对应的视觉标记。

    ``max_days`` 是该档位的上界（天）；``0`` 表示兜底档，命中所有更宽松的剩余时间。
    """

    marker: str
    max_days: float = 0.0

    @property
    def is_fallback(self) -> bool:
        return self.max_days <= 0


DEFAULT_URGENCY_LEVELS: tuple[UrgencyLevel, ...] = (
    UrgencyLevel("🟥", 1),
    UrgencyLevel("🟧", 3),
    UrgencyLevel("🟩", 7),
    UrgencyLevel("🟦", 0),
)


def parse_urgency_rules(value: object) -> tuple[UrgencyLevel, ...]:
    """把 ``"1:🟥,3:🟧,7:🟩,*:🟦"`` 解析成按紧急度升序的档位表。

    标记可以写成 emoji，也可以写颜色名（``1:红``）；天数写成 ``*`` 或 ``0``
    表示兜底档。无法识别的项会被跳过；整体解析不出内容时回退到
    ``DEFAULT_URGENCY_LEVELS``。
    """
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return DEFAULT_URGENCY_LEVELS
        items: list[object] = [
            item for item in text.replace("，", ",").split(",") if item.strip()
        ]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        return DEFAULT_URGENCY_LEVELS

    levels: list[UrgencyLevel] = []
    for item in items:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            days_raw, marker_raw = item[0], item[1]
        elif isinstance(item, str):
            normalized = item.replace("：", ":")
            if ":" not in normalized:
                continue
            days_raw, marker_raw = normalized.split(":", 1)
        else:
            continue

        marker_text = str(marker_raw).strip()
        marker = (
            URGENCY_MARKER_COLORS.get(marker_text)
            or URGENCY_MARKER_COLORS.get(marker_text.casefold())
            or marker_text
        )
        if not marker:
            continue

        days_text = str(days_raw).strip()
        if days_text in ("*", "0", "其他", "其它", "else", "default"):
            days = 0.0
        else:
            try:
                days = float(days_text)
            except (TypeError, ValueError):
                continue
        levels.append(UrgencyLevel(marker, max(days, 0.0)))

    if not levels:
        return DEFAULT_URGENCY_LEVELS
    levels.sort(key=lambda level: (level.is_fallback, level.max_days))
    return tuple(levels)


def urgency_level_for(
    remaining: timedelta | None,
    rules: Sequence[UrgencyLevel],
) -> UrgencyLevel | None:
    """返回剩余时间命中的最紧急档位；已结束或无法计算时返回 ``None``。"""
    if remaining is None or remaining <= timedelta(0):
        return None
    for level in rules:
        if level.is_fallback:
            continue
        if remaining <= timedelta(days=level.max_days):
            return level
    for level in rules:
        if level.is_fallback:
            return level
    return None


def activity_urgency_level(
    activity: Activity,
    now: datetime,
    rules: Sequence[UrgencyLevel],
) -> UrgencyLevel | None:
    """活动当前的剩余时间档位。"""
    return urgency_level_for(activity.remaining(now), rules)


@dataclass(frozen=True, slots=True)
class MessagePart:
    """消息的一个片段：文本或图片。"""

    kind: str
    value: str

    @property
    def is_image(self) -> bool:
        return self.kind == PART_IMAGE


@dataclass(frozen=True, slots=True)
class MessageSettings:
    """推送消息的内容开关。

    默认值就是「只列标题」：描述、标签、额外时间段、封面一律不出现在输出里。
    ``show_deadline`` 是可选项，只给每日推送与临期提醒用。
    """

    #: 是否附带活动描述（默认关，只留标题）
    include_description: bool = False
    #: 是否附带活动标签
    include_tags: bool = False
    #: 是否附带除开止时间以外的其它时间段
    include_extra_times: bool = False
    #: 是否在标题后附带截止日期；日常查询关，每日推送开
    show_deadline: bool = False
    #: 一个游戏的多条提醒合并成一条消息
    single_message_per_game: bool = True
    activity_separator: str = DEFAULT_ACTIVITY_SEPARATOR
    # 剩余时间标记（🟥 / 🟧 / 🟩 / 🟦）
    urgency_marker: bool = True
    urgency_rules: tuple[UrgencyLevel, ...] = DEFAULT_URGENCY_LEVELS
    # 把剩余时间写进活动标题，消息顶部就能看到
    headline_remaining: bool = False
    # 消息标题里附上各档位数量，例如「🟥1 🟧2」
    tier_summary: bool = False
    # 把最紧急的活动排在前面
    sort_by_urgency: bool = True
    # 游戏显示名覆盖：(代号/原名/内置别名 -> 想显示的名字)
    game_aliases: tuple[tuple[str, str], ...] = ()


def normalize_game_aliases(value: object) -> tuple[tuple[str, str], ...]:
    """解析「代号=显示名」的游戏别名，例如 sr=星铁 / arknights:舟游。

    左边可以写游戏代号、完整名称或内置别名，右边是想显示的名字。
    换行、逗号、分号都能分隔；解析不出来的项直接跳过。
    """
    if isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
    else:
        text = str(value or "")
        normalized = (
            text.replace("\r", "\n")
            .replace("，", ",")
            .replace("；", ",")
            .replace(";", ",")
        )
        items = []
        for line in normalized.split("\n"):
            items.extend(line.split(","))

    pairs: list[tuple[str, str]] = []
    for item in items:
        raw = str(item).strip()
        if not raw:
            continue
        key = ""
        name = ""
        for separator in ("=", "：", ":", "＝"):
            if separator in raw:
                key, _, name = raw.partition(separator)
                break
        key = key.strip().casefold()
        name = name.strip()
        if not key or not name:
            continue
        if all(key != existing for existing, _ in pairs):
            pairs.append((key, name))
    return tuple(pairs)


def display_game_name(game: Game, settings: MessageSettings) -> str:
    """游戏在消息里的显示名：配置了别名就用别名，否则用原名。

    匹配不区分大小写，代号、完整名、内置别名（崩铁 / 星铁 / 舟 等）都能命中。
    """
    if not settings.game_aliases:
        return game.name
    candidates = {game.game_id.casefold(), game.name.casefold()}
    candidates.update(str(alias).casefold() for alias in game.aliases)
    for key, alias in settings.game_aliases:
        if key in candidates:
            return alias
    return game.name


def truncate(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)]}…"


def format_moment(moment: datetime, now: datetime) -> str:
    """格式化时间；跨年时补上年份，避免出现「开始 07-17 / 结束 03-10」的歧义。"""
    return format_datetime(moment, with_year=moment.year != now.year)


def _remaining_suffix(activity: Activity, now: datetime) -> str:
    remaining = activity.remaining(now)
    if remaining is None or remaining <= timedelta(0):
        return ""
    return f"（剩余 {format_remaining(remaining)}）"


def build_activity_block(
    activity: Activity,
    reasons: Sequence[str],
    now: datetime,
    settings: MessageSettings,
    *,
    include_remaining: bool = True,
) -> list[MessagePart]:
    """构建单个活动的消息片段。

    默认只输出活动标题这一行，尽量短。以下内容都是可选的：

    * ``settings.urgency_marker``：紧急度色块（提醒推送用）
    * ``include_remaining`` + ``settings.show_deadline``：截止日期
    * ``reasons``：触发提醒的原因，如「剩3天」
    """
    parts_line = ""
    if settings.urgency_marker:
        level = activity_urgency_level(activity, now, settings.urgency_rules)
        if level is not None:
            parts_line = f"{level.marker} "

    labels = " · ".join(reason_label(reason) for reason in reasons)
    prefix = f"【{labels}】" if labels else ""
    headline = f"· {parts_line}{prefix}{activity.name}"

    # 「开始前」类提醒显示开始时间，否则会让人误以为活动快结束了
    start_reminder = any(tier_kind(reason) == TIER_START for reason in reasons)

    if start_reminder and settings.show_deadline and activity.start_time is not None:
        countdown = activity.start_time - now
        if countdown > timedelta(0):
            headline += (
                f"　{format_moment(activity.start_time, now)} 开始"
                f"（还有 {format_remaining(countdown)}）"
            )
        else:
            headline += f"　{format_moment(activity.start_time, now)} 开始"
    # 截止时间是可选项：日常查询只给标题，推送时才带上日期
    elif include_remaining and settings.show_deadline and activity.end_time is not None:
        remaining = activity.remaining(now)
        if remaining is not None and remaining > timedelta(0):
            headline += (
                f"　至 {format_moment(activity.end_time, now)}"
                f"（剩 {format_remaining(remaining)}）"
            )
        else:
            headline += f"　至 {format_moment(activity.end_time, now)}"

    lines = [headline]
    if activity.url:
        lines.append(f"  详情：{activity.url}")

    return [MessagePart(PART_TEXT, "\n".join(lines))]


def _assemble_list(
    header: str,
    lines: Sequence[str],
    *,
    footer: str = "",
) -> list[MessagePart]:
    """把头部与若干行拼成一条纯文本消息。

    与 :func:`_assemble` 的区别是这里不做分隔线，也不处理封面，专供
    「只发标题」的紧凑输出使用。
    """
    body = [header]
    body.extend(line for line in lines if line)
    if footer:
        body.append(footer)
    return [MessagePart(PART_TEXT, "\n".join(body))]


def _assemble(
    header: str,
    segments: Sequence[str | Sequence[MessagePart]],
    separator: str = "",
) -> list[MessagePart]:
    """把头部、文本行与活动块按顺序合并为一条消息。

    ``segments`` 里每个元素要么是一行独立文本（分段标题、来源说明），要么是一个
    活动块。所有片段之间统一用一个空行分隔。

    ``separator`` 非空时会插在**相邻活动块之间**。紧跟分段标题的那个活动块不加分隔线，
    否则标题会和它自己的第一批活动被割开。

    相邻的文本片段会合并成一段，避免产生大量零碎的消息组件。
    """
    parts: list[MessagePart] = []
    buffer = header
    blocks_seen = 0
    after_heading = False

    for segment in segments:
        if isinstance(segment, str):
            buffer += f"\n\n{segment}"
            after_heading = True
            continue

        prefix = "\n\n"
        if blocks_seen and separator and not after_heading:
            prefix = f"\n\n{separator}\n\n"
        blocks_seen += 1
        after_heading = False

        for part in segment:
            if part.is_image:
                if buffer:
                    parts.append(MessagePart(PART_TEXT, buffer))
                    buffer = ""
                parts.append(part)
            else:
                buffer += f"{prefix}{part.value}"
                prefix = "\n\n"

    if buffer:
        parts.append(MessagePart(PART_TEXT, buffer))
    return parts


def parts_to_text(parts: Sequence[MessagePart]) -> str:
    """把片段渲染为纯文本，图片片段渲染为封面链接。

    文本片段本身可能以空行开头（图片之后的续接文本），此时不再额外补分隔符，
    否则会出现多出一个空行。
    """
    text = ""
    for part in parts:
        chunk = f"封面：{part.value}" if part.is_image else part.value
        if not chunk:
            continue
        if text and not chunk.startswith("\n"):
            text += "\n"
        text += chunk
    return text


def sort_plans_by_urgency(
    plans: Sequence[ReminderPlan],
    now: datetime,
) -> list[ReminderPlan]:
    """按剩余时间升序排列，让最紧急的活动排在最前面。"""

    def sort_key(plan: ReminderPlan) -> tuple[int, float]:
        # 只按剩余时间排序：稳定排序保证同一档位内维持原有顺序
        remaining = plan.activity.remaining(now)
        if remaining is None:
            return (1, 0.0)
        return (0, remaining.total_seconds())

    return sorted(plans, key=sort_key)


def summarize_markers(
    activities: Sequence[Activity],
    now: datetime,
    settings: MessageSettings,
) -> str:
    """统计各剩余时间档位的活动数量，例如 ``🟥1 🟧2``。"""
    if not (settings.urgency_marker and settings.tier_summary):
        return ""

    counts: dict[str, int] = {}
    order: list[str] = []
    for activity in activities:
        level = activity_urgency_level(activity, now, settings.urgency_rules)
        if level is None:
            continue
        if level.marker not in counts:
            counts[level.marker] = 0
            order.append(level.marker)
        counts[level.marker] += 1

    if not order:
        return ""
    return " ".join(f"{marker}{counts[marker]}" for marker in order)


def build_reminder_parts(
    game: Game,
    plans: Sequence[ReminderPlan],
    now: datetime,
    settings: MessageSettings,
) -> list[MessagePart]:
    """把一个游戏本轮的全部提醒合并为一条消息。

    开启 ``sort_by_urgency`` 时最紧急的活动排在最前，标题里附上各档位数量，
    这样一眼就能看出这波提醒里有没有火烧眉毛的。
    """
    ordered = (
        sort_plans_by_urgency(plans, now)
        if settings.sort_by_urgency
        else list(plans)
    )
    blocks = [
        build_activity_block(plan.activity, plan.reasons, now, settings)
        for plan in ordered
    ]
    header = f"🎮 {display_game_name(game, settings)} · 活动提醒"
    summary = summarize_markers([plan.activity for plan in ordered], now, settings)
    if summary:
        header = f"{header}  {summary}"
    return _assemble(header, blocks, settings.activity_separator)


def _window_line(
    activity: Activity,
    moment: datetime,
    now: datetime,
    *,
    end: datetime | None = None,
) -> str:
    """一行「标题 + 时间」，例如 ``· 「归野」叙事活动　11-05 12:00~11-26 05:59``。

    不写「开始 / 结束」——上面的分组标题已经说明了方向，每行都以时间收尾。
    传了 ``end`` 就把整段时间写全：只有开始时刻的话，读者还得自己去找它开多久。
    """
    stamp = format_moment(moment, now)
    if end is not None:
        stamp = f"{stamp}~{format_moment(end, now)}"
    return f"· {activity.name}　{stamp}"


def build_window_parts(
    game: Game,
    plans: "Sequence[ReminderPlan]",
    now: datetime,
    settings: MessageSettings,
) -> list[MessagePart]:
    """一个游戏在 24 小时窗口内的提醒，按「即将结束 / 即将开始」分组。

    只输出标题和时间——这是提醒唯一的信息量，前缀里的
    「剩余不足24小时」在分组标题里已经说过一次了。
    """
    ending: list[ReminderPlan] = []
    starting: list[ReminderPlan] = []
    for plan in plans:
        if plan.tier.startswith(TIER_END) and plan.activity.end_time is not None:
            ending.append(plan)
        if plan.start_tier.startswith(TIER_START) and plan.activity.start_time is not None:
            starting.append(plan)

    if settings.sort_by_urgency:
        ending.sort(key=lambda p: p.activity.end_time or now)
        starting.sort(key=lambda p: p.activity.start_time or now)
    else:
        ending.reverse()
        starting.reverse()

    lines: list[str] = []
    if ending:
        lines.append(f"▎即将结束（{len(ending)}）")
        lines.extend(
            _window_line(p.activity, p.activity.end_time, now) for p in ending
        )
    if starting:
        lines.append(f"▎即将开始（{len(starting)}）")
        lines.extend(
            _window_line(
                p.activity,
                p.activity.start_time,
                now,
                end=p.activity.end_time,
            )
            for p in starting
        )

    if not lines:
        return []

    return _assemble_list(f"🎮 {display_game_name(game, settings)}", lines)


def build_multi_game_reminder_parts(
    entries: "Sequence[tuple[Game, Sequence[ReminderPlan]]]",
    now: datetime,
    settings: MessageSettings,
    *,
    window_hours: int = 24,
) -> list[MessagePart]:
    """把一个会话本轮所有游戏的提醒合并成一条消息。

    多个游戏时用统一的顶层标题，每个游戏一个分组小节；只有一条消息，
    不会再出现「同一轮连发好几条」。
    """
    blocks = [
        build_window_parts(game, list(plans), now, settings)
        for game, plans in entries
        if plans
    ]
    blocks = [block for block in blocks if block]

    if not blocks:
        return []
    if len(blocks) == 1:
        return blocks[0]

    parts: list[MessagePart] = [MessagePart(PART_TEXT, f"⏰ {now:%m-%d}")]
    for block in blocks:
        parts.append(MessagePart(PART_TEXT, ""))
        parts.extend(block)
    return parts


def build_single_reminder_parts(
    game: Game,
    plan: ReminderPlan,
    now: datetime,
    settings: MessageSettings,
) -> list[MessagePart]:
    """只包含一个活动的提醒消息。"""
    return _assemble(
        f"🎮 {display_game_name(game, settings)} · 活动提醒",
        [build_activity_block(plan.activity, plan.reasons, now, settings)],
        settings.activity_separator,
    )


def build_overview_parts(
    game: Game,
    activities: Sequence[Activity],
    now: datetime,
    settings: MessageSettings,
    *,
    source_name: str = "",
    note: str = "",
) -> list[MessagePart]:
    """构建「当前活动一览」，用于手动查询、订阅确认与每日推送。

    只输出活动标题，进行中与即将开始各成一段，超出上限时补一行省略说明。
    """
    active = sorted(
        (item for item in activities if item.is_active(now)),
        key=lambda item: item.end_time or item.sort_key()[0],
    )
    upcoming = sorted(
        (item for item in activities if item.is_upcoming(now)),
        key=lambda item: item.start_time or item.sort_key()[0],
    )

    header = f"🎮 {display_game_name(game, settings)}"

    lines: list[str] = []
    if not active and not upcoming:
        lines.append("当前没有进行中或即将开始的活动。")
    else:
        if active:
            lines.append(f"▎进行中（{len(active)}）")
            for item in active[:MAX_ACTIVE_IN_OVERVIEW]:
                lines.extend(_title_lines(item, now, settings))
            if len(active) > MAX_ACTIVE_IN_OVERVIEW:
                lines.append(
                    f"　……另有 {len(active) - MAX_ACTIVE_IN_OVERVIEW} 个进行中"
                )

        if upcoming:
            lines.append(f"▎即将开始（{len(upcoming)}）")
            for item in upcoming[:MAX_UPCOMING_IN_OVERVIEW]:
                lines.extend(
                    _title_lines(item, now, settings, include_remaining=False)
                )
            if len(upcoming) > MAX_UPCOMING_IN_OVERVIEW:
                lines.append(
                    f"　……另有 {len(upcoming) - MAX_UPCOMING_IN_OVERVIEW} 个即将开始"
                )

    footer_parts = []
    if note:
        footer_parts.append(f"（{note}）")
    if source_name:
        footer_parts.append(f"数据来源：{source_name}")
    footer = "　".join(footer_parts)

    return _assemble_list(header, lines, footer=footer)


def _title_lines(
    activity: Activity,
    now: datetime,
    settings: MessageSettings,
    *,
    include_remaining: bool = True,
) -> list[str]:
    """单个活动的标题行（可能带截止日期）。"""
    line = f"· {activity.name}"
    if not (include_remaining and settings.show_deadline):
        return [line]
    if activity.end_time is None:
        return [line]
    remaining = activity.remaining(now)
    if remaining is not None and remaining > timedelta(0):
        line += (
            f"　至 {format_moment(activity.end_time, now)}"
            f"（剩 {format_remaining(remaining)}）"
        )
    else:
        line += f"　至 {format_moment(activity.end_time, now)}"
    return [line]


# ---- 纯文本便捷封装（WebUI 预览、测试与降级输出使用） ----


def build_reminder_message(
    game: Game,
    plans: Sequence[ReminderPlan],
    now: datetime,
    settings: MessageSettings,
) -> str:
    return parts_to_text(build_reminder_parts(game, plans, now, settings))


def build_single_reminder_message(
    game: Game,
    plan: ReminderPlan,
    now: datetime,
    settings: MessageSettings,
) -> str:
    return parts_to_text(build_single_reminder_parts(game, plan, now, settings))


def build_overview_message(
    game: Game,
    activities: Sequence[Activity],
    now: datetime,
    settings: MessageSettings,
    *,
    source_name: str = "",
) -> str:
    return parts_to_text(
        build_overview_parts(game, activities, now, settings, source_name=source_name)
    )
