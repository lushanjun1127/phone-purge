"""高性能 SQLite 号码数据库（持久化，唯一模式）。"""

import logging
import re
import sqlite3

from phone_filter.core.normalizer import PhoneNumberNormalizer

logger = logging.getLogger(__name__)

_SCHEMA = '''
CREATE TABLE IF NOT EXISTS phone_numbers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    number TEXT NOT NULL,
    source_tag TEXT,
    category TEXT DEFAULT 'unclassified',
    suffix8 TEXT,
    UNIQUE(number, category)
)
'''

_INDEXES = (
    'CREATE INDEX IF NOT EXISTS idx_number ON phone_numbers(number)',
    'CREATE INDEX IF NOT EXISTS idx_category ON phone_numbers(category)',
    'CREATE INDEX IF NOT EXISTS idx_suffix8 ON phone_numbers(suffix8)',
)


class PhoneNumberDatabase:
    """号码分类库的持久化存储层。"""

    def __init__(self, db_path="phone_numbers.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._setup_tables()

    def _setup_tables(self):
        cur = self.conn.cursor()
        cur.execute(_SCHEMA)
        for ddl in _INDEXES:
            cur.execute(ddl)
        self.conn.commit()

    @staticmethod
    def _suffix8(number):
        digits = re.sub(r'\D', '', number or '')
        return digits[-8:] if len(digits) >= 8 else digits

    def insert_batch(self, numbers_data, category='unclassified'):
        """批量插入号码。

        Args:
            numbers_data: [(number, source_tag), ...]
        Returns:
            受影响的行数。
        """
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

    def find_source_tag(self, number):
        """精确查找号码所属分类的来源标签；不存在返回 None。"""
        cur = self.conn.cursor()
        cur.execute('SELECT source_tag FROM phone_numbers WHERE number = ? LIMIT 1', (number,))
        row = cur.fetchone()
        return row[0] if row else None

    def search_similar(self, target_number, threshold=0.8, max_candidates=200):
        """基于 suffix8 索引的候选召回 + Levenshtein 精算。

        Returns:
            [(number, source_tag, similarity, category), ...]
        """
        target_digits = re.sub(r'\D', '', target_number or '')
        if len(target_digits) < 4:
            return []

        suffix = self._suffix8(target_number)
        if len(suffix) < 4:
            return []

        cur = self.conn.cursor()
        # 用 suffix 前4位或后4位做召回
        cur.execute('''
            SELECT number, source_tag, category FROM phone_numbers
            WHERE suffix8 LIKE ? OR suffix8 LIKE ?
            LIMIT ?
        ''', (suffix[:4] + '%', '%' + suffix[-4:], max_candidates))

        results = []
        for number, tag, cat in cur.fetchall():
            sim = PhoneNumberNormalizer.calculate_similarity(target_number, number)
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
        except Exception:  # pragma: no cover
            logger.debug("关闭数据库连接时出错", exc_info=True)
