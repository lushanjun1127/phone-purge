"""美国号码高速剔除器：对话框。"""

from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QVBoxLayout,
)
from PyQt6.QtGui import QFont


class PreviewDialog(QDialog):
    """只读文本预览对话框（最多显示前 N 条号码）。"""

    def __init__(self, title, numbers, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(450, 600)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"共 {len(numbers)} 条（最多显示前 100 条）："))

        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setFont(QFont("Consolas", 11))
        text.setPlainText('\n'.join(numbers) if numbers else "(无)")
        layout.addWidget(text)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        btn_box.accepted.connect(self.accept)
        layout.addWidget(btn_box)
