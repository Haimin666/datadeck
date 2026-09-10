"""运行时模式（datadeck：知识库不可用，恒等于 Yuxi LITE 模式）。"""

from __future__ import annotations


def lite_mode_enabled() -> bool:
    """datadeck 无知识库栈，始终返回 True（对应 Yuxi LITE_MODE 语义）。"""
    return True


def knowledge_capability_enabled() -> bool:
    return False
