"""工具解析服务（自 Yuxi agents/toolkits/service.py 抽离）。

去掉 MCP / Skill 门控工具，聚焦按 context.tools 选 buildin 工具。
MCP/Skill 依赖工具的自动注册由宿主经 middleware 扩展。
"""

from __future__ import annotations

from copy import deepcopy
from threading import RLock
from typing import Any

from datadeck import logger
from datadeck.agents.toolkits.packages import TOOL_TO_PACKAGE, package_options, expand_tool_selection
from datadeck.ports.tools import ToolDescriptor

# 只在锁内替换整个 tuple；调用方拿到的是深拷贝，避免运行中的请求修改全局注册快照。
_metadata_cache: tuple[dict, ...] | None = None
_metadata_lock = RLock()
_WORKSPACE_TOOL_METADATA = [
    {"slug": "workspace_list_directory", "name": "浏览项目文件", "description": "列出当前项目工作目录中的文件和子目录。", "risk_level": "read"},
    {"slug": "workspace_search_files", "name": "搜索项目文件", "description": "在当前项目工作目录内按文件名搜索文件。", "risk_level": "read"},
    {"slug": "workspace_read_file", "name": "读取项目文件", "description": "读取当前项目工作目录内的 UTF-8 文本文件。", "risk_level": "read"},
    {"slug": "workspace_write_file", "name": "写入项目文件", "description": "写入当前项目工作目录内的文本文件。", "risk_level": "write", "requires_approval": True},
]
_PLATFORM_TOOL_METADATA = [
    {"slug": "execute", "name": "执行项目命令", "description": "在当前项目工作目录内执行命令；默认需要人工审批。", "risk_level": "write", "requires_approval": True},
    {"slug": "run_skill_script", "name": "执行 Skill 脚本", "description": "执行已挂载 Skill 的 scripts/ 下脚本；默认需要人工审批。", "risk_level": "write", "requires_approval": True},
    {"slug": "read_file", "name": "读取 Skill 文件", "description": "读取当前用户已授权 Skill 的文本文件。"},
    {"slug": "read_attachment", "name": "读取对话附件", "description": "读取当前对话已上传的文本附件。"},
    {"slug": "present_artifacts", "name": "交付文件产物", "description": "登记当前 Workdir /outputs 下的文件，供对话中预览和下载。", "package_slug": "package:platform", "category": "platform", "group": "platform", "tags": ["产物"]},
    {"slug": "metric_lookup", "name": "指标口径查询", "description": "按名称、别名和定义查询已审核的 Ossie 指标口径。", "package_slug": "package:knowledge", "category": "knowledge", "group": "knowledge", "tags": ["RAG", "指标口径"]},
    {"slug": "code_search", "name": "数仓代码检索", "description": "按需检索已授权 Git 数仓代码、SQL 和注释，不自动写入知识库。", "package_slug": "package:code", "category": "knowledge", "group": "knowledge", "tags": ["代码", "SQL", "数仓"]},
    {"slug": "ask_user_question", "name": "向用户提问", "description": "遇到无法安全判断的歧义时暂停运行并请求用户选择。"},
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
    args_schema = getattr(tool_obj, "args_schema", None)
    input_schema = {}
    if args_schema:
        input_schema = (
            args_schema.model_json_schema()
            if hasattr(args_schema, "model_json_schema")
            else args_schema.schema() if hasattr(args_schema, "schema") else {}
        )
    info = {
        "slug": getattr(tool_obj, "name", ""),
        "name": metadata.get("name", getattr(tool_obj, "name", "")),
        "description": getattr(tool_obj, "description", ""),
        "metadata": metadata,
        "args": [],
        "input_schema": input_schema,
        "output_schema": {},
    }
    if args_schema:
        schema = (
            args_schema.model_json_schema()
            if hasattr(args_schema, "model_json_schema")
            else args_schema.schema() if hasattr(args_schema, "schema") else args_schema
        )
        for arg_name, arg_info in schema.get("properties", {}).items():
            info["args"].append({
                "name": arg_name,
                "type": arg_info.get("type", ""),
                "description": arg_info.get("description", ""),
            })
    return info


def _ensure_metadata_loaded() -> None:
    with _metadata_lock:
        global _metadata_cache
        if _metadata_cache is not None:
            return

        from datadeck.agents.toolkits.registry import (
            get_all_extra_metadata,
            get_all_tool_instances,
        )

        metadata: list[dict] = []
        extra_registry = get_all_extra_metadata()
        for tool in get_all_tool_instances():
            runtime_info = _extract_tool_info(tool)
            extra_meta = extra_registry.get(tool.name)
            runtime_info["category"] = extra_meta.category if extra_meta else "buildin"
            runtime_info["group"] = (
                extra_meta.group if extra_meta and extra_meta.group
                else runtime_info["category"]
            )
            runtime_info["kind"] = "tool"
            runtime_info["source"] = "builtin"
            runtime_info["package_slug"] = (
                extra_meta.package_slug if extra_meta and extra_meta.package_slug
                else TOOL_TO_PACKAGE.get(tool.name, "")
            )
            runtime_info["risk_level"] = extra_meta.risk_level if extra_meta else "read"
            runtime_info["requires_approval"] = bool(extra_meta.requires_approval) if extra_meta else False
            runtime_info["tags"] = list(extra_meta.tags) if extra_meta and extra_meta.tags else []
            runtime_info["version"] = extra_meta.version if extra_meta else "1"
            runtime_info["visible"] = not bool(runtime_info["package_slug"])
            runtime_info["configurable"] = not bool(runtime_info["package_slug"])
            runtime_info["config_guide"] = extra_meta.config_guide if extra_meta else ""
            if extra_meta and extra_meta.display_name:
                runtime_info["name"] = extra_meta.display_name
            metadata.append(runtime_info)
        _metadata_cache = tuple(metadata)


def invalidate_tool_metadata_cache() -> None:
    """工具注册表变化后清空元数据快照，下一次读取时重建。"""
    global _metadata_cache
    with _metadata_lock:
        _metadata_cache = None


def get_tool_metadata(category: str | None = None) -> list[dict]:
    _ensure_metadata_loaded()
    with _metadata_lock:
        result = deepcopy(list(_metadata_cache or ()))
    if not any(item["slug"] == _WORKSPACE_TOOL_METADATA[0]["slug"] for item in result):
        result.extend({
            **item,
            "category": "filesystem",
            "group": "filesystem",
            "kind": "tool",
            "source": "platform",
            "package_slug": "package:platform",
            "fixed": False,
            "package": False,
            "requires_approval": bool(item.get("requires_approval", False)),
            "input_schema": {},
            "output_schema": {},
            "tags": [],
            "args": [],
        } for item in _WORKSPACE_TOOL_METADATA)
    known_slugs = {item["slug"] for item in result}
    result.extend(
        {
            **item,
            "category": item.get("category", "platform"),
            "group": item.get("group", "platform"),
            "kind": "tool",
            "source": "platform",
            "package_slug": item.get("package_slug", "package:platform"),
            "fixed": False,
            "package": False,
            "requires_approval": bool(item.get("requires_approval", False)),
            "input_schema": {},
            "output_schema": {},
            "tags": item.get("tags", ["平台能力"]),
            "args": [],
        }
        for item in _PLATFORM_TOOL_METADATA if item["slug"] not in known_slugs
    )
    if category:
        return [tool for tool in result if tool.get("category") == category]
    return result


def get_tool_descriptors(
    category: str | None = None,
    *,
    include_packages: bool = True,
    include_internal: bool = True,
    fixed_packages: set[str] | None = None,
) -> list[ToolDescriptor]:
    """返回管理端、装配器和审计共用的不可变工具描述。"""
    descriptors: list[ToolDescriptor] = []
    if include_packages:
        package_rows = package_options(fixed_packages=fixed_packages)
        for item in package_rows:
            descriptors.append(ToolDescriptor(
                slug=item["slug"], name=item["name"], description=item["description"],
                kind="package", group=item["group"], package_slug=item["slug"],
                source="builtin", category="capability", version="1",
                configurable=bool(item.get("configurable", True)), visible=True,
                fixed=bool(item.get("fixed", False)), tags=[item["group"]],
                metadata={"tools": list(item.get("tools", []))},
            ))

    for item in get_tool_metadata(category):
        if not include_internal and not item.get("package", False):
            continue
        if not include_internal and item.get("package_slug"):
            continue
        package_slug = str(item.get("package_slug") or "")
        if not include_internal and package_slug:
            continue
        descriptors.append(ToolDescriptor(
            slug=str(item.get("slug") or ""), name=str(item.get("name") or item.get("slug") or ""),
            description=str(item.get("description") or ""), kind=str(item.get("kind") or "tool"),
            group=str(item.get("group") or item.get("category") or "buildin"),
            package_slug=package_slug, source=str(item.get("source") or "builtin"),
            category=str(item.get("category") or "buildin"), version=str(item.get("version") or "1"),
            configurable=bool(item.get("configurable", not package_slug)),
            visible=bool(item.get("visible", not package_slug)), fixed=bool(item.get("fixed", False)),
            enabled=bool(item.get("enabled", True)), risk_level=str(item.get("risk_level") or "read"),
            requires_approval=bool(item.get("requires_approval", False)),
            tags=tuple(item.get("tags") or ()), input_schema=item.get("input_schema") or {},
            output_schema=item.get("output_schema") or {},
            metadata={"config_guide": item.get("config_guide", "")},
        ))
    return [item for item in descriptors if not category or item.category == category]


def get_tool_instances_for_context(context) -> list[Any]:
    """按 context.tools（None=全部 buildin）解析工具实例列表。"""
    # 纯闲聊由任务分类器确定不需要任何工具；在核心解析入口再次
    # 门控，避免工作区工具绕过宿主工具层进入正式 Graph。
    if getattr(context, "task_kind", "") == "chat":
        return []

    from datadeck.agents.toolkits.registry import (
        get_all_extra_metadata,
        get_all_tool_instances,
    )

    extra_meta = get_all_extra_metadata()

    def _default_category(meta) -> str:
        return meta.category if meta else "buildin"

    # 工具注册表保留完整能力，默认返回时只暴露基础工具；显式选择能力包
    # 后再从完整注册表展开对应的 SQL/OMD/RAG 工具。
    allowed_categories = {"buildin", "data", "sql"}
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
    # 正式运行时以请求级快照为唯一工具选择来源。
    runtime_snapshot = getattr(context, "_runtime_snapshot", None)
    if runtime_snapshot is not None:
        selected = runtime_snapshot.selected("tools")
    selected = expand_tool_selection(selected) if selected is not None else None
    if selected is None:
        if (getattr(context, "knowledge_base_collection", None)
                or getattr(context, "knowledge_base_id", None)
                or getattr(context, "knowledges", None)):
            selected = ["rag_search"]
        else:
            return [
                tool for name, tool in buildin_tools.items()
                if _default_category(extra_meta.get(name)) == "buildin"
            ]
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
            # 平台工具在宿主装配器中注入，能力包展开到核心注册器时不应
            # 把它们误报为缺失；真正的未知配置仍保留 warning 便于排查。
            if name in TOOL_TO_PACKAGE:
                logger.debug(f"Configured platform tool deferred to host runtime: {name}")
            else:
                logger.warning(f"Configured buildin tool not found, skip: {name}")
            continue
        tools.append(tool)
        seen.add(name)
    return tools


async def resolve_configured_runtime_tools(context) -> list[Any]:
    """仅供 standalone/metadata 建图使用；正式运行由宿主装配器直接注入工具快照。"""
    return get_tool_instances_for_context(context)
