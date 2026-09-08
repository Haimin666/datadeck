from __future__ import annotations

import traceback
from typing import Any

from datadeck import logger


def get_tool_info(tools) -> list[dict[str, Any]]:
    """获取所有工具的信息（用于前端展示；自 Yuxi 原样迁移）。"""
    tools_info: list[dict[str, Any]] = []
    try:
        for tool_obj in tools:
            try:
                metadata = getattr(tool_obj, "metadata", {}) or {}
                info = {
                    "id": tool_obj.name,
                    "name": metadata.get("name", tool_obj.name),
                    "description": tool_obj.description,
                    "metadata": metadata,
                    "args": [],
                }
                if hasattr(tool_obj, "args_schema") and tool_obj.args_schema:
                    schema = tool_obj.args_schema
                    if not isinstance(schema, dict):
                        schema = schema.schema()
                    for arg_name, arg_info in schema.get("properties", {}).items():
                        info["args"].append({
                            "name": arg_name,
                            "type": arg_info.get("type", ""),
                            "description": arg_info.get("description", ""),
                        })
                tools_info.append(info)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    f"Failed to process tool {getattr(tool_obj, 'name', 'unknown')}: {exc}\n{traceback.format_exc()}"
                )
                continue
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Failed to get tools info: {exc}\n{traceback.format_exc()}")
        return []
    return tools_info