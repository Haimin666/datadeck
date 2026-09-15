"""智能体管理路由。"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.deps import get_required_user, get_role_permissions, get_role_agent_slugs, require_agent_access
from server.models import User, Agent, ScheduledTask
from datadeck.agents.toolkits.service import get_tool_descriptors
from datadeck.agents.policy import COMMON_PLATFORM_PACKAGE, DATA_AGENT_POLICY
from datadeck.agents.toolkits.packages import (
    TOOL_PACKAGES,
    mcp_package_options,
    mcp_server_slug_from_package,
)
from datadeck.ports.tools import ToolDescriptor
from server.services.mcp.service import get_all_mcp_servers
from server.services.skills.service import list_accessible_skills

agent = APIRouter(prefix="/agent", tags=["agent"])
DATA_AGENT_FIXED_PACKAGES = set(DATA_AGENT_POLICY.fixed_packages)


def _configurable_items(
    *,
    backend_id: str = "ChatbotAgent",
    knowledge_options: list[dict] | None = None,
    skill_options: list[dict] | None = None,
    mcp_options: list[dict] | None = None,
    subagent_options: list[dict] | None = None,
) -> dict:
    # 能力包是配置页唯一入口；包内成员只在运行时展开，不重复暴露给用户选择。
    fixed_packages = {COMMON_PLATFORM_PACKAGE}
    if backend_id == "DataAgent":
        fixed_packages.update(DATA_AGENT_FIXED_PACKAGES)
    tool_options = get_tool_descriptors(
        include_internal=False,
        fixed_packages=fixed_packages,
    )
    for item in mcp_package_options(mcp_options):
        tool_options.append(ToolDescriptor(
            slug=item["slug"],
            name=item["name"],
            description=item["description"],
            kind="package",
            group="mcp",
            package_slug=item["package_slug"],
            source="postgres",
            category="mcp",
            version="1",
            configurable=True,
            visible=True,
            fixed=False,
            metadata=item.get("metadata", {}),
        ))
    return {
        "identity_prompt": {
            "name": "Agent 身份",
            "description": "可选。留空时不使用固定身份，也不会自动宣称未挂载的能力。",
            "type": "text",
            "kind": "prompt",
            "default": "",
        },
        "model": {
            "name": "模型",
            "description": "为该智能体选择默认聊天模型；留空使用系统默认模型",
            "type": "string",
            "kind": "llm",
            "default": "",
        },
        "tools": {
            "name": "工具包",
            "description": "按工具包挂载平台、文件、数据和内置工具；留空使用智能体默认配置",
            "type": "list",
            "kind": "tools",
            "options": [
                tool.to_dict()
                for tool in tool_options
            ],
        },
        "knowledges": {
            "name": "知识库",
            "description": "选择 Agent 检索使用的知识库；留空时使用当前用户最近的知识库",
            "type": "list",
            "kind": "knowledges",
            "options": knowledge_options or [],
        },
        "skills": {
            "name": "Skills",
            "description": "启用的 Skill slug 列表",
            "type": "list",
            "kind": "skills",
            "options": skill_options or [],
        },
        "subagents": {
            "name": "子智能体",
            "description": "允许主智能体分派任务的子智能体",
            "type": "list",
            "kind": "subagents",
            "options": subagent_options or [],
        },
    }


def _require_admin(user: User) -> None:
    if user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="需要管理员权限")


async def _require_agent_manager(db: AsyncSession, user: User) -> None:
    _require_admin(user)
    if "agents" not in await get_role_permissions(db, user.role):
        raise HTTPException(status_code=403, detail="没有智能体管理权限")


class AgentCreate(BaseModel):
    name: str
    backend_id: str = "ChatbotAgent"
    slug: str | None = None
    description: str | None = None
    config_json: dict | None = None
    icon: str | None = None
    share_config: dict | None = None
    execution_role: str = Field(default="standalone", pattern="^(standalone|subagent)$")
    delegation_enabled: bool = False


class AgentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    config_json: dict | None = None
    icon: str | None = None
    share_config: dict | None = None
    execution_role: str | None = Field(default=None, pattern="^(standalone|subagent)$")
    delegation_enabled: bool | None = None


def _validate_agent_config(config_json: dict | None) -> None:
    """校验 Agent 持久化配置只使用能力包作为工具选择。"""
    if config_json is None:
        return
    if not isinstance(config_json, dict):
        raise HTTPException(status_code=422, detail="Agent 配置必须是对象")
    context = config_json.get("context", config_json)
    if not isinstance(context, dict):
        raise HTTPException(status_code=422, detail="Agent context 必须是对象")
    selected = context.get("tools")
    if selected is None:
        return
    if not isinstance(selected, list) or any(not isinstance(item, str) for item in selected):
        raise HTTPException(status_code=422, detail="tools 必须是工具包 slug 列表")
    invalid = sorted({
        item for item in selected
        if item not in TOOL_PACKAGES and not mcp_server_slug_from_package(item)
    })
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"tools 只能选择工具包，非法项：{', '.join(invalid)}",
        )


async def _validate_collaboration_config(
    db: AsyncSession,
    role: str,
    delegation_enabled: bool,
    config_json: dict | None,
    *,
    agent_slug: str | None = None,
) -> None:
    if role == "subagent" and delegation_enabled:
        raise HTTPException(status_code=422, detail="子智能体不能启用任务委派")
    if not delegation_enabled:
        return
    context = (config_json or {}).get("context", config_json or {})
    selected = context.get("subagents") if isinstance(context, dict) else None
    if not isinstance(selected, list) or not [item for item in selected if isinstance(item, str) and item.strip()]:
        raise HTTPException(status_code=422, detail="启用任务委派时必须至少选择一个子智能体")
    slugs = [item.strip() for item in selected if isinstance(item, str) and item.strip()]
    if len(slugs) != len(set(slugs)):
        raise HTTPException(status_code=422, detail="子智能体不能重复选择")
    if agent_slug and agent_slug in slugs:
        raise HTTPException(status_code=422, detail="智能体不能委派给自己")
    rows = await db.execute(select(Agent.slug).where(
        Agent.slug.in_(slugs),
        Agent.execution_role == "subagent",
    ))
    existing = set(rows.scalars().all())
    missing = set(slugs) - existing
    if missing:
        raise HTTPException(status_code=422, detail="所选子智能体不存在或不是子智能体")
    workflow = context.get("subagent_workflow") if isinstance(context, dict) else None
    if workflow is None:
        return
    if not isinstance(workflow, dict):
        raise HTTPException(status_code=422, detail="子智能体编排格式非法")
    nodes = workflow.get("nodes")
    edges = workflow.get("edges", [])
    if not isinstance(nodes, list) or not nodes or len(nodes) > 8 or not isinstance(edges, list):
        raise HTTPException(status_code=422, detail="子智能体编排需包含 1-8 个节点")
    node_ids: set[str] = set()
    workflow_slugs: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            raise HTTPException(status_code=422, detail="子智能体编排节点格式非法")
        node_id = str(node.get("id") or "").strip()
        node_slug = str(node.get("subagent_slug") or "").strip()
        task = str(node.get("task_template") or "").strip()
        if not node_id or node_id in node_ids or not node_slug or not task:
            raise HTTPException(status_code=422, detail="每个编排节点都需要唯一标识、子智能体和任务说明")
        node_ids.add(node_id)
        workflow_slugs.add(node_slug)
    if workflow_slugs != set(slugs):
        raise HTTPException(status_code=422, detail="编排节点必须与已选择的子智能体完全一致")
    dependencies: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    for edge in edges:
        source = str(edge.get("source") or "").strip() if isinstance(edge, dict) else ""
        target = str(edge.get("target") or "").strip() if isinstance(edge, dict) else ""
        if not source or not target or source == target or source not in node_ids or target not in node_ids:
            raise HTTPException(status_code=422, detail="编排连线必须连接两个不同的有效节点")
        dependencies[target].add(source)
    resolved: set[str] = set()
    while len(resolved) < len(node_ids):
        ready = {node_id for node_id, deps in dependencies.items() if node_id not in resolved and deps <= resolved}
        if not ready:
            raise HTTPException(status_code=422, detail="子智能体编排不能包含循环依赖")
        resolved.update(ready)


def _serialize_agent(agent: Agent, current_user: User) -> dict:
    data = agent.to_dict()
    data["can_manage"] = current_user.role in ("admin", "superadmin")
    return data


async def _get_agent_by_identifier(db: AsyncSession, identifier: str) -> Agent | None:
    return (await db.execute(
        select(Agent).where(or_(Agent.id == identifier, Agent.slug == identifier))
    )).scalar_one_or_none()


async def _get_configurable_items(
    db: AsyncSession,
    current_user: User,
    backend_id: str = "ChatbotAgent",
    *,
    runtime_access: bool = False,
    allowed_skill_slugs: set[str] | None = None,
    allowed_knowledge_ids: set[str] | None = None,
    allowed_subagent_slugs: set[str] | None = None,
) -> dict:
    """Return the same runtime resource catalogue for new and existing agents."""
    from server.services.knowledge_service import list_knowledge_bases

    permissions = set(await get_role_permissions(db, current_user.role))
    knowledge_options = []
    if runtime_access or "knowledge" in permissions:
        knowledge_options = [
            {
                "slug": item["kb_id"] if item.get("kb_id") else item["id"],
                "name": item["name"],
                "description": item.get("description", ""),
                "group": "knowledge",
            }
            for item in await list_knowledge_bases(
                db,
                current_user.uid,
                allowed_ids=allowed_knowledge_ids if runtime_access else None,
            )
        ]
    accessible_skills = []
    if runtime_access or "extensions" in permissions:
        accessible_skills = await list_accessible_skills(
            db,
            current_user,
            require_enabled=False,
            bypass_share=runtime_access,
            allowed_slugs=allowed_skill_slugs,
        )
    skill_options = [
        {"slug": item.slug, "name": item.name, "description": item.description or "", "group": "skill"}
        for item in accessible_skills
        if item.source_scope != "builtin"
    ]
    mcp_options = []
    if runtime_access or "extensions" in permissions:
        mcp_options = [
            {"slug": item.slug, "name": item.name, "description": item.description or "", "group": "mcp"}
            for item in await get_all_mcp_servers(db)
            if bool(item.enabled)
        ]
    subagents = []
    if runtime_access or "agents" in permissions:
        subagent_query = select(Agent).where(
            Agent.execution_role == "subagent",
        ).order_by(Agent.name)
        allowed = await get_role_agent_slugs(db, current_user.role)
        if runtime_access and allowed_subagent_slugs:
            allowed = (
                None if allowed is None
                else set(allowed) | set(allowed_subagent_slugs)
            )
        if allowed is not None:
            subagent_query = subagent_query.where(Agent.slug.in_(allowed))
        subagents = (await db.execute(subagent_query)).scalars().all()
    subagent_options = [
        {"slug": item.slug, "name": item.name, "description": item.description or "", "group": "subagent"}
        for item in subagents
    ]
    return _configurable_items(
        backend_id=backend_id,
        knowledge_options=knowledge_options,
        skill_options=skill_options,
        mcp_options=mcp_options,
        subagent_options=subagent_options,
    )


@agent.get("/backends")
async def list_backends():
    """列出内置 agent 后端。"""
    backends = [
        {
            "id": "DataAgent",
            "name": "数据分析助手",
            "description": "按知识库口径、元数据和只读 SQL 完成企业数据分析",
            "type": "agent_backend",
            "is_builtin": True,
        },
        {
            "id": "ChatbotAgent",
            "name": "对话助手",
            "description": "内置通用对话智能体，按实际挂载能力完成问答和任务协作",
            "type": "agent_backend",
            "is_builtin": True,
        }
    ]
    return {"backends": backends}


@agent.get("")
async def list_agents(
    include_subagents: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    """列出当前角色可用的主 Agent；管理编排页可显式包含子 Agent。"""
    result = await db.execute(select(Agent).order_by(Agent.is_builtin.desc(), Agent.name))
    all_agents = result.scalars().all()
    if not all_agents:
        # 插入内置默认智能体
        builtin = Agent(
            id="default-chatbot",
            slug="default-chatbot",
            name="对话助手",
            description="内置对话智能体",
            backend_id="ChatbotAgent",
            is_builtin=True,
        )
        db.add(builtin)
        await db.commit()
        await db.refresh(builtin)
        all_agents = [builtin]
    if not include_subagents:
        all_agents = [item for item in all_agents if item.execution_role != "subagent"]
    allowed = await get_role_agent_slugs(db, current_user.role)
    agents = all_agents if allowed is None else [item for item in all_agents if item.slug in allowed]
    return {"agents": [_serialize_agent(a, current_user) for a in agents]}


@agent.get("/default")
async def get_default_agent(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    result = await db.execute(select(Agent).where(Agent.slug == "default-chatbot"))
    a = result.scalar_one_or_none()
    if a:
        return {"agent": _serialize_agent(a, current_user)}
    builtin = Agent(id="default-chatbot", slug="default-chatbot", name="对话助手",
                    description="内置对话智能体", backend_id="ChatbotAgent", is_builtin=True)
    db.add(builtin)
    await db.commit()
    await db.refresh(builtin)
    return {"agent": _serialize_agent(builtin, current_user)}


@agent.get("/configurable-items")
async def get_configurable_items(
    backend_id: str = "ChatbotAgent",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    """Resources selectable while an agent is still a creation draft."""
    return {"configurable_items": await _get_configurable_items(db, current_user, backend_id)}


@agent.get("/{agent_id}")
async def get_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    a = await _get_agent_by_identifier(db, agent_id)
    if not a:
        raise HTTPException(status_code=404, detail="智能体不存在")
    await require_agent_access(db, current_user, a.slug)
    raw_context = (a.config_json or {}).get("context", a.config_json or {})
    configured_skill_slugs = None
    configured_knowledge_ids = None
    configured_subagent_slugs = None
    if isinstance(raw_context, dict) and isinstance(raw_context.get("skills"), list):
        configured_skill_slugs = {
            str(item).strip() for item in raw_context["skills"] if str(item).strip()
        }
    if isinstance(raw_context, dict) and isinstance(raw_context.get("knowledges"), list):
        configured_knowledge_ids = {
            str(item).strip() for item in raw_context["knowledges"] if str(item).strip()
        }
    if isinstance(raw_context, dict) and isinstance(raw_context.get("subagents"), list):
        configured_subagent_slugs = {
            str(item).strip() for item in raw_context["subagents"] if str(item).strip()
        }
    return {
        "agent": {
            **_serialize_agent(a, current_user),
            "configurable_items": await _get_configurable_items(
                db,
                current_user,
                a.backend_id,
                runtime_access=True,
                allowed_skill_slugs=configured_skill_slugs,
                allowed_knowledge_ids=configured_knowledge_ids,
                allowed_subagent_slugs=configured_subagent_slugs,
            ),
        }
    }


@agent.post("")
async def create_agent(
    payload: AgentCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_agent_manager(db, current_user)
    slug = payload.slug or payload.name
    existing = await db.execute(select(Agent).where(Agent.slug == slug))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="slug 已存在")
    role = payload.execution_role
    _validate_agent_config(payload.config_json)
    await _validate_collaboration_config(db, role, payload.delegation_enabled, payload.config_json, agent_slug=slug)
    a = Agent(
        id=str(uuid.uuid4()),
        slug=slug,
        name=payload.name,
        description=payload.description,
        backend_id=payload.backend_id,
        config_json=payload.config_json or {},
        icon=payload.icon,
        share_config=payload.share_config or {},
        execution_role=role,
        delegation_enabled=bool(payload.delegation_enabled) and role == "standalone",
    )
    db.add(a)
    await db.commit()
    await db.refresh(a)
    return {"agent": _serialize_agent(a, current_user)}


@agent.put("/{agent_id}")
async def update_agent(
    agent_id: str,
    payload: AgentUpdate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_agent_manager(db, current_user)
    a = await _get_agent_by_identifier(db, agent_id)
    if not a:
        raise HTTPException(status_code=404, detail="智能体不存在")
    if payload.name:
        a.name = payload.name
    if payload.description is not None:
        a.description = payload.description
    next_role = payload.execution_role or a.execution_role or "standalone"
    if a.is_builtin and next_role != (a.execution_role or "standalone"):
        raise HTTPException(status_code=400, detail="内置智能体不能变更运行角色")
    next_delegation = bool(payload.delegation_enabled) if payload.delegation_enabled is not None else bool(a.delegation_enabled)
    next_config = payload.config_json if payload.config_json is not None else a.config_json
    _validate_agent_config(next_config)
    await _validate_collaboration_config(db, next_role, next_delegation, next_config, agent_slug=a.slug)
    if payload.config_json is not None:
        a.config_json = payload.config_json
    if payload.icon is not None:
        a.icon = payload.icon
    if payload.share_config is not None:
        a.share_config = payload.share_config
    a.execution_role = next_role
    a.delegation_enabled = next_delegation and next_role == "standalone"
    await db.commit()
    await db.refresh(a)
    return {"agent": _serialize_agent(a, current_user)}


@agent.delete("/{agent_id}")
async def delete_agent(
    agent_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    await _require_agent_manager(db, current_user)
    a = await _get_agent_by_identifier(db, agent_id)
    if a and a.is_builtin:
        a = None
    if not a:
        raise HTTPException(status_code=404, detail="智能体不存在或无法删除内置智能体")
    scheduled_task = (await db.execute(
        select(ScheduledTask.id).where(ScheduledTask.agent_slug == a.slug).limit(1)
    )).scalar_one_or_none()
    if scheduled_task:
        raise HTTPException(status_code=409, detail="该智能体仍被定时任务引用，请先删除或改绑任务")
    agents = (await db.execute(select(Agent).where(Agent.id != a.id))).scalars().all()
    referenced_by = []
    for candidate in agents:
        raw_context = (candidate.config_json or {}).get("context", candidate.config_json or {})
        selected = raw_context.get("subagents", []) if isinstance(raw_context, dict) else []
        if a.slug in selected:
            referenced_by.append(candidate.name or candidate.slug)
    if referenced_by:
        raise HTTPException(
            status_code=409,
            detail="该智能体仍被协调 Agent 引用，请先解除挂载",
        )
    await db.delete(a)
    await db.commit()
    return {"ok": True}
