"""受项目 Workdir 限制的 Agent 文件工具。"""

from __future__ import annotations

import json
from typing import Protocol

from langchain_core.tools import StructuredTool

MAX_READ_BYTES = 128 * 1024


class WorkdirCapability(Protocol):
    """Agent 内核所需的最小 Workdir 能力，由宿主层实现。"""

    def list_directory(self, path: str = "/") -> list[dict]: ...
    def search(self, query: str, **limits) -> list[dict]: ...
    def read_file_prefix(self, path: str, max_bytes: int) -> tuple[bytes, bool]: ...
    def write_file(self, path: str, content: bytes) -> dict: ...


def build_filesystem_tools(workdir: WorkdirCapability) -> list:
    """为一次 Agent 运行创建绑定到宿主已授权 Workdir 的工具实例。"""

    def list_directory(path: str = "/") -> str:
        return json.dumps(workdir.list_directory(path), ensure_ascii=False)

    def search_files(query: str) -> str:
        return json.dumps(workdir.search(query, max_results=50), ensure_ascii=False)

    def read_file(path: str) -> str:
        data, truncated = workdir.read_file_prefix(path, MAX_READ_BYTES)
        content = data.decode("utf-8", errors="replace")
        suffix = "\n[文件内容已截断]" if truncated else ""
        return content + suffix

    def write_file(path: str, content: str) -> str:
        result = workdir.write_file(path, content.encode("utf-8"))
        return json.dumps(result, ensure_ascii=False)

    return [
        StructuredTool.from_function(
            list_directory,
            name="workspace_list_directory",
            description="列出当前项目工作目录中的文件和子目录。path 必须是以 / 开头的项目内相对路径。",
        ),
        StructuredTool.from_function(
            search_files,
            name="workspace_search_files",
            description="在当前项目工作目录内按文件名搜索文件。",
        ),
        StructuredTool.from_function(
            read_file,
            name="workspace_read_file",
            description="读取当前项目工作目录内的 UTF-8 文本文件。",
        ),
        StructuredTool.from_function(
            write_file,
            name="workspace_write_file",
            description="写入当前项目工作目录内的文本文件；仅在用户明确要求时使用。",
        ),
    ]
