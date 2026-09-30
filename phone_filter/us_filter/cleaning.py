"""美国号码清洗与解析（纯逻辑，无 GUI 依赖）。

约定：目标格式为美国 11 位号码（10 位区号+号码前补 1）。
"""

import logging
import os
import re

import numpy as np

logger = logging.getLogger(__name__)

_NON_DIGIT = re.compile(r'\D')
_SPLIT = re.compile(r'[,;\s]+')


def app_root_dir():
    """应用根目录（兼容 PyInstaller 打包环境）。"""
    import sys
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def clean_one(s):
    """清洗为纯数字字符串。"""
    if s is None:
        return ''
    return _NON_DIGIT.sub('', str(s))


def normalize_us_number(d):
    """规范化为美国 11 位格式：10 位补前缀 1；11 位保留；其他原样返回。"""
    if not d:
        return ''
    if len(d) == 10:
        return '1' + d
    return d


def detect_encoding(path):
    """探测文本文件编码（utf-8 / utf-8-sig / gbk）。"""
    with open(path, 'rb') as f:
        head = f.read(8192)
    for enc in ('utf-8', 'utf-8-sig', 'gbk'):
        try:
            head.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return 'utf-8'


def load_classify_set(paths):
    """读取多个分类文件，合并为一个 int 集合。"""
    s = set()
    for path in paths:
        if not path or not os.path.exists(path):
            continue
        enc = detect_encoding(path)
        with open(path, 'r', encoding=enc, errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if line.isdigit():
                    s.add(int(line))
                    continue
                for raw in _SPLIT.split(line):
                    if not raw:
                        continue
                    d = clean_one(raw)
                    if d.isdigit():
                        s.add(int(d))
    return s


# ============================================================
# 源文件解析（自动选择极速/慢速路径）
# ============================================================
def parse_source(path):
    """把源 txt 解析为 int64 numpy 数组。

    若文件仅由数字/换行构成且为标准每行 11 位，则走向量化极速路径。
    """
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
    enc = detect_encoding(path)
    buf_int = []
    chunks = []
    BATCH = 500_000

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
                    if not raw:
                        continue
                    d = clean_one(raw)
                    if d.isdigit():
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
def _write_numbers(path, numbers):
    """分块写出号码文件（大缓冲减少 IO 次数）。"""
    BUF = 200_000
    with open(path, 'w', encoding='utf-8', buffering=8 << 20) as f:
        for i in range(0, len(numbers), BUF):
            f.write('\n'.join(map(str, numbers[i:i + BUF])))
            f.write('\n')


def filter_arrays(source_arr, classify_set, output_path, removed_path=None,
                  preview_limit=100):
    """从源数组中剔除分类库中的号码。

    Returns:
        (total, kept, removed, preview) —— preview 为保留号码样本（str 列表）。
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

    for x in src_list:
        if x in classify_set:
            removed.append(x)
        else:
            if len(preview) < preview_limit:
                preview.append(str(x))
            kept.append(x)

    _write_numbers(output_path, kept)
    if removed_path and removed:
        _write_numbers(removed_path, removed)

    return len(src_list), len(kept), len(removed), preview


# ============================================================
# 规范化
# ============================================================
def normalize_txt(input_path, output_path, progress_cb=None, log_cb=None):
    """把任意格式 txt 规范化为"每行一个 11 位数字"。

    Returns:
        (total_input, valid_output)
    """
    enc = detect_encoding(input_path)
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
            open(output_path, 'w', encoding='utf-8', buffering=8 << 20) as fout:

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
