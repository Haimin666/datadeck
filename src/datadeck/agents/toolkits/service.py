"""工具解析服务（自 Yuxi agents/toolkits/service.py 抽离）。

去掉 MCP / Skill 门控工具，聚焦按 context.tools 选 buildin 工具。
MCP/Skill 依赖工具的自动注册由宿主经 middleware 扩展。
"""

from __future__ import annotations

from threading import RLock
from typing import Any

from datadeck import logger

_metadata_cache: list[dict] = []
_metadata_lock = RLock()
_WORKSPACE_TOOL_METADATA = [
    {"slug": "workspace_list_directory", "name": "浏览项目文件", "description": "列出当前项目工作目录中的文件和子目录。"},
    {"slug": "workspace_search_files", "name": "搜索项目文件", "description": "在当前项目工作目录内按文件名搜索文件。"},
    {"slug": "workspace_read_file", "name": "读取项目文件", "description": "读取当前项目工作目录内的 UTF-8 文本文件。"},
    {"slug": "workspace_write_file", "name": "写入项目文件", "description": "写入当前项目工作目录内的文本文件。"},
]
_PLATFORM_TOOL_METADATA = [
    {"slug": "read_file", "name": "读取 Skill 文件", "description": "读取当前用户已授权 Skill 的文本文件。"},
    {"slug": "scheduled_task_list", "name": "查看定时任务", "description": "列出当前用户的定时任务。"},
    {"slug": "scheduled_task_create", "name": "创建定时任务", "description": "创建定时执行的 Agent 任务，仅在用户明确要求时调用。"},
    {"slug": "scheduled_task_update", "name": "修改定时任务", "description": "修改当前用户的定时任务，仅在用户明确要求时调用。"},
    {"slug": "scheduled_task_delete", "name": "删除定时任务", "description": "删除当前用户的定时任务，仅在用户明确要求时调用。"},
    {"slug": "subagent_start", "name": "启动子智能体", "description": "启动一个已配置的子智能体执行独立任务。"},
    {"slug": "subagent_status", "name": "查询子智能体", "description": "查询子智能体运行状态。"},
    {"slug": "subagent_events", "name": "读取子智能体事件", "description": "读取子智能体的结构化运行事件。"},
    {"slug": "subagent_cancel", "name": "取消子智能体", "description": "取消一个正在运行的子智能体。"},
    {"slug": "subagent_await", "name": "等待子智能体", "description": "等待子智能体完成并返回结果。"},
    {"slug": "subagent_orchestrate", "name": "编排子智能体", "description": "按依赖关系并行编排多个子智能体并汇总结果。"},
]


def _extract_tool_info(tool_obj) -> dict:
    metadata = getattr(tool_obj, "metadata", {}) or {}
    info = {
        "slug": getattr(tool_obj, "name", ""),
        "name": metadata.get("name", getattr(tool_obj, "name", "")),
        "description": getattr(tool_obj, "description", ""),
        "metadata": metadata,
        "args": [],
    }
    args_schema = getattr(tool_obj, "args_schema", None)
    if args_schema:
        schema = args_schema.schema() if hasattr(args_schema, "schema") else args_schema
        for arg_name, arg_info in schema.get("properties", {}).items():
            info["args"].append({
                "name": arg_name,
                "type": arg_info.get("type", ""),
                "description": arg_info.get("description", ""),
            })
    return info


def _ensure_metadata_loaded() -> None:
    with _metadata_lock:
        if _metadata_cache:
            return

        from datadeck.agents.toolkits.registry import (
            get_all_extra_metadata,
            get_all_tool_instances,
        )

        for tool in get_all_tool_instances():
            runtime_info = _extract_tool_info(tool)
            extra_meta = get_all_extra_metadata().get(tool.name)
            runtime_info["category"] = extra_meta.category if extra_meta else "buildin"
            runtime_info["tags"] = list(extra_meta.tags) if extra_meta and extra_meta.tags else []
            runtime_info["config_guide"] = extra_meta.config_guide if extra_meta else ""
            if extra_meta and extra_meta.display_name:
                runtime_info["name"] = extra_meta.display_name
            _metadata_cache.append(runtime_info)


def invalidate_tool_metadata_cache() -> None:
    """工具注册表变化后清空元数据快照，下一次读取时重建。"""
    with _metadata_lock:
        _metadata_cache.clear()


def get_tool_metadata(category: str | None = None) -> list[dict]:
    _ensure_metadata_loaded()
    with _metadata_lock:
        result = list(_metadata_cache)
    if not any(item["slug"] == _WORKSPACE_TOOL_METADATA[0]["slug"] for item in result):
        result.extend({**item, "category": "filesystem", "tags": [], "args": []} for item in _WORKSPACE_TOOL_METADATA)
    known_slugs = {item["slug"] for item in result}
    result.extend(
        {**item, "category": "platform", "tags": ["平台能力"], "args": []}
        for item in _PLATFORM_TOOL_METADATA if item["slug"] not in known_slugs
    )
    if category:
        return [tool for tool in result if tool.get("category") == category]
    return result


def get_tool_instances_for_context(context) -> list[Any]:
    """按 context.tools（None=全部 buildin）解析工具实例列表。"""
    from datadeck.agents.toolkits.registry import (
        get_all_extra_metadata,
        get_all_tool_instances,
    )

    extra_meta = get_all_extra_metadata()

    def _default_category(meta) -> str:
        return meta.category if meta else "buildin"

    # 默认装配：示例 buildin + 企业数据工具（sql/omd/rag）
    allowed_categories = {"buildin", "data"}
    buildin_tools = {
        tool.name: tool
        for tool in get_all_tool_instances()
        if _default_category(extra_meta.get(tool.name)) in allowed_categories
    }
    filesystem_tools = []
    if getattr(context, "workdir", None) is not None:
        from datadeck.agents.toolkits.filesystem_tools import build_filesystem_tools

        filesystem_tools = build_filesystem_tools(context.workdir)
        buildin_tools.update({tool.name: tool for tool in filesystem_tools})

    selected = getattr(context, "tools", None)
    if selected is None:
        return list(buildin_tools.values())
    if (getattr(context, "knowledge_base_collection", None)
            or getattr(context, "knowledge_base_id", None)
            or getattr(context, "knowledges", None)) and "rag_search" in buildin_tools:
        # 选择了知识库就必须具备检索入口，避免前端保存的工具白名单把 RAG 静默排除。
        selected = [*selected, "rag_search"]
    tools: list[Any] = []
    seen: set[str] = set()
    for name in selected:
        if not isinstance(name, str) or name in seen:
            continue
        tool = buildin_tools.get(name)
        if tool is None:
            logger.warning(f"Configured buildin tool not found, skip: {name}")
            continue
        tools.append(tool)
        seen.add(name)
    return tools


async def resolve_configured_runtime_tools(context) -> list[Any]:
    """graph 构建入口（对应 Yuxi resolve_configured_runtime_tools，去掉 MCP/Skill）。"""
    return get_tool_instances_for_context(context)
