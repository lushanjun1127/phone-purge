"""客户号码处理器：文件加载、分类入库、过滤输出（统一数据库模式）。

纯逻辑层，不依赖任何 GUI 库，可独立测试与复用。
"""

import logging
import os
import re
from datetime import datetime

import pandas as pd

from phone_filter.core.database import PhoneNumberDatabase
from phone_filter.core.normalizer import PhoneNumberNormalizer

logger = logging.getLogger(__name__)

# 号码分隔符（一行可能含多个号码）
SPLIT_RE = re.compile(r'[,\s;]+')


class CustomerNumberProcessor:
    """客户号码处理工具（统一数据库模式）。"""

    CATEGORIES = ('invalid', 'valid', 'opened', 'unclassified')
    CLASSIFIED_CATEGORIES = ('invalid', 'valid', 'opened')
    PHONE_COLUMN_DEFAULT = 'phone'
    SUPPORTED_FORMATS = {'.xlsx', '.xls', '.csv', '.txt'}
    OUTPUT_FORMATS = ('xlsx', 'csv', 'txt')

    INSERT_BATCH_SIZE = 5000
    PROGRESS_EVERY = 5000

    def __init__(self, db_path="phone_numbers.db"):
        self.db = PhoneNumberDatabase(db_path)
        self.normalizer = PhoneNumberNormalizer()

    # ---------- 分类计数 ----------
    def get_category_count(self, category):
        return self.db.get_count(category)

    def has_classified_customers(self):
        return any(self.db.get_count(c) > 0 for c in self.CLASSIFIED_CATEGORIES)

    # ---------- 文件校验 ----------
    def _validate_file_path(self, file_path):
        if not file_path:
            raise ValueError("文件路径不能为空")
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"文件未找到: {file_path}")
        _, ext = os.path.splitext(file_path.lower())
        if ext not in self.SUPPORTED_FORMATS:
            raise ValueError(
                f"不支持的文件格式: {ext}。支持: {', '.join(sorted(self.SUPPORTED_FORMATS))}")

    # ---------- 从 Excel/CSV 读取 ----------
    def _read_dataframe(self, file_path, header=None, usecols=None):
        if file_path.lower().endswith('.csv'):
            return pd.read_csv(file_path, header=header, usecols=usecols)
        # Excel: 优先 openpyxl，失败回退 xlrd
        try:
            return pd.read_excel(file_path, engine='openpyxl', header=header, usecols=usecols)
        except Exception:
            return pd.read_excel(file_path, engine='xlrd', header=header, usecols=usecols)

    def load_numbers_from_table(self, file_path, phone_column=None, source_tag=None,
                                category='unclassified'):
        """从 Excel/CSV 加载号码并入库，返回读取到的号码数。"""
        self._validate_file_path(file_path)
        if phone_column is None:
            phone_column = self.PHONE_COLUMN_DEFAULT

        col = self.PHONE_COLUMN_DEFAULT
        # 先尝试无表头读第一列，失败再按表头读取指定列
        try:
            df = self._read_dataframe(file_path, header=None, usecols=[0])
            df.columns = [col]
        except Exception as e1:
            try:
                df = self._read_dataframe(file_path)
            except Exception as e2:
                raise ValueError(f"读取文件失败: {e1} / {e2}")
            if phone_column not in df.columns:
                raise KeyError(f"未找到列 '{phone_column}'，可用: {list(df.columns)}")
            df = df.rename(columns={phone_column: col})

        tag = source_tag or f"file_{os.path.basename(file_path)}_{datetime.now():%Y%m%d}"
        numbers = []
        for raw in df[col].astype(str).dropna().unique():
            n = self.normalizer.normalize_to_e164(raw)
            if n:
                numbers.append((n, tag))

        inserted = self.db.insert_batch(numbers, category)
        logger.info("[%s] 读取 %d 个号码，新增 %d 个到 %s",
                    file_path, len(numbers), inserted, category)
        return len(numbers)

    # ---------- 从 TXT 读取 ----------
    @staticmethod
    def _iter_lines(file_path, chunk_size=1 << 20):
        """流式按行读取文本文件（分块读，避免整文件载入内存）。"""
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
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

    def _normalize_tokens(self, lines, tag):
        """把行迭代器转换为 (number, tag) 批次迭代器。"""
        batch = []
        for line in lines:
            for raw in SPLIT_RE.split(line.strip()):
                if not raw:
                    continue
                n = self.normalizer.normalize_to_e164(raw)
                if n:
                    batch.append((n, tag))
                    if len(batch) >= self.INSERT_BATCH_SIZE:
                        yield batch
                        batch = []
        if batch:
            yield batch

    def load_numbers_from_txt(self, file_path, source_tag=None, category='unclassified'):
        """流式读取 TXT（自动 UTF-8/GBK 回退），返回读取到的号码数。"""
        self._validate_file_path(file_path)
        tag = source_tag or f"txt_{os.path.basename(file_path)}_{datetime.now():%Y%m%d}"

        total = 0
        for batch in self._normalize_tokens(self._iter_lines(file_path), tag):
            self.db.insert_batch(batch, category)
            total += len(batch)

        logger.info("[%s] 读取 %d 个号码到 %s", file_path, total, category)
        return total

    def load_numbers_from_generic(self, file_path, phone_column=None, source_tag=None,
                                  category='unclassified'):
        """按扩展名分发到对应的加载器。"""
        if file_path.lower().endswith('.txt'):
            return self.load_numbers_from_txt(file_path, source_tag, category)
        return self.load_numbers_from_table(file_path, phone_column, source_tag, category)

    # ---------- 分类加载 ----------
    def classify_numbers(self, invalid_file=None, valid_file=None, opened_file=None,
                         phone_column=None):
        """加载三类文件到数据库对应分类。"""
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        plan = (('invalid', invalid_file), ('valid', valid_file), ('opened', opened_file))
        try:
            for category, path in plan:
                if not path:
                    continue
                self.load_numbers_from_generic(path, phone_column, f"{category}_{ts}", category)
                logger.info("%s 客户号码: %d 个", category, self.get_category_count(category))
        except Exception as e:
            logger.error("分类号码加载出错: %s", e)
            raise

    # ---------- 核心：处理源文件 ----------
    def _match_excluded(self, number, fuzzy_mode, similarity_threshold):
        """检查号码是否命中分类库（精确或模糊）。"""
        if fuzzy_mode:
            return bool(self.db.search_similar(number, similarity_threshold))
        return self.db.exists(number)

    def process_and_filter(self, source_file_path, output_file=None, output_format='xlsx',
                           similarity_threshold=0.8, fuzzy_mode=False,
                           progress_callback=None, log_callback=None):
        """流式处理源文件，剔除已分类号码，输出结果。

        Returns:
            dict: {'total': n, 'kept': n, 'removed': n, 'output': path}
        """
        if output_format not in self.OUTPUT_FORMATS:
            raise ValueError(f"不支持的输出格式: {output_format}")
        if not os.path.exists(source_file_path):
            raise FileNotFoundError(f"源文件不存在: {source_file_path}")

        base = (os.path.splitext(output_file)[0] if output_file
                else f"filtered_numbers_{datetime.now():%Y%m%d_%H%M%S}")
        final_filename = f"{base}.{output_format}"

        writer = _make_writer(output_format, final_filename)
        kept = removed = total = 0
        try:
            for raw in self._iter_number_tokens(source_file_path):
                n = self.normalizer.normalize_to_e164(raw)
                if not n:
                    continue
                total += 1
                if self._match_excluded(n, fuzzy_mode, similarity_threshold):
                    removed += 1
                else:
                    kept += 1
                    writer.write_row(n)

                if total % self.PROGRESS_EVERY == 0 and progress_callback:
                    progress_callback(min(95, int(kept / max(total, 1) * 95)))

            writer.finalize(empty=(total == 0))
            writer = None
        finally:
            if writer:
                writer.close()

        logger.info("处理完成: 总 %d, 保留 %d, 剔除 %d, 输出 %s",
                    total, kept, removed, final_filename)
        if progress_callback:
            progress_callback(100)
        if log_callback:
            log_callback(f"处理完成: 保留 {kept}, 剔除 {removed}\n")
        return {'kept': kept, 'removed': removed, 'total': total, 'output': final_filename}

    def _iter_number_tokens(self, source_file_path):
        """产出源文件中的原始号码 token（跨行按分隔符切分）。"""
        for line in self._iter_lines(source_file_path):
            for raw in SPLIT_RE.split(line.strip()):
                if raw:
                    yield raw

    # ---------- 预览 ----------
    def preview_filtered_numbers(self, source_file_path, similarity_threshold=0.8,
                                 max_items=1000, fuzzy_mode=True):
        """预览前 max_items 个将被剔除的号码。

        Returns:
            [(源号码, 匹配号码, 相似度, 来源标签), ...]
        """
        if not os.path.exists(source_file_path):
            raise FileNotFoundError(f"源文件不存在: {source_file_path}")

        to_remove = []
        for raw in self._iter_number_tokens(source_file_path):
            if len(to_remove) >= max_items:
                break
            n = self.normalizer.normalize_to_e164(raw)
            if not n:
                continue
            if fuzzy_mode:
                matches = self.db.search_similar(n, similarity_threshold, max_candidates=50)
                if matches:
                    best = max(matches, key=lambda x: x[2])
                    to_remove.append((n, best[0], best[2], best[1]))
            else:
                tag = self.db.find_source_tag(n)
                if tag is not None:
                    to_remove.append((n, n, 1.0, tag))
        return to_remove[:max_items]

    # ---------- 清空 / 报告 ----------
    def clear_category(self, category=None):
        self.db.clear(category)

    def generate_report(self):
        inv = self.get_category_count('invalid')
        val = self.get_category_count('valid')
        opn = self.get_category_count('opened')
        return {
            '无效客户': inv,
            '有效客户': val,
            '已开客户': opn,
            '合计剔除': inv + val + opn,
            '处理时间': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    def close(self):
        self.db.close()


# ============================================================
# 输出写入器（策略模式：txt / csv / xlsx 流式写入）
# ============================================================
class _BaseWriter:
    def write_row(self, number):
        raise NotImplementedError

    def finalize(self, empty=False):
        pass

    def close(self):
        pass


class _TxtWriter(_BaseWriter):
    def __init__(self, path):
        self._f = open(path, 'w', encoding='utf-8')

    def write_row(self, number):
        self._f.write(number + '\n')

    def close(self):
        self._f.close()


class _CsvWriter(_BaseWriter):
    def __init__(self, path):
        import csv
        self._f = open(path, 'w', encoding='utf-8', newline='')
        self._writer = csv.writer(self._f)
        self._writer.writerow(['phone'])

    def write_row(self, number):
        self._writer.writerow([number])

    def close(self):
        self._f.close()


class _XlsxWriter(_BaseWriter):
    """openpyxl 流式写入，避免大数据 OOM。"""

    FLUSH_EVERY = 5000

    def __init__(self, path):
        from openpyxl import Workbook
        self._path = path
        self._wb = Workbook(write_only=True)
        self._ws = self._wb.create_sheet('numbers')
        self._buffer = []

    def write_row(self, number):
        self._buffer.append(number)
        if len(self._buffer) >= self.FLUSH_EVERY:
            self._flush()

    def _flush(self):
        self._ws.append(self._buffer)
        self._buffer = []

    def finalize(self, empty=False):
        if empty:
            self._ws.append(['phone'])
        elif self._buffer:
            self._flush()

    def close(self):
        try:
            self._wb.save(self._path)
        finally:
            self._wb.close()


_WRITERS = {
    'txt': _TxtWriter,
    'csv': _CsvWriter,
    'xlsx': _XlsxWriter,
}


def _make_writer(output_format, path):
    return _WRITERS[output_format](path)
