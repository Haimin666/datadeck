"""工具解析服务（自 Yuxi agents/toolkits/service.py 抽离）。

去掉 MCP / Skill 门控工具，聚焦按 context.tools 选 buildin 工具。
MCP/Skill 依赖工具的自动注册由宿主经 middleware 扩展。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from datadeck import logger


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


def get_tool_instances_for_context(context) -> list[Any]:
    """按 context.tools（None=全部 buildin）解析工具实例列表。"""
    from datadeck.agents.toolkits.registry import (
        get_all_extra_metadata,
        get_all_tool_instances,
    )

    extra_meta = get_all_extra_metadata()
    buildin_tools = {
        tool.name: tool
        for tool in get_all_tool_instances()
        if (extra_meta.get(tool.name).category if extra_meta.get(tool.name) else "buildin") == "buildin"
    }
    selected = getattr(context, "tools", None)
    if selected is None:
        return list(buildin_tools.values())
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