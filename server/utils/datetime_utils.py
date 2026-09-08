"""UTC 时间工具函数。"""
from __future__ import annotations
from datetime import datetime


def utc_now() -> datetime:
    return datetime.utcnow()


def format_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()
