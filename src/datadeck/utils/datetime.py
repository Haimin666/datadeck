"""datadeck 时间工具（自 Yuxi utils.datetime_utils 简化为时区无关的当天日期）。"""

from __future__ import annotations

from datetime import date


def today_str() -> str:
    return date.today().strftime("%Y-%m-%d")