"""后台工作线程：号码处理与分类导入（数据库版 GUI 专用）。

统一了进度/日志/完成/错误四个信号，消除原代码中两个线程类的重复样板。
"""

import logging
from datetime import datetime

from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)


class WorkerThread(QThread):
    """通用后台任务线程基类。

    子类实现 ``run``，通过信号向 GUI 汇报进度、日志、结果与错误。
    """

    progress_updated = pyqtSignal(int)
    log_updated = pyqtSignal(str)
    finished = pyqtSignal(object)
    error_occurred = pyqtSignal(str)


class ProcessingThread(WorkerThread):
    """源文件剔除处理线程。"""

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
            self.log_updated.emit(f"开始处理源文件: {self.source_file}\n")
            result = self.processor.process_and_filter(
                source_file_path=self.source_file,
                output_file=self.output_file,
                output_format=self.output_format,
                similarity_threshold=self.similarity_threshold,
                fuzzy_mode=self.fuzzy_mode,
                progress_callback=self.progress_updated.emit,
                log_callback=self.log_updated.emit,
            )
            self.finished.emit(result)
        except Exception as e:
            logger.exception("处理线程异常")
            self.error_occurred.emit(str(e))


class ImportNumbersThread(WorkerThread):
    """向指定分类库导入号码的线程。"""

    def __init__(self, processor, file_path, category, parent=None):
        super().__init__(parent)
        self.processor = processor
        self.file_path = file_path
        self.category = category

    def run(self):
        try:
            self.progress_updated.emit(10)
            old = self.processor.get_category_count(self.category)
            self.progress_updated.emit(30)

            tag = f"{self.category}_{datetime.now():%Y%m%d_%H%M%S}"
            self.processor.load_numbers_from_generic(
                self.file_path, source_tag=tag, category=self.category)

            new = self.processor.get_category_count(self.category)
            self.progress_updated.emit(100)
            self.log_updated.emit(
                f"向 {self.category} 库导入完成：原有 {old}，现有 {new}，新增 {new - old}\n")
            self.finished.emit((self.category, old, new))
        except Exception as e:
            logger.exception("导入线程异常")
            self.error_occurred.emit(str(e))
