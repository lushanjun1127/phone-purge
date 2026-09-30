"""美国号码高速剔除器：后台线程（GUI 与清洗逻辑之间的桥接层）。

统一了 ProcessThread / NormalizeThread 的信号样板。
"""

import logging
import os
import time

from PyQt6.QtCore import QThread, pyqtSignal

from phone_filter.us_filter import cleaning

logger = logging.getLogger(__name__)


class BaseWorkerThread(QThread):
    """带 进度/日志/完成/错误 信号的后台线程基类。"""

    progress = pyqtSignal(int)
    log = pyqtSignal(str)
    done = pyqtSignal(dict)
    error = pyqtSignal(str)


class ProcessThread(BaseWorkerThread):
    """完整剔除流程：加载分类库 → 解析源文件 → 剔除并输出。"""

    def __init__(self, source_path, output_path, removed_path, classify_paths, parent=None):
        super().__init__(parent)
        self.source_path = source_path
        self.output_path = output_path
        self.removed_path = removed_path
        self.classify_paths = classify_paths

    def run(self):
        try:
            t_start = time.time()

            self.log.emit("加载分类库...")
            t0 = time.time()
            classify_set = cleaning.load_classify_set(self.classify_paths)
            self.log.emit(f"  → {len(classify_set):,} 个分类号码 ({time.time()-t0:.2f}s)")
            self.progress.emit(15)

            self.log.emit("解析源文件...")
            t0 = time.time()
            source_arr = cleaning.parse_source(self.source_path)
            self.log.emit(f"  → {len(source_arr):,} 个源号码 ({time.time()-t0:.2f}s)")
            self.progress.emit(70)

            self.log.emit("剔除...")
            t0 = time.time()
            total, kept, removed, preview = cleaning.filter_arrays(
                source_arr, classify_set, self.output_path, self.removed_path)
            self.log.emit(f"  → 保留 {kept:,}，剔除 {removed:,} ({time.time()-t0:.2f}s)")
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


class NormalizeThread(BaseWorkerThread):
    """规范化线程：把任意格式 txt 规范为每行一个 11 位号码。"""

    def __init__(self, input_path, output_path, parent=None):
        super().__init__(parent)
        self.input_path = input_path
        self.output_path = output_path

    def run(self):
        try:
            t0 = time.time()
            self.log.emit(f"规范化: {os.path.basename(self.input_path)}")
            total_in, total_out = cleaning.normalize_txt(
                self.input_path, self.output_path,
                progress_cb=lambda n: self.progress.emit(min(99, n // 100_000)),
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
