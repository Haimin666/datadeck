"""运行时目录与数据根配置（自 Yuxi config/__init__.py 抽取照搬，env 前缀 DATADECK_）。

datadeck 单进程部署：所有持久数据统一收在 DATADECK_DATA_ROOT（默认 ./data）下。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def _data_root() -> Path:
    """datadeck 数据根：所有持久目录的父目录。"""
    configured = os.getenv("DATADECK_DATA_ROOT")
    if configured:
        return Path(configured)
    # server/dataroot.py -> <project-root>/data
    return Path(__file__).resolve().parents[2] / "data"


def get_runtime_dir() -> Path:
    """读取可丢弃日志与缓存使用的当前进程运行目录。"""
    configured = os.getenv("DATADECK_RUNTIME_DIR")
    if configured:
        return Path(configured)
    return Path(tempfile.gettempdir()) / f"datadeck-runtime-{os.getpid()}"


def get_skill_data_dir() -> Path:
    """读取共享与个人 Skill 持久源目录。"""
    configured = os.getenv("DATADECK_SKILL_DATA_DIR")
    return Path(configured) if configured else _data_root() / "skill-sources"


def get_skill_projection_dir() -> Path:
    """读取用户授权 Skill 只读投影目录。"""
    configured = os.getenv("DATADECK_SKILL_PROJECTION_DIR")
    return Path(configured) if configured else _data_root() / "skill-projections"


def get_user_data_dir() -> Path:
    """读取用户级实时文件持久目录（UserWorkspace 数据根）。"""
    configured = os.getenv("DATADECK_USER_DATA_DIR")
    return Path(configured) if configured else _data_root() / "user-data"


__all__ = [
    "get_runtime_dir",
    "get_skill_data_dir",
    "get_skill_projection_dir",
    "get_user_data_dir",
]
