"""Qt 绑定兼容层：优先 PyQt6，回退 PySide6。

QFluentWidgets（Win11 Fluent 风格 UI 库）同时提供 PyQt6 / PySide6 两个发行版，
二者不能同时安装。Fluent 版界面代码统一从 ``phone_filter.gui.qt_shim`` 导入 Qt
组件与枚举，即可在两种绑定下运行：

    pip install PyQt6 PyQt6-Fluent-Widgets          # 首选组合
    pip install PySide6 PySide6-Fluent-Widgets      # 备选组合

注意：必须先导入本模块（或先创建 QApplication），再导入 qfluentwidgets，
qfluentwidgets 会自动跟随当前已加载的 Qt 绑定。
"""


try:
    from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
    from PyQt6.QtGui import (
        QAction, QColor, QFont, QIcon, QPalette, Qt as _QtModule,
    )

    class StyleHint:  # noqa: N801 - 与 PySide6 分支保持同样的短名访问方式
        Monospace = QFont.StyleHint.Monospace

    _ENUM_SCOPES = {"QFont": QFont, "QPalette": QPalette, "Qt": _QtModule}
    from PyQt6.QtWidgets import (
        QApplication, QDialogButtonBox, QFileDialog, QFrame, QGridLayout,
        QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox,
        QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSlider,
        QStatusBar, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
    )

    _BINDING = "pyqt"

    class _EnumProxy:
        """PyQt6 枚举代理：支持 ``DialogButton.Ok | DialogButton.Cancel`` 短名写法。"""

        def __init__(self, *classes):
            self._members = [m for cls in classes for m in cls]

        def __getattr__(self, name):
            for member in self._members:
                if member.name == name:
                    return member
            raise AttributeError(name)

    DialogButton = _EnumProxy(QMessageBox.StandardButton, QDialogButtonBox.StandardButton)
    Orientation = _EnumProxy(Qt.Orientation)
    CheckState = _EnumProxy(Qt.CheckState)
    MessageIcon = _EnumProxy(QMessageBox.Icon)

except ImportError:  # pragma: no cover - 取决于环境安装的绑定
    from PySide6.QtCore import Qt, QThread, QTimer, Signal as pyqtSignal
    from PySide6.QtGui import (
        QAction, QColor, QFont, QIcon, QPalette, Qt as _QtModule,
    )

    class StyleHint:  # noqa: N801
        Monospace = QFont.Monospace

    _ENUM_SCOPES = {"QFont": QFont, "QPalette": QPalette, "Qt": _QtModule}
    from PySide6.QtWidgets import (
        QApplication, QDialogButtonBox, QFileDialog, QFrame, QGridLayout,
        QHBoxLayout, QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox,
        QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSlider,
        QStatusBar, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
    )

    _BINDING = "pyside"

    class _EnumProxy:
        """PySide6 枚举代理：把全局短名（如 ``Ok``、``Horizontal``）映射到完整枚举成员。"""

        def __init__(self, scope):
            self._scope = scope

        def __getattr__(self, name):
            try:
                return getattr(self._scope, name)
            except AttributeError:
                raise AttributeError(name) from None

    DialogButton = _EnumProxy(QMessageBox)
    Orientation = _EnumProxy(Qt)
    CheckState = _EnumProxy(Qt)
    MessageIcon = _EnumProxy(QMessageBox)


def emit(signal, *args):
    """跨绑定发射信号：PyQt6 需要 ``signal[[类型列表]](*args)``，PySide6 直接 ``emit``。"""
    if _BINDING == "pyqt":
        signal[[type(a) for a in args]](*args)
    else:
        signal.emit(*args)


__all__ = [
    "Qt", "QThread", "QTimer", "pyqtSignal", "QAction", "QColor", "QFont", "QIcon",
    "QPalette", "StyleHint", "_ENUM_SCOPES",
    "QApplication", "QDialogButtonBox", "QFileDialog", "QFrame", "QGridLayout",
    "QHBoxLayout", "QLabel", "QLineEdit", "QListWidget", "QMainWindow", "QMessageBox",
    "QProgressBar", "QPushButton", "QScrollArea", "QSizePolicy", "QSlider",
    "QStatusBar", "QTabWidget", "QTextEdit", "QVBoxLayout", "QWidget",
    "DialogButton", "Orientation", "CheckState", "MessageIcon", "emit", "_BINDING",
]
