"""对话框：待剔除号码预览 / 关于。"""

from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QLabel, QListWidget, QMessageBox, QVBoxLayout,
)


class PreviewDialog(QDialog):
    """展示将被剔除的号码列表（源号码 ↔ 匹配号码 ↔ 相似度 ↔ 来源）。"""

    def __init__(self, to_remove_list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("待剔除号码预览")
        self.setGeometry(200, 200, 900, 550)
        layout = QVBoxLayout(self)

        info_label = QLabel(f"以下 {len(to_remove_list)} 个号码将被剔除（与分类库相似）：")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        list_widget = QListWidget()
        for src_num, exclude_num, similarity, source_tag in to_remove_list:
            list_widget.addItem(
                f"源: {src_num} | 匹配: {exclude_num} | "
                f"相似度: {similarity:.2f} | 来源: {source_tag}")
        layout.addWidget(list_widget)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)


ABOUT_TEXT = (
    "客户号码剔除器 Professional Edition v4.1\n\n"
    "修复与优化：\n"
    "- 处理线程与 process_and_filter 参数统一\n"
    "- 持久化 SQLite（WAL模式）+ suffix8 索引\n"
    "- 相似度算法：Levenshtein + 后缀加权\n"
    "- XLSX 输出流式写入，避免大数据 OOM\n"
    "- TXT 读取流式 + 编码自动回退\n"
    "- 代码分层重构：core（逻辑）/ gui（界面）\n\n"
    "作者：陆山君"
)


def show_about(parent):
    QMessageBox.about(parent, "关于", ABOUT_TEXT)
