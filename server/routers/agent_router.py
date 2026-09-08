"""智能体管理路由。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.deps import get_required_user
from server.models import User, Agent
from datadeck.agents.buildin.chatbot.graph import ChatbotAgent
from datadeck.ports.checkpointer import CheckpointerProvider

agent = APIRouter(prefix="/agent", tags=["agent"])


class AgentCreate(BaseModel):
    name: str
    backend_id: str = "ChatbotAgent"
    slug: str | None = None
    description: str | None = None
    config_json: dict | None = None


class AgentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    config_json: dict | None = None


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
        return {"agents": [builtin.to_dict()]}
    return {"agents": [a.to_dict() for a in agents]}


@agent.get("/default")
async def get_default_agent(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Agent).where(Agent.slug == "default-chatbot"))
    a = result.scalar_one_or_none()
    if a:
        return {"agent": a.to_dict()}
    builtin = Agent(id="default-chatbot", slug="default-chatbot", name="对话助手",
                    description="内置对话智能体", backend_id="ChatbotAgent", is_builtin=True)
    db.add(builtin)
    await db.commit()
    await db.refresh(builtin)
    return {"agent": builtin.to_dict()}


@agent.get("/{agent_id}")
async def get_agent(agent_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    a = result.scalar_one_or_none()
    if not a:
        raise HTTPException(status_code=404, detail="智能体不存在")
    return {"agent": a.to_dict()}


@agent.post("")
async def create_agent(body: AgentCreate, db: AsyncSession = Depends(get_db)):
    slug = body.slug or body.name
    existing = await db.execute(select(Agent).where(Agent.slug == slug))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="slug 已存在")
    a = Agent(
        id=str(hash(slug) % (2**63)),
        slug=slug,
        name=body.name,
        description=body.description,
        backend_id=body.backend_id,
        config_json=body.config_json or {},
    )
    db.add(a)
    await db.commit()
    await db.refresh(a)
    return {"agent": a.to_dict()}


@agent.put("/{agent_id}")
async def update_agent(agent_id: str, body: AgentUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    a = result.scalar_one_or_none()
    if not a:
        raise HTTPException(status_code=404, detail="智能体不存在")
    if body.name:
        a.name = body.name
    if body.description is not None:
        a.description = body.description
    if body.config_json is not None:
        a.config_json = body.config_json
    await db.commit()
    await db.refresh(a)
    return {"agent": a.to_dict()}


@agent.delete("/{agent_id}")
async def delete_agent(agent_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Agent).where(Agent.id == agent_id, Agent.is_builtin == False))
    a = result.scalar_one_or_none()
    if not a:
        raise HTTPException(status_code=404, detail="智能体不存在或无法删除内置智能体")
    await db.delete(a)
    await db.commit()
    return {"ok": True}
