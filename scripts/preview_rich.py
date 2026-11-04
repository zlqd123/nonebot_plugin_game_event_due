"""用真实 SRA 数据跑一遍 24 小时窗口，打印图文混排的实际输出。

只读网络与本地缓存，不发任何消息。用来肉眼确认「有图插图、无图文字」
在真实数据上的样子，以及 QQ markdown 渲染出来的排版。
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:  # Windows 控制台是 GBK，不换成 UTF-8 会直接崩在 emoji 上
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001 - 换不了就照原样输出
    pass

import nonebot  # noqa: E402

# 插件包在被 import 时就会读 driver.config，所以先初始化一个空壳 NoneBot
nonebot.init()

from nonebot_plugin_game_event_due.events.services.message_formatter import (  # noqa: E402
    MessageSettings,
    build_multi_game_reminder_parts,
    parts_to_text,
)
from nonebot_plugin_game_event_due.events.services.reminder_service import (  # noqa: E402
    ActivityNotifyState,
    ReminderPolicy,
    collect_plans,
    normalize_thresholds,
)
from nonebot_plugin_game_event_due.events.sources.base import (  # noqa: E402
    GAME_TIMEZONE,
    JsonHttpClient,
    filter_relevant,
)
from nonebot_plugin_game_event_due.events.sources.sra import SraSource  # noqa: E402
from nonebot_plugin_game_event_due.platform.push import (  # noqa: E402
    parts_to_markdown,
    parts_to_onebot,
)

GAMES = ["end", "ak"]
CACHE = Path(__file__).resolve().parent / "_probe_cache"


async def main() -> None:
    client = JsonHttpClient(timeout=30.0, cache_dir=CACHE)
    source = SraSource(client)
    now = datetime.now(GAME_TIMEZONE)
    policy = ReminderPolicy(
        thresholds=normalize_thresholds("1"),
        start_thresholds=normalize_thresholds("1"),
    )

    entries = []
    for game_id in GAMES:
        raw = await source.fetch_with_policy(game_id, max_age_seconds=3600)
        activities = filter_relevant(raw, now, lookahead_days=45, keep_past_days=3)
        plans = collect_plans(activities, now, {}, policy)
        cover = source.game_cover(game_id)
        print(f"[{game_id}] activities={len(activities)} plans={len(plans)} cover={cover or '(无)'}")
        if plans:
            game = next(g for g in source.games() if g.game_id == game_id)
            entries.append((game, plans))

    if not entries:
        print("今天没有命中")
        return

    covers = {game.game_id: source.game_cover(game.game_id) for game, _ in entries}
    settings = MessageSettings(show_deadline=True)
    parts = build_multi_game_reminder_parts(entries, now, settings, covers=covers)

    print("\n===== 纯文本（OneBot 降级 / 兜底）=====")
    print(parts_to_text(parts))
    print("\n===== OneBot 实际发送的内容 =====")
    print(parts_to_onebot(parts))
    print("\n===== QQ 官方 markdown =====")
    print(parts_to_markdown(parts, 640))

    await client.close()


asyncio.run(main())
