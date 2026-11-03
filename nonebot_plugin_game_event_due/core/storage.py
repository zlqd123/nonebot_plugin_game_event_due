"""键值存储。

订阅、提醒状态这类数据需要跨重启保留。优先使用
`nonebot-plugin-localstore`（统一管理各插件的数据目录），没装时退回到插件
自己创建的数据目录，保证单独测试或最小部署也能跑。

对外只暴露两个异步函数：

- ``get(key, default)``
- ``put(key, value)``
- ``delete(key)``

值统一按 JSON 存盘，所以只能放可序列化的结构。
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from nonebot import logger

#: 兜底目录名，仅在 localstore 不可用时使用
_FALLBACK_DIR_NAME = "nonebot_plugin_game_wiki"


def _fallback_dir() -> Path:
    """没有 localstore 时的数据目录。

    优先尊重 ``LOCALSTORE_DATA_DIR`` 环境变量，其次是当前工作目录下的
    ``data/``，与 NoneBot 生态的惯例保持一致。
    """
    base = os.environ.get("LOCALSTORE_DATA_DIR") or os.environ.get("NONEBOT_DATA_DIR")
    root = Path(base) if base else Path.cwd() / "data"
    return root / _FALLBACK_DIR_NAME


def _resolve_dir() -> Path:
    """拿到数据目录：localstore 优先。"""
    try:
        from nonebot_plugin_localstore import get_plugin_data_dir

        path = get_plugin_data_dir()
        if path is not None:
            return Path(path)
    except Exception:  # noqa: BLE001 - 未安装或未初始化都退回兜底目录
        pass
    return _fallback_dir()


class JsonStore:
    """一个按 key 分文件的 JSON 存储。

    每个 key 一个文件，避免并发写整张表；写入先落临时文件再替换，防止进程
    中途被杀导致文件损坏。
    """

    def __init__(self, directory: Path | str | None = None) -> None:
        self.directory = Path(directory) if directory else _resolve_dir()
        self._lock = asyncio.Lock()

    # ---- 路径 ----

    def _path(self, key: str) -> Path:
        safe = "".join(
            ch if (ch.isalnum() or ch in "._-") else "_" for ch in str(key)
        ).strip("._")
        return self.directory / f"{safe or 'kv'}.json"

    # ---- 读写 ----

    async def get(self, key: str, default: Any = None) -> Any:
        path = self._path(key)
        if not path.exists():
            return default
        try:
            raw = await asyncio.to_thread(path.read_text, encoding="utf-8")
            return json.loads(raw)
        except (OSError, ValueError) as exc:
            logger.warning(f"读取存储失败 {path.name}: {exc}")
            return default

    async def put(self, key: str, value: Any) -> None:
        path = self._path(key)
        tmp = path.with_suffix(path.suffix + ".tmp")
        payload = json.dumps(value, ensure_ascii=False, indent=2)
        async with self._lock:
            try:
                await asyncio.to_thread(self.directory.mkdir, parents=True, exist_ok=True)
                await asyncio.to_thread(tmp.write_text, payload, encoding="utf-8")
                await asyncio.to_thread(os.replace, tmp, path)
            except OSError as exc:
                logger.warning(f"写入存储失败 {path.name}: {exc}")

    async def delete(self, key: str) -> bool:
        path = self._path(key)
        if not path.exists():
            return False
        try:
            await asyncio.to_thread(path.unlink)
            return True
        except OSError as exc:
            logger.warning(f"删除存储失败 {path.name}: {exc}")
            return False


#: 默认实例。业务模块直接 ``from ..core.storage import store`` 使用。
store = JsonStore()


def get_store() -> JsonStore:
    return store
