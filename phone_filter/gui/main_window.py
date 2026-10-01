"""客户号码剔除器（数据库版）主窗口。

界面层：仅负责布局与交互，业务逻辑全部委托给
``phone_filter.core.processor.CustomerNumberProcessor``。
"""

import logging
import os
from datetime import datetime

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QFont, QKeySequence
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QSlider, QStatusBar, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from phone_filter.core.processor import CustomerNumberProcessor
from phone_filter.gui.dialogs import PreviewDialog, show_about
from phone_filter.gui.recent import RecentFiles
from phone_filter.gui.settings import UserSettings
from phone_filter.gui.threads import ImportNumbersThread, ProcessingThread

logger = logging.getLogger(__name__)

# 分类定义：category key -> (中文标题, 自动查找的文件名前缀)
CATEGORIES = {
    'invalid': ('无效客户', 'invalid_customers'),
    'valid': ('有效客户', 'valid_customers'),
    'opened': ('已开客户', 'opened_customers'),
}

FILE_FILTER = "All Supported (*.xlsx *.xls *.csv *.txt);;All Files (*)"


class NumberFilterGUI(QMainWindow):
    """主窗口：号码分类管理 + 号码处理两个标签页。"""

    def __init__(self, db_path="phone_numbers.db"):
        super().__init__()
        self.setWindowTitle("客户号码剔除器 Professional Edition v4.1")

        # ---- 用户设置：记住窗口大小、上次文件、匹配模式等，下次启动自动恢复 ----
        self.settings = UserSettings("db_app.json")
        self.recents = {
            "source": RecentFiles(self.settings, "db_source"),
            **{cat: RecentFiles(self.settings, f"db_category_{cat}")
               for cat in CATEGORIES},
        }

        width = int(self.settings.get("window.width", 1400))
        height = int(self.settings.get("window.height", 900))
        self.setGeometry(100, 100, width, height)

        self.processor = CustomerNumberProcessor(db_path)

        # 各分类文件路径 / 行编辑控件
        self.file_paths = {cat: "" for cat in CATEGORIES}
        self.line_edits = {}

        self.source_file = ""
        self.output_file = self.settings.get("output.name",
                                             "filtered_numbers_output")
        self.output_format = self.settings.get("output.format", "xlsx")

        self.strict_mode = bool(self.settings.get("match.strict", True))
        self.similarity_threshold = float(self.settings.get("match.threshold", 0.8))
        self._closed = False
        self.processing_thread = None
        self.import_thread = None

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        self._create_menu_bar()

        self.tab_widget = QTabWidget()
        main_layout.addWidget(self.tab_widget)
        self._create_classification_tab()
        self._create_processing_tab()

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("就绪 | 支持拖拽 xlsx/csv/txt 到窗口")

        # 允许把号码文件直接拖进主窗口（自动识别分类库 / 源文件）
        self.setAcceptDrops(True)

        # 恢复上次使用的标签页与源文件
        try:
            last_tab = int(self.settings.get("last.tab", 0))
            if 0 <= last_tab < self.tab_widget.count():
                self.tab_widget.setCurrentIndex(last_tab)
        except (TypeError, ValueError):
            pass
        last_source = self.settings.get("last.source", "")
        if last_source and os.path.exists(last_source):
            self.source_file = last_source
            self.source_line_edit.setText(last_source)

        self.update_current_stats()

        # 启动后台自动加载分类文件
        QTimer.singleShot(200, self.auto_find_classification_files)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def closeEvent(self, event):
        if not self._closed:
            self._closed = True
            try:
                self.settings.update({
                    "window.width": self.width(),
                    "window.height": self.height(),
                    "output.name": self.output_file_edit.text().strip()
                                   or "filtered_numbers_output",
                    "output.format": self.output_format_combo.currentText(),
                    "match.strict": self.strict_mode,
                    "match.threshold": self.similarity_threshold,
                    "last.tab": self.tab_widget.currentIndex(),
                    "last.source": self.source_file,
                })
            except Exception:  # pragma: no cover
                logger.debug("保存设置失败", exc_info=True)
            try:
                self.processor.close()
            except Exception:  # pragma: no cover
                logger.debug("关闭处理器失败", exc_info=True)
        event.accept()

    def changeEvent(self, event):
        """处理 Ctrl+W / Esc 快捷键退出。"""
        if event.type() == event.Type.WindowStateChange:
            super().changeEvent(event)
            return
        key = event.key() if hasattr(event, "key") else None
        if key in (Qt.Key.Key_Escape,):
            self.close()
            return
        super().changeEvent(event)

    def keyPressEvent(self, event):
        modifiers = event.modifiers()
        if modifiers & Qt.KeyboardModifier.Control and \
                event.key() == Qt.Key.Key_W:
            self.close()
            return
        if event.key() == Qt.Key.Key_F5:
            self.load_classifications()
            return
        if event.key() == Qt.Key.Key_F9:
            self.start_processing()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # 菜单
    # ------------------------------------------------------------------
    def _create_menu_bar(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu('文件')

        open_action = QAction('打开源号码文件…', self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self.browse_source_file)
        file_menu.addAction(open_action)

        file_menu.addSeparator()
        self._recent_menu = file_menu.addMenu('最近使用')
        self._rebuild_recent_menu()

        file_menu.addSeparator()
        exit_action = QAction('退出', self)
        exit_action.setShortcut(QKeySequence("Ctrl+Q"))
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        tools_menu = menubar.addMenu('工具')
        refresh_action = QAction('刷新分类', self)
        refresh_action.setShortcut(QKeySequence("F5"))
        refresh_action.triggered.connect(self.load_classifications)
        tools_menu.addAction(refresh_action)
        clear_action = QAction('清空分类', self)
        clear_action.triggered.connect(self.clear_classifications)
        tools_menu.addAction(clear_action)
        tools_menu.addSeparator()
        process_action = QAction('开始处理', self)
        process_action.setShortcut(QKeySequence("F9"))
        process_action.triggered.connect(self.start_processing)
        tools_menu.addAction(process_action)

        help_menu = menubar.addMenu('帮助')
        shortcut_action = QAction('快捷键说明', self)
        shortcut_action.triggered.connect(self._show_shortcuts)
        help_menu.addAction(shortcut_action)
        about_action = QAction('关于', self)
        about_action.triggered.connect(lambda: show_about(self))
        help_menu.addAction(about_action)

    def _show_shortcuts(self):
        QMessageBox.information(
            self, "快捷键",
            "Ctrl+O   打开源号码文件\n"
            "Ctrl+L   选择当前分类的文件（在分类页时）\n"
            "F5       刷新分类号码\n"
            "F9       开始处理\n"
            "Ctrl+Q   退出")

    def _rebuild_recent_menu(self):
        """重建"最近使用"子菜单：源文件 + 各分类库文件。"""
        menu = self._recent_menu
        menu.clear()
        items = []
        for path in self.recents["source"].list():
            items.append(("source", "", path))
        for cat, (title, _) in CATEGORIES.items():
            for path in self.recents[cat].list():
                items.append(("category", cat, path))
        if not items:
            act = menu.addAction("（暂无记录，选择文件后自动记录）")
            act.setEnabled(False)
            return
        for kind, cat, path in items:
            label = os.path.basename(path)
            if kind == "category":
                label = f"[{CATEGORIES[cat][0]}] {label}"
            act = menu.addAction(label)
            act.setToolTip(path)
            act.triggered.connect(
                lambda checked=False, k=kind, c=cat, p=path:
                self._load_recent(k, c, p))
        menu.addSeparator()
        clear_act = menu.addAction("清除最近记录")
        clear_act.triggered.connect(self._clear_recents)

    def _load_recent(self, kind, category, path):
        if not os.path.exists(path):
            QMessageBox.warning(self, "提示", "文件已不存在：" + path)
            return
        if kind == "source":
            self.source_file = path
            self.source_line_edit.setText(path)
            self.status_bar.showMessage(f"已载入源文件: {os.path.basename(path)}")
        else:
            self.file_paths[category] = path
            self.line_edits[category].setText(path)
            self.status_bar.showMessage(
                f"已载入{CATEGORIES[category][0]}文件: {os.path.basename(path)}")

    def _clear_recents(self):
        for rf in self.recents.values():
            rf.clear()
        self._rebuild_recent_menu()

    # ------------------------------------------------------------------
    # 分类标签页
    # ------------------------------------------------------------------
    def _create_classification_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        layout.addWidget(self._create_match_mode_group())
        for category, (title, _) in CATEGORIES.items():
            layout.addWidget(self._create_category_group(category, title))
        layout.addLayout(self._create_category_button_row())

        self.tab_widget.addTab(tab, "号码分类管理")

    def _create_match_mode_group(self):
        group = QGroupBox("匹配模式")
        mode_layout = QHBoxLayout(group)

        self.strict_radio = QCheckBox("严格模式（精确匹配）")
        self.strict_radio.setChecked(self.strict_mode)
        self.strict_radio.toggled.connect(self.toggle_match_mode)

        self.fuzzy_radio = QCheckBox("宽松模式（模糊匹配）")
        self.fuzzy_radio.setChecked(not self.strict_mode)
        self.fuzzy_radio.toggled.connect(self.toggle_match_mode)

        self.threshold_label = QLabel("相似度阈值:")
        self.threshold_slider = QSlider(Qt.Orientation.Horizontal)
        self.threshold_slider.setMinimum(50)
        self.threshold_slider.setMaximum(100)
        self.threshold_slider.setValue(int(round(self.similarity_threshold * 100)))
        self.threshold_slider.valueChanged.connect(self.update_threshold_label)
        self.threshold_value_label = QLabel(f"{self.similarity_threshold:.2f}")

        mode_layout.addWidget(self.strict_radio)
        mode_layout.addWidget(self.fuzzy_radio)
        mode_layout.addStretch()
        mode_layout.addWidget(self.threshold_label)
        mode_layout.addWidget(self.threshold_slider)
        mode_layout.addWidget(self.threshold_value_label)

        # 恢复初始启用状态
        fuzzy_enabled = not self.strict_mode
        self.threshold_slider.setEnabled(fuzzy_enabled)
        self.threshold_value_label.setEnabled(fuzzy_enabled)
        self.threshold_label.setEnabled(fuzzy_enabled)
        return group

    def _create_category_group(self, category, title):
        group = QGroupBox(f"{title}号码库")
        h = QHBoxLayout(group)

        line_edit = QLineEdit(self.file_paths[category])
        self.line_edits[category] = line_edit

        browse_btn = QPushButton("浏览")
        browse_btn.clicked.connect(lambda: self.browse_file(category))

        import_btn = QPushButton("导入号码")
        import_btn.clicked.connect(lambda: self.import_numbers_to_category(category))

        h.addWidget(line_edit)
        h.addWidget(browse_btn)
        h.addWidget(import_btn)
        return group

    def _create_category_button_row(self):
        button_layout = QHBoxLayout()

        refresh_btn = QPushButton("刷新分类号码")
        refresh_btn.clicked.connect(self.load_classifications)
        clear_btn = QPushButton("清空分类号码")
        clear_btn.clicked.connect(self.clear_classifications)

        self.current_stats_label = QLabel()
        bold_font = QFont()
        bold_font.setBold(True)
        self.current_stats_label.setFont(bold_font)

        button_layout.addWidget(refresh_btn)
        button_layout.addWidget(clear_btn)
        button_layout.addStretch()
        button_layout.addWidget(self.current_stats_label)
        return button_layout

    def toggle_match_mode(self):
        """严格/宽松互斥切换（模拟单选按钮行为）。"""
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
        self.threshold_label.setEnabled(fuzzy_enabled)

    def update_threshold_label(self, value):
        self.similarity_threshold = value / 100.0
        self.threshold_value_label.setText(f"{self.similarity_threshold:.2f}")

    # ------------------------------------------------------------------
    # 处理标签页
    # ------------------------------------------------------------------
    def _create_processing_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # 源文件
        source_group = QGroupBox("源号码文件")
        source_layout = QHBoxLayout(source_group)
        self.source_line_edit = QLineEdit(self.source_file)
        self.source_line_edit.setPlaceholderText(
            "点击浏览，或直接把 xlsx/csv/txt 拖拽到窗口任意位置")
        source_browse_btn = QPushButton("浏览")
        source_browse_btn.clicked.connect(self.browse_source_file)
        source_layout.addWidget(self.source_line_edit)
        source_layout.addWidget(source_browse_btn)

        # 输出设置
        output_group = QGroupBox("输出设置")
        output_layout = QHBoxLayout(output_group)

        file_box = QVBoxLayout()
        file_box.addWidget(QLabel("输出文件名:"))
        self.output_file_edit = QLineEdit(self.output_file)
        file_box.addWidget(self.output_file_edit)

        fmt_box = QVBoxLayout()
        fmt_box.addWidget(QLabel("输出格式:"))
        self.output_format_combo = QComboBox()
        self.output_format_combo.addItems(list(CustomerNumberProcessor.OUTPUT_FORMATS))
        self.output_format_combo.setCurrentText(self.output_format)
        fmt_box.addWidget(self.output_format_combo)

        open_box = QVBoxLayout()
        open_box.addWidget(QLabel("快捷操作:"))
        open_out_btn = QPushButton("打开输出所在文件夹")
        open_out_btn.clicked.connect(self.open_output_folder)
        open_box.addWidget(open_out_btn)

        output_layout.addLayout(file_box)
        output_layout.addLayout(fmt_box)
        output_layout.addLayout(open_box)

        # 操作按钮
        button_layout = QHBoxLayout()
        preview_btn = QPushButton("预览待剔除号码")
        preview_btn.clicked.connect(self.preview_filtered_numbers)
        preview_btn.setStyleSheet(_button_style("#2196F3"))
        process_btn = QPushButton("开始处理")
        process_btn.clicked.connect(self.start_processing)
        process_btn.setStyleSheet(_button_style("#4CAF50"))
        button_layout.addWidget(preview_btn)
        button_layout.addWidget(process_btn)

        # 统计与日志
        stats_group = QGroupBox("处理统计")
        stats_layout = QHBoxLayout(stats_group)
        self.stats_label = QLabel("待处理号码: 0 | 已剔除号码: 0 | 剩余号码: 0")
        stats_layout.addWidget(self.stats_label)

        log_group = QGroupBox("处理日志")
        log_layout = QVBoxLayout(log_group)
        self.log_text_edit = QTextEdit()
        self.log_text_edit.setMaximumHeight(180)
        self.log_text_edit.setReadOnly(True)
        log_layout.addWidget(self.log_text_edit)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)

        layout.addWidget(source_group)
        layout.addWidget(output_group)
        layout.addLayout(button_layout)
        layout.addWidget(stats_group)
        layout.addWidget(log_group)
        layout.addWidget(self.progress_bar)

        self.tab_widget.addTab(tab, "号码处理")

    # ------------------------------------------------------------------
    # 文件浏览 / 拖拽 / 最近文件
    # ------------------------------------------------------------------
    def browse_file(self, category):
        file_path, _ = QFileDialog.getOpenFileName(
            self, f"选择{CATEGORIES[category][0]}文件", "", FILE_FILTER)
        if file_path:
            self.file_paths[category] = file_path
            self.line_edits[category].setText(file_path)
            self.recents[category].add(file_path)
            self._rebuild_recent_menu()

    def browse_source_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择源号码文件", "", FILE_FILTER)
        if file_path:
            self.set_source_file(file_path)

    def set_source_file(self, path):
        """统一的源文件设置入口（供菜单、拖拽共用）。"""
        self.source_file = path
        self.source_line_edit.setText(path)
        self.recents["source"].add(path)
        self._rebuild_recent_menu()

    def open_output_folder(self):
        """在系统文件管理器中打开输出目录，并选中最新输出文件。"""
        name = self.output_file_edit.text().strip() or "filtered_numbers_output"
        fmt = self.output_format_combo.currentText()
        candidate = os.path.abspath(f"{os.path.splitext(name)[0]}.{fmt}")
        target_dir = os.path.dirname(candidate) or os.getcwd()
        try:
            QApplication.openUrlExternally("file://" + target_dir)
            if os.path.exists(candidate):
                logger.info("输出文件已生成: %s", candidate)
        except Exception as e:  # pragma: no cover - 桌面环境差异
            logger.warning("无法打开文件夹: %s", e)
            QMessageBox.information(self, "输出位置", candidate)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                p = url.toLocalFile()
                if p and p.lower().endswith(('.xlsx', '.xls', '.csv', '.txt')):
                    event.acceptProposedAction()
                    return

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls()
                 if u.toLocalFile()]
        supported = [p for p in paths
                     if p.lower().endswith(('.xlsx', '.xls', '.csv', '.txt'))]
        if not supported:
            return
        # 优先匹配分类文件名（invalid_customers.* 等），否则视为源文件
        matched_cat = None
        for p in supported:
            base = os.path.basename(p).lower()
            for cat, (_, prefix) in CATEGORIES.items():
                if base.startswith(prefix):
                    self.file_paths[cat] = p
                    self.line_edits[cat].setText(p)
                    self.recents[cat].add(p)
                    matched_cat = cat
                    break
            if matched_cat:
                break
        if matched_cat:
            self.status_bar.showMessage(
                f"已识别 {CATEGORIES[matched_cat][0]} 文件: "
                f"{os.path.basename(supported[0])}")
        else:
            self.set_source_file(supported[0])
            self.status_bar.showMessage(
                f"已添加源文件: {os.path.basename(supported[0])}")
        self._rebuild_recent_menu()
        event.acceptProposedAction()

    # ------------------------------------------------------------------
    # 导入
    # ------------------------------------------------------------------
    def import_numbers_to_category(self, category):
        title = CATEGORIES[category][0]
        file_path, _ = QFileDialog.getOpenFileName(
            self, f"选择要导入到{title}的号码文件", "", FILE_FILTER)
        if not file_path:
            return

        self.import_thread = ImportNumbersThread(self.processor, file_path, category)
        self.import_thread.progress_updated.connect(self.progress_bar.setValue)
        self.import_thread.log_updated.connect(self.update_log)
        self.import_thread.finished.connect(self.on_import_finished)
        self.import_thread.error_occurred.connect(self.on_import_error)
        self.status_bar.showMessage(f"正在导入号码到{title}库...")
        self.import_thread.start()

    def on_import_finished(self, result):
        category, old_count, new_count = result
        title = CATEGORIES[category][0]
        QMessageBox.information(
            self, "成功",
            f"成功向{title}库导入号码！\n原有: {old_count}, 现有: {new_count}, "
            f"新增: {new_count - old_count}")
        self.update_current_stats()
        self.status_bar.showMessage(f"向{title}库导入号码完成")

    def on_import_error(self, error_msg):
        QMessageBox.critical(self, "错误", f"导入号码时出错: {error_msg}")
        self.status_bar.showMessage("导入号码失败")

    # ------------------------------------------------------------------
    # 预览
    # ------------------------------------------------------------------
    def preview_filtered_numbers(self):
        if not self.source_file:
            QMessageBox.warning(self, "警告", "请上传源文件！")
            return
        if not self.processor.has_classified_customers():
            QMessageBox.warning(self, "警告", "分类号码库为空，请检查分类文件！")
            return
        try:
            to_remove = self.processor.preview_filtered_numbers(
                self.source_file,
                similarity_threshold=self.similarity_threshold,
                max_items=1000,
                fuzzy_mode=not self.strict_mode,
            )
            if not to_remove:
                QMessageBox.information(self, "预览结果", "没有号码会被剔除。")
                return
            PreviewDialog(to_remove, self).exec()
        except Exception as e:
            logger.exception("预览失败")
            QMessageBox.critical(self, "错误", f"预览号码时出错: {e}")
            self.update_log(f"预览出错: {e}\n")

    # ------------------------------------------------------------------
    # 加载 / 清空分类
    # ------------------------------------------------------------------
    def load_classifications(self):
        try:
            self.status_bar.showMessage("正在加载分类号码...")
            self.progress_bar.setValue(0)
            self.processor.classify_numbers(**{
                f"{cat}_file": path or None
                for cat, path in self.file_paths.items()
            })
            QMessageBox.information(self, "成功", "分类号码刷新完成！")
            self.update_log("分类号码加载完成。\n")
            self.update_current_stats()
            self.status_bar.showMessage("分类号码加载完成")
        except Exception as e:
            logger.exception("加载分类失败")
            QMessageBox.critical(self, "错误", f"加载分类号码时出错: {e}")
            self.update_log(f"加载出错: {e}\n")
            self.status_bar.showMessage("加载分类号码失败")

    def clear_classifications(self):
        reply = QMessageBox.question(
            self, "确认", "您确定要清空所有分类号码吗？此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self.processor.clear_category(None)
            self.update_log("所有分类号码已清空。\n")
            self.update_current_stats()
            QMessageBox.information(self, "成功", "分类号码已清空！")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"清空失败: {e}")

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
            QMessageBox.warning(self, "警告", "请上传源文件！")
            return
        if not self.processor.has_classified_customers():
            QMessageBox.warning(self, "警告", "分类号码库为空，请检查分类文件！")
            return

        self.output_file = self.output_file_edit.text().strip() or "filtered_numbers_output"
        self.output_format = self.output_format_combo.currentText()

        self.progress_bar.setValue(0)

        self.processing_thread = ProcessingThread(
            self.processor, self.source_file, self.output_file, self.output_format,
            self.similarity_threshold, not self.strict_mode,
        )
        self.processing_thread.progress_updated.connect(self.progress_bar.setValue)
        self.processing_thread.log_updated.connect(self.update_log)
        self.processing_thread.finished.connect(self.on_processing_finished)
        self.processing_thread.error_occurred.connect(self.on_processing_error)

        self.status_bar.showMessage("正在处理号码...")
        self.processing_thread.start()

    def on_processing_finished(self, result):
        report = self.processor.generate_report()
        self.stats_label.setText(
            f"总: {result['total']} | 保留: {result['kept']} | 剔除: {result['removed']}")
        self.update_log(f"结果已保存至: {result['output']}\n")
        self.update_log(f"报告: {report}\n")
        output_path = str(result['output'])

        box = QMessageBox(self)
        box.setWindowTitle("处理完成")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(
            f"处理完成！\n输出文件: {output_path}\n"
            f"保留 {result['kept']} / 剔除 {result['removed']}")
        open_btn = box.addButton("打开所在文件夹", QMessageBox.ButtonRole.ActionRole)
        show_btn = box.addButton("查看输出文件", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        role = box.buttonRole(box.clickedButton())
        if box.clickedButton() is open_btn:
            QApplication.openUrlExternally(
                "file://" + os.path.dirname(os.path.abspath(output_path)))
        elif box.clickedButton() is show_btn and os.path.exists(output_path):
            QApplication.openUrlExternally("file://" + os.path.abspath(output_path))
        self.status_bar.showMessage("号码处理完成")

    def on_processing_error(self, error_msg):
        QMessageBox.critical(self, "错误", f"处理号码时出错: {error_msg}")
        self.status_bar.showMessage("号码处理失败")

    def update_log(self, message):
        self.log_text_edit.append(f"[{datetime.now():%H:%M:%S}] {message}")

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


def _button_style(color):
    return (f"QPushButton {{ background-color: {color}; color: white; padding: 10px; "
            f"font-size: 14px; border-radius: 5px; }}")
