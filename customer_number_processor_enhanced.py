import sys
import os
import re
import logging
import time
from datetime import datetime

import numpy as np

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QPushButton, QLineEdit, QLabel, QFileDialog,
    QMessageBox, QTextEdit, QProgressBar, QStatusBar,
    QDialog, QDialogButtonBox, QPlainTextEdit
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# ============================================================
# 根目录
# ============================================================
def app_root_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


CLASSIFY_FILES = {
    'invalid': 'invalid_customers.txt',
    'valid':   'valid_customers.txt',
    'opened':  'opened_customers.txt',
}


# ============================================================
# 号码清洗
# ============================================================
_NON_DIGIT = re.compile(r'\D')
_SPLIT = re.compile(r'[,\s;]+')


def clean_one(s):
    """清洗为纯数字（美国 11 位，1 前缀保留）"""
    if s is None:
        return ''
    return _NON_DIGIT.sub('', str(s))


def normalize_us_number(d):
    """
    规范化为美国 11 位格式：
    - 10 位 → 前面补 1
    - 11 位 → 保留
    - 其他 → 保留原样
    """
    if not d:
        return ''
    if len(d) == 10:
        return '1' + d
    return d


def _detect_encoding(path):
    with open(path, 'rb') as f:
        head = f.read(8192)
    for enc in ('utf-8', 'utf-8-sig', 'gbk'):
        try:
            head.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return 'utf-8'


# ============================================================
# 读分类库
# ============================================================
def load_classify_set(paths):
    s = set()
    for path in paths:
        if not path or not os.path.exists(path):
            continue
        enc = _detect_encoding(path)
        with open(path, 'r', encoding=enc, errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if line.isdigit():
                    s.add(int(line))
                else:
                    for raw in _SPLIT.split(line):
                        if raw:
                            d = clean_one(raw)
                            if d and d.isdigit():
                                s.add(int(d))
    return s


# ============================================================
# 极速解析源 txt
# ============================================================
def parse_source_fast(path):
    with open(path, 'rb') as f:
        raw = np.frombuffer(f.read(), dtype=np.uint8)

    is_digit = (raw >= 48) & (raw <= 57)
    is_clean = is_digit | (raw == 10) | (raw == 13) | (raw == 32)

    if not is_clean.all():
        logger.info("源文件含非数字字符，走慢速路径")
        return _parse_source_slow(path)

    digit_count = int(is_digit.sum())
    if digit_count == 0:
        return np.empty(0, dtype=np.int64)

    newline_count = int((raw == 10).sum())
    if digit_count % 11 == 0 and newline_count > 0:
        expected_lines = digit_count // 11
        if abs(newline_count - expected_lines) <= 1:
            logger.info("源文件为标准 11 位格式，走极速路径")
            return _parse_11digit_fast(raw, is_digit)

    logger.info("源文件长度不固定，走慢速路径")
    return _parse_source_slow(path)


def _parse_11digit_fast(raw, is_digit):
    digits = raw[is_digit]
    n = len(digits) // 11
    digits = digits[:n * 11].reshape(n, 11).astype(np.int64) - 48
    weights = (10 ** np.arange(10, -1, -1)).astype(np.int64)
    return digits @ weights


def _parse_source_slow(path):
    enc = _detect_encoding(path)
    buf_int = []
    BATCH = 500_000
    chunks = []

    def flush():
        if buf_int:
            chunks.append(np.array(buf_int, dtype=np.int64))
            buf_int.clear()

    with open(path, 'r', encoding=enc, errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.isdigit():
                buf_int.append(int(line))
            else:
                for raw in _SPLIT.split(line):
                    if raw:
                        d = clean_one(raw)
                        if d and d.isdigit():
                            buf_int.append(int(d))
            if len(buf_int) >= BATCH:
                flush()

    flush()
    if not chunks:
        return np.empty(0, dtype=np.int64)
    return np.concatenate(chunks)


# ============================================================
# 剔除
# ============================================================
def filter_arrays(source_arr, classify_set, output_path, removed_path=None):
    """
    返回 (total, kept, removed, preview)
    同时输出保留号码文件和剔除号码文件
    """
    if len(source_arr) == 0:
        open(output_path, 'w').close()
        if removed_path:
            open(removed_path, 'w').close()
        return 0, 0, 0, []

    src_list = source_arr.tolist()
    kept = []
    removed = []
    preview = []
    PREVIEW_LIMIT = 100

    for x in src_list:
        if x in classify_set:
            removed.append(x)
        else:
            if len(preview) < PREVIEW_LIMIT:
                preview.append(str(x))
            kept.append(x)

    # 写保留文件
    with open(output_path, 'w', encoding='utf-8',
              buffering=8 << 20) as f:
        BUF = 200_000
        for i in range(0, len(kept), BUF):
            chunk = kept[i:i+BUF]
            f.write('\n'.join(map(str, chunk)))
            f.write('\n')

    # 写剔除文件（可选）
    if removed_path and removed:
        with open(removed_path, 'w', encoding='utf-8',
                  buffering=8 << 20) as f:
            BUF = 200_000
            for i in range(0, len(removed), BUF):
                chunk = removed[i:i+BUF]
                f.write('\n'.join(map(str, chunk)))
                f.write('\n')

    return len(src_list), len(kept), len(removed), preview


# ============================================================
# 规范化
# ============================================================
def normalize_txt(input_path, output_path, progress_cb=None, log_cb=None):
    """把任意格式 txt 规范化为'每行一个 11 位数字'"""
    enc = _detect_encoding(input_path)
    total_input = 0
    valid_output = 0
    skipped = 0

    BUF = 200_000
    buf = []

    def flush(f):
        nonlocal valid_output
        if buf:
            f.write('\n'.join(buf))
            f.write('\n')
            valid_output += len(buf)
            buf.clear()

    with open(input_path, 'r', encoding=enc, errors='ignore') as fin, \
         open(output_path, 'w', encoding='utf-8',
              buffering=8 << 20) as fout:

        for line in fin:
            line = line.strip()
            if not line:
                continue
            for raw in _SPLIT.split(line):
                if not raw:
                    continue
                total_input += 1
                d = clean_one(raw)
                if not d:
                    skipped += 1
                    continue
                d = normalize_us_number(d)
                if 10 <= len(d) <= 15:
                    buf.append(d)
                else:
                    skipped += 1

                if len(buf) >= BUF:
                    flush(fout)
                    if progress_cb:
                        progress_cb(total_input)

        flush(fout)

    if log_cb:
        log_cb(f"  输入行数: {total_input:,}")
        log_cb(f"  输出号码: {valid_output:,}")
        log_cb(f"  跳过异常: {skipped:,}")

    return total_input, valid_output


# ============================================================
# 处理线程
# ============================================================
class ProcessThread(QThread):
    progress = pyqtSignal(int)
    log = pyqtSignal(str)
    done = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, source_path, output_path, removed_path, classify_paths):
        super().__init__()
        self.source_path = source_path
        self.output_path = output_path
        self.removed_path = removed_path
        self.classify_paths = classify_paths

    def run(self):
        try:
            t_start = time.time()

            self.log.emit("加载分类库...")
            t0 = time.time()
            classify_set = load_classify_set(self.classify_paths)
            self.log.emit(
                f"  → {len(classify_set):,} 个分类号码 ({time.time()-t0:.2f}s)")
            self.progress.emit(15)

            self.log.emit("解析源文件...")
            t0 = time.time()
            source_arr = parse_source_fast(self.source_path)
            self.log.emit(
                f"  → {len(source_arr):,} 个源号码 ({time.time()-t0:.2f}s)")
            self.progress.emit(70)

            self.log.emit("剔除...")
            t0 = time.time()
            total, kept, removed, preview = filter_arrays(
                source_arr, classify_set,
                self.output_path, self.removed_path)
            self.log.emit(
                f"  → 保留 {kept:,}，剔除 {removed:,} ({time.time()-t0:.2f}s)")
            self.progress.emit(100)

            elapsed = time.time() - t_start
            self.log.emit(f"✅ 完成！总耗时 {elapsed:.2f} 秒")

            self.done.emit({
                'total': total, 'kept': kept, 'removed': removed,
                'output': self.output_path,
                'removed_output': self.removed_path,
                'preview': preview,
                'elapsed': elapsed,
            })
        except Exception as e:
            logger.exception("处理失败")
            self.error.emit(str(e))


class NormalizeThread(QThread):
    progress = pyqtSignal(int)
    log = pyqtSignal(str)
    done = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, input_path, output_path):
        super().__init__()
        self.input_path = input_path
        self.output_path = output_path

    def run(self):
        try:
            t0 = time.time()
            self.log.emit(f"规范化: {os.path.basename(self.input_path)}")
            total_in, total_out = normalize_txt(
                self.input_path, self.output_path,
                progress_cb=lambda n: self.progress.emit(
                    min(99, n // 100_000)),
                log_cb=self.log.emit,
            )
            elapsed = time.time() - t0
            self.progress.emit(100)
            self.log.emit(f"✅ 规范化完成 ({elapsed:.2f}s)")
            self.done.emit({
                'input': self.input_path,
                'output': self.output_path,
                'total_in': total_in,
                'total_out': total_out,
                'elapsed': elapsed,
            })
        except Exception as e:
            logger.exception("规范化失败")
            self.error.emit(str(e))


# ============================================================
# 预览对话框
# ============================================================
class PreviewDialog(QDialog):
    def __init__(self, title, numbers, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(450, 600)
        v = QVBoxLayout(self)
        v.addWidget(QLabel(f"共 {len(numbers)} 条（最多显示前 100 条）："))
        txt = QPlainTextEdit()
        txt.setReadOnly(True)
        txt.setFont(QFont("Consolas", 11))
        txt.setPlainText('\n'.join(numbers) if numbers else "(无)")
        v.addWidget(txt)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(self.accept)
        v.addWidget(bb)


# ============================================================
# 主窗口
# ============================================================
class FilterWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("号码剔除器 v5.2 - 美国号码全自动版")
        self.resize(1000, 820)

        self.root = app_root_dir()
        logger.info(f"根目录: {self.root}")

        self.classify_paths = {}
        for cat, fname in CLASSIFY_FILES.items():
            p = os.path.join(self.root, fname)
            self.classify_paths[cat] = p if os.path.exists(p) else None

        self.source_path = ""
        self.output_path = os.path.join(self.root, "结果.txt")
        self.removed_path = os.path.join(self.root, "被剔除.txt")

        self.setAcceptDrops(True)

        central = QWidget()
        self.setCentralWidget(central)
        v = QVBoxLayout(central)

        v.addWidget(self._status_group())
        v.addWidget(self._source_group())
        v.addWidget(self._output_group())
        v.addWidget(self._action_group())
        v.addWidget(self._run_group())

        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setFont(QFont("Consolas", 10))
        v.addWidget(self.log_edit)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        v.addWidget(self.progress)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("就绪 | 拖拽源 txt 到窗口即可")

    # ---------- UI ----------
    def _status_group(self):
        g = QGroupBox(f"分类库（根目录: {self.root}）")
        v = QVBoxLayout(g)
        for cat, fname in CLASSIFY_FILES.items():
            h = QHBoxLayout()
            h.addWidget(QLabel(f"● {fname}"))
            p = self.classify_paths[cat]
            lbl = QLabel("✅ 已找到" if p else "❌ 未找到")
            lbl.setStyleSheet(
                f"color:{'green' if p else 'red'}; font-weight: bold;")
            h.addWidget(lbl)
            h.addStretch()
            v.addLayout(h)
        return g

    def _source_group(self):
        g = QGroupBox("源号码文件（拖拽或点击浏览）")
        h = QHBoxLayout(g)
        self.src_le = QLineEdit()
        self.src_le.setPlaceholderText("拖拽 txt 到此，或点浏览选择")
        h.addWidget(self.src_le)
        b = QPushButton("浏览")
        b.clicked.connect(self._browse_source)
        h.addWidget(b)
        return g

    def _output_group(self):
        g = QGroupBox("输出文件")
        v = QVBoxLayout(g)

        h1 = QHBoxLayout()
        h1.addWidget(QLabel("保留号码:"))
        self.out_le = QLineEdit(self.output_path)
        h1.addWidget(self.out_le)
        b1 = QPushButton("另存为")
        b1.clicked.connect(self._browse_output)
        h1.addWidget(b1)
        v.addLayout(h1)

        h2 = QHBoxLayout()
        h2.addWidget(QLabel("被剔除号码:"))
        self.removed_le = QLineEdit(self.removed_path)
        h2.addWidget(self.removed_le)
        b2 = QPushButton("另存为")
        b2.clicked.connect(self._browse_removed)
        h2.addWidget(b2)
        v.addLayout(h2)

        return g

    def _action_group(self):
        g = QGroupBox("规范化（可选，处理前统一格式）")
        v = QVBoxLayout(g)
        h = QHBoxLayout(g)

        self.normalize_btn = QPushButton("一键规范成 11 位（美国格式）")
        self.normalize_btn.setStyleSheet(
            "QPushButton{background:#FF9800;color:#fff;padding:10px;"
            "font-size:14px;border-radius:6px;font-weight:bold;}")
        self.normalize_btn.clicked.connect(self._normalize)
        h.addWidget(self.normalize_btn)

        tip = QLabel("规范化后会自动切换到新文件")
        tip.setStyleSheet("color: gray; font-size: 12px;")
        h.addWidget(tip)
        h.addStretch()
        v.addLayout(h)
        return g

    def _run_group(self):
        g = QGroupBox("操作")
        h = QHBoxLayout(g)
        self.run_btn = QPushButton("开始剔除")
        self.run_btn.setStyleSheet(
            "QPushButton{background:#4CAF50;color:#fff;padding:14px;"
            "font-size:16px;border-radius:6px;font-weight:bold;}")
        self.run_btn.clicked.connect(self._run)
        h.addWidget(self.run_btn)
        return g

    # ---------- 浏览 ----------
    def _browse_source(self):
        p, _ = QFileDialog.getOpenFileName(
            self, "选择源号码 txt", self.root,
            "文本文件 (*.txt);;所有 (*)")
        if p:
            self.source_path = p
            self.src_le.setText(p)

    def _browse_output(self):
        p, _ = QFileDialog.getSaveFileName(
            self, "保存保留号码为", self.output_path,
            "文本文件 (*.txt)")
        if p:
            if not p.lower().endswith('.txt'):
                p += '.txt'
            self.output_path = p
            self.out_le.setText(p)

    def _browse_removed(self):
        p, _ = QFileDialog.getSaveFileName(
            self, "保存被剔除号码为", self.removed_path,
            "文本文件 (*.txt)")
        if p:
            if not p.lower().endswith('.txt'):
                p += '.txt'
            self.removed_path = p
            self.removed_le.setText(p)

    # ---------- 拖拽 ----------
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls()
                 if u.toLocalFile()]
        txts = [p for p in paths if p.lower().endswith('.txt')]
        if not txts:
            return
        p = txts[0]
        name = os.path.basename(p).lower()
        for cat, fname in CLASSIFY_FILES.items():
            if name == fname.lower():
                import shutil
                target = os.path.join(self.root, fname)
                if os.path.abspath(p) != os.path.abspath(target):
                    shutil.copy2(p, target)
                self.classify_paths[cat] = target
                self._refresh_status_labels()
                self.statusBar().showMessage(f"已识别分类文件: {fname}")
                return
        self.source_path = p
        self.src_le.setText(p)
        self.statusBar().showMessage(
            f"已添加源文件: {os.path.basename(p)}")

    def _refresh_status_labels(self):
        # 重新扫描根目录（简单起见重建整个状态组）
        pass

    # ---------- 规范化 ----------
    def _normalize(self):
        if not self.source_path:
            QMessageBox.warning(self, "提示", "请先选择源文件")
            return

        base, ext = os.path.splitext(self.source_path)
        norm_path = base + "_normalized" + ext

        self.normalize_btn.setEnabled(False)
        self.run_btn.setEnabled(False)
        self.progress.setValue(0)
        self.log_edit.clear()
        self._log(f"开始规范化: {os.path.basename(self.source_path)}")
        self._log(f"输出: {norm_path}")

        self.norm_thread = NormalizeThread(self.source_path, norm_path)
        self.norm_thread.progress.connect(self.progress.setValue)
        self.norm_thread.log.connect(self._log)
        self.norm_thread.done.connect(self._on_norm_done)
        self.norm_thread.error.connect(self._on_error)
        self.norm_thread.finished.connect(
            lambda: (self.normalize_btn.setEnabled(True),
                     self.run_btn.setEnabled(True)))
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

    # ---------- 剔除 ----------
    def _run(self):
        if not self.source_path:
            QMessageBox.warning(self, "提示", "请选择源文件")
            return
        if not any(self.classify_paths.values()):
            QMessageBox.warning(self, "提示", "未找到分类库文件")
            return

        self.output_path = self.out_le.text().strip() or self.output_path
        if not self.output_path.lower().endswith('.txt'):
            self.output_path += '.txt'

        self.removed_path = self.removed_le.text().strip() or self.removed_path
        if not self.removed_path.lower().endswith('.txt'):
            self.removed_path += '.txt'

        self.run_btn.setEnabled(False)
        self.normalize_btn.setEnabled(False)
        self.progress.setValue(0)
        self.log_edit.clear()

        paths = [p for p in self.classify_paths.values() if p]

        self.thread = ProcessThread(
            self.source_path, self.output_path,
            self.removed_path, paths)
        self.thread.progress.connect(self.progress.setValue)
        self.thread.log.connect(self._log)
        self.thread.done.connect(self._on_done)
        self.thread.error.connect(self._on_error)
        self.thread.finished.connect(
            lambda: (self.run_btn.setEnabled(True),
                     self.normalize_btn.setEnabled(True)))

        self.statusBar().showMessage("处理中...")
        self.thread.start()

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
            PreviewDialog("保留号码预览（前 100 条）",
                          r['preview'], self).exec()

    # ---------- 工具 ----------
    def _log(self, msg):
        self.log_edit.append(f"[{datetime.now():%H:%M:%S}] {msg}")

    def _on_error(self, msg):
        self.statusBar().showMessage("失败")
        QMessageBox.critical(self, "错误", msg)


def main():
    app = QApplication(sys.argv)
    w = FilterWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
