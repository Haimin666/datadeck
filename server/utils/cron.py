"""定时任务 Cron 的统一校验与匹配。"""

from __future__ import annotations

from datetime import datetime
import re

_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))
_FIELD_RE = re.compile(r"[0-9*/,-]+")


def validate_cron_expression(value: str) -> str:
    fields = str(value or "").strip().split()
    if len(fields) != 5 or any(not _FIELD_RE.fullmatch(item) for item in fields):
        raise ValueError("Cron 必须是 5 段数字表达式，例如：0 9 * * *")
    for field, (lower, upper) in zip(fields, _RANGES):
        for part in field.split(","):
            base, _, step_text = part.partition("/")
            step = int(step_text or 1)
            if step <= 0:
                raise ValueError("Cron 步长必须大于 0")
            if base == "*":
                start, end = lower, upper
            elif "-" in base:
                start_text, end_text = base.split("-", 1)
                start, end = int(start_text), int(end_text)
            else:
                start = end = int(base)
            if start < lower or end > upper or start > end:
                raise ValueError(f"Cron 字段超出允许范围：{lower}-{upper}")
    return " ".join(fields)


def cron_matches(expression: str, now: datetime) -> bool:
    fields = validate_cron_expression(expression).split()
    values = (now.minute, now.hour, now.day, now.month, (now.weekday() + 1) % 7)
    for value, field, (lower, upper) in zip(values, fields, _RANGES):
        allowed: set[int] = set()
        for part in field.split(","):
            base, _, step_text = part.partition("/")
            step = int(step_text or 1)
            if base == "*":
                start, end = lower, upper
            elif "-" in base:
                start_text, end_text = base.split("-", 1)
                start, end = int(start_text), int(end_text)
            else:
                start = end = int(base)
            allowed.update(range(start, end + 1, step))
        if value not in allowed:
            return False
    return True
