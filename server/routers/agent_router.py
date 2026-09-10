"""智能体管理路由。"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.deps import get_required_user
from server.models import User, Agent, ScheduledTask
from datadeck.agents.buildin.chatbot.graph import ChatbotAgent
from datadeck.agents.toolkits.service import get_tool_metadata
from datadeck.ports.checkpointer import CheckpointerProvider

agent = APIRouter(prefix="/agent", tags=["agent"])


def _configurable_items() -> dict:
    tools = get_tool_metadata()
    return {
        "tools": {
            "name": "工具",
            "description": "启用的工具，留空使用智能体默认配置",
            "type": "list",
            "kind": "tools",
            "options": [
                {"value": tool["slug"], "name": tool["name"], "description": tool["description"]}
                for tool in tools
            ],
        }
    }


def _require_admin(user: User) -> None:
    if user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="需要管理员权限")


class AgentCreate(BaseModel):
    name: str
    backend_id: str = "ChatbotAgent"
    slug: str | None = None
    description: str | None = None
    config_json: dict | None = None
    icon: str | None = None
    share_config: dict | None = None
    is_subagent: bool = False


class AgentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    config_json: dict | None = None
    icon: str | None = None
    share_config: dict | None = None
    is_subagent: bool | None = None


def _serialize_agent(agent: Agent, current_user: User) -> dict:
    data = agent.to_dict()
    data["can_manage"] = current_user.role in ("admin", "superadmin")
    return data


async def _get_agent_by_identifier(db: AsyncSession, identifier: str) -> Agent | None:
    return (await db.execute(
        select(Agent).where(or_(Agent.id == identifier, Agent.slug == identifier))
    )).scalar_one_or_none()


@agent.get("/backends")
async def list_backends():
    """列出内置 agent 后端。"""
    backends = [
        {
            "id": "ChatbotAgent",
            "name": "对话助手",
            "description": "内置对话智能体，支持 Text2SQL",
            "type": "agent_backend",
            "is_builtin": True,
        }
    ]
    return {"backends": backends}


@agent.get("")
async def list_agents(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_required_user)):
    """列出所有智能体。"""
    result = await db.execute(select(Agent).order_by(Agent.is_builtin.desc(), Agent.name))
    agents = result.scalars().all()
    if not agents:
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
        return {"agents": [_serialize_agent(builtin, current_user)]}
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


@agent.get("/{agent_id}")
async def get_agent(
    agent_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_required_user),
):
    a = await _get_agent_by_identifier(db, agent_id)
    if not a:
        raise HTTPException(status_code=404, detail="智能体不存在")
    return {
        "agent": {
            **_serialize_agent(a, current_user),
            "configurable_items": _configurable_items(),
        }
    }


@agent.post("")
async def create_agent(
    payload: AgentCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(current_user)
    slug = payload.slug or payload.name
    existing = await db.execute(select(Agent).where(Agent.slug == slug))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="slug 已存在")
    a = Agent(
        id=str(uuid.uuid4()),
        slug=slug,
        name=payload.name,
        description=payload.description,
        backend_id=payload.backend_id,
        config_json=payload.config_json or {},
        icon=payload.icon,
        share_config=payload.share_config or {},
        is_subagent=payload.is_subagent,
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
    _require_admin(current_user)
    a = await _get_agent_by_identifier(db, agent_id)
    if not a:
        raise HTTPException(status_code=404, detail="智能体不存在")
    if payload.name:
        a.name = payload.name
    if payload.description is not None:
        a.description = payload.description
    if payload.config_json is not None:
        a.config_json = payload.config_json
    if payload.icon is not None:
        a.icon = payload.icon
    if payload.share_config is not None:
        a.share_config = payload.share_config
    if payload.is_subagent is not None:
        a.is_subagent = payload.is_subagent
    await db.commit()
    await db.refresh(a)
    return {"agent": _serialize_agent(a, current_user)}


@agent.delete("/{agent_id}")
async def delete_agent(
    agent_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    _require_admin(current_user)
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
    await db.delete(a)
    await db.commit()
    return {"ok": True}
