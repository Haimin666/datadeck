"""Agent 运行时资源契约。

这里仅定义宿主与 Agent 核心之间的不可变快照，不负责查询数据库或创建工具。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, Mapping

from datadeck.ports.tools import ToolDescriptor

ResourceStatus = Literal["mounted", "denied", "unavailable", "skipped"]
DiagnosticSeverity = Literal["info", "warning", "error"]
RUNTIME_SNAPSHOT_VERSION = 1


@dataclass(frozen=True, slots=True)
class RuntimeResource:
    """一次运行中的资源记录。"""

    kind: str
    key: str
    name: str = ""
    source: str = ""
    status: ResourceStatus = "mounted"
    reason: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.kind).strip():
            raise ValueError("runtime resource kind cannot be empty")
        if not str(self.key).strip():
            raise ValueError("runtime resource key cannot be empty")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "key": self.key,
            "name": self.name,
            "source": self.source,
            "status": self.status,
            "reason": self.reason,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class RuntimeResourceSnapshot:
    """Agent 单次运行使用的资源快照。

    ``selections[kind]`` 的语义固定为：
    - ``None``：该资源类型使用默认策略（通常是全部可用资源）；
    - 空 tuple：明确不挂载该资源类型；
    - 非空 tuple：只挂载列出的 key。
    """

    uid: str
    agent_slug: str
    thread_id: str
    project_id: str | None = None
    workdir_path: str | None = None
    selections: Mapping[str, tuple[str, ...] | None] = field(default_factory=dict)
    resources: tuple[RuntimeResource, ...] = ()
    diagnostics: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = RUNTIME_SNAPSHOT_VERSION
    agent_backend_id: str = ""
    permissions: tuple[str, ...] = ()
    package_selections: Mapping[str, tuple[str, ...] | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.uid).strip():
            raise ValueError("runtime snapshot uid cannot be empty")
        if not str(self.agent_slug).strip():
            raise ValueError("runtime snapshot agent_slug cannot be empty")
        if not str(self.thread_id).strip():
            raise ValueError("runtime snapshot thread_id cannot be empty")
        if int(self.schema_version) != RUNTIME_SNAPSHOT_VERSION:
            raise ValueError(f"unsupported runtime snapshot version: {self.schema_version}")

        normalized = self._normalize_selections(self.selections, "runtime selection")
        packages = self._normalize_selections(self.package_selections, "runtime package selection")
        object.__setattr__(self, "package_selections", MappingProxyType(packages))
        object.__setattr__(self, "permissions", tuple(
            sorted({str(item).strip() for item in self.permissions if str(item).strip()})
        ))
        object.__setattr__(self, "agent_backend_id", str(self.agent_backend_id or ""))
        object.__setattr__(self, "schema_version", int(self.schema_version))
        object.__setattr__(self, "selections", MappingProxyType(normalized))
        object.__setattr__(self, "resources", tuple(self.resources))
        object.__setattr__(self, "diagnostics", MappingProxyType(dict(self.diagnostics)))

    @classmethod
    def _normalize_selections(
        cls, values: Mapping[str, Any], label: str,
    ) -> dict[str, tuple[str, ...] | None]:
        if not isinstance(values, Mapping):
            raise ValueError(f"{label} must be a mapping")
        normalized: dict[str, tuple[str, ...] | None] = {}
        for kind, selected in values.items():
            if not str(kind).strip():
                raise ValueError("runtime selection kind cannot be empty")
            normalized[str(kind)] = cls.normalize_selection(selected)
        return normalized

    @staticmethod
    def normalize_selection(value: Any) -> tuple[str, ...] | None:
        """保留 None/空列表的区别，并拒绝非法资源选择项。"""
        if value is None:
            return None
        if not isinstance(value, (list, tuple, set)):
            raise ValueError("resource selection must be None or a list-like value")
        if any(not isinstance(item, str) for item in value):
            raise ValueError("resource selection items must be strings")
        return tuple(item.strip() for item in value if item.strip())

    def selected(self, kind: str) -> tuple[str, ...] | None:
        return self.selections.get(kind)

    def mounted_resources(self, kind: str | None = None) -> tuple[RuntimeResource, ...]:
        return tuple(
            item for item in self.resources
            if item.status == "mounted" and (kind is None or item.kind == kind)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "uid": self.uid,
            "agent_slug": self.agent_slug,
            "agent_backend_id": self.agent_backend_id,
            "thread_id": self.thread_id,
            "project_id": self.project_id,
            "workdir_path": self.workdir_path,
            "permissions": list(self.permissions),
            "selections": {
                kind: None if selected is None else list(selected)
                for kind, selected in self.selections.items()
            },
            "package_selections": {
                kind: None if selected is None else list(selected)
                for kind, selected in self.package_selections.items()
            },
            "resources": [item.to_dict() for item in self.resources],
            "diagnostics": dict(self.diagnostics),
        }


@dataclass(frozen=True, slots=True)
class RuntimeDiagnostic:
    """运行时装配或动态资源加载过程中的结构化诊断。"""

    code: str
    message: str
    severity: DiagnosticSeverity = "warning"
    resource_kind: str = ""
    resource_key: str = ""
    recoverable: bool = True
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.code).strip():
            raise ValueError("runtime diagnostic code cannot be empty")
        if not str(self.message).strip():
            raise ValueError("runtime diagnostic message cannot be empty")
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "resource_kind": self.resource_kind,
            "resource_key": self.resource_key,
            "recoverable": self.recoverable,
            "details": dict(self.details),
        }


class RuntimeAssemblyError(ValueError):
    """运行时选择或资源装配失败，携带可落库的结构化诊断。"""

    def __init__(self, diagnostic: RuntimeDiagnostic) -> None:
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


__all__ = [
    "ResourceStatus", "DiagnosticSeverity", "RuntimeResource",
    "RUNTIME_SNAPSHOT_VERSION", "RuntimeResourceSnapshot", "RuntimeDiagnostic",
    "RuntimeAssemblyError", "ToolDescriptor",
]
