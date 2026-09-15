"""面向 Agent 配置的能力包目录。

能力包是配置层的稳定契约；包内真实工具只在运行时展开，便于后续替换实现。
"""

from __future__ import annotations

TOOL_PACKAGES: dict[str, dict] = {
    "package:core": {
        "name": "基础对话",
        "description": "通用 Agent 的基础内置工具。",
        "group": "capability",
        "tools": ("echo", "add"),
    },
    "package:platform": {
        "name": "平台操作",
        "description": "工作区命令、Skill 文件、定时任务等平台能力。",
        "group": "capability",
        "tools": (
        "workspace_list_directory", "workspace_search_files", "workspace_read_file", "workspace_write_file",
            "execute", "run_skill_script", "read_file", "read_attachment", "present_artifacts", "ask_user_question", "scheduled_task_list", "scheduled_task_create",
            "scheduled_task_update", "scheduled_task_delete",
        ),
    },
    "package:subagents": {
        "name": "子智能体协作",
        "description": "启动、编排、等待和管理已授权的子智能体。",
        "group": "capability",
        "tools": ("subagent_start", "subagent_status", "subagent_events", "subagent_cancel", "subagent_await", "subagent_orchestrate"),
    },
    "package:sql": {
        "name": "SQL 分析",
        "description": "只读 SQL 校验与执行能力。",
        "group": "capability",
        "tools": ("sql_validate", "sql_execute_query"),
    },
    "package:omd": {
        "name": "OMD 元数据",
        "description": "库、表、字段、描述和血缘查询能力。",
        "group": "capability",
        "tools": ("omd_list_services", "omd_list_databases", "omd_search_tables", "omd_list_tables", "omd_get_table_schema", "omd_get_table_lineage"),
    },
    "package:knowledge": {
        "name": "知识库检索",
        "description": "知识库和已审核指标口径检索能力。",
        "group": "capability",
        "tools": ("rag_search", "metric_lookup"),
    },
    "package:code": {
        "name": "数仓代码检索",
        "description": "按需检索已授权 Git 数仓代码、SQL 和注释。",
        "group": "capability",
        "tools": ("code_search",),
    },
}

TOOL_TO_PACKAGE = {
    tool: package
    for package, metadata in TOOL_PACKAGES.items()
    for tool in metadata["tools"]
}

MCP_PACKAGE_PREFIX = "package:mcp:"


def mcp_package_slug(server_slug: str) -> str:
    """把 MCP Server 的唯一 slug 映射为 Agent 配置中的工具包 slug。"""
    value = str(server_slug or "").strip()
    if not value or ":" in value:
        raise ValueError("MCP server slug cannot be empty or contain ':'")
    return f"{MCP_PACKAGE_PREFIX}{value}"


def mcp_server_slug_from_package(value: str) -> str | None:
    """从动态 MCP 工具包中取出 Server slug；普通工具包返回 None。"""
    normalized = str(value or "").strip()
    if not normalized.startswith(MCP_PACKAGE_PREFIX):
        return None
    server_slug = normalized[len(MCP_PACKAGE_PREFIX):].strip()
    return server_slug or None


def mcp_package_options(servers) -> list[dict]:
    """生成与静态能力包相同 Schema 的 MCP 动态能力包。"""
    options = []
    for server in servers or []:
        if isinstance(server, dict):
            slug = server.get("slug")
            name = server.get("name")
            description = server.get("description", "")
        else:
            slug = getattr(server, "slug", "")
            name = getattr(server, "name", "")
            description = getattr(server, "description", "") or ""
        try:
            package_slug = mcp_package_slug(slug)
        except ValueError:
            continue
        options.append({
            "slug": package_slug,
            "kind": "package",
            "name": f"MCP · {name or slug}",
            "description": description or f"挂载 MCP Server：{slug}",
            "group": "mcp",
            "package_slug": package_slug,
            "tools": [],
            "fixed": False,
            "package": True,
            "configurable": True,
            "metadata": {"server_slug": str(slug)},
        })
    return options


def expand_tool_selection(selection) -> list[str]:
    """将能力包选择展开为运行时工具名。

    运行时策略中的 fixed_tools 也会进入此函数，因此已注册工具名是
    内部策略输入，不是编辑器的第二种配置格式；用户配置只允许 package:*。
    未知值不展开，最终由运行时注册表过滤。
    """
    result: list[str] = []
    seen: set[str] = set()
    for item in selection or []:
        value = str(item).strip()
        if value in TOOL_PACKAGES:
            values = TOOL_PACKAGES[value]["tools"]
        elif mcp_server_slug_from_package(value):
            # MCP 工具由宿主在统一装配器中按 package slug 动态发现。
            values = ()
        elif value in TOOL_TO_PACKAGE:
            # AgentPolicy 的 fixed_tools 是运行时内部输入，不是用户配置格式。
            values = (value,)
        else:
            continue
        for tool in values:
            if tool not in seen:
                result.append(tool)
                seen.add(tool)
    return result


def package_options(*, fixed_packages: set[str] | None = None) -> list[dict]:
    fixed_packages = fixed_packages or set()
    return [
        {
            "slug": slug,
            "kind": "package",
            "name": item["name"],
            "description": item["description"],
            "group": item["group"],
            "package_slug": slug,
            "tools": list(item["tools"]),
            "fixed": slug in fixed_packages,
            "package": True,
            "configurable": slug not in fixed_packages,
        }
        for slug, item in TOOL_PACKAGES.items()
    ]


__all__ = [
    "MCP_PACKAGE_PREFIX", "TOOL_PACKAGES", "TOOL_TO_PACKAGE",
    "expand_tool_selection", "mcp_package_options", "mcp_package_slug",
    "mcp_server_slug_from_package", "package_options",
]
