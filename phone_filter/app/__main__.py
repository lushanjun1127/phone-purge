"""客户号码剔除器（数据库版）应用入口。

用法：
    python -m phone_filter.app
"""

import logging
import sys

from PyQt6.QtWidgets import QApplication

from phone_filter.gui.main_window import NumberFilterGUI


def main():
    app = QApplication(sys.argv)
    window = NumberFilterGUI()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - %(levelname)s - %(message)s')
    main()
