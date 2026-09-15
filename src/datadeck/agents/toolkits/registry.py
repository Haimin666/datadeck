"""工具注册器（自 Yuxi agents/toolkits/registry.py 原样迁移）。"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from threading import RLock


@dataclass
class ToolExtraMetadata:
    """附加元数据（用装饰器注册）。"""

    category: str = ""          # 分类: buildin, knowledge, subagents, debug, 等
    tags: list[str] = field(default_factory=list)
    display_name: str = ""
    icon: str = ""
    config_guide: str = ""
    group: str = ""
    package_slug: str = ""
    risk_level: str = "read"
    requires_approval: bool = False
    version: str = "1"
    source: str = "builtin"
    configurable: bool = True
    visible: bool = True
    enabled: bool = True


# 全局注册表: tool_name -> ToolExtraMetadata
_registry_lock = RLock()
_extra_registry: dict[str, ToolExtraMetadata] = {}
# 按 slug 保存实例，避免模块热加载或重复导入产生重复工具。
_tool_registry: dict[str, object] = {}


def get_extra_metadata(tool_name: str) -> ToolExtraMetadata | None:
    with _registry_lock:
        item = _extra_registry.get(tool_name)
        return deepcopy(item) if item is not None else None


def get_all_extra_metadata() -> dict[str, ToolExtraMetadata]:
    with _registry_lock:
        return deepcopy(_extra_registry)


def get_all_tool_instances() -> tuple:
    """返回不可变快照，调用方不能在运行中修改注册中心。"""
    with _registry_lock:
        return tuple(_tool_registry.values())


def register_tool(tool_obj, metadata: ToolExtraMetadata) -> None:
    """按 slug 原子注册工具；重复导入会替换同一 slug 的旧实现。"""
    slug = str(getattr(tool_obj, "name", "") or "").strip()
    if not slug:
        raise ValueError("tool slug cannot be empty")
    with _registry_lock:
        _tool_registry[slug] = tool_obj
        _extra_registry[slug] = metadata


def unregister_tool(slug: str) -> bool:
    """注销工具并返回是否存在。"""
    normalized = str(slug or "").strip()
    if not normalized:
        return False
    with _registry_lock:
        existed = normalized in _tool_registry
        _tool_registry.pop(normalized, None)
        _extra_registry.pop(normalized, None)
        return existed


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
    package_slug: str = "",
    risk_level: str = "read",
    requires_approval: bool = False,
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
        metadata = ToolExtraMetadata(
            category=category, tags=tags or [], display_name=display_name,
            icon=icon, config_guide=config_guide, package_slug=package_slug,
            risk_level=risk_level, requires_approval=requires_approval,
        )
        tool_obj.handle_tool_error = True
        register_tool(tool_obj, metadata)
        # 注册发生在运行期时，让工具元数据快照立即失效；导入期循环依赖则安全忽略。
        try:
            from datadeck.agents.toolkits.service import invalidate_tool_metadata_cache
        except ImportError:
            pass
        else:
            invalidate_tool_metadata_cache()
        return tool_obj

    return decorator
