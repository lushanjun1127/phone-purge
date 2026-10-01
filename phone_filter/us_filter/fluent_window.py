"""美国号码高速剔除器 Fluent 风格主窗口。

Win11 Fluent 视觉（QFluentWidgets）：卡片式布局 + InfoBar 通知 + 状态徽章。
业务逻辑与 ``phone_filter.us_filter.window.FilterWindow`` 完全一致，
清洗/剔除在 ``phone_filter.us_filter.cleaning``，后台任务在
``phone_filter.gui.fluent_threads``。支持拖拽 txt。
"""

import logging
import os
import shutil
from datetime import datetime

from phone_filter.gui.qt_shim import (
    QFileDialog, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QVBoxLayout,
    QWidget,
)

# qt_shim 必须先于 qfluentwidgets 导入（见 qt_shim 模块文档）
from qfluentwidgets import (
    BodyLabel, CardWidget, FluentIcon, InfoBar, InfoBarPosition, LineEdit,
    PlainTextEdit, PrimaryPushButton, ProgressBar, PushButton, ToolButton,
    TransparentToggleToolButton,
)

from phone_filter.gui import theme
from phone_filter.gui.fluent_threads import NormalizeThread, ProcessThread
from phone_filter.us_filter.cleaning import app_root_dir
from phone_filter.us_filter.dialogs import PreviewDialog

logger = logging.getLogger(__name__)

CLASSIFY_FILES = {
    'invalid': 'invalid_customers.txt',
    'valid': 'valid_customers.txt',
    'opened': 'opened_customers.txt',
}

_BADGE_LIGHT = ("QLabel{{background:{bg};color:{fg};border-radius:10px;"
                "padding:2px 10px;font-weight:bold;}}")


class _StatusBadge(BodyLabel):
    """分类库存在状态徽章（替代 emoji ✅/❌）。"""

    def __init__(self, text, found, parent=None):
        super().__init__(text, parent)
        self.set_found(found)

    def set_found(self, found):
        if found:
            bg, fg = "#DFF6DD", theme.SUCCESS
        else:
            bg, fg = "#FDE7E9", theme.ERROR
        self.setStyleSheet(_BADGE_LIGHT.format(bg=bg, fg=fg))


class FilterFluentWindow(QMainWindow):
    """单窗口 Fluent 版：状态 → 源文件 → 输出 → 规范化 → 剔除。支持拖拽 txt。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("号码剔除器 v5.2 · Fluent - 美国号码全自动版")
        self.resize(1000, 820)

        self.root = app_root_dir()
        logger.info("根目录: %s", self.root)

        self.classify_paths = {
            cat: (p if os.path.exists(p := os.path.join(self.root, fname)) else None)
            for cat, fname in CLASSIFY_FILES.items()
        }

        self.source_path = ""
        self.output_path = os.path.join(self.root, "结果.txt")
        self.removed_path = os.path.join(self.root, "被剔除.txt")

        self.setAcceptDrops(True)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)
        layout.addWidget(self._build_status_group())
        layout.addWidget(self._build_source_group())
        layout.addWidget(self._build_output_group())
        layout.addWidget(self._build_action_group())
        layout.addWidget(self._build_run_group())

        self.log_edit = PlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setFont(theme.mono_font())
        layout.addWidget(self.log_edit, 1)

        self.progress = ProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)

        self.status_label = QLabel("就绪 | 拖拽源 txt 到窗口即可")
        layout.addWidget(self.status_label)

        self.norm_thread = None
        self.thread = None

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _build_status_group(self):
        group = CardWidget()
        v = QVBoxLayout(group)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(8)
        from qfluentwidgets import SubtitleLabel
        v.addWidget(SubtitleLabel(f"分类库（根目录: {self.root}）", group))

        self.classify_badges = {}
        for cat, fname in CLASSIFY_FILES.items():
            h = QHBoxLayout()
            h.addWidget(BodyLabel(fname, group))
            badge = _StatusBadge("", bool(self.classify_paths.get(cat)), group)
            self.classify_badges[cat] = badge
            h.addWidget(badge)
            h.addStretch(1)
            v.addLayout(h)
        self._refresh_status_labels()
        return group

    def _build_source_group(self):
        group = CardWidget()
        v = QVBoxLayout(group)
        v.setContentsMargins(20, 16, 20, 16)
        from qfluentwidgets import SubtitleLabel
        v.addWidget(SubtitleLabel("源号码文件（拖拽或点击浏览）", group))
        h = QHBoxLayout()
        self.src_le = LineEdit(group)
        self.src_le.setPlaceholderText("拖拽 txt 到此，或点浏览选择")
        browse_btn = ToolButton(FluentIcon.FOLDER, group)
        browse_btn.setToolTip("浏览选择源文件")
        browse_btn.clicked.connect(self._browse_source)
        h.addWidget(self.src_le, 1)
        h.addWidget(browse_btn)
        v.addLayout(h)
        return group

    def _build_output_group(self):
        group = CardWidget()
        v = QVBoxLayout(group)
        v.setContentsMargins(20, 16, 20, 16)
        from qfluentwidgets import SubtitleLabel
        v.addWidget(SubtitleLabel("输出文件", group))
        self.out_le = self._output_row(v, "保留号码:", self.output_path,
                                       self._browse_output, group)
        self.removed_le = self._output_row(v, "被剔除号码:", self.removed_path,
                                           self._browse_removed, group)
        return group

    def _output_row(self, parent_layout, label_text, default_path,
                    browse_slot, parent):
        h = QHBoxLayout()
        h.addWidget(BodyLabel(label_text, parent))
        edit = LineEdit(parent)
        edit.setText(default_path)
        btn = ToolButton(FluentIcon.SAVE_AS, parent)
        btn.setToolTip("另存为…")
        btn.clicked.connect(browse_slot)
        h.addWidget(edit, 1)
        h.addWidget(btn)
        parent_layout.addLayout(h)
        return edit

    def _build_action_group(self):
        group = CardWidget()
        v = QVBoxLayout(group)
        v.setContentsMargins(20, 16, 20, 16)
        from qfluentwidgets import SubtitleLabel
        v.addWidget(SubtitleLabel("规范化（可选，处理前统一格式）", group))
        h = QHBoxLayout()
        self.normalize_btn = PushButton("一键规范成 11 位（美国格式）", group)
        self.normalize_btn.setIcon(FluentIcon.HISTORY)
        self.normalize_btn.clicked.connect(self._normalize)
        tip = BodyLabel("规范化后会自动切换到新文件", group)
        tip.setEnabled(False)
        h.addWidget(self.normalize_btn)
        h.addWidget(tip)
        h.addStretch(1)
        v.addLayout(h)
        return group

    def _build_run_group(self):
        group = CardWidget()
        h = QHBoxLayout(group)
        h.setContentsMargins(20, 16, 20, 16)
        self.run_btn = PrimaryPushButton("开始剔除", group)
        self.run_btn.setIcon(FluentIcon.PLAY)
        self.run_btn.setMinimumHeight(40)
        self.run_btn.clicked.connect(self._run)

        self.dark_toggle = TransparentToggleToolButton(FluentIcon.CONSTRACT, group)
        self.dark_toggle.setToolTip("切换深色模式")
        self.dark_toggle.toggled.connect(self._on_dark_toggled)

        h.addWidget(self.run_btn)
        h.addStretch(1)
        h.addWidget(self.dark_toggle)
        return group

    def _on_dark_toggled(self, checked):
        from qfluentwidgets import Theme, setTheme
        setTheme(Theme.DARK if checked else Theme.LIGHT)

    # ------------------------------------------------------------------
    # 浏览
    # ------------------------------------------------------------------
    def _browse_source(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "选择源号码 txt", self.root, "文本文件 (*.txt);;所有 (*)")
        if p:
            self.source_path = p
            self.src_le.setText(p)

    def _browse_txt_output(self, title, current):
        p, _ = QFileDialog.getSaveFileName(self, title, current,
                                           "文本文件 (*.txt)")
        if not p:
            return None
        if not p.lower().endswith('.txt'):
            p += '.txt'
        return p

    def _browse_output(self):
        p = self._browse_txt_output("保存保留号码为", self.output_path)
        if p:
            self.output_path = p
            self.out_le.setText(p)

    def _browse_removed(self):
        p = self._browse_txt_output("保存被剔除号码为", self.removed_path)
        if p:
            self.removed_path = p
            self.removed_le.setText(p)

    # ------------------------------------------------------------------
    # 拖拽
    # ------------------------------------------------------------------
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.toLocalFile()]
        txts = [p for p in paths if p.lower().endswith('.txt')]
        if not txts:
            return
        p = txts[0]
        name = os.path.basename(p).lower()

        for cat, fname in CLASSIFY_FILES.items():
            if name != fname.lower():
                continue
            target = os.path.join(self.root, fname)
            if os.path.abspath(p) != os.path.abspath(target):
                shutil.copy2(p, target)
            self.classify_paths[cat] = target
            self._refresh_status_labels()
            self._show_message(f"已识别分类文件: {fname}")
            InfoBar.success("分类库已更新", fname, parent=self,
                            position=InfoBarPosition.TOP, duration=3000)
            return

        self.source_path = p
        self.src_le.setText(p)
        self._show_message(f"已添加源文件: {os.path.basename(p)}")

    def _refresh_status_labels(self):
        for cat, badge in self.classify_badges.items():
            found = bool(self.classify_paths.get(cat))
            badge.setText("已找到" if found else "未找到")
            badge.set_found(found)

    # ------------------------------------------------------------------
    # 规范化
    # ------------------------------------------------------------------
    def _normalize(self):
        if not self.source_path:
            InfoBar.warning("提示", "请先选择源文件", parent=self,
                            position=InfoBarPosition.TOP, duration=3000)
            return

        base, ext = os.path.splitext(self.source_path)
        norm_path = base + "_normalized" + ext

        self._set_busy(True)
        self.progress.setValue(0)
        self.log_edit.clear()
        self._log(f"开始规范化: {os.path.basename(self.source_path)}")
        self._log(f"输出: {norm_path}")

        self.norm_thread = NormalizeThread(self.source_path, norm_path)
        self._connect_worker(self.norm_thread, self._on_norm_done)
        self._show_message("规范化中...")
        self.norm_thread.start()

    def _on_norm_done(self, r):
        self._show_message(f"规范化完成 ({r['elapsed']:.2f}s)")
        InfoBar.success(
            "规范化完成",
            f"输入 {r['total_in']:,} 行 → 输出 {r['total_out']:,} 个号码，"
            f"耗时 {r['elapsed']:.2f}s",
            parent=self, position=InfoBarPosition.TOP, duration=6000)
        self.source_path = r['output']
        self.src_le.setText(r['output'])
        self._log(f"已切换源文件到: {os.path.basename(r['output'])}")

    # ------------------------------------------------------------------
    # 剔除
    # ------------------------------------------------------------------
    def _run(self):
        if not self.source_path:
            InfoBar.warning("提示", "请选择源文件", parent=self,
                            position=InfoBarPosition.TOP, duration=3000)
            return
        if not any(self.classify_paths.values()):
            InfoBar.warning("提示", "未找到分类库文件", parent=self,
                            position=InfoBarPosition.TOP, duration=3000)
            return

        self.output_path = self._ensure_txt(
            self.out_le.text().strip() or self.output_path)
        self.removed_path = self._ensure_txt(
            self.removed_le.text().strip() or self.removed_path)

        self._set_busy(True)
        self.progress.setValue(0)
        self.log_edit.clear()

        paths = [p for p in self.classify_paths.values() if p]
        self.thread = ProcessThread(
            self.source_path, self.output_path, self.removed_path, paths)
        self._connect_worker(self.thread, self._on_done)
        self._show_message("处理中...")
        self.thread.start()

    @staticmethod
    def _ensure_txt(path):
        return path if path.lower().endswith('.txt') else path + '.txt'

    def _on_done(self, r):
        self._show_message(f"完成 | 耗时 {r['elapsed']:.2f}s")
        InfoBar.success(
            "剔除完成",
            f"总 {r['total']:,}｜保留 {r['kept']:,}｜剔除 {r['removed']:,}｜"
            f"耗时 {r['elapsed']:.2f}s",
            parent=self, position=InfoBarPosition.TOP, duration=8000)
        if r.get('preview'):
            PreviewDialog("保留号码预览（前 100 条）", r['preview'], self).exec()

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    def _connect_worker(self, worker, done_slot):
        """统一连接后台线程信号，并在结束时恢复按钮。"""
        worker.progress.connect(self.progress.setValue)
        worker.log.connect(self._log)
        worker.done.connect(done_slot)
        worker.error.connect(self._on_error)
        worker.finished.connect(lambda: self._set_busy(False))

    def _set_busy(self, busy):
        self.run_btn.setEnabled(not busy)
        self.normalize_btn.setEnabled(not busy)

    def _show_message(self, text):
        self.status_label.setText(text)

    def _log(self, msg):
        self.log_edit.appendPlainText(f"[{datetime.now():%H:%M:%S}] {msg}")

    def _on_error(self, msg):
        self._show_message("失败")
        QMessageBox.critical(self, "错误", str(msg))
