"""QFluentWidgets（Win11 Fluent 风格）全局主题配置。

集中管理设计令牌与少量 QSS 补充样式，避免各窗口散落硬编码颜色。
导入本模块会自动完成：

1. 设置全局字体（Microsoft YaHei UI / PingFang SC / Noto Sans CJK）；
2. 应用主题色 ``#0067C9``（Windows 11 蓝）；
3. 根据系统注册表自动跟随浅色/深色模式（非 Windows 默认浅色）。

使用前提：先创建 QApplication（或先导入 ``phone_filter.gui.qt_shim``），
再导入本模块，qfluentwidgets 才能绑定到正确的 Qt 发行版。
"""

import sys

from phone_filter.gui.qt_shim import QApplication, QFont, StyleHint

# 必须在 qfluentwidgets 之前导入 qt_shim，确保其跟随项目所用的 Qt 绑定
from qfluentwidgets import (
    FluentStyleSheet, Theme, isDarkTheme, setTheme, setThemeColor,
)

# ---------------------------------------------------------------------------
# 设计令牌（唯一颜色来源，业务代码请从这里取值）
# ---------------------------------------------------------------------------
ACCENT = "#0067C9"          # Windows 11 主强调色
SUCCESS = "#0F7B0F"         # 成功绿（开始处理）
WARNING = "#C34F1E"         # 警示橙（规范化）
ERROR = "#C42B1C"           # 错误红
UI_FONT_SIZE = 14
MONO_FONT_SIZE = 12


def apply_theme(app: QApplication | None = None) -> QApplication:
    """应用全局 Fluent 主题：字体 + 主题色 + 浅/深色模式。幂等。"""
    app = app or QApplication.instance() or QApplication(sys.argv)

    font = QFont()
    font.setFamilies(["Microsoft YaHei UI", "PingFang SC", "Noto Sans CJK SC",
                      "Segoe UI", "sans-serif"])
    font.setPointSize(UI_FONT_SIZE)
    app.setFont(font)

    setTheme(_system_theme())
    setThemeColor(ACCENT)
    return app


def _system_theme() -> Theme:
    """读取系统偏好：Windows 下跟随注册表，其它平台默认浅色。"""
    if sys.platform == "win32":
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            winreg.CloseKey(key)
            return Theme.LIGHT if value else Theme.DARK
        except OSError:  # pragma: no cover - 注册表不可读时退回浅色
            return Theme.LIGHT
    return Theme.LIGHT


def mono_font(point_size: int = MONO_FONT_SIZE) -> QFont:
    """日志/号码列表使用的等宽字体（按平台给出候选族列表，Qt 自动回退）。"""
    if sys.platform == "win32":
        families = ["Cascadia Mono", "Consolas", "Courier New"]
    else:
        families = ["JetBrains Mono", "DejaVu Sans Mono", "monospace"]
    font = QFont()
    font.setFamilies(families)
    font.setStyleHint(StyleHint.Monospace)
    font.setPointSize(point_size)
    return font


__all__ = [
    "ACCENT", "SUCCESS", "WARNING", "ERROR",
    "apply_theme", "mono_font", "isDarkTheme", "FluentStyleSheet",
]
