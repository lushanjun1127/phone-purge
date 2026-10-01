"""客户号码剔除器（数据库版）Fluent 风格入口。

用法：
    pip install PyQt6 PyQt6-Fluent-Widgets      # 或 PySide6 + PySide6-Fluent-Widgets
    python -m phone_filter.app.fluent_main

与 ``python -m phone_filter.app``（经典 Fusion 风格）功能完全一致。
"""

import logging
import sys

from phone_filter.gui.qt_shim import QApplication  # noqa: F401  先于 qfluentwidgets

from phone_filter.gui.theme import apply_theme


def main():
    app = QApplication(sys.argv)
    apply_theme(app)

    from phone_filter.gui.fluent_main_window import NumberFilterFluentWindow

    window = NumberFilterFluentWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - %(levelname)s - %(message)s')
    main()
