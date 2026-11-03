"""本地预览：按每天 12:00 单轮检查的规则，渲染若干天的推送样例。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import nonebot
from nonebot.adapters.onebot.v11 import Adapter

nonebot.init()
nonebot.get_driver().register_adapter(Adapter)

from nonebot_plugin_game_event_due.events.services.message_formatter import (  # noqa: E402
    MessageSettings,
    build_multi_game_reminder_parts,
    parts_to_text,
)
from nonebot_plugin_game_event_due.events.services.reminder_service import (  # noqa: E402
    ReminderPolicy,
    collect_plans,
)
from nonebot_plugin_game_event_due.events.sources.base import (  # noqa: E402
    GAME_TIMEZONE,
    JsonHttpClient,
)
from nonebot_plugin_game_event_due.events.sources.sra import SRA_GAMES, SraSource  # noqa: E402

WANTED = ("ak", "end")


async def main() -> None:
    source = SraSource(JsonHttpClient())
    data = {}
    for gid in WANTED:
        data[gid] = list(await source.fetch(gid))

    policy = ReminderPolicy(thresholds=(1,), start_thresholds=(1,), notify_new=False)
    settings = MessageSettings(show_deadline=True, single_message_per_game=True)
    games = {g.game_id: g for g in SRA_GAMES}

    base = datetime.now(GAME_TIMEZONE).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    lines: list[str] = []
    for offset in range(0, 7):
        now = base + timedelta(days=offset)
        entries = []
        for gid in WANTED:
            plans = collect_plans(data[gid], now, {}, policy)
            if plans:
                entries.append((games[gid], plans))
        lines.append("=" * 60)
        lines.append(f"### {now:%m-%d %H:%M}  命中 {sum(len(p) for _, p in entries)} 条")
        if not entries:
            lines.append("（不推送）")
            continue
        lines.append(parts_to_text(build_multi_game_reminder_parts(entries, now, settings)))

    with open("_preview_out.txt", "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


asyncio.run(main())
