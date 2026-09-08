# toolkits 包（自 Yuxi agents/toolkits 抽离，去掉 knowledge 工具/kbs）。

# @tool 注册依赖 import 副作用：buildin 工具必须在此导入才进 registry
from . import buildin  # noqa: F401  # 注册 echo/add/sql_validate
from .registry import (
    ToolExtraMetadata,
    get_all_extra_metadata,
    get_all_tool_instances,
    get_extra_metadata,
    tool,
)

__all__ = [
    "get_extra_metadata",
    "get_all_extra_metadata",
    "get_all_tool_instances",
    "ToolExtraMetadata",
    "tool",
]