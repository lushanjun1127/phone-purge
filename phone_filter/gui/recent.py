"""最近使用文件列表（跨会话持久化），用于"启动即可续用"。

基于 :class:`phone_filter.gui.settings.UserSettings` 存储，按标签维护多个
独立列表（如 ``db_source`` / ``db_category`` / ``us_source``）。自动去重、
置顶最新、过滤已删除的文件路径。
"""

import logging
import os

from phone_filter.gui.settings import UserSettings

logger = logging.getLogger(__name__)

MAX_ITEMS = 8


class RecentFiles:
    """一个命名空间下的"最近文件"队列（最新的在最前）。"""

    def __init__(self, settings: UserSettings, tag: str,
                 max_items: int = MAX_ITEMS):
        self._settings = settings
        self._key = f"recent.{tag}"
        self._max = max_items

    # ------------------------------------------------------------------
    def list(self) -> list[str]:
        raw = self._settings.get(self._key, []) or []
        # 过滤掉已经不存在的路径；保证返回副本
        return [p for p in raw if isinstance(p, str) and os.path.exists(p)]

    def add(self, path: str):
        if not path:
            return
        path = os.path.abspath(path)
        if not os.path.exists(path):
            return
        items = [path] + [p for p in self.list() if p != path]
        self._settings.set(self._key, items[: self._max])

    def clear(self):
        self._settings.set(self._key, [])


__all__ = ["RecentFiles", "MAX_ITEMS"]
