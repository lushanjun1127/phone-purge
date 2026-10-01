"""轻量用户设置（JSON 持久化），让程序"记住"用户的上次状态。

保存内容示例：窗口尺寸、深色模式、数据库版的上次文件路径/输出格式，
美国版的分类库与输出路径等。下次启动自动恢复，减少重复操作。

设置文件位置：
    Windows: %APPDATA%/<AppName>/settings.json
    macOS:   ~/Library/Application Support/<AppName>/settings.json
    Linux:   ~/.config/<AppName>/settings.json
若上述目录不可写，则退回当前工作目录 ``.phone_filter_settings.json``。
"""

import json
import logging
import os
import sys
from typing import Any

logger = logging.getLogger(__name__)

APP_NAME = "PhoneFilter"


def settings_dir() -> str:
    """跨平台用户配置目录（不存在则创建）。"""
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        path = os.path.join(base, APP_NAME)
    elif sys.platform == "darwin":
        path = os.path.join(os.path.expanduser("~"), "Library",
                            "Application Support", APP_NAME)
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        path = os.path.join(xdg or os.path.join(os.path.expanduser("~"), ".config"),
                            APP_NAME)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:  # pragma: no cover - 只读文件系统等极端情况
        path = os.getcwd()
    return path


class UserSettings:
    """点分键名的 JSON 设置存储：``s.get("window.width", 900)``。"""

    def __init__(self, filename: str = "settings.json"):
        self.path = os.path.join(settings_dir(), filename)
        self._data: dict[str, Any] = {}
        self.load()

    # ------------------------------------------------------------------
    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except FileNotFoundError:
            self._data = {}
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("读取设置失败（使用默认值）: %s", e)
            self._data = {}

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except OSError as e:  # pragma: no cover
            logger.warning("保存设置失败: %s", e)

    # ------------------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, key: str, value: Any, save: bool = True):
        parts = key.split(".")
        node = self._data
        for part in parts[:-1]:
            nxt = node.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                node[part] = nxt
            node = nxt
        node[parts[-1]] = value
        if save:
            self.save()

    def update(self, mapping: dict[str, Any], save: bool = True):
        """批量写入并一次落盘（用于退出时保存窗口几何等）。"""
        for k, v in mapping.items():
            self.set(k, v, save=False)
        if save:
            self.save()


__all__ = ["UserSettings", "settings_dir"]
