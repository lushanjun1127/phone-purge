"""Fluent 版后台线程：在 PyQt6 / PySide6 下均可用的类型化信号线程。

与 ``phone_filter.gui.threads`` / ``phone_filter.us_filter.threads`` 功能完全相同，
区别仅在于：

- 信号声明使用字符串签名（如 ``pyqtSignal("int")``、``pyqtSignal("PyQt_PyObject")``），
  两种 Qt 绑定语义一致；
- 发射统一走 ``qt_shim.emit``，屏蔽 PyQt6/PySide6 的 API 差异。

业务逻辑仍然完全复用 ``phone_filter.core`` 与 ``phone_filter.us_filter.cleaning``。
"""

import logging
import os
import time
from datetime import datetime

from phone_filter.gui.qt_shim import QThread, pyqtSignal, emit

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据库版（gui）线程
# ---------------------------------------------------------------------------
class WorkerThread(QThread):
    """通用后台任务线程基类：进度 / 日志 / 完成 / 错误 四个信号。"""

    progress_updated = pyqtSignal("int")
    log_updated = pyqtSignal(str)
    finished_result = pyqtSignal("PyQt_PyObject")
    error_occurred = pyqtSignal(str)


class ProcessingThread(WorkerThread):
    """源文件剔除处理线程（数据库版）。"""

    def __init__(self, processor, source_file, output_file, output_format,
                 similarity_threshold, fuzzy_mode, parent=None):
        super().__init__(parent)
        self.processor = processor
        self.source_file = source_file
        self.output_file = output_file
        self.output_format = output_format
        self.similarity_threshold = similarity_threshold
        self.fuzzy_mode = fuzzy_mode

    def run(self):
        try:
            emit(self.log_updated, f"开始处理源文件: {self.source_file}\n")
            result = self.processor.process_and_filter(
                source_file_path=self.source_file,
                output_file=self.output_file,
                output_format=self.output_format,
                similarity_threshold=self.similarity_threshold,
                fuzzy_mode=self.fuzzy_mode,
                progress_callback=lambda p: emit(self.progress_updated, p),
                log_callback=lambda m: emit(self.log_updated, m),
            )
            emit(self.finished_result, result)
        except Exception as e:
            logger.exception("处理线程异常")
            emit(self.error_occurred, str(e))


class ImportNumbersThread(WorkerThread):
    """向指定分类库导入号码的线程（数据库版）。"""

    def __init__(self, processor, file_path, category, parent=None):
        super().__init__(parent)
        self.processor = processor
        self.file_path = file_path
        self.category = category

    def run(self):
        try:
            emit(self.progress_updated, 10)
            old = self.processor.get_category_count(self.category)
            emit(self.progress_updated, 30)

            tag = f"{self.category}_{datetime.now():%Y%m%d_%H%M%S}"
            self.processor.load_numbers_from_generic(
                self.file_path, source_tag=tag, category=self.category)

            new = self.processor.get_category_count(self.category)
            emit(self.progress_updated, 100)
            emit(self.log_updated,
                 f"向 {self.category} 库导入完成：原有 {old}，现有 {new}，"
                 f"新增 {new - old}\n")
            emit(self.finished_result, (self.category, old, new))
        except Exception as e:
            logger.exception("导入线程异常")
            emit(self.error_occurred, str(e))


# ---------------------------------------------------------------------------
# 美国高速版（us_filter）线程
# ---------------------------------------------------------------------------
class BaseWorkerThread(QThread):
    """带 进度/日志/完成/错误 信号的后台线程基类（高速版）。"""

    progress = pyqtSignal("int")
    log = pyqtSignal(str)
    done = pyqtSignal("PyQt_PyObject")
    error = pyqtSignal(str)


class ProcessThread(BaseWorkerThread):
    """完整剔除流程：加载分类库 → 解析源文件 → 剔除并输出。"""

    def __init__(self, source_path, output_path, removed_path, classify_paths,
                 parent=None):
        super().__init__(parent)
        self.source_path = source_path
        self.output_path = output_path
        self.removed_path = removed_path
        self.classify_paths = classify_paths

    def run(self):
        from phone_filter.us_filter import cleaning

        try:
            t_start = time.time()

            emit(self.log, "加载分类库...")
            t0 = time.time()
            classify_set = cleaning.load_classify_set(self.classify_paths)
            emit(self.log,
                 f"  → {len(classify_set):,} 个分类号码 ({time.time()-t0:.2f}s)")
            emit(self.progress, 15)

            emit(self.log, "解析源文件...")
            t0 = time.time()
            source_arr = cleaning.parse_source(self.source_path)
            emit(self.log,
                 f"  → {len(source_arr):,} 个源号码 ({time.time()-t0:.2f}s)")
            emit(self.progress, 70)

            emit(self.log, "剔除...")
            t0 = time.time()
            total, kept, removed, preview = cleaning.filter_arrays(
                source_arr, classify_set, self.output_path, self.removed_path)
            emit(self.log,
                 f"  → 保留 {kept:,}，剔除 {removed:,} ({time.time()-t0:.2f}s)")
            emit(self.progress, 100)

            elapsed = time.time() - t_start
            emit(self.log, f"✅ 完成！总耗时 {elapsed:.2f} 秒")

            emit(self.done, {
                'total': total, 'kept': kept, 'removed': removed,
                'output': self.output_path,
                'removed_output': self.removed_path,
                'preview': preview,
                'elapsed': elapsed,
            })
        except Exception as e:
            logger.exception("处理失败")
            emit(self.error, str(e))


class NormalizeThread(BaseWorkerThread):
    """规范化线程：把任意格式 txt 规范为每行一个 11 位号码。"""

    def __init__(self, input_path, output_path, parent=None):
        super().__init__(parent)
        self.input_path = input_path
        self.output_path = output_path

    def run(self):
        from phone_filter.us_filter import cleaning

        try:
            t0 = time.time()
            emit(self.log, f"规范化: {os.path.basename(self.input_path)}")
            total_in, total_out = cleaning.normalize_txt(
                self.input_path, self.output_path,
                progress_cb=lambda n: emit(
                    self.progress, min(99, n // 100_000)),
                log_cb=lambda m: emit(self.log, m),
            )
            elapsed = time.time() - t0
            emit(self.progress, 100)
            emit(self.log, f"✅ 规范化完成 ({elapsed:.2f}s)")
            emit(self.done, {
                'input': self.input_path,
                'output': self.output_path,
                'total_in': total_in,
                'total_out': total_out,
                'elapsed': elapsed,
            })
        except Exception as e:
            logger.exception("规范化失败")
            emit(self.error, str(e))
