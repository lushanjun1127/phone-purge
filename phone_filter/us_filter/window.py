"""美国号码高速剔除器：主窗口。

界面层：仅负责布局与交互，清洗/剔除逻辑在 ``phone_filter.us_filter.cleaning``，
耗时任务在 ``phone_filter.us_filter.threads``。
"""

import logging
import os
import shutil
from datetime import datetime

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFileDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QStatusBar, QTextEdit, QVBoxLayout,
    QWidget,
)

from phone_filter.us_filter.cleaning import app_root_dir
from phone_filter.us_filter.dialogs import PreviewDialog
from phone_filter.us_filter.threads import NormalizeThread, ProcessThread

logger = logging.getLogger(__name__)

# 分类库文件名（位于应用根目录）
CLASSIFY_FILES = {
    'invalid': 'invalid_customers.txt',
    'valid': 'valid_customers.txt',
    'opened': 'opened_customers.txt',
}


def _button_style(color, padding=10, font_size=14):
    return (f"QPushButton{{background:{color};color:#fff;padding:{padding}px;"
            f"font-size:{font_size}px;border-radius:6px;font-weight:bold;}}")


class FilterWindow(QMainWindow):
    """单窗口：状态 → 源文件 → 输出 → 规范化 → 剔除。支持拖拽 txt。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("号码剔除器 v5.2 - 美国号码全自动版")
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
        layout.addWidget(self._build_status_group())
        layout.addWidget(self._build_source_group())
        layout.addWidget(self._build_output_group())
        layout.addWidget(self._build_action_group())
        layout.addWidget(self._build_run_group())

        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setFont(QFont("Consolas", 10))
        layout.addWidget(self.log_edit)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("就绪 | 拖拽源 txt 到窗口即可")

        self.norm_thread = None
        self.thread = None

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _build_status_group(self):
        group = QGroupBox(f"分类库（根目录: {self.root}）")
        v = QVBoxLayout(group)
        self.classify_labels = {}
        for cat, fname in CLASSIFY_FILES.items():
            h = QHBoxLayout()
            h.addWidget(QLabel(f"● {fname}"))
            lbl = QLabel()
            self.classify_labels[cat] = lbl
            h.addWidget(lbl)
            h.addStretch()
            v.addLayout(h)
        self._refresh_status_labels()
        return group

    def _build_source_group(self):
        group = QGroupBox("源号码文件（拖拽或点击浏览）")
        h = QHBoxLayout(group)
        self.src_le = QLineEdit()
        self.src_le.setPlaceholderText("拖拽 txt 到此，或点浏览选择")
        browse_btn = QPushButton("浏览")
        browse_btn.clicked.connect(self._browse_source)
        h.addWidget(self.src_le)
        h.addWidget(browse_btn)
        return group

    def _build_output_group(self):
        group = QGroupBox("输出文件")
        v = QVBoxLayout(group)
        self.out_le = self._output_row(v, "保留号码:", self.output_path, self._browse_output)
        self.removed_le = self._output_row(v, "被剔除号码:", self.removed_path, self._browse_removed)
        return group

    def _output_row(self, parent_layout, label_text, default_path, browse_slot):
        h = QHBoxLayout()
        h.addWidget(QLabel(label_text))
        edit = QLineEdit(default_path)
        btn = QPushButton("另存为")
        btn.clicked.connect(browse_slot)
        h.addWidget(edit)
        h.addWidget(btn)
        parent_layout.addLayout(h)
        return edit

    def _build_action_group(self):
        group = QGroupBox("规范化（可选，处理前统一格式）")
        h = QHBoxLayout(group)
        self.normalize_btn = QPushButton("一键规范成 11 位（美国格式）")
        self.normalize_btn.setStyleSheet(_button_style("#FF9800"))
        self.normalize_btn.clicked.connect(self._normalize)
        tip = QLabel("规范化后会自动切换到新文件")
        tip.setStyleSheet("color: gray; font-size: 12px;")
        h.addWidget(self.normalize_btn)
        h.addWidget(tip)
        h.addStretch()
        return group

    def _build_run_group(self):
        group = QGroupBox("操作")
        h = QHBoxLayout(group)
        self.run_btn = QPushButton("开始剔除")
        self.run_btn.setStyleSheet(_button_style("#4CAF50", padding=14, font_size=16))
        self.run_btn.clicked.connect(self._run)
        h.addWidget(self.run_btn)
        return group

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
        p, _ = QFileDialog.getSaveFileName(self, title, current, "文本文件 (*.txt)")
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
            self.statusBar().showMessage(f"已识别分类文件: {fname}")
            return

        self.source_path = p
        self.src_le.setText(p)
        self.statusBar().showMessage(f"已添加源文件: {os.path.basename(p)}")

    def _refresh_status_labels(self):
        """按当前 classify_paths 重建状态标签。"""
        for cat, lbl in self.classify_labels.items():
            found = bool(self.classify_paths.get(cat))
            lbl.setText("✅ 已找到" if found else "❌ 未找到")
            lbl.setStyleSheet(
                f"color:{'green' if found else 'red'}; font-weight: bold;")

    # ------------------------------------------------------------------
    # 规范化
    # ------------------------------------------------------------------
    def _normalize(self):
        if not self.source_path:
            QMessageBox.warning(self, "提示", "请先选择源文件")
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
        self.statusBar().showMessage("规范化中...")
        self.norm_thread.start()

    def _on_norm_done(self, r):
        self.statusBar().showMessage(f"规范化完成 ({r['elapsed']:.2f}s)")
        QMessageBox.information(
            self, "规范化完成",
            f"输入行数: {r['total_in']:,}\n"
            f"输出号码: {r['total_out']:,}\n"
            f"耗时: {r['elapsed']:.2f} 秒\n\n"
            f"输出文件: {r['output']}")
        self.source_path = r['output']
        self.src_le.setText(r['output'])
        self._log(f"已切换源文件到: {os.path.basename(r['output'])}")

    # ------------------------------------------------------------------
    # 剔除
    # ------------------------------------------------------------------
    def _run(self):
        if not self.source_path:
            QMessageBox.warning(self, "提示", "请选择源文件")
            return
        if not any(self.classify_paths.values()):
            QMessageBox.warning(self, "提示", "未找到分类库文件")
            return

        self.output_path = self._ensure_txt(self.out_le.text().strip() or self.output_path)
        self.removed_path = self._ensure_txt(self.removed_le.text().strip() or self.removed_path)

        self._set_busy(True)
        self.progress.setValue(0)
        self.log_edit.clear()

        paths = [p for p in self.classify_paths.values() if p]
        self.thread = ProcessThread(
            self.source_path, self.output_path, self.removed_path, paths)
        self._connect_worker(self.thread, self._on_done)
        self.statusBar().showMessage("处理中...")
        self.thread.start()

    @staticmethod
    def _ensure_txt(path):
        return path if path.lower().endswith('.txt') else path + '.txt'

    def _on_done(self, r):
        self.statusBar().showMessage(f"完成 | 耗时 {r['elapsed']:.2f}s")
        QMessageBox.information(
            self, "完成",
            f"总：{r['total']:,}\n"
            f"保留：{r['kept']:,}\n"
            f"剔除：{r['removed']:,}\n"
            f"耗时：{r['elapsed']:.2f} 秒\n\n"
            f"保留文件：{r['output']}\n"
            f"剔除文件：{r['removed_output']}")
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

    def _log(self, msg):
        self.log_edit.append(f"[{datetime.now():%H:%M:%S}] {msg}")

    def _on_error(self, msg):
        self.statusBar().showMessage("失败")
        QMessageBox.critical(self, "错误", msg)
