"""工具注册器（自 Yuxi agents/toolkits/registry.py 原样迁移）。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class ToolExtraMetadata:
    """附加元数据（用装饰器注册）。"""

    category: str = ""          # 分类: buildin, knowledge, subagents, debug, 等
    tags: list[str] = field(default_factory=list)
    display_name: str = ""
    icon: str = ""
    config_guide: str = ""


# 全局注册表: tool_name -> ToolExtraMetadata
_extra_registry: dict[str, ToolExtraMetadata] = {}
# 全局工具实例列表（由 @tool 装饰器自动收集）
_all_tool_instances: list = []


def get_extra_metadata(tool_name: str) -> ToolExtraMetadata | None:
    return _extra_registry.get(tool_name)


def get_all_extra_metadata() -> dict[str, ToolExtraMetadata]:
    return _extra_registry.copy()


def get_all_tool_instances() -> list:
    return _all_tool_instances


def tool(
    category: str = "",
    tags: list[str] = None,
    display_name: str = "",
    icon: str = "",
    config_guide: str = "",
    name_or_callable: str | Callable | None = None,
    description: str | None = None,
    args_schema: type | None = None,
    return_direct: bool = False,
):
    """基于 langchain.tool 的拓展装饰器，同时注册元数据与收集实例。

    用法:
        @tool(category="buildin", tags=["计算"], display_name="计算器")
        def calculator(a: float, b: float, operation: str) -> float: ...
    """
    from langchain.tools import tool as langchain_tool

    langchain_decorator = langchain_tool(
        name_or_callable=name_or_callable,
        description=description,
        args_schema=args_schema,
        return_direct=return_direct,
    )

    def decorator(func: Callable) -> Callable:
        tool_obj = langchain_decorator(func)
        _extra_registry[tool_obj.name] = ToolExtraMetadata(
            category=category, tags=tags or [], display_name=display_name,
            icon=icon, config_guide=config_guide,
        )
        tool_obj.handle_tool_error = True
        _all_tool_instances.append(tool_obj)
        return tool_obj

    return decorator