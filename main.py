import sys
import pandas as pd
import numpy as np
from datetime import datetime
import re
import os
import logging
import sqlite3
import hashlib
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QTabWidget, QGroupBox, QPushButton, QLineEdit, QLabel, QComboBox,
                             QFileDialog, QMessageBox, QTextEdit, QProgressBar, QStatusBar,
                             QMenuBar, QMenu, QFrame, QGridLayout, QSlider, QCheckBox, QListWidget,
                             QDialog, QDialogButtonBox, QSpinBox, QDoubleSpinBox)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QIcon, QPixmap, QAction

# 可选依赖
try:
    import phonenumbers
    HAS_PHONENUMBERS = True
except ImportError:
    HAS_PHONENUMBERS = False

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# ============================================================
# 号码标准化与相似度
# ============================================================
class PhoneNumberNormalizer:
    """号码标准化处理类"""

    @staticmethod
    def normalize_to_e164(phone_number, region='CN'):
        """将电话号码标准化为E.164格式"""
        if phone_number is None:
            return ''
        if isinstance(phone_number, float) and pd.isna(phone_number):
            return ''
        s = str(phone_number).strip()
        if not s:
            return ''

        # 已经是 E.164 格式
        if s.startswith('+'):
            digits = re.sub(r'\D', '', s)
            return '+' + digits if digits else ''

        digits = re.sub(r'\D', '', s)
        if not digits:
            return ''

        # 优先使用 phonenumbers
        if HAS_PHONENUMBERS:
            try:
                parsed = phonenumbers.parse(s, region)
                if phonenumbers.is_valid_number(parsed):
                    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
            except phonenumbers.NumberParseException:
                pass

        # 回退：简单加区号
        if digits.startswith('00'):
            return '+' + digits[2:]
        if len(digits) == 11 and digits.startswith('1'):
            return '+86' + digits
        if len(digits) == 10:
            return '+86' + digits
        if len(digits) > 11:
            return '+' + digits
        return digits

    @staticmethod
    def calculate_similarity(num1, num2):
        """计算两个号码的相似度 (0-1)
        使用改进的 Levenshtein + 后缀匹配加权
        """
        if num1 == num2:
            return 1.0

        d1 = re.sub(r'\D', '', str(num1))
        d2 = re.sub(r'\D', '', str(num2))

        if not d1 or not d2:
            return 0.0

        # 长度差异过大直接剪枝
        if abs(len(d1) - len(d2)) > 4:
            return 0.0

        # 后缀匹配加权（电话最重要）
        min_len = min(len(d1), len(d2))
        if min_len >= 4:
            suffix = min(8, min_len)
            if d1[-suffix:] == d2[-suffix:]:
                # 后缀完全相同，基础分 0.9
                base = 0.9
            else:
                base = 0.0
        else:
            base = 0.0

        # 编辑距离
        lev = PhoneNumberNormalizer._levenshtein(d1, d2)
        max_len = max(len(d1), len(d2))
        lev_sim = 1.0 - lev / max_len if max_len else 0.0

        return min(1.0, max(base, lev_sim))

    @staticmethod
    def _levenshtein(a, b):
        """Levenshtein 距离（滚动数组优化）"""
        if len(a) < len(b):
            a, b = b, a
        if not b:
            return len(a)
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a, 1):
            cur = [i]
            for j, cb in enumerate(b, 1):
                cur.append(min(
                    prev[j] + 1,
                    cur[j - 1] + 1,
                    prev[j - 1] + (ca != cb)
                ))
            prev = cur
        return prev[-1]


# ============================================================
# 数据库层（唯一模式）
# ============================================================
class PhoneNumberDatabase:
    """高性能 SQLite 号码数据库（持久化）"""

    def __init__(self, db_path="phone_numbers.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._setup_tables()

    def _setup_tables(self):
        cur = self.conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS phone_numbers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                number TEXT NOT NULL,
                source_tag TEXT,
                category TEXT DEFAULT 'unclassified',
                suffix8 TEXT,
                UNIQUE(number, category)
            )
        ''')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_number ON phone_numbers(number)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_category ON phone_numbers(category)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_suffix8 ON phone_numbers(suffix8)')
        self.conn.commit()

    @staticmethod
    def _suffix8(number):
        digits = re.sub(r'\D', '', number or '')
        return digits[-8:] if len(digits) >= 8 else digits

    def insert_batch(self, numbers_data, category='unclassified'):
        """numbers_data: [(number, source_tag), ...]"""
        if not numbers_data:
            return 0
        prepared = [(num, tag, category, self._suffix8(num))
                    for num, tag in numbers_data if num]
        cur = self.conn.cursor()
        cur.executemany('''
            INSERT OR IGNORE INTO phone_numbers (number, source_tag, category, suffix8)
            VALUES (?, ?, ?, ?)
        ''', prepared)
        self.conn.commit()
        return cur.rowcount

    def get_count(self, category=None):
        cur = self.conn.cursor()
        if category:
            cur.execute('SELECT COUNT(*) FROM phone_numbers WHERE category = ?', (category,))
        else:
            cur.execute('SELECT COUNT(*) FROM phone_numbers')
        return cur.fetchone()[0]

    def exists(self, number, category=None):
        cur = self.conn.cursor()
        if category:
            cur.execute('SELECT 1 FROM phone_numbers WHERE number = ? AND category = ? LIMIT 1',
                        (number, category))
        else:
            cur.execute('SELECT 1 FROM phone_numbers WHERE number = ? LIMIT 1', (number,))
        return cur.fetchone() is not None

    def search_similar(self, target_number, threshold=0.8, max_candidates=200):
        """基于 suffix8 索引的候选召回 + Levenshtein 精算"""
        target_digits = re.sub(r'\D', '', target_number or '')
        if len(target_digits) < 4:
            return []

        suffix = self._suffix8(target_number)
        cur = self.conn.cursor()

        # 用 suffix 前4位或后4位做召回
        if len(suffix) >= 4:
            cur.execute('''
                SELECT number, source_tag, category FROM phone_numbers
                WHERE suffix8 LIKE ? OR suffix8 LIKE ?
                LIMIT ?
            ''', (suffix[:4] + '%', '%' + suffix[-4:], max_candidates))
        else:
            return []

        candidates = cur.fetchall()
        normalizer = PhoneNumberNormalizer()
        results = []
        for number, tag, cat in candidates:
            sim = normalizer.calculate_similarity(target_number, number)
            if sim >= threshold:
                results.append((number, tag, sim, cat))
        return results

    def clear(self, category=None):
        cur = self.conn.cursor()
        if category:
            cur.execute('DELETE FROM phone_numbers WHERE category = ?', (category,))
        else:
            cur.execute('DELETE FROM phone_numbers')
        self.conn.commit()

    def get_all_in_category(self, category):
        cur = self.conn.cursor()
        cur.execute('SELECT number, source_tag FROM phone_numbers WHERE category = ?', (category,))
        return dict(cur.fetchall())

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass


# ============================================================
# 处理器
# ============================================================
class CustomerNumberProcessor:
    """客户号码处理工具（统一数据库模式）"""

    CATEGORIES = ('invalid', 'valid', 'opened', 'unclassified')

    def __init__(self, db_path="phone_numbers.db"):
        self.db = PhoneNumberDatabase(db_path)
        self.PHONE_COLUMN_DEFAULT = 'phone'
        self.SUPPORTED_FORMATS = {'.xlsx', '.xls', '.csv', '.txt'}
        self.normalizer = PhoneNumberNormalizer()

    # ---------- 分类计数 ----------
    def get_category_count(self, category):
        return self.db.get_count(category)

    def has_classified_customers(self):
        return any(self.db.get_count(c) > 0 for c in ('invalid', 'valid', 'opened'))

    # ---------- 文件校验 ----------
    def _validate_file_path(self, file_path):
        if not file_path:
            raise ValueError("文件路径不能为空")
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"文件未找到: {file_path}")
        _, ext = os.path.splitext(file_path.lower())
        if ext not in self.SUPPORTED_FORMATS:
            raise ValueError(f"不支持的文件格式: {ext}。支持: {', '.join(self.SUPPORTED_FORMATS)}")

    # ---------- 从 Excel/CSV 读取 ----------
    def _read_dataframe(self, file_path, header=None, usecols=None):
        if file_path.lower().endswith('.csv'):
            return pd.read_csv(file_path, header=header, usecols=usecols)
        # Excel: 优先 openpyxl
        try:
            return pd.read_excel(file_path, engine='openpyxl', header=header, usecols=usecols)
        except Exception:
            return pd.read_excel(file_path, engine='xlrd', header=header, usecols=usecols)

    def load_numbers_from_table(self, file_path, phone_column=None, source_tag=None,
                                category='unclassified'):
        """从 Excel/CSV 加载"""
        self._validate_file_path(file_path)
        if phone_column is None:
            phone_column = self.PHONE_COLUMN_DEFAULT

        # 尝试无标题读取
        try:
            df = self._read_dataframe(file_path, header=None, usecols=[0])
            df.columns = [self.PHONE_COLUMN_DEFAULT]
        except Exception as e1:
            # 有标题读取
            try:
                df = self._read_dataframe(file_path)
                if phone_column not in df.columns:
                    raise KeyError(f"未找到列 '{phone_column}'，可用: {list(df.columns)}")
            except Exception as e2:
                raise ValueError(f"读取文件失败: {e1} / {e2}")

        # 标准化
        series = df[self.PHONE_COLUMN_DEFAULT].astype(str)
        numbers = []
        tag = source_tag or f"file_{os.path.basename(file_path)}_{datetime.now():%Y%m%d}"
        for raw in series.dropna().unique():
            if not str(raw).strip():
                continue
            n = self.normalizer.normalize_to_e164(raw)
            if n:
                numbers.append((n, tag))

        inserted = self.db.insert_batch(numbers, category)
        logger.info(f"[{file_path}] 读取 {len(numbers)} 个号码，新增 {inserted} 个到 {category}")
        return len(numbers)

    # ---------- 从 TXT 读取 ----------
    def load_numbers_from_txt(self, file_path, source_tag=None, category='unclassified',
                              chunk_size=1 << 20):
        """流式读取 TXT"""
        self._validate_file_path(file_path)
        tag = source_tag or f"txt_{os.path.basename(file_path)}_{datetime.now():%Y%m%d}"

        def read_lines(path, encoding):
            with open(path, 'r', encoding=encoding) as f:
                buf = ""
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        if buf:
                            yield buf
                        break
                    buf += chunk
                    parts = buf.split('\n')
                    buf = parts[-1]
                    for p in parts[:-1]:
                        yield p

        def process(lines):
            batch = []
            total = 0
            for line in lines:
                for raw in re.split(r'[,\s;]+', line.strip()):
                    if not raw:
                        continue
                    n = self.normalizer.normalize_to_e164(raw)
                    if n:
                        batch.append((n, tag))
                        total += 1
                    if len(batch) >= 5000:
                        self.db.insert_batch(batch, category)
                        batch = []
            if batch:
                self.db.insert_batch(batch, category)
            return total

        try:
            total = process(read_lines(file_path, 'utf-8'))
        except UnicodeDecodeError:
            total = process(read_lines(file_path, 'gbk'))
        logger.info(f"[{file_path}] 读取 {total} 个号码到 {category}")
        return total

    def load_numbers_from_generic(self, file_path, phone_column=None, source_tag=None,
                                   category='unclassified'):
        """按扩展名分发"""
        if file_path.lower().endswith('.txt'):
            return self.load_numbers_from_txt(file_path, source_tag, category)
        return self.load_numbers_from_table(file_path, phone_column, source_tag, category)

    # ---------- 分类加载 ----------
    def classify_numbers(self, invalid_file=None, valid_file=None, opened_file=None,
                          phone_column=None, strict_mode=True):
        """加载三类文件到数据库，strict_mode 保留兼容（实际都用精确存储）"""
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        try:
            if invalid_file:
                self.load_numbers_from_generic(
                    invalid_file, phone_column, f"invalid_{ts}", 'invalid')
                logger.info(f"无效客户号码: {self.get_category_count('invalid')} 个")
            if valid_file:
                self.load_numbers_from_generic(
                    valid_file, phone_column, f"valid_{ts}", 'valid')
                logger.info(f"有效客户号码: {self.get_category_count('valid')} 个")
            if opened_file:
                self.load_numbers_from_generic(
                    opened_file, phone_column, f"opened_{ts}", 'opened')
                logger.info(f"已开客户号码: {self.get_category_count('opened')} 个")
        except Exception as e:
            logger.error(f"分类号码加载出错: {e}")
            raise

    # ---------- 核心：处理源文件 ----------
    def process_and_filter(self, source_file_path, output_file=None, output_format='xlsx',
                            similarity_threshold=0.8, fuzzy_mode=False,
                            progress_callback=None, log_callback=None):
        """流式处理源文件，剔除已分类号码，输出结果。

        Returns:
            dict: {'kept': n, 'removed': m, 'output': path}
        """
        if output_format not in ('xlsx', 'csv', 'txt'):
            raise ValueError(f"不支持的输出格式: {output_format}")

        if not os.path.exists(source_file_path):
            raise FileNotFoundError(f"源文件不存在: {source_file_path}")

        base = os.path.splitext(output_file)[0] if output_file else \
            f"filtered_numbers_{datetime.now():%Y%m%d_%H%M%S}"
        final_filename = f"{base}.{output_format}"

        # 准备写入器
        csv_writer = None
        txt_handle = None
        xlsx_handle = None
        xlsx_writer = None
        xlsx_temp_path = None

        try:
            if output_format == 'csv':
                import csv
                txt_handle = open(final_filename, 'w', encoding='utf-8', newline='')
                csv_writer = csv.writer(txt_handle)
                csv_writer.writerow(['phone'])
            elif output_format == 'txt':
                txt_handle = open(final_filename, 'w', encoding='utf-8')
            else:  # xlsx — 用 openpyxl 流式写入避免 OOM
                from openpyxl import Workbook
                wb = Workbook(write_only=True)
                ws = wb.create_sheet('numbers')
                xlsx_handle = wb
                xlsx_writer = ws

            kept = 0
            removed = 0
            total = 0
            BATCH = 2000
            xlsx_buffer = []

            def is_excluded(num):
                """检查号码是否与分类库匹配"""
                if fuzzy_mode:
                    if self.db.search_similar(num, similarity_threshold):
                        return True
                else:
                    if self.db.exists(num):
                        return True
                return False

            with open(source_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    for raw in re.split(r'[,\s;]+', line.strip()):
                        if not raw:
                            continue
                        n = self.normalizer.normalize_to_e164(raw)
                        if not n:
                            continue
                        total += 1
                        if is_excluded(n):
                            removed += 1
                        else:
                            kept += 1
                            if output_format == 'txt':
                                txt_handle.write(n + '\n')
                            elif output_format == 'csv':
                                csv_writer.writerow([n])
                            else:
                                xlsx_buffer.append((n,))
                                if len(xlsx_buffer) >= 5000:
                                    xlsx_writer.append([n for (n,) in xlsx_buffer])
                                    xlsx_buffer = []

                        if total % 5000 == 0 and progress_callback:
                            progress_callback(min(95, int(kept / max(total, 1) * 95)))

            # 收尾
            if output_format == 'xlsx':
                if xlsx_buffer:
                    xlsx_writer.append([n for (n,) in xlsx_buffer])
                if total == 0:
                    xlsx_writer.append(['phone'])
                wb.save(final_filename)
            elif txt_handle:
                txt_handle.close()
                txt_handle = None

            logger.info(f"处理完成: 总 {total}, 保留 {kept}, 剔除 {removed}, 输出 {final_filename}")
            if progress_callback:
                progress_callback(100)
            if log_callback:
                log_callback(f"处理完成: 保留 {kept}, 剔除 {removed}\n")
            return {'kept': kept, 'removed': removed, 'total': total, 'output': final_filename}

        finally:
            if txt_handle:
                txt_handle.close()

    # ---------- 预览 ----------
    def preview_filtered_numbers(self, source_file_path, similarity_threshold=0.8,
                                  max_items=1000, fuzzy_mode=True):
        """预览前 max_items 个将被剔除的号码"""
        if not os.path.exists(source_file_path):
            raise FileNotFoundError(f"源文件不存在: {source_file_path}")

        to_remove = []
        with open(source_file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                if len(to_remove) >= max_items:
                    break
                for raw in re.split(r'[,\s;]+', line.strip()):
                    if not raw:
                        continue
                    n = self.normalizer.normalize_to_e164(raw)
                    if not n:
                        continue
                    if fuzzy_mode:
                        matches = self.db.search_similar(n, similarity_threshold, max_candidates=50)
                        if matches:
                            best = max(matches, key=lambda x: x[2])
                            to_remove.append((n, best[0], best[2], best[1]))
                    else:
                        # 精确匹配
                        cur = self.db.conn.cursor()
                        cur.execute('SELECT source_tag FROM phone_numbers WHERE number = ? LIMIT 1', (n,))
                        row = cur.fetchone()
                        if row:
                            to_remove.append((n, n, 1.0, row[0]))
                    if len(to_remove) >= max_items:
                        break
        return to_remove

    # ---------- 清空 ----------
    def clear_category(self, category=None):
        self.db.clear(category)

    # ---------- 报告 ----------
    def generate_report(self):
        inv = self.get_category_count('invalid')
        val = self.get_category_count('valid')
        opn = self.get_category_count('opened')
        return {
            '无效客户': inv,
            '有效客户': val,
            '已开客户': opn,
            '合计剔除': inv + val + opn,
            '处理时间': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

    def close(self):
        self.db.close()


# ============================================================
# 预览对话框
# ============================================================
class PreviewDialog(QDialog):
    def __init__(self, to_remove_list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("待剔除号码预览")
        self.setGeometry(200, 200, 900, 550)
        layout = QVBoxLayout(self)

        info_label = QLabel(f"以下 {len(to_remove_list)} 个号码将被剔除（与分类库相似）：")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        self.list_widget = QListWidget()
        for src_num, exclude_num, similarity, source_tag in to_remove_list:
            item = f"源: {src_num} | 匹配: {exclude_num} | 相似度: {similarity:.2f} | 来源: {source_tag}"
            self.list_widget.addItem(item)
        layout.addWidget(self.list_widget)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                    QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)


# ============================================================
# 线程
# ============================================================
class ProcessingThread(QThread):
    progress_updated = pyqtSignal(int)
    log_updated = pyqtSignal(str)
    finished = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self, processor, source_file, output_file, output_format,
                 similarity_threshold, fuzzy_mode):
        super().__init__()
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


class ImportNumbersThread(QThread):
    progress_updated = pyqtSignal(int)
    log_updated = pyqtSignal(str)
    finished = pyqtSignal(str, int, int)
    error_occurred = pyqtSignal(str)

    def __init__(self, processor, file_path, category):
        super().__init__()
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
            self.finished.emit(self.category, old, new)
        except Exception as e:
            logger.exception("导入线程异常")
            self.error_occurred.emit(str(e))


# ============================================================
# GUI
# ============================================================
class NumberFilterGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("客户号码剔除器 Professional Edition v4.1")
        self.setGeometry(100, 100, 1400, 900)

        self.processor = CustomerNumberProcessor()

        self.invalid_file = ""
        self.valid_file = ""
        self.opened_file = ""
        self.source_file = ""
        self.output_file = "filtered_numbers_output"
        self.output_format = "xlsx"

        self.strict_mode = True
        self.similarity_threshold = 0.8

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        self.create_menu_bar()

        self.tab_widget = QTabWidget()
        main_layout.addWidget(self.tab_widget)

        self.create_classification_tab()
        self.create_processing_tab()

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("就绪")

        self.update_current_stats()

        # 关闭时释放资源
        self._closed = False

        # 启动后台自动加载
        QTimer.singleShot(200, self.auto_find_classification_files)

    def closeEvent(self, event):
        if self._closed:
            event.accept()
            return
        self._closed = True
        try:
            self.processor.close()
        except Exception:
            pass
        event.accept()

    # ---------- 菜单 ----------
    def create_menu_bar(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu('文件')
        exit_action = QAction('退出', self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        tools_menu = menubar.addMenu('工具')
        refresh_action = QAction('刷新分类', self)
        refresh_action.triggered.connect(self.load_classifications)
        tools_menu.addAction(refresh_action)

        clear_action = QAction('清空分类', self)
        clear_action.triggered.connect(self.clear_classifications)
        tools_menu.addAction(clear_action)

        help_menu = menubar.addMenu('帮助')
        about_action = QAction('关于', self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

    # ---------- 分类标签页 ----------
    def create_classification_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        mode_group = QGroupBox("匹配模式")
        mode_layout = QHBoxLayout(mode_group)

        self.strict_radio = QCheckBox("严格模式（精确匹配）")
        self.strict_radio.setChecked(True)
        self.strict_radio.toggled.connect(self.toggle_match_mode)

        self.fuzzy_radio = QCheckBox("宽松模式（模糊匹配）")
        self.fuzzy_radio.toggled.connect(self.toggle_match_mode)

        self.threshold_label = QLabel("相似度阈值:")
        self.threshold_slider = QSlider(Qt.Orientation.Horizontal)
        self.threshold_slider.setMinimum(50)
        self.threshold_slider.setMaximum(100)
        self.threshold_slider.setValue(80)
        self.threshold_slider.valueChanged.connect(self.update_threshold_label)

        self.threshold_value_label = QLabel("0.80")

        mode_layout.addWidget(self.strict_radio)
        mode_layout.addWidget(self.fuzzy_radio)
        mode_layout.addStretch()
        mode_layout.addWidget(self.threshold_label)
        mode_layout.addWidget(self.threshold_slider)
        mode_layout.addWidget(self.threshold_value_label)

        def make_group(title, attr_prefix):
            g = QGroupBox(title)
            h = QHBoxLayout(g)
            le = QLineEdit()
            le.setText(getattr(self, f"{attr_prefix}_file", ""))
            setattr(self, f"{attr_prefix}_line_edit", le)
            btn_browse = QPushButton("浏览")
            btn_browse.clicked.connect(lambda: self.browse_file(attr_prefix))
            btn_import = QPushButton("导入号码")
            btn_import.clicked.connect(lambda: self.import_numbers_to_category(attr_prefix))
            h.addWidget(le)
            h.addWidget(btn_browse)
            h.addWidget(btn_import)
            return g

        invalid_group = make_group("无效客户号码库", 'invalid')
        valid_group = make_group("有效客户号码库", 'valid')
        opened_group = make_group("已开客户号码库", 'opened')

        button_layout = QHBoxLayout()
        refresh_btn = QPushButton("刷新分类号码")
        refresh_btn.clicked.connect(self.load_classifications)
        clear_btn = QPushButton("清空分类号码")
        clear_btn.clicked.connect(self.clear_classifications)

        self.current_stats_label = QLabel()
        font = QFont()
        font.setBold(True)
        self.current_stats_label.setFont(font)

        button_layout.addWidget(refresh_btn)
        button_layout.addWidget(clear_btn)
        button_layout.addStretch()
        button_layout.addWidget(self.current_stats_label)

        layout.addWidget(mode_group)
        layout.addWidget(invalid_group)
        layout.addWidget(valid_group)
        layout.addWidget(opened_group)
        layout.addLayout(button_layout)

        self.tab_widget.addTab(tab, "号码分类管理")

    def toggle_match_mode(self):
        if self.sender() is self.strict_radio and self.strict_radio.isChecked():
            self.fuzzy_radio.setChecked(False)
            self.strict_mode = True
        elif self.sender() is self.fuzzy_radio and self.fuzzy_radio.isChecked():
            self.strict_radio.setChecked(False)
            self.strict_mode = False

        enabled = not self.strict_mode
        self.threshold_slider.setEnabled(enabled)
        self.threshold_value_label.setEnabled(enabled)
        self.threshold_label.setEnabled(enabled)

    def update_threshold_label(self, value):
        self.similarity_threshold = value / 100.0
        self.threshold_value_label.setText(f"{self.similarity_threshold:.2f}")

    # ---------- 处理标签页 ----------
    def create_processing_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        source_group = QGroupBox("源号码文件")
        source_layout = QHBoxLayout(source_group)
        self.source_line_edit = QLineEdit()
        self.source_line_edit.setText(self.source_file)
        source_browse_btn = QPushButton("浏览")
        source_browse_btn.clicked.connect(self.browse_source_file)
        source_layout.addWidget(self.source_line_edit)
        source_layout.addWidget(source_browse_btn)

        output_group = QGroupBox("输出设置")
        output_layout = QHBoxLayout(output_group)

        of_layout = QVBoxLayout()
        of_layout.addWidget(QLabel("输出文件名:"))
        self.output_file_edit = QLineEdit()
        self.output_file_edit.setText(self.output_file)
        of_layout.addWidget(self.output_file_edit)

        fmt_layout = QVBoxLayout()
        fmt_layout.addWidget(QLabel("输出格式:"))
        self.output_format_combo = QComboBox()
        self.output_format_combo.addItems(["xlsx", "csv", "txt"])
        self.output_format_combo.setCurrentText(self.output_format)
        fmt_layout.addWidget(self.output_format_combo)

        output_layout.addLayout(of_layout)
        output_layout.addLayout(fmt_layout)

        button_layout = QHBoxLayout()
        preview_btn = QPushButton("预览待剔除号码")
        preview_btn.clicked.connect(self.preview_filtered_numbers)
        preview_btn.setStyleSheet(
            "QPushButton { background-color: #2196F3; color: white; padding: 10px; "
            "font-size: 14px; border-radius: 5px; }")
        process_btn = QPushButton("开始处理")
        process_btn.clicked.connect(self.start_processing_from_generic)
        process_btn.setStyleSheet(
            "QPushButton { background-color: #4CAF50; color: white; padding: 10px; "
            "font-size: 14px; border-radius: 5px; }")
        button_layout.addWidget(preview_btn)
        button_layout.addWidget(process_btn)

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

    # ---------- 文件浏览 ----------
    def browse_file(self, category):
        titles = {'invalid': '无效客户', 'valid': '有效客户', 'opened': '已开客户'}
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            f"选择{titles[category]}文件",
            "",
            "All Supported (*.xlsx *.xls *.csv *.txt);;Excel (*.xlsx *.xls);;CSV (*.csv);;Text (*.txt);;All (*)"
        )
        if file_path:
            setattr(self, f"{category}_file", file_path)
            getattr(self, f"{category}_line_edit").setText(file_path)

    def browse_source_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择源号码文件", "",
            "All Supported (*.xlsx *.xls *.csv *.txt);;All (*)"
        )
        if file_path:
            self.source_file = file_path
            self.source_line_edit.setText(file_path)

    # ---------- 导入 ----------
    def import_numbers_to_category(self, category):
        titles = {'invalid': '无效客户', 'valid': '有效客户', 'opened': '已开客户'}
        file_path, _ = QFileDialog.getOpenFileName(
            self, f"选择要导入到{titles[category]}的号码文件", "",
            "All Supported (*.xlsx *.xls *.csv *.txt);;All (*)"
        )
        if not file_path:
            return

        self.import_thread = ImportNumbersThread(self.processor, file_path, category)
        self.import_thread.progress_updated.connect(self.progress_bar.setValue)
        self.import_thread.log_updated.connect(self.update_log)
        self.import_thread.finished.connect(self.on_import_finished)
        self.import_thread.error_occurred.connect(self.on_import_error)
        self.status_bar.showMessage(f"正在导入号码到{titles[category]}库...")
        self.import_thread.start()

    def on_import_finished(self, category, old_count, new_count):
        titles = {'invalid': '无效客户', 'valid': '有效客户', 'opened': '已开客户'}
        QMessageBox.information(
            self, "成功",
            f"成功向{titles[category]}库导入号码！\n原有: {old_count}, 现有: {new_count}, "
            f"新增: {new_count - old_count}")
        self.update_current_stats()
        self.status_bar.showMessage(f"向{titles[category]}库导入号码完成")

    def on_import_error(self, error_msg):
        QMessageBox.critical(self, "错误", f"导入号码时出错: {error_msg}")
        self.status_bar.showMessage("导入号码失败")

    # ---------- 预览 ----------
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
                similarity_threshold=1.0 if self.strict_mode else self.similarity_threshold,
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

    # ---------- 加载/清空分类 ----------
    def load_classifications(self):
        try:
            self.status_bar.showMessage("正在加载分类号码...")
            self.progress_bar.setValue(0)
            self.processor.classify_numbers(
                invalid_file=self.invalid_file or None,
                valid_file=self.valid_file or None,
                opened_file=self.opened_file or None,
                strict_mode=self.strict_mode
            )
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
        if reply == QMessageBox.StandardButton.Yes:
            try:
                self.processor.clear_category(None)
                self.update_log("所有分类号码已清空。\n")
                self.update_current_stats()
                QMessageBox.information(self, "成功", "分类号码已清空！")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"清空失败: {e}")

    # ---------- 统计 ----------
    def update_current_stats(self):
        try:
            report = self.processor.generate_report()
            self.current_stats_label.setText(
                f"统计: 无效: {report['无效客户']} | 有效: {report['有效客户']} | "
                f"已开: {report['已开客户']} | 合计: {report['合计剔除']}")
        except Exception as e:
            logger.error(f"更新统计失败: {e}")

    # ---------- 处理 ----------
    def start_processing_from_generic(self):
        if not self.source_file:
            QMessageBox.warning(self, "警告", "请上传源文件！")
            return
        if not self.processor.has_classified_customers():
            QMessageBox.warning(self, "警告", "分类号码库为空，请检查分类文件！")
            return

        self.output_file = self.output_file_edit.text().strip() or "filtered_numbers_output"
        self.output_format = self.output_format_combo.currentText()

        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.processing_thread = ProcessingThread(
            self.processor, self.source_file, self.output_file, self.output_format,
            self.similarity_threshold, not self.strict_mode
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
        QMessageBox.information(
            self, "成功",
            f"处理完成！\n输出文件: {result['output']}\n"
            f"保留 {result['kept']} / 剔除 {result['removed']}")
        self.status_bar.showMessage("号码处理完成")

    def on_processing_error(self, error_msg):
        QMessageBox.critical(self, "错误", f"处理号码时出错: {error_msg}")
        self.status_bar.showMessage("号码处理失败")

    def update_log(self, message):
        self.log_text_edit.append(f"[{datetime.now():%H:%M:%S}] {message}")

    # ---------- 自动加载 ----------
    def auto_find_classification_files(self):
        if self._closed:
            return
        current_dir = os.getcwd()
        found = {}
        for name in ('invalid_customers', 'valid_customers', 'opened_customers'):
            for ext in ('.xlsx', '.csv', '.xls'):
                p = os.path.join(current_dir, name + ext)
                if os.path.exists(p):
                    found[name] = p
                    break

        if 'invalid_customers' in found:
            self.invalid_file = found['invalid_customers']
            self.invalid_line_edit.setText(self.invalid_file)
            logger.info(f"自动找到无效客户文件: {self.invalid_file}")
        if 'valid_customers' in found:
            self.valid_file = found['valid_customers']
            self.valid_line_edit.setText(self.valid_file)
            logger.info(f"自动找到有效客户文件: {self.valid_file}")
        if 'opened_customers' in found:
            self.opened_file = found['opened_customers']
            self.opened_line_edit.setText(self.opened_file)
            logger.info(f"自动找到已开客户文件: {self.opened_file}")

        if not found:
            return
        try:
            self.processor.classify_numbers(
                invalid_file=self.invalid_file or None,
                valid_file=self.valid_file or None,
                opened_file=self.opened_file or None,
                strict_mode=self.strict_mode,
            )
            self.update_current_stats()
            logger.info("程序启动：分类号码自动加载完成。")
        except Exception as e:
            logger.error(f"程序启动：加载分类号码时出错: {e}")

    def show_about(self):
        QMessageBox.about(
            self, "关于",
            "客户号码剔除器 Professional Edition v4.1\n\n"
            "修复与优化：\n"
            "- 修复处理线程与 process_and_filter 参数不匹配的致命Bug\n"
            "- 修复数据库模式下属性访问冲突（clear/update 失效）\n"
            "- 修复模糊匹配阈值实际未生效的问题\n"
            "- 使用持久化 SQLite（WAL模式）+ suffix8 索引\n"
            "- 相似度算法改为 Levenshtein + 后缀加权\n"
            "- XLSX 输出改为流式写入，避免大数据 OOM\n"
            "- TXT 读取流式 + 自动 UTF-8/GBK 回退\n"
            "- 统一导入/处理线程逻辑\n\n"
            "作者：陆山君")


def main():
    app = QApplication(sys.argv)
    window = NumberFilterGUI()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()