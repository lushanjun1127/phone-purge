"""号码标准化与相似度计算。"""

import logging
import re

logger = logging.getLogger(__name__)

# 可选依赖：google-i18n-phonenumbers（缺失时回退到内置规则）
try:
    import phonenumbers
    HAS_PHONENUMBERS = True
except ImportError:  # pragma: no cover
    phonenumbers = None
    HAS_PHONENUMBERS = False

_NON_DIGIT_RE = re.compile(r'\D')


class PhoneNumberNormalizer:
    """号码标准化处理类。"""

    @staticmethod
    def normalize_to_e164(phone_number, region='CN'):
        """将电话号码标准化为 E.164 格式；无法解析时返回 ''。"""
        if phone_number is None:
            return ''
        s = str(phone_number).strip()
        if not s or s.lower() in ('nan', 'none'):
            return ''

        # 已经是 E.164 格式
        if s.startswith('+'):
            digits = _NON_DIGIT_RE.sub('', s)
            return '+' + digits if digits else ''

        digits = _NON_DIGIT_RE.sub('', s)
        if not digits:
            return ''

        # 优先使用 phonenumbers
        if HAS_PHONENUMBERS:
            try:
                parsed = phonenumbers.parse(s, region)
                if phonenumbers.is_valid_number(parsed):
                    return phonenumbers.format_number(
                        parsed, phonenumbers.PhoneNumberFormat.E164)
            except Exception:
                logger.debug("phonenumbers 解析失败，使用回退规则: %s", s)

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
        """计算两个号码的相似度 (0-1)。

        使用改进的 Levenshtein + 后缀匹配加权。
        """
        if num1 == num2:
            return 1.0

        d1 = _NON_DIGIT_RE.sub('', str(num1))
        d2 = _NON_DIGIT_RE.sub('', str(num2))

        if not d1 or not d2:
            return 0.0

        # 长度差异过大直接剪枝
        if abs(len(d1) - len(d2)) > 4:
            return 0.0

        # 后缀匹配加权（电话后几位最重要）
        min_len = min(len(d1), len(d2))
        base = 0.0
        if min_len >= 4:
            suffix = min(8, min_len)
            if d1[-suffix:] == d2[-suffix:]:
                base = 0.9  # 后缀完全相同，基础分 0.9

        # 编辑距离
        lev = PhoneNumberNormalizer._levenshtein(d1, d2)
        max_len = max(len(d1), len(d2))
        lev_sim = 1.0 - lev / max_len if max_len else 0.0

        return min(1.0, max(base, lev_sim))

    @staticmethod
    def _levenshtein(a, b):
        """Levenshtein 距离（滚动数组优化）。"""
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
                    prev[j - 1] + (ca != cb),
                ))
            prev = cur
        return prev[-1]
