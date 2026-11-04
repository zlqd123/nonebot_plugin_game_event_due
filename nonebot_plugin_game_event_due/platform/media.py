"""图片取字节。

QQ 官方 Bot 的群聊图片只有一条路能走通：``msg_type=7`` 富媒体，且**每条
消息只能带一张图**。markdown（``msg_type=2``）里写 ``![](url)`` 实测不渲染
——连外链也一样，只会把 alt 文字当成链接显示出来。

适配器已经封装好了上传：给它 ``MessageSegment.file_image(bytes)``，它会自己
``post_group_upload`` 换 ``file_info`` 再发 ``msg_type=7``。所以这里只需要
把外链图下载成字节。

下载失败一律返回空字节，让调用方降级成纯文字——推送因为配图挂了而整条丢
掉是不可接受的。
"""

from __future__ import annotations

from typing import Any

from nonebot import logger

#: 单张图上限，超过就不传了（免得推送卡住几十秒）
MAX_IMAGE_BYTES = 8 * 1024 * 1024

DEFAULT_TIMEOUT = 60.0


async def fetch_image(bot: Any, url: str) -> bytes:
    """下载一张外链图，返回字节；失败或过大返回 ``b""``。"""
    if not url:
        return b""

    try:
        from nonebot.drivers import Request

        response = await bot.adapter.request(
            Request(
                "GET",
                url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=DEFAULT_TIMEOUT,
            )
        )
    except Exception as exc:  # 下载失败不是致命错误
        logger.warning(f"下载封面失败 {url}: {exc}")
        return b""

    if response.status_code >= 400:
        logger.warning(f"下载封面失败 {url}: HTTP {response.status_code}")
        return b""

    content = response.content or b""
    if isinstance(content, str):
        content = content.encode("utf-8")
    if not content:
        return b""
    if len(content) > MAX_IMAGE_BYTES:
        logger.warning(f"封面过大（{len(content)} 字节），跳过: {url}")
        return b""
    return content


def guess_suffix(data: bytes) -> str:
    """按魔数猜扩展名，QQ 那边对文件名后缀比较敏感。"""
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".jpg"
