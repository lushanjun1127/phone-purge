"""客户号码剔除器（数据库版）Fluent 风格主窗口。

Win11 Fluent 视觉（QFluentWidgets）：顶部页面导航 + 卡片式布局 + InfoBar 轻提示。
业务逻辑与 ``phone_filter.gui.main_window.NumberFilterGUI`` 完全一致，
均委托给 ``phone_filter.core.processor.CustomerNumberProcessor``。

跨 Qt 绑定：所有 Qt 组件从 ``phone_filter.gui.qt_shim`` 导入，
PyQt6 / PySide6 + 对应的 QFluentWidgets 发行版均可运行。
"""

import logging
import os
from datetime import datetime

from phone_filter.gui.qt_shim import (
    DialogButton, Orientation, QFileDialog, QHBoxLayout, QLabel, QPushButton,
    QMainWindow, QMessageBox, Qt, QTimer, QVBoxLayout, QWidget,
)

# qt_shim 必须先于 qfluentwidgets 导入（见 qt_shim 模块文档）
from qfluentwidgets import (
    CardWidget, ComboBox, FluentIcon, InfoBar, InfoBarPosition, LineEdit,
    ListWidget, ProgressBar, PrimaryPushButton, RadioButton,
    ScrollArea, Slider as FluentSlider, StrongBodyLabel, SubtitleLabel,
    SwitchButton, ToolButton,
)

from phone_filter.core.processor import CustomerNumberProcessor
from phone_filter.gui import theme
from phone_filter.gui.fluent_threads import ImportNumbersThread, ProcessingThread

logger = logging.getLogger(__name__)

CATEGORIES = {
    'invalid': ('无效客户', 'invalid_customers'),
    'valid': ('有效客户', 'valid_customers'),
    'opened': ('已开客户', 'opened_customers'),
}

FILE_FILTER = "All Supported (*.xlsx *.xls *.csv *.txt);;All Files (*)"

_CURSOR_HAND = (Qt.CursorShape.PointingHandCursor if hasattr(Qt, "CursorShape")
                else Qt.PointingHandCursor)


class _NavButton(QPushButton):
    """顶部页面切换按钮（强调色下划线表示选中）。"""

    def __init__(self, text, active, parent=None):
        super().__init__(text, parent)
        self.setCursor(_CURSOR_HAND)
        self.set_active(active)

    def set_active(self, active):
        self.setStyleSheet(
            "QPushButton{background:transparent;border:none;"
            f"border-bottom:2px solid {'#0067C9' if active else 'transparent'};"
            f"color:{theme.ACCENT if active else 'palette(text)'};"
            f"font-weight:{'bold' if active else 'normal'};padding:6px 14px;}}")


class _SecondaryButton(QPushButton):
    """描边次要按钮（Fluent secondary 语义）。"""

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setCursor(_CURSOR_HAND)
        self.setStyleSheet(
            "QPushButton{border:1px solid rgba(128,128,128,0.55);"
            "border-radius:6px;padding:8px 18px;background:transparent;}"
            f"QPushButton:hover{{border-color:{theme.ACCENT};"
            f"color:{theme.ACCENT};}}")


class _DangerButton(QPushButton):
    """危险操作按钮（红色描边，悬停填充）。"""

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setCursor(_CURSOR_HAND)
        self.setStyleSheet(
            f"QPushButton{{border:1px solid {theme.ERROR};color:{theme.ERROR};"
            "border-radius:6px;padding:8px 18px;background:transparent;}"
            f"QPushButton:hover{{background:{theme.ERROR};color:white;}}")


def _setting_card(widget, parent):
    """把单个设置控件包进卡片。"""
    card = CardWidget(parent)
    h = QHBoxLayout(card)
    h.setContentsMargins(20, 12, 20, 12)
    h.addWidget(widget)
    h.addStretch(1)
    return card


class _CategoryCard(CardWidget):
    """单个分类库卡片：文件路径 + 浏览 + 导入。"""

    def __init__(self, title, window, category, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 16)
        v.addWidget(StrongBodyLabel(title, self))

        h = QHBoxLayout()
        self.path_edit = LineEdit(self)
        self.path_edit.setPlaceholderText("未选择文件（启动时自动查找同名文件）")
        browse_btn = ToolButton(FluentIcon.FOLDER, self)
        browse_btn.setToolTip("浏览选择文件")
        browse_btn.clicked.connect(lambda: window.browse_file(category))
        import_btn = ToolButton(FluentIcon.SYNC, self)
        import_btn.setToolTip("导入号码到此库")
        import_btn.clicked.connect(
            lambda: window.import_numbers_to_category(category))
        h.addWidget(self.path_edit, 1)
        h.addWidget(browse_btn)
        h.addWidget(import_btn)
        v.addLayout(h)


class NumberFilterFluentWindow(QMainWindow):
    """Fluent 风格主窗口：分类管理 / 号码处理 / 设置 三个页面。"""

    def __init__(self, db_path="phone_numbers.db"):
        super().__init__()
        self.setWindowTitle("客户号码剔除器 Professional Edition v4.1 · Fluent")
        self.resize(1180, 820)

        self.processor = CustomerNumberProcessor(db_path)

        self.file_paths = {cat: "" for cat in CATEGORIES}
        self.line_edits = {}

        self.source_file = ""
        self.output_file = "filtered_numbers_output"
        self.output_format = "xlsx"

        self.strict_mode = True
        self.similarity_threshold = 0.8
        self._closed = False
        self.processing_thread = None
        self.import_thread = None

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        nav = QHBoxLayout()
        nav.addWidget(SubtitleLabel("客户号码剔除器", central))
        nav.addStretch(1)
        self.nav_buttons = {}
        for key, text in (("classify", "分类管理"), ("process", "号码处理"),
                          ("settings", "设置")):
            btn = _NavButton(text, key == "classify", central)
            btn.clicked.connect(
                lambda checked=False, k=key: self._switch_page(k))
            nav.addWidget(btn)
            self.nav_buttons[key] = btn
        root.addLayout(nav)

        self.classify_page = self._build_classify_page()
        self.process_page = self._build_process_page()
        self.settings_page = self._build_settings_page()
        pages_host = QWidget()
        page_layout = QVBoxLayout(pages_host)
        page_layout.setContentsMargins(0)
        for p in (self.classify_page, self.process_page, self.settings_page):
            p.hide()
            page_layout.addWidget(p)
        self.classify_page.show()
        root.addWidget(pages_host, 1)

        self.status_label = QLabel("就绪", central)
        root.addWidget(self.status_label)

        self.update_current_stats()
        QTimer.singleShot(200, self.auto_find_classification_files)

    # ------------------------------------------------------------------
    # 生命周期 / 通用
    # ------------------------------------------------------------------
    def closeEvent(self, event):
        if not self._closed:
            self._closed = True
            try:
                self.processor.close()
            except Exception:  # pragma: no cover
                logger.debug("关闭处理器失败", exc_info=True)
        event.accept()

    def _switch_page(self, key):
        pages = {"classify": self.classify_page,
                 "process": self.process_page,
                 "settings": self.settings_page}
        for k, page in pages.items():
            page.setVisible(k == key)
        for k, btn in self.nav_buttons.items():
            btn.set_active(k == key)

    def show_message(self, text):
        self.status_label.setText(text)

    def notify(self, level, title, content, duration=4000):
        """InfoBar 轻提示（替代部分打断式弹窗）。"""
        kwargs = dict(parent=self, position=InfoBarPosition.TOP_RIGHT,
                      duration=duration)
        {"success": InfoBar.success, "warning": InfoBar.warning,
         "error": InfoBar.error}.get(level, InfoBar.info)(title, content, **kwargs)

    # ------------------------------------------------------------------
    # 页面：分类管理
    # ------------------------------------------------------------------
    def _build_classify_page(self):
        page = ScrollArea()
        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(12)

        match_card = CardWidget(body)
        mv = QVBoxLayout(match_card)
        mv.setContentsMargins(20, 16, 20, 16)
        mv.addWidget(SubtitleLabel("匹配模式", match_card))

        modes = QHBoxLayout()
        self.strict_radio = RadioButton("严格模式（精确匹配）", match_card)
        self.strict_radio.setChecked(True)
        self.strict_radio.toggled.connect(self.toggle_match_mode)
        self.fuzzy_radio = RadioButton("宽松模式（模糊匹配）", match_card)
        self.fuzzy_radio.toggled.connect(self.toggle_match_mode)
        modes.addWidget(self.strict_radio)
        modes.addWidget(self.fuzzy_radio)
        modes.addStretch(1)
        mv.addLayout(modes)

        th = QHBoxLayout()
        th.addWidget(QLabel("相似度阈值:", match_card))
        self.threshold_slider = FluentSlider(Orientation.Horizontal, match_card)
        self.threshold_slider.setRange(50, 100)
        self.threshold_slider.setValue(80)
        self.threshold_slider.valueChanged.connect(self.update_threshold_label)
        self.threshold_value_label = StrongBodyLabel("0.80", match_card)
        th.addWidget(self.threshold_slider, 1)
        th.addWidget(self.threshold_value_label)
        mv.addLayout(th)
        v.addWidget(match_card)

        for category, (title, _) in CATEGORIES.items():
            card = _CategoryCard(f"{title}号码库", self, category, body)
            self.line_edits[category] = card.path_edit
            v.addWidget(card)

        actions = QHBoxLayout()
        refresh_btn = _SecondaryButton("刷新分类号码", body)
        refresh_btn.clicked.connect(self.load_classifications)
        clear_btn = _DangerButton("清空分类号码", body)
        clear_btn.clicked.connect(self.clear_classifications)
        actions.addWidget(refresh_btn)
        actions.addWidget(clear_btn)
        actions.addStretch(1)
        self.current_stats_label = StrongBodyLabel("", body)
        actions.addWidget(self.current_stats_label)
        v.addLayout(actions)
        v.addStretch(1)

        page.setWidget(body)
        return page

    def toggle_match_mode(self):
        sender = self.sender()
        if sender is self.strict_radio and self.strict_radio.isChecked():
            self.fuzzy_radio.setChecked(False)
            self.strict_mode = True
        elif sender is self.fuzzy_radio and self.fuzzy_radio.isChecked():
            self.strict_radio.setChecked(False)
            self.strict_mode = False

        fuzzy_enabled = not self.strict_mode
        self.threshold_slider.setEnabled(fuzzy_enabled)
        self.threshold_value_label.setEnabled(fuzzy_enabled)

    def update_threshold_label(self, value):
        self.similarity_threshold = value / 100.0
        self.threshold_value_label.setText(f"{self.similarity_threshold:.2f}")

    # ------------------------------------------------------------------
    # 页面：号码处理
    # ------------------------------------------------------------------
    def _build_process_page(self):
        page = ScrollArea()
        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(12)

        src_card = CardWidget(body)
        sv = QVBoxLayout(src_card)
        sv.setContentsMargins(20, 16, 20, 16)
        sv.addWidget(SubtitleLabel("源号码文件", src_card))
        sh = QHBoxLayout()
        self.source_line_edit = LineEdit(src_card)
        self.source_line_edit.setPlaceholderText(
            "选择包含待筛选号码的 xlsx/csv/txt")
        browse_btn = ToolButton(FluentIcon.FOLDER, src_card)
        browse_btn.setToolTip("浏览选择源文件")
        browse_btn.clicked.connect(self.browse_source_file)
        sh.addWidget(self.source_line_edit, 1)
        sh.addWidget(browse_btn)
        sv.addLayout(sh)
        v.addWidget(src_card)

        out_card = CardWidget(body)
        ov = QVBoxLayout(out_card)
        ov.setContentsMargins(20, 16, 20, 16)
        ov.addWidget(SubtitleLabel("输出设置", out_card))
        oh = QHBoxLayout()
        oh.addWidget(QLabel("输出文件名:", out_card))
        self.output_file_edit = LineEdit(out_card)
        self.output_file_edit.setText(self.output_file)
        oh.addWidget(self.output_file_edit, 1)
        oh.addWidget(QLabel("格式:", out_card))
        self.output_format_combo = ComboBox(out_card)
        self.output_format_combo.setMinimumWidth(110)
        self.output_format_combo.addItems(list(CustomerNumberProcessor.OUTPUT_FORMATS))
        self.output_format_combo.setCurrentText(self.output_format)
        oh.addWidget(self.output_format_combo)
        ov.addLayout(oh)
        v.addWidget(out_card)

        actions = QHBoxLayout()
        preview_btn = _SecondaryButton("预览待剔除号码", body)
        preview_btn.clicked.connect(self.preview_filtered_numbers)
        process_btn = PrimaryPushButton("开始处理", body)
        process_btn.clicked.connect(self.start_processing)
        actions.addWidget(preview_btn)
        actions.addWidget(process_btn)
        actions.addStretch(1)
        v.addLayout(actions)

        stats_card = CardWidget(body)
        stv = QVBoxLayout(stats_card)
        stv.setContentsMargins(20, 16, 20, 16)
        stv.addWidget(SubtitleLabel("处理统计", stats_card))
        self.stats_label = StrongBodyLabel(
            "待处理号码: 0 | 已剔除号码: 0 | 剩余号码: 0", stats_card)
        stv.addWidget(self.stats_label)
        v.addWidget(stats_card)

        log_card = CardWidget(body)
        lg = QVBoxLayout(log_card)
        lg.setContentsMargins(20, 16, 20, 16)
        lg.addWidget(SubtitleLabel("处理日志", log_card))
        self.log_list = ListWidget(log_card)
        self.log_list.setFont(theme.mono_font(11))
        self.log_list.setMinimumHeight(180)
        lg.addWidget(self.log_list)
        v.addWidget(log_card)

        self.progress_bar = ProgressBar(body)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        v.addWidget(self.progress_bar)
        v.addStretch(1)

        page.setWidget(body)
        return page

    # ------------------------------------------------------------------
    # 页面：设置
    # ------------------------------------------------------------------
    def _build_settings_page(self):
        page = ScrollArea()
        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(12)

        v.addWidget(SubtitleLabel("外观", body))
        self.dark_switch = SwitchButton("深色模式", body)
        self.dark_switch.checkedChanged.connect(self._on_dark_changed)
        v.addWidget(_setting_card(self.dark_switch, body))

        about_card = CardWidget(body)
        av = QVBoxLayout(about_card)
        av.setContentsMargins(20, 16, 20, 16)
        av.addWidget(SubtitleLabel("关于", about_card))
        from phone_filter.gui.dialogs import ABOUT_TEXT
        about_label = QLabel(ABOUT_TEXT.replace("\n\n", "\n"), about_card)
        about_label.setWordWrap(True)
        av.addWidget(about_label)
        v.addWidget(about_card)
        v.addStretch(1)

        page.setWidget(body)
        return page

    def _on_dark_changed(self, checked):
        from qfluentwidgets import Theme, setTheme
        setTheme(Theme.DARK if checked else Theme.LIGHT)

    # ------------------------------------------------------------------
    # 文件浏览
    # ------------------------------------------------------------------
    def browse_file(self, category):
        file_path, _ = QFileDialog.getOpenFileName(
            self, f"选择{CATEGORIES[category][0]}文件", "", FILE_FILTER)
        if file_path:
            self.file_paths[category] = file_path
            self.line_edits[category].setText(file_path)

    def browse_source_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择源号码文件", "", FILE_FILTER)
        if file_path:
            self.source_file = file_path
            self.source_line_edit.setText(file_path)

    # ------------------------------------------------------------------
    # 导入
    # ------------------------------------------------------------------
    def import_numbers_to_category(self, category):
        title = CATEGORIES[category][0]
        file_path, _ = QFileDialog.getOpenFileName(
            self, f"选择要导入到{title}的号码文件", "", FILE_FILTER)
        if not file_path:
            return

        self.import_thread = ImportNumbersThread(
            self.processor, file_path, category)
        self.import_thread.progress_updated.connect(self.progress_bar.setValue)
        self.import_thread.log_updated.connect(self.update_log)
        self.import_thread.finished_result.connect(self.on_import_finished)
        self.import_thread.error_occurred.connect(self.on_import_error)
        self.show_message(f"正在导入号码到{title}库...")
        self.import_thread.start()

    def on_import_finished(self, result):
        category, old_count, new_count = result
        title = CATEGORIES[category][0]
        self.notify("success", "导入完成",
                    f"{title}库：原有 {old_count}，现有 {new_count}，"
                    f"新增 {new_count - old_count}")
        self.update_current_stats()
        self.show_message(f"向{title}库导入号码完成")

    def on_import_error(self, error_msg):
        self.notify("error", "导入失败", str(error_msg))
        self.show_message("导入号码失败")

    # ------------------------------------------------------------------
    # 预览
    # ------------------------------------------------------------------
    def preview_filtered_numbers(self):
        if not self.source_file:
            self.notify("warning", "提示", "请先上传源文件！")
            return
        if not self.processor.has_classified_customers():
            self.notify("warning", "提示", "分类号码库为空，请检查分类文件！")
            return
        try:
            to_remove = self.processor.preview_filtered_numbers(
                self.source_file,
                similarity_threshold=self.similarity_threshold,
                max_items=1000,
                fuzzy_mode=not self.strict_mode,
            )
            if not to_remove:
                self.notify("info", "预览结果", "没有号码会被剔除。")
                return
            from phone_filter.gui.dialogs import PreviewDialog
            PreviewDialog(to_remove, self).exec()
        except Exception as e:
            logger.exception("预览失败")
            self.notify("error", "预览出错", str(e))
            self.update_log(f"预览出错: {e}")

    # ------------------------------------------------------------------
    # 加载 / 清空分类
    # ------------------------------------------------------------------
    def load_classifications(self):
        try:
            self.show_message("正在加载分类号码...")
            self.progress_bar.setValue(0)
            self.processor.classify_numbers(**{
                f"{cat}_file": path or None
                for cat, path in self.file_paths.items()
            })
            self.notify("success", "刷新完成", "分类号码刷新完成！")
            self.update_log("分类号码加载完成。")
            self.update_current_stats()
            self.show_message("分类号码加载完成")
        except Exception as e:
            logger.exception("加载分类失败")
            self.notify("error", "加载失败", str(e))
            self.update_log(f"加载出错: {e}")
            self.show_message("加载分类号码失败")

    def clear_classifications(self):
        reply = QMessageBox.question(
            self, "确认", "您确定要清空所有分类号码吗？此操作不可撤销。",
            DialogButton.Yes | DialogButton.No)
        if reply != DialogButton.Yes:
            return
        try:
            self.processor.clear_category(None)
            self.update_log("所有分类号码已清空。")
            self.update_current_stats()
            self.notify("success", "已清空", "分类号码已清空！")
        except Exception as e:
            self.notify("error", "清空失败", str(e))

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------
    def update_current_stats(self):
        try:
            report = self.processor.generate_report()
            self.current_stats_label.setText(
                f"统计: 无效: {report['无效客户']} | 有效: {report['有效客户']} | "
                f"已开: {report['已开客户']} | 合计: {report['合计剔除']}")
        except Exception as e:
            logger.error("更新统计失败: %s", e)

    # ------------------------------------------------------------------
    # 处理
    # ------------------------------------------------------------------
    def start_processing(self):
        if not self.source_file:
            self.notify("warning", "提示", "请先上传源文件！")
            return
        if not self.processor.has_classified_customers():
            self.notify("warning", "提示", "分类号码库为空，请检查分类文件！")
            return

        self.output_file = (self.output_file_edit.text().strip()
                            or "filtered_numbers_output")
        self.output_format = self.output_format_combo.currentText()

        self.progress_bar.setValue(0)

        self.processing_thread = ProcessingThread(
            self.processor, self.source_file, self.output_file,
            self.output_format, self.similarity_threshold, not self.strict_mode,
        )
        self.processing_thread.progress_updated.connect(
            self.progress_bar.setValue)
        self.processing_thread.log_updated.connect(self.update_log)
        self.processing_thread.finished_result.connect(
            self.on_processing_finished)
        self.processing_thread.error_occurred.connect(self.on_processing_error)

        self.show_message("正在处理号码...")
        self.processing_thread.start()

    def on_processing_finished(self, result):
        self.stats_label.setText(
            f"总: {result['total']:,} | 保留: {result['kept']:,} | "
            f"剔除: {result['removed']:,}")
        self.update_log(f"结果已保存至: {result['output']}")
        self.notify("success", "处理完成",
                    f"输出 {os.path.basename(str(result['output']))}｜"
                    f"保留 {result['kept']:,} / 剔除 {result['removed']:,}",
                    duration=8000)
        self.show_message("号码处理完成")

    def on_processing_error(self, error_msg):
        self.notify("error", "处理失败", str(error_msg))
        self.show_message("号码处理失败")

    def update_log(self, message):
        self.log_list.addItem(f"[{datetime.now():%H:%M:%S}] {message}")
        self.log_list.scrollToBottom()

    # ------------------------------------------------------------------
    # 自动加载分类文件
    # ------------------------------------------------------------------
    def auto_find_classification_files(self):
        if self._closed:
            return
        current_dir = os.getcwd()
        found = {}
        for _, (_, name_prefix) in CATEGORIES.items():
            for ext in ('.xlsx', '.csv', '.xls'):
                p = os.path.join(current_dir, name_prefix + ext)
                if os.path.exists(p):
                    found[name_prefix] = p
                    break

        for category, (title, name_prefix) in CATEGORIES.items():
            path = found.get(name_prefix)
            if path:
                self.file_paths[category] = path
                self.line_edits[category].setText(path)
                logger.info("自动找到%s文件: %s", title, path)

        if not found:
            return
        try:
            self.processor.classify_numbers(**{
                f"{cat}_file": path or None
                for cat, path in self.file_paths.items()
            })
            self.update_current_stats()
            logger.info("程序启动：分类号码自动加载完成。")
        except Exception as e:
            logger.error("程序启动：加载分类号码时出错: %s", e)
