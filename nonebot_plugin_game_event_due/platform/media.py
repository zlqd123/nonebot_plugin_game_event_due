"""QQ 官方 Bot 的图片素材上传。

QQ 官方 Bot 的 markdown 消息不支持直接引用外链图片，必须先把图上传到
素材库，拿到 ``qqbot-media://...`` 这样的地址才能内联。上传是三步：

1. ``POST /v2/media-images/prepare`` 申请上传，拿 ``upload_id`` 与预签名地址；
2. ``PUT {presigned_url}`` 把字节传上去，响应头 ``ETag`` 就是 content md5；
3. ``POST /v2/media-images/complete`` 确认，拿到最终可用的图片地址。

同一张图（md5 相同）只上传一次，结果落在 ``material_cache.json``。缓存按
内容哈希索引，图片换了 URL 但内容没变也不会重复上传。

上传失败一律返回空串，让调用方降级成纯文字——推送因为配图挂了而整条丢
掉是不可接受的。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from nonebot import logger

#: 缓存只在本次进程内复用；写盘是为了重启后不必重新上传
_CACHE_FILE_NAME = "material_cache.json"

#: 单张图上限，超过就不传了（免得推送卡住几十秒）
MAX_IMAGE_BYTES = 8 * 1024 * 1024


async def _http(
    bot: Any,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    content: bytes | None = None,
    timeout: float = 60.0,
) -> tuple[int, bytes, dict[str, str]]:
    """借 driver 的 HTTP 客户端发请求，返回 ``(状态码, 响应体, 响应头)``。"""
    from nonebot.drivers import Request

    request = Request(
        method,
        url,
        headers=headers or {},
        json=json_body,
        content=content,
        timeout=timeout,
    )
    response = await bot.adapter.request(request)
    raw = response.content or b""
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    return response.status_code, raw, dict(response.headers)


class MediaCache:
    """``md5 -> 图片地址`` 的持久化缓存。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._data: dict[str, dict[str, Any]] | None = None

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._data is not None:
            return self._data
        self._data = {}
        if self.path is not None and self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._data = {
                        str(key): value
                        for key, value in loaded.items()
                        if isinstance(value, dict)
                    }
            except (OSError, ValueError) as exc:
                logger.warning(f"读取图片缓存失败，忽略: {exc}")
        return self._data

    def get(self, md5: str) -> str:
        entry = self._load().get(md5) or {}
        return str(entry.get("url") or "")

    async def upload_image(self, bot: Any, image_url: str) -> str:
        """下载外链图并换成 QQ 素材库地址，结果按内容 md5 缓存。

        这是投递层实际调用的入口；它只是把缓存实例绑好的
        :func:`upload_image` 包一层。
        """
        return await upload_image(bot, image_url, self)

    def put(self, md5: str, url: str, image_id: str = "") -> None:
        data = self._load()
        data[md5] = {"url": url, "image_id": image_id}
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            tmp.replace(self.path)
        except OSError as exc:
            logger.warning(f"写入图片缓存失败: {exc}")


async def upload_image(bot: Any, image_url: str, cache: MediaCache) -> str:
    """把外链图上传到 QQ 素材库，返回可直接内联的地址。

    任何一步失败都返回空串，调用方据此退化为纯文字。
    """
    if not image_url:
        return ""

    try:
        status, data, headers = await _http(
            bot, "GET", image_url, headers={"User-Agent": "Mozilla/5.0"}
        )
    except Exception as exc:  # 下载失败不是致命错误
        logger.warning(f"下载封面失败 {image_url}: {exc}")
        return ""

    if status >= 400 or not data:
        logger.warning(f"下载封面失败 {image_url}: HTTP {status}")
        return ""
    if len(data) > MAX_IMAGE_BYTES:
        logger.warning(f"封面过大（{len(data)} 字节），跳过: {image_url}")
        return ""

    md5 = hashlib.md5(data).hexdigest()
    cached = cache.get(md5)
    if cached:
        return cached

    content_type = headers.get("Content-Type") or headers.get("content-type") or ""
    if not content_type.startswith("image/"):
        content_type = "image/jpeg"

    auth = await _auth_headers(bot)
    api = str(bot.adapter.get_api_base()).rstrip("/")
    name = image_url.rsplit("/", 1)[-1].split("?")[0] or "cover.jpg"

    try:
        status, raw, _ = await _http(
            bot,
            "POST",
            f"{api}/v2/media-images/prepare",
            headers=auth,
            json_body={
                "file_name": name,
                "file_size": len(data),
                "content_type": content_type,
                "md5": md5,
                "sha1": hashlib.sha1(data).hexdigest(),
            },
        )
        if status >= 400:
            logger.warning(f"申请图片上传失败 {status}: {raw[:200]!r}")
            return ""
        prepared = json.loads(raw or b"{}")
        presigned = prepared.get("presigned_url")
        upload_id = prepared.get("upload_id")
        if not presigned or not upload_id:
            logger.warning(f"申请图片上传返回异常: {raw[:200]!r}")
            return ""

        # 预签名地址自带鉴权，不能再带 Bot 的 Authorization
        status, raw, put_headers = await _http(
            bot,
            "PUT",
            presigned,
            headers={"Content-Type": content_type},
            content=data,
            timeout=120.0,
        )
        if status >= 400:
            logger.warning(f"上传图片失败 {status}: {raw[:200]!r}")
            return ""
        etag = (
            put_headers.get("ETag")
            or put_headers.get("etag")
            or md5
        ).strip('"')

        status, raw, _ = await _http(
            bot,
            "POST",
            f"{api}/v2/media-images/complete",
            headers=auth,
            json_body={"upload_id": upload_id, "md5": etag},
        )
        if status >= 400:
            logger.warning(f"确认图片上传失败 {status}: {raw[:200]!r}")
            return ""
        done = json.loads(raw or b"{}")
        url = str(done.get("url") or "")
        if not url:
            logger.warning(f"确认图片上传未返回地址: {raw[:200]!r}")
            return ""
        cache.put(md5, url, str(done.get("image_id") or ""))
        return url
    except Exception as exc:  # 配图失败不该打断推送
        logger.opt(exception=exc).warning(f"上传封面失败 {image_url}: {exc}")
        return ""


async def _auth_headers(bot: Any) -> dict[str, str]:
    """取 QQ Bot 的鉴权头；拿不到就让后续请求自然失败。"""
    try:
        return await bot.get_authorization_header()
    except Exception:  # 老版本适配器可能没有这个方法
        token = await bot.get_access_token()
        return {"Authorization": f"QQBot {token}"}
