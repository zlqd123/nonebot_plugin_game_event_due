"""用真实 SRA 数据预览消息形态。

跑法（在仓库根目录）：``python scripts/preview_rich.py``

会打印三种形态：
1. 纯文本（降级用）；
2. OneBot v11（CQ 码内联，一条消息）；
3. QQ 官方（按游戏拆条，每条一张图）。
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # 控制台不支持就算了
    pass

import nonebot

nonebot.init()

from nonebot_plugin_game_event_due.events.services.message_formatter import (  # noqa: E402
    MessageSettings,
    build_overview_parts,
    build_window_parts,
    parts_to_text,
)
from nonebot_plugin_game_event_due.events.sources.base import (  # noqa: E402
    GAME_TIMEZONE,
    JsonHttpClient,
    filter_relevant,
)
from nonebot_plugin_game_event_due.events.sources.sra import SraSource  # noqa: E402
from nonebot_plugin_game_event_due.platform import (  # noqa: E402
    parts_to_onebot,
    parts_to_plain,
    parts_to_text_only,
    split_game_blocks,
)

GAMES = ("end", "ak")


async def main() -> None:
    client = JsonHttpClient(timeout=30.0, cache_dir=ROOT / ".cache")
    source = SraSource(client)
    now = datetime.now(GAME_TIMEZONE)
    settings = MessageSettings(show_deadline=True)

    for game_id in GAMES:
        raw = await source.fetch_with_policy(game_id, max_age_seconds=3600)
        activities = filter_relevant(raw, now, lookahead_days=45, keep_past_days=3)
        game = next(item for item in source.games() if item.game_id == game_id)
        cover = source.game_cover(game_id)

        print("=" * 60)
        print(f"{game.name}  活动 {len(activities)} 项  封面 {'有' if cover else '无'}")
        print("=" * 60)

        parts = build_overview_parts(
            game, activities, now, settings, source_name=source.display_name, cover=cover
        )

        print("\n--- 纯文本 ---")
        print(parts_to_text(parts))

        print("\n--- OneBot v11（一条消息）---")
        print(parts_to_onebot(parts))

        print("\n--- QQ 官方（逐条，每条一张图）---")
        for index, block in enumerate(split_game_blocks(parts), 1):
            image = next((part.value for part in block if part.is_image), "")
            print(f"[消息 {index}] 文字:")
            print(parts_to_text_only(block))
            print(f"[消息 {index}] 图片: {image or '（无）'}")
            print()

        # 24 小时窗口形态（不带图时也应该逐字节一致）
        empty = build_window_parts(game, [], now, settings)
        assert empty == [], "空窗口不该产出片段"

    await client.close()


asyncio.run(main())
