"""敏感工具人工审批（自 Yuxi agents/tool_approval.py 原样迁移）。

模式 default / always_trust。default 下敏感工具(write_file/edit_file/execute)
在 HumanInTheLoopMiddleware 中被拦截，命中审批谓词时请求人工确认。
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal

from langchain.agents.middleware import HumanInTheLoopMiddleware

ToolApprovalMode = Literal["default", "always_trust"]

DEFAULT_TOOL_APPROVAL_MODE: ToolApprovalMode = "default"
TOOL_APPROVAL_MODES = frozenset({"default", "always_trust"})
SENSITIVE_BACKEND_TOOLS = frozenset({"write_file", "edit_file", "execute"})
_ALLOWED_DECISIONS = ["approve", "reject"]


def normalize_tool_approval_mode(value: object) -> ToolApprovalMode:
    mode = value.strip() if isinstance(value, str) else value
    if mode not in TOOL_APPROVAL_MODES:
        raise ValueError(f"不支持的 tool_approval_mode: {value}")
    return mode


def create_tool_approval_middleware(
    mode: ToolApprovalMode,
    *,
    current_project_path: str | None = None,
):
    """按审批模式与当前 Project 构造敏感工具审批中间件。"""
    if mode == "always_trust":
        return None

    write_requires_approval = _project_write_requires_approval(current_project_path or "")

    return HumanInTheLoopMiddleware(
        interrupt_on={
            "write_file": {"allowed_decisions": _ALLOWED_DECISIONS, "when": write_requires_approval},
            "edit_file": {"allowed_decisions": _ALLOWED_DECISIONS, "when": write_requires_approval},
            "execute": {"allowed_decisions": _ALLOWED_DECISIONS},
        }
    )


def _project_write_requires_approval(current_project_path: str):
    """创建仅豁免当前 Project 写入的审批谓词：Project 外写入始终请求人工确认。"""
    project_root = _normalize_runtime_path(current_project_path)

    def requires_approval(request) -> bool:
        args = request.tool_call.get("args")
        file_path = _normalize_runtime_path(args.get("file_path") if isinstance(args, dict) else None)
        if project_root is None or file_path is None:
            return True
        return file_path != project_root and project_root not in file_path.parents

    return requires_approval


def _normalize_runtime_path(value: object) -> PurePosixPath | None:
    """将审批参数收敛为无跳转的绝对路径；非法(相对/.. / 反斜杠/协议)返回 None。"""
    if not isinstance(value, str):
        return None
    raw = value.strip()
    path = PurePosixPath(raw)
    if not raw or not path.is_absolute() or ".." in path.parts or "\\" in raw or "://" in raw:
        return None
    return path