"""统一工具描述契约。

工具注册、管理端展示和运行时审计都使用这个不可变描述；工具实例本身不
携带平台授权状态，授权只在每次 Run 的运行时快照中决定。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    slug: str
    name: str
    description: str = ""
    kind: str = "tool"
    group: str = "buildin"
    package_slug: str = ""
    source: str = "builtin"
    category: str = "buildin"
    version: str = "1"
    configurable: bool = True
    visible: bool = True
    fixed: bool = False
    enabled: bool = True
    risk_level: str = "read"
    requires_approval: bool = False
    tags: tuple[str, ...] = ()
    input_schema: Mapping[str, Any] = field(default_factory=dict)
    output_schema: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.slug).strip():
            raise ValueError("tool descriptor slug cannot be empty")
        object.__setattr__(self, "tags", tuple(str(item) for item in self.tags))
        object.__setattr__(self, "input_schema", MappingProxyType(dict(self.input_schema)))
        object.__setattr__(self, "output_schema", MappingProxyType(dict(self.output_schema)))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "kind": self.kind,
            "group": self.group,
            "package_slug": self.package_slug,
            "source": self.source,
            "category": self.category,
            "version": self.version,
            "configurable": self.configurable,
            "visible": self.visible,
            "fixed": self.fixed,
            "enabled": self.enabled,
            "risk_level": self.risk_level,
            "requires_approval": self.requires_approval,
            "tags": list(self.tags),
            "input_schema": dict(self.input_schema),
            "output_schema": dict(self.output_schema),
            "metadata": dict(self.metadata),
        }


__all__ = ["ToolDescriptor"]
