"""Agent 运行时资源统一装配入口。

数据库和平台资源只在宿主侧解析；核心 Agent 消费不可变的请求级快照。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from time import monotonic
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from datadeck.agents.toolkits.service import get_tool_descriptors, get_tool_instances_for_context
from datadeck.agents.toolkits.packages import (
    TOOL_PACKAGES,
    TOOL_TO_PACKAGE,
    mcp_package_options,
    mcp_server_slug_from_package,
)
from datadeck.ports.tools import ToolDescriptor
from server.models import Agent, ScheduledTask, ThreadAttachment, User
from server.services.agent_runtime_contract import (
    RuntimeAssemblyError, RuntimeDiagnostic, RuntimeResource, RuntimeResourceSnapshot,
)
from server.services.knowledge_service import list_knowledge_bases
from server.services.mcp.service import get_all_mcp_servers
from server.services.skills.service import list_accessible_skills
from server.deps import AGENT_RUNTIME_MODULES, get_role_agent_slugs, get_role_permissions
from datadeck import logger


def _selected(value: Any) -> tuple[str, ...] | None:
    try:
        return RuntimeResourceSnapshot.normalize_selection(value)
    except ValueError as exc:
        raise RuntimeAssemblyError(RuntimeDiagnostic(
            code="RUNTIME_SELECTION_INVALID",
            message=str(exc),
            severity="error",
            recoverable=False,
        )) from exc


def _package_selection(tools: tuple[str, ...] | None) -> tuple[str, ...] | None:
    """把 Agent 配置中的工具包选择单独快照化，不把包选择混进工具实例。"""
    if tools is None:
        return None
    packages = []
    for item in tools:
        package = (
            item if item in TOOL_PACKAGES
            else item if mcp_server_slug_from_package(item)
            else TOOL_TO_PACKAGE.get(item)
        )
        if package and package not in packages:
            packages.append(package)
    return tuple(packages)


def _mcp_selection_from_tools(
    tools: tuple[str, ...] | None,
    skills: tuple[str, ...] | None,
    skill_items: Iterable[Any],
) -> tuple[str, ...]:
    """从工具包和 Skill 依赖推导本次运行的 MCP Server 选择。"""
    selected: list[str] = []
    for item in tools or ():
        server_slug = mcp_server_slug_from_package(item)
        if server_slug and server_slug not in selected:
            selected.append(server_slug)

    skill_by_slug = {
        str(item.slug): item for item in skill_items
        if getattr(item, "slug", None)
    }
    if skills is None:
        roots = list(skill_by_slug)
    else:
        builtin = [
            slug for slug, item in skill_by_slug.items()
            if getattr(item, "source_scope", None) == "builtin"
        ]
        roots = [*builtin, *skills]
    visited: set[str] = set()
    pending = list(dict.fromkeys(roots))
    while pending:
        slug = pending.pop(0)
        if slug in visited:
            continue
        visited.add(slug)
        item = skill_by_slug.get(slug)
        if item is None:
            continue
        for server_slug in getattr(item, "mcp_dependencies", None) or []:
            normalized = str(server_slug).strip()
            if normalized and normalized not in selected:
                selected.append(normalized)
        pending.extend(
            str(dep).strip() for dep in (getattr(item, "skill_dependencies", None) or [])
            if str(dep).strip() and str(dep).strip() not in visited
        )
    return tuple(selected)


def _resource_map(items: Iterable[Any], *, kind: str, source: str, key_attr: str = "slug") -> list[RuntimeResource]:
    result = []
    for item in items:
        key = str(item.get(key_attr, "") if isinstance(item, dict) else getattr(item, key_attr, ""))
        if not key:
            continue
        name = str(item.get("name", key) if isinstance(item, dict) else getattr(item, "name", key))
        result.append(RuntimeResource(kind=kind, key=key, name=name, source=source))
    return result


def _with_selection_status(
    available: list[RuntimeResource],
    selected: tuple[str, ...] | None,
    *,
    kind: str,
    default_keys: set[str] | None = None,
    unavailable_reason: str = "资源不存在或已禁用",
) -> list[RuntimeResource]:
    """把默认策略和显式选择都准确记录，不静默伪装成已挂载。"""
    available_by_key = {item.key: item for item in available}
    if selected is None:
        if default_keys is not None:
            return [
                item if item.key in default_keys else RuntimeResource(
                    kind=item.kind,
                    key=item.key,
                    name=item.name,
                    source=item.source,
                    status="skipped",
                    reason="默认策略未挂载",
                    metadata=item.metadata,
                )
                for item in available
            ]
        return available
    result = [available_by_key[key] for key in selected if key in available_by_key]
    result.extend(RuntimeResource(
        kind=kind, key=key, status="unavailable", reason=unavailable_reason,
    ) for key in selected if key not in available_by_key)
    return result


def record_runtime_diagnostic(context: Any, diagnostic: RuntimeDiagnostic | dict[str, Any]) -> None:
    """把运行期间的资源问题追加到请求上下文，并去重。"""
    current = getattr(context, "_runtime_diagnostics", None)
    if not isinstance(current, list):
        current = []
        setattr(context, "_runtime_diagnostics", current)
    payload = diagnostic.to_dict() if isinstance(diagnostic, RuntimeDiagnostic) else dict(diagnostic)
    identity = (payload.get("code"), payload.get("resource_kind"), payload.get("resource_key"))
    if any((item.get("code"), item.get("resource_kind"), item.get("resource_key")) == identity
           for item in current if isinstance(item, dict)):
        return
    current.append(payload)


def _resource_diagnostic(resource: RuntimeResource) -> RuntimeDiagnostic | None:
    """仅把异常资源转成诊断；默认策略跳过是正常状态，不应刷屏。"""
    if resource.status in {"mounted", "skipped"}:
        return None
    return RuntimeDiagnostic(
        code="RUNTIME_RESOURCE_UNAVAILABLE",
        message=resource.reason or "运行资源不可用",
        resource_kind=resource.kind,
        resource_key=resource.key,
        severity="warning",
        recoverable=True,
    )


class AgentRuntimeAssembler:
    """按 uid + Agent Context 生成单次运行的资源快照。"""

    async def prepare_context(
        self,
        context: Any,
        *,
        db: AsyncSession,
        user: User,
        agent_slug: str,
    ) -> RuntimeResourceSnapshot:
        """一次性准备本次 Run 的平台资源上下文。

        这里是宿主层唯一的资源解析入口。Agent 核心只消费已经写入 Context
        的快照和派生数据，不在 graph 构建阶段再次访问数据库。
        """
        # 静态 AgentPolicy 在宿主装配前生效，否则 DataAgent 的固定数据工具
        # 会在快照生成时仍被视为“未选择”，后续运行级白名单会把它们丢掉。
        from datadeck.agents.policy import CHATBOT_POLICY, DATA_AGENT_POLICY
        from server.deps import require_agent_access

        await require_agent_access(db, user, agent_slug)
        await self._load_attachments(context, db=db, uid=str(user.uid))
        preparation_diagnostics = list(getattr(context, "_runtime_diagnostics", []) or [])

        policy = (
            DATA_AGENT_POLICY
            if getattr(context, "agent_backend_id", "") == DATA_AGENT_POLICY.backend_id
            else CHATBOT_POLICY
        )
        context.tools = policy.merge_tools(getattr(context, "tools", None))
        if policy.backend_id == DATA_AGENT_POLICY.backend_id:
            context.data_workflow_enabled = DATA_AGENT_POLICY.data_workflow
            # 数仓代码是 DataAgent 的按需能力：代码逻辑类问题才进入本次
            # 快照，保证资源状态、能力提示和实际工具白名单一致。
            if getattr(context, "task_kind", "") == "code":
                context.tools = list(dict.fromkeys([*context.tools, "code_search"]))

        try:
            snapshot = await self.assemble(context, db=db, user=user, agent_slug=agent_slug)
        except RuntimeAssemblyError as exc:
            record_runtime_diagnostic(context, exc.diagnostic)
            raise
        context._runtime_snapshot = snapshot
        # 保留附件加载等快照装配前阶段已经记录的诊断；这些信息会被
        # event_translator 落库，不能因为快照成功而静默丢失。
        context._runtime_diagnostics = preparation_diagnostics
        for resource in snapshot.resources:
            diagnostic = _resource_diagnostic(resource)
            if diagnostic is not None:
                record_runtime_diagnostic(context, diagnostic)
        self.apply_knowledge_context(context, snapshot)

        try:
            from server.services.skills.runtime import (
                resolve_runtime_skills_for_context,
                resolve_skill_gated_tools,
            )

            resolved = await resolve_runtime_skills_for_context(context, db=db, user=user)
            context.skills = resolved["context_skills"]
            context.preload_skills = resolved["context_preload_skills"]
            context._effective_skill_slugs = resolved["effective_skills"]
            context._runtime_skills = resolved["runtime_skills"]
            context._preloaded_skills = resolved["preloaded_skills"]
            context._preloaded_skill_contents = resolved["preloaded_skill_contents"]
            context._skill_tool_instances = resolve_skill_gated_tools(context)
        except Exception as exc:  # noqa: BLE001
            # 资源准备失败必须留下诊断，不能把失败伪装成“没有配置 Skill”。
            logger.warning("Agent Skill resource preparation failed: %s", exc)
            requested_skills = getattr(context, "skills", None)
            if requested_skills:
                raise RuntimeAssemblyError(RuntimeDiagnostic(
                    code="SKILL_PREPARATION_FAILED",
                    message=f"已配置 Skill 装配失败：{str(exc)[:400]}",
                    severity="error", resource_kind="skills", recoverable=False,
                )) from exc
            context._effective_skill_slugs = []
            context._runtime_skills = {}
            context._preloaded_skills = []
            context._preloaded_skill_contents = {}
            context._skill_tool_instances = []
            record_runtime_diagnostic(context, {
                "severity": "error",
                "code": "SKILL_PREPARATION_FAILED",
                "message": str(exc)[:500],
                "recoverable": False,
            })

        # 宿主 middleware 也在统一装配阶段实例化；核心 Agent 只消费 Context
        # 中的不可变运行时列表，不再持有 resolver/factory 回调。
        from server.services.agent_runtime_middlewares import build_runtime_middlewares

        # 所有工具实例在这里一次性生成。核心 Graph 不再在建图阶段查询注册器、
        # MCP 或 Skill 服务，避免运行中配置变化污染当前 Run。
        from server.services.agent_runtime_tools import build_agent_runtime_tools
        from server.services.agent_runtime_tools import (
            build_knowledge_search_tool,
            tool_allowed_by_runtime_permissions,
        )

        context._platform_tools = build_agent_runtime_tools(context, user)

        runtime_tools = list(get_tool_instances_for_context(context))
        runtime_tools = [
            build_knowledge_search_tool(context, user, tool)
            if getattr(tool, "name", "") == "rag_search" else tool
            for tool in runtime_tools
        ]
        runtime_tools.extend(getattr(context, "_skill_tool_instances", []) or [])
        runtime_tools.extend(getattr(context, "_platform_tools", []) or [])

        effective_skills = getattr(context, "_effective_skill_slugs", []) or []
        runtime_skills = getattr(context, "_runtime_skills", {}) or {}
        skill_mcps = [
            mcp
            for slug in effective_skills
            for mcp in (runtime_skills.get(slug, {}) or {}).get("mcps", []) or []
        ]
        selected_mcps = snapshot.selected("mcps") or ()
        requested_mcps = list(dict.fromkeys([*selected_mcps, *skill_mcps]))
        mcp_tools_by_server = await self.resolve_mcp_tools_by_server(
            context, extra_mcps=requested_mcps,
        )
        context.runtime_mcp_tools = {
            name: tuple(tools) for name, tools in mcp_tools_by_server.items()
        }
        runtime_tools.extend(
            tool for tools in mcp_tools_by_server.values() for tool in tools
        )

        unique_tools = []
        seen_tool_names: set[str] = set()
        for tool in runtime_tools:
            tool_name = str(getattr(tool, "name", "") or "").strip()
            if (
                not tool_name
                or tool_name in seen_tool_names
                or not tool_allowed_by_runtime_permissions(context, tool_name)
            ):
                continue
            seen_tool_names.add(tool_name)
            unique_tools.append(tool)
        context.runtime_tools = tuple(unique_tools)
        context.runtime_tool_descriptors = self._build_runtime_tool_descriptors(
            unique_tools, mcp_tools_by_server,
        )

        context.runtime_middlewares = tuple(await build_runtime_middlewares(context))
        context._runtime_prepared = True
        return snapshot

    @staticmethod
    async def _load_attachments(context: Any, *, db: AsyncSession, uid: str) -> None:
        """按线程和用户加载本次运行可读附件，禁止信任前端的磁盘路径。"""
        thread_id = str(getattr(context, "thread_id", "") or "")
        if not thread_id:
            context.attachments = []
            return
        requested = [
            str(item).strip()
            for item in (getattr(context, "attachment_file_ids", None) or [])
            if str(item).strip()
        ]
        query = select(ThreadAttachment).where(
            ThreadAttachment.thread_id == thread_id,
            ThreadAttachment.uid == uid,
        ).order_by(ThreadAttachment.id.asc())
        numeric_ids = []
        for item in requested:
            if not item.isdigit() or len(item) > 19:
                continue
            numeric_ids.append(int(item))
        if requested:
            query = query.where(ThreadAttachment.id.in_(numeric_ids)) if numeric_ids else query.where(False)
        rows = (await db.execute(query)).scalars().all()
        context.attachments = [item.to_dict() for item in rows]
        if requested and len(rows) < len(set(numeric_ids)):
            record_runtime_diagnostic(context, RuntimeDiagnostic(
                code="ATTACHMENT_NOT_FOUND",
                message="部分对话附件不存在或已被删除，Agent 只能读取仍然有效的附件。",
                resource_kind="attachments",
                severity="warning",
                recoverable=True,
            ))

    async def assemble(
        self,
        context: Any,
        *,
        db: AsyncSession,
        user: User,
        agent_slug: str,
    ) -> RuntimeResourceSnapshot:
        started = monotonic()
        role_permissions = set(await get_role_permissions(db, user.role))
        assigned_agents = (
            await get_role_agent_slugs(db, user.role)
            if hasattr(db, "get")
            else set()
        )
        if assigned_agents is None or agent_slug in assigned_agents:
            role_permissions.update(AGENT_RUNTIME_MODULES)
            context.agent_resource_access = True
        else:
            context.agent_resource_access = False
        permissions = tuple(sorted(role_permissions))
        context.runtime_permissions = permissions
        configured_knowledge_ids: set[str] = set()
        if getattr(context, "agent_resource_access", False) and hasattr(db, "scalar"):
            agent = await db.scalar(select(Agent).where(Agent.slug == agent_slug))
            raw_config = (agent.config_json or {}).get("context", agent.config_json or {}) if agent else {}
            configured = raw_config.get("knowledges") if isinstance(raw_config, dict) else None
            if isinstance(configured, (list, tuple, set)):
                configured_knowledge_ids = {
                    str(item).strip() for item in configured if str(item).strip()
                }
        # 工作区对象可能已由运行入口按会话绑定创建，但没有 workspace 模块权限时
        # 不能把路径继续带入快照，也不能让后续工具看到它。
        if "workspace" not in permissions:
            context.workdir = None
            context.workdir_path = None

        tool_selection = _selected(getattr(context, "tools", None))
        skill_selection = _selected(getattr(context, "skills", None))
        skill_items = (
            [item for item in await list_accessible_skills(
                db,
                user,
                bypass_share=bool(getattr(context, "agent_resource_access", False)),
                allowed_slugs=(set(skill_selection) if skill_selection is not None else None),
            ) if item.slug]
            if "extensions" in permissions else []
        )
        selections = {
            "tools": tool_selection,
            "knowledges": _selected(getattr(context, "knowledges", None)),
            "skills": skill_selection,
            # MCP 是工具包的一种动态实现；Skill 的 MCP 依赖在这里一并加入，
            # 后续 middleware 不需要读取第二份 context 配置。
            "mcps": _mcp_selection_from_tools(tool_selection, skill_selection, skill_items),
            "subagents": _selected(getattr(context, "subagents", None)),
            "scheduled_tasks": _selected(getattr(context, "scheduled_tasks", None)),
        }
        package_selections = {
            "tool_packages": _package_selection(selections["tools"]),
        }
        resources: list[RuntimeResource] = []

        mcp_items = (
            [item for item in await get_all_mcp_servers(db) if bool(item.enabled)]
            if "extensions" in permissions else []
        )
        tool_items = [item.to_dict() for item in get_tool_descriptors()]
        tool_items.extend(mcp_package_options(mcp_items))
        tool_resources = [RuntimeResource(
            kind="tools", key=str(item["slug"]), name=str(item.get("name", item["slug"])),
            source="registry", metadata={
                "category": item.get("category", "buildin"),
                "kind": item.get("kind", "tool"),
                "package": bool(item.get("package", False)),
                "tools": list(item.get("tools", [])),
                "tags": list(item.get("tags") or []),
                "version": item.get("metadata", {}).get("version", "")
                if isinstance(item.get("metadata"), dict) else "",
            },
        ) for item in tool_items if item.get("slug")]
        default_tool_keys = None
        if tool_selection is None:
            # ``None`` 对通用 Agent 的实际语义是基础工具（以及当前工作区
            # 文件工具），不是所有可选数据能力包；直接复用核心解析结果，
            # 保证 Runtime Snapshot 与最终白名单来自同一规则。
            default_tool_keys = {
                str(getattr(item, "name", ""))
                for item in get_tool_instances_for_context(context)
                if str(getattr(item, "name", "")).strip()
            }
        resources.extend(_with_selection_status(
            tool_resources,
            selections["tools"], kind="tools", default_keys=default_tool_keys,
        ))

        knowledge_items = (
            await list_knowledge_bases(
                db,
                str(user.uid),
                allowed_ids=configured_knowledge_ids,
            )
            if "knowledge" in permissions else []
        )
        knowledge_resources = [RuntimeResource(
            kind="knowledges", key=str(item["id"]), name=str(item.get("name", item["id"])),
            source="postgres", metadata={"collection_name": item.get("collection_name", "")},
        ) for item in knowledge_items if item.get("id")]
        resources.extend(_with_selection_status(
            knowledge_resources,
            selections["knowledges"], kind="knowledges",
            unavailable_reason=(
                "当前角色没有知识库模块权限"
                if "knowledge" not in permissions else "资源不存在或已禁用"
            ),
        ))

        skill_resources = [RuntimeResource(
            kind="skills", key=str(item.slug), name=str(item.name), source="postgres",
            metadata={
                "path": str(item.source_dir),
                "description": str(item.description or ""),
                "source_scope": str(item.source_scope),
                "tool_dependencies": list(item.tool_dependencies or []),
                "mcp_dependencies": list(item.mcp_dependencies or []),
                "skill_dependencies": list(item.skill_dependencies or []),
                "version": item.version or "",
            },
        ) for item in skill_items]
        resources.extend(_with_selection_status(
            skill_resources,
            selections["skills"], kind="skills",
            unavailable_reason=(
                "当前角色没有 Skill 模块权限"
                if "extensions" not in permissions else "资源不存在或已禁用"
            ),
        ))

        resources.extend(_with_selection_status(
            _resource_map(mcp_items, kind="mcps", source="postgres"),
            selections["mcps"], kind="mcps",
            unavailable_reason=(
                "当前角色没有扩展模块权限"
                if "extensions" not in permissions else "资源不存在或已禁用"
            ),
        ))

        subagent_items = []
        if "agents" in permissions:
            allowed_agent_slugs = await get_role_agent_slugs(db, user.role)
            # 子 Agent 是父 Agent 的运行时资源。用户被分配父 Agent 后，父
            # Agent 显式挂载的子 Agent 不要求再次出现在角色管理白名单中；
            # 未显式挂载的资源仍受角色白名单约束。
            configured_subagent_slugs = set(selections["subagents"] or ())
            if getattr(context, "agent_resource_access", False) and configured_subagent_slugs:
                allowed_agent_slugs = (
                    None if allowed_agent_slugs is None
                    else set(allowed_agent_slugs) | configured_subagent_slugs
                )
            subagent_query = select(Agent).where(Agent.execution_role == "subagent")
            if allowed_agent_slugs is not None:
                subagent_query = subagent_query.where(Agent.slug.in_(allowed_agent_slugs))
            subagent_items = (await db.execute(
                subagent_query.order_by(Agent.name)
            )).scalars().all()
        resources.extend(_with_selection_status(
            _resource_map(subagent_items, kind="subagents", source="postgres"),
            selections["subagents"], kind="subagents",
            unavailable_reason=(
                "当前角色没有智能体模块权限"
                if "agents" not in permissions else "资源不存在或已禁用"
            ),
        ))

        task_rows = []
        if "scheduled_tasks" in permissions:
            task_rows = (await db.execute(
                select(ScheduledTask).where(ScheduledTask.uid == str(user.uid))
                    .order_by(ScheduledTask.updated_at.desc())
            )).scalars().all()
        resources.extend(_with_selection_status(
            _resource_map(task_rows, kind="scheduled_tasks", source="postgres", key_attr="id"),
            selections["scheduled_tasks"], kind="scheduled_tasks",
            unavailable_reason=(
                "当前角色没有定时任务模块权限"
                if "scheduled_tasks" not in permissions else "资源不存在或已禁用"
            ),
        ))

        workdir_path = getattr(context, "workdir_path", None)
        if getattr(context, "workdir", None) is not None or workdir_path:
            resources.append(RuntimeResource(
                kind="workspace", key=str(workdir_path or "current"),
                name="当前工作区", source="project",
            ))
        else:
            resources.append(RuntimeResource(
                kind="workspace", key="current", source="project",
                status="unavailable", reason="当前运行没有授权工作区",
            ))

        unavailable = next((item for item in resources if item.status == "unavailable"
                            and selections.get(item.kind) is not None
                            and item.key in (selections.get(item.kind) or ())), None)
        if unavailable is not None:
            raise RuntimeAssemblyError(RuntimeDiagnostic(
                code="RUNTIME_RESOURCE_UNAVAILABLE",
                message=f"运行资源不可用：{unavailable.name or unavailable.key}。{unavailable.reason}",
                resource_kind=unavailable.kind,
                resource_key=unavailable.key,
                severity="error",
                recoverable=False,
            ))

        snapshot = RuntimeResourceSnapshot(
            uid=str(user.uid),
            agent_slug=agent_slug,
            thread_id=str(getattr(context, "thread_id", "")),
            project_id=getattr(context, "project_id", None),
            workdir_path=workdir_path,
            selections=selections,
            package_selections=package_selections,
            agent_backend_id=str(getattr(context, "agent_backend_id", "") or ""),
            permissions=permissions,
            resources=tuple(resources),
            diagnostics={
                "resource_count": len(resources),
                "mounted_count": sum(item.status == "mounted" for item in resources),
                "unavailable_count": sum(item.status == "unavailable" for item in resources),
                "duration_ms": round((monotonic() - started) * 1000, 2),
            },
        )
        logger.info(
            f"Agent runtime resources assembled: agent={agent_slug} uid={user.uid} "
            f"resources={len(resources)} mounted={snapshot.diagnostics['mounted_count']} "
            f"unavailable={snapshot.diagnostics['unavailable_count']} "
            f"duration_ms={snapshot.diagnostics['duration_ms']}"
        )
        return snapshot

    @staticmethod
    async def resolve_mcp_tools(
        context: Any,
        *,
        extra_mcps: list[str] | None = None,
    ) -> list[Any]:
        """按运行快照解析 MCP 工具；快照外的 Server 不得被 Skill 间接带入。"""
        grouped = await AgentRuntimeAssembler.resolve_mcp_tools_by_server(
            context, extra_mcps=extra_mcps,
        )
        return [tool for tools in grouped.values() for tool in tools]

    @staticmethod
    async def resolve_mcp_tools_by_server(
        context: Any,
        *,
        extra_mcps: list[str] | None = None,
    ) -> dict[str, list[Any]]:
        """在一次装配中发现 MCP 工具，并按 Server 保存请求级实例。"""
        from server.services.mcp.service import get_mcp_tools, _mcp_error_summary

        snapshot = getattr(context, "_runtime_snapshot", None)
        if snapshot is None:
            return {}
        allowed = {
            item.key for item in snapshot.mounted_resources("mcps")
        }
        configured = snapshot.selected("mcps")
        if configured is not None:
            allowed &= set(configured)
        names = list(dict.fromkeys(
            item for item in (extra_mcps or [])
            if isinstance(item, str) and item in allowed
        ))
        async def load(name: str) -> tuple[str, list[Any]]:
            try:
                tools = await get_mcp_tools(name)
            except Exception as exc:  # noqa: BLE001
                record_runtime_diagnostic(context, RuntimeDiagnostic(
                    code="MCP_DISCOVERY_FAILED",
                    message=f"MCP {name} 工具发现失败：{_mcp_error_summary(exc, limit=4)}",
                    resource_kind="mcps", resource_key=name,
                    severity="warning", recoverable=True,
                ))
                return name, []
            if not tools:
                record_runtime_diagnostic(context, RuntimeDiagnostic(
                    code="MCP_DISCOVERY_EMPTY",
                    message=f"MCP {name} 未返回可用工具",
                    resource_kind="mcps", resource_key=name,
                    severity="warning", recoverable=True,
                ))
            return name, list(tools)

        loaded = await asyncio.gather(*(load(name) for name in names))
        return {name: tools for name, tools in loaded}

    @staticmethod
    def _build_runtime_tool_descriptors(
        tools: list[Any], mcp_tools_by_server: dict[str, list[Any]],
    ) -> dict[str, dict[str, Any]]:
        """为本次实际挂载的工具生成 Trace 使用的统一描述。"""
        descriptors = {
            item.slug: item.to_dict()
            for item in get_tool_descriptors(include_packages=False, include_internal=True)
        }
        mcp_names = {
            getattr(tool, "name", ""): server
            for server, server_tools in mcp_tools_by_server.items()
            for tool in server_tools
        }
        for tool in tools:
            slug = str(getattr(tool, "name", "") or "").strip()
            if not slug:
                continue
            schema = (
                tool.args_schema.model_json_schema()
                if getattr(tool, "args_schema", None)
                and hasattr(tool.args_schema, "model_json_schema")
                else {}
            )
            if slug in descriptors:
                # 注册表中的静态描述可能只有展示信息；运行快照必须携带
                # 本次实际工具实例的 schema，避免模型/Trace 看到 {}。
                if schema and not descriptors[slug].get("input_schema"):
                    descriptors[slug]["input_schema"] = schema
                continue
            server = mcp_names.get(slug)
            metadata = getattr(tool, "metadata", {}) or {}
            descriptors[slug] = ToolDescriptor(
                slug=slug,
                name=str(metadata.get("name") or slug),
                description=str(getattr(tool, "description", "") or ""),
                kind="tool",
                group="mcp" if server else "runtime",
                package_slug=f"package:mcp:{server}" if server else "",
                source="mcp" if server else "runtime",
                category="mcp" if server else "runtime",
                version="1",
                configurable=False,
                visible=False,
                risk_level="read",
                input_schema=schema,
                metadata={"server_slug": server or ""},
            ).to_dict()
        return descriptors

    @staticmethod
    def apply_knowledge_context(context: Any, snapshot: RuntimeResourceSnapshot) -> None:
        """把已授权知识库快照映射到运行时检索范围。"""
        available = snapshot.mounted_resources("knowledges")
        selected = snapshot.selected("knowledges")
        # None 表示全部授权知识库；显式列表只允许检索所选集合；空 tuple
        # 表示明确不挂载知识库。不能把“已授权”误当成“已选择”，否则 Agent
        # 仍然可以通过 rag_search 的 collection_name 参数检索未挂载内容。
        selected_keys = set(selected or ()) if selected is not None else None
        collections = [
            str(item.metadata.get("collection_name"))
            for item in available
            if item.metadata.get("collection_name")
            and (selected_keys is None or item.key in selected_keys)
        ]
        context.knowledge_base_collections = collections
        # None 使用默认全部可用知识库；空 tuple 表示明确不挂载知识库。
        target = available[0] if selected is None and available else None
        if selected:
            target = next((item for item in available if item.key == selected[0]), None)
        if target is None:
            context.knowledge_base_id = None
            context.knowledge_base_collection = None
            return
        context.knowledge_base_id = target.key
        context.knowledge_base_collection = target.metadata.get("collection_name") or None


async def assemble_agent_runtime(context: Any, *, db: AsyncSession, user: User, agent_slug: str):
    """函数式运行时入口；完成快照和 Context 派生资源的一次性准备。"""
    return await AgentRuntimeAssembler().prepare_context(
        context, db=db, user=user, agent_slug=agent_slug,
    )


__all__ = ["AgentRuntimeAssembler", "assemble_agent_runtime", "record_runtime_diagnostic"]
