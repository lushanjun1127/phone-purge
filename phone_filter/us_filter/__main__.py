"""美国号码高速剔除器应用入口。

用法：
    python -m phone_filter.us_filter
"""

import logging
import sys

from PyQt6.QtWidgets import QApplication

from phone_filter.us_filter.window import FilterWindow


def main():
    app = QApplication(sys.argv)
    window = FilterWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - %(levelname)s - %(message)s')
    main()
