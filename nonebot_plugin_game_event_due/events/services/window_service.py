"""24 小时窗口快照：把活动按「已开始 / 即将开始 / 即将结束 / 已结束」分组。

和 :mod:`reminder_service` 的档位状态机不同，这里是**每天一轮的时间窗口
快照**：不写提醒状态、不做「一个活动一辈子只播一次」的去重，因为窗口本身
每天随检查点移动，同一活动不会在两天里重复落在同一组里。

检查点固定为「当天 12:00:01」，不是 12:00:00。上游 SRA 的时间戳都是整点，
差这一秒就能让归属毫无歧义：

* ``12:00:00`` **开始**的活动 → ``已开始``（不会从消息里凭空消失）
* ``12:00:00`` **结束**的活动 → ``已结束``

窗口（W = ``window_hours`` 小时，默认 24）：

===================  =======================================
分组                  判定
===================  =======================================
已开始                C-W <= start <  C
即将开始              C   <= start <= C+W
即将结束              C   <= end   <= C+W
已结束                C-W <= end   <  C
===================  =======================================

一个活动只归一组，按上表顺序取第一个命中的（防止同一活动同时落在两组里）。
区间端点按用户确认：严格 C、不加整点例外；未来侧闭区间、过去侧含下界不含 C。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from ..sources.base import Activity

#: 四组的固定顺序，也用作「一个活动只归一组」的优先级
WINDOW_GROUPS: tuple[str, ...] = (
    "已开始",
    "即将开始",
    "即将结束",
    "已结束",
)

DEFAULT_WINDOW_HOURS = 24
DEFAULT_CHECK_SECOND = 1


def normalize_check_time(
    moment: datetime,
    *,
    hour: int = 12,
    minute: int = 0,
    second: int = DEFAULT_CHECK_SECOND,
) -> datetime:
    """把任意时刻归一到「当天 hh:mm:ss」这个检查点。

    一天只跑一轮，但真正执行往往比 cron 时刻晚几百毫秒；若用真实执行时间
    做判定，12:00:00 的活动会因为亚秒抖动而在不同日期归属不同。这里统一
    钉死到配置时刻，边界就完全可复现。
    """
    return datetime.combine(
        moment.date(),
        time(hour=hour, minute=minute, second=second),
        tzinfo=moment.tzinfo,
    )


@dataclass(frozen=True, slots=True)
class WindowItem:
    """一条落在窗口里的活动，以及它属于哪一组、该显示哪个时刻。"""

    activity: Activity
    group: str
    #: 分组内排序用的时刻：开始组用 start，结束组用 end
    moment: datetime
    #: 是否把完整时段（开始~结束）写进行尾
    show_range: bool


@dataclass(frozen=True, slots=True)
class WindowSnapshot:
    """一次检查的结果。"""

    check_at: datetime
    window: timedelta
    groups: dict[str, tuple[WindowItem, ...]]

    @property
    def total(self) -> int:
        return sum(len(items) for items in self.groups.values())

    def is_empty(self) -> bool:
        return self.total == 0


def classify_window(
    activities: Iterable[Activity],
    now: datetime,
    *,
    window_hours: int = DEFAULT_WINDOW_HOURS,
) -> WindowSnapshot:
    """把活动切进四组，返回快照。

    ``now`` 应当已经是 :func:`normalize_check_time` 归一过的检查点；调用方
    负责钉死边界，本函数不做二次归约，避免把「归一」这件事藏起来。
    """
    width = timedelta(hours=max(1, window_hours))
    past = now - width
    future = now + width

    buckets: dict[str, list[WindowItem]] = {key: [] for key in WINDOW_GROUPS}

    for activity in activities:
        start = activity.start_time
        end = activity.end_time

        hits: list[str] = []
        if start is not None and past <= start < now:
            hits.append("已开始")
        if start is not None and now <= start <= future:
            hits.append("即将开始")
        if end is not None and now <= end <= future:
            hits.append("即将结束")
        if end is not None and past <= end < now:
            hits.append("已结束")
        if not hits:
            continue

        group = next(key for key in WINDOW_GROUPS if key in hits)
        start_group = group in ("已开始", "即将开始")
        moment = (start if start_group else end) or now
        buckets[group].append(
            WindowItem(
                activity=activity,
                group=group,
                moment=moment,
                show_range=start_group and end is not None,
            )
        )

    for items in buckets.values():
        items.sort(key=lambda item: (item.moment, item.activity.name))
    return WindowSnapshot(
        check_at=now,
        window=width,
        groups={key: tuple(items) for key, items in buckets.items()},
    )


__all__ = [
    "DEFAULT_CHECK_SECOND",
    "DEFAULT_WINDOW_HOURS",
    "WINDOW_GROUPS",
    "WindowItem",
    "WindowSnapshot",
    "classify_window",
    "normalize_check_time",
]
