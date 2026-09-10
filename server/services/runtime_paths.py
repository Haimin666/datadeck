"""Agent 运行时虚拟路径契约（自 Yuxi agents/backends/paths.py 照搬）。

datadeck 无沙箱 Backend：这些函数只做「Workdir/Workspace scope ↔ 虚拟绝对路径」
的字符串映射，供 viewer/mention/前端提示词使用统一命名空间；不涉及真实挂载。
"""

from __future__ import annotations

from pathlib import PurePosixPath

from server.workspace.paths import VIRTUAL_PATH_PREFIX, normalize_workdir_path
from server.services.skills.virtual_paths import VIRTUAL_SKILLS_PATH

LARGE_TOOL_RESULTS_DIR_NAME = "large_tool_results"
CONVERSATION_HISTORY_DIR_NAME = "conversation_history"


def runtime_workdir_path(workdir_path: str) -> str:
    """把持久化 Workdir 标识映射到运行时虚拟路径。"""
    return f"{VIRTUAL_PATH_PREFIX.rstrip('/')}/{normalize_workdir_path(workdir_path)}"


def workdir_runtime_paths(workdir_path: str) -> tuple[str, str]:
    """返回当前 Workdir runtime 的大结果与对话历史目录。"""
    normalized = PurePosixPath(str(workdir_path)).as_posix().rstrip("/")
    if not normalized.startswith(f"{VIRTUAL_PATH_PREFIX.rstrip('/')}/"):
        raise ValueError("workdir_path must be a Backend runtime path")
    outputs = f"{normalized}/outputs"
    return (
        f"{outputs}/large_tool_results",
        f"{outputs}/conversation_history",
    )


def runtime_user_data_path(workspace_path: str) -> str:
    """把 UserWorkspace scope 映射到运行时虚拟路径。"""
    raw = str(workspace_path or "").strip()
    pure = PurePosixPath(raw)
    if not pure.is_absolute() or ".." in pure.parts or "\\" in raw or "://" in raw:
        raise ValueError("invalid Workspace scope path")
    root = VIRTUAL_PATH_PREFIX.rstrip("/")
    return root if pure.as_posix() == "/" else f"{root}{pure.as_posix()}"


def runtime_path_for_workdir_scope(workdir_path: str, path: str | None) -> str:
    """把 Workdir scope 映射到运行时虚拟绝对路径。"""
    raw = str(path or "/").strip() or "/"
    pure = PurePosixPath(raw)
    if not pure.is_absolute() or ".." in pure.parts or "\\" in raw or "://" in raw:
        raise ValueError("invalid Workdir scope path")
    workspace_root = f"/{normalize_workdir_path(workdir_path)}"
    workspace_path = workspace_root if pure.as_posix() == "/" else f"{workspace_root}{pure.as_posix()}"
    return runtime_user_data_path(workspace_path)


def workdir_scope_from_runtime_path(workdir_path: str, runtime_path: str) -> str:
    """把当前 Workdir runtime 路径还原为持久化 Workdir scope。"""
    normalized = PurePosixPath(str(runtime_path)).as_posix()
    root = runtime_workdir_path(workdir_path).rstrip("/")
    if normalized == root:
        return "/"
    if not normalized.startswith(f"{root}/"):
        raise ValueError("runtime path is outside the Workdir")
    return f"/{normalized[len(root) + 1 :]}"


def is_runtime_path(path: str) -> bool:
    """判断绝对路径是否属于运行时命名空间。"""
    normalized = PurePosixPath(str(path)).as_posix()
    roots = (VIRTUAL_PATH_PREFIX.rstrip("/"), VIRTUAL_SKILLS_PATH.rstrip("/"))
    return any(normalized == root or normalized.startswith(f"{root}/") for root in roots)


__all__ = [
    "CONVERSATION_HISTORY_DIR_NAME",
    "LARGE_TOOL_RESULTS_DIR_NAME",
    "VIRTUAL_PATH_PREFIX",
    "VIRTUAL_SKILLS_PATH",
    "is_runtime_path",
    "runtime_path_for_workdir_scope",
    "runtime_user_data_path",
    "runtime_workdir_path",
    "workdir_runtime_paths",
    "workdir_scope_from_runtime_path",
]
