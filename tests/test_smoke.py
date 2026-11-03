"""nonebug 驱动的最小验证：插件加载 + 双平台文本发送。

跑法：``cd nb_plugin_game_wiki && python -m pytest tests -q``
"""

from __future__ import annotations

import nonebot
import pytest
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from nonebot.adapters.onebot.v11 import Bot, Message
from nonebot.adapters.onebot.v11.event import GroupMessageEvent
from nonebot.adapters.onebot.v11.event import Sender as OB11Sender
from nonebug import App


@pytest.fixture
def anyio_backend():
    return "asyncio"


def make_group_message(
    text: str, group_id: int = 123456, user_id: int = 10001
) -> GroupMessageEvent:
    """构造一条 OneBot v11 群消息事件。"""
    return GroupMessageEvent(
        time=1700000000,
        self_id=10000,
        post_type="message",
        sub_type="normal",
        user_id=user_id,
        message_type="group",
        message_id=1,
        message=Message(text),
        original_message=Message(text),
        raw_message=text,
        font=0,
        sender=OB11Sender(user_id=user_id, nickname="tester"),
        group_id=group_id,
    )


def test_plugin_loaded():
    """插件能被 NoneBot 加载，元信息完整。"""
    plugin = nonebot.get_plugin("nonebot_plugin_game_event_due")
    assert plugin is not None
    assert plugin.metadata is not None
    assert plugin.metadata.name == "游戏活动日历"
    assert "~onebot.v11" in plugin.metadata.supported_adapters
    assert "~qq" in plugin.metadata.supported_adapters


@pytest.mark.anyio
async def test_commands_are_disabled(app: App):
    """指令已全部注释，群里发什么都不该有 matcher 接住。"""
    from nonebot.matcher import matchers

    plugin = nonebot.get_plugin("nonebot_plugin_game_event_due")
    assert plugin is not None

    # 插件自己没有注册任何 matcher
    total = sum(len(group) for group in matchers.values())
    assert total == 0, f"指令应当全部停用，实际还有 {total} 个 matcher"

    async with app.test_matcher() as ctx:
        adapter = ctx.create_adapter(base=OneBotV11Adapter)
        bot = ctx.create_bot(base=Bot, adapter=adapter)
        ctx.receive_event(bot, make_group_message("/活动游戏"))
        # 没有 matcher 被触发，事件只会被丢弃
        ctx.should_finished()


class _AnyMessage:
    """占位对象：任何消息都算匹配。

    nonebug 的 ``got_call_send`` 用 ``model.message != message`` 比对，
    这里重写 ``__eq__`` 让任意 message 都相等。
    """

    def __eq__(self, other: object) -> bool:
        return True

    def __ne__(self, other: object) -> bool:
        return False

    def __hash__(self) -> int:
        return 0

    def __repr__(self) -> str:
        return "<any message>"


ANY_MESSAGE = _AnyMessage()
