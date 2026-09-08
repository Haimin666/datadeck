"""Agent 运行服务：run 生命周期 + 后台执行器。

架构（对齐 DEVELOPMENT.md §2.2）：
- create_agent_run: 落库（新 run=pending；resume 复用 interrupted 原行）
- dispatch_run: 派发后台 task 执行真图，事件写 run_events；SSE 只轮询表
- 进程内 registry 记录运行中 task（取消用）；多实例部署由 run_events 轮询兜底
"""
from __future__ import annotations

import asyncio
import uuid

from langgraph.types import Command
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import async_session_factory
from server.event_translator import append_event, consume_graph_stream
from server.models import AgentRun
from server.services.agents_provider import get_chatbot_agent
from server.utils.datetime_utils import utc_now

# 进程内运行注册表：run_id → asyncio.Task
_running: dict[str, asyncio.Task] = {}


async def _get_user_domain(uid: str) -> str:
    """查用户业务域；查询失败返回空（不阻塞链路）。"""
    try:
        async with async_session_factory() as db:
            r = await db.execute(sa_text(
                "SELECT domain FROM users WHERE uid=:u LIMIT 1"), {"u": uid})
            row = r.fetchone()
        return (row[0] if row else "") or "default"
    except Exception:  # noqa: BLE001
        return "default"


async def create_agent_run(
    *,
    query: str,
    agent_slug: str,
    thread_id: str,
    uid: str,
    resume: str | None = None,
    db: AsyncSession,
) -> AgentRun:
    """创建 run 行（pending）；resume 场景复用原 interrupted 行。"""
    if resume:
        r = await db.execute(sa_text(
            "SELECT id FROM agent_runs WHERE id=:rid AND uid=:uid AND status='interrupted'"
        ), {"rid": resume, "uid": uid})
        if not r.fetchone():
            raise ValueError(f"待恢复的 run 不存在或状态不可恢复: {resume}")
        existing = await db.get(AgentRun, resume)
        if existing is None:
            raise ValueError(f"run 不存在: {resume}")
        return existing

    run_id = str(uuid.uuid4())
    run = AgentRun(
        id=run_id,
        thread_id=thread_id,
        uid=uid,
        agent_slug=agent_slug,
        status="pending",
        source="web",
        channel="web",
        request_id=str(uuid.uuid4()),
        input_payload={"query": query, "agent_slug": agent_slug, "thread_id": thread_id},
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def dispatch_run(
    run_id: str, *, resume_command: Command | None = None,
) -> None:
    """派发后台执行 task（幂等：同 run 已在运行则跳过）。"""
    existing = _running.get(run_id)
    if existing is not None and not existing.done():
        return
    task = asyncio.create_task(_execute_run(run_id, resume_command=resume_command))
    _running[run_id] = task
    task.add_done_callback(lambda _t: _running.pop(run_id, None))


async def _execute_run(run_id: str, *, resume_command: Command | None = None) -> None:
    """后台执行器：组装 agent + 真图流式翻译（细节在 event_translator）。"""
    from langchain_core.messages import HumanMessage

    try:
        async with async_session_factory() as db:
            r = await db.execute(sa_text(
                "SELECT thread_id, uid, input_payload FROM agent_runs WHERE id=:rid"
            ), {"rid": run_id})
            row = r.fetchone()
        if not row:
            return
        thread_id, uid, input_payload = row
        query = (input_payload or {}).get("query", "")

        # 任务分类路由提示（阶段二 2.2）：确定性预分类，辅助模型选工具；不确定则无提示
        from datadeck.agents.middlewares.task_router import routing_hint
        hint = routing_hint(query)
        if hint:
            query = f"[路由提示] {hint}\n\n{query}"

        agent = await get_chatbot_agent()
        graph = await agent.get_graph()
        config = {"configurable": {"thread_id": thread_id, "uid": uid}}

        # 业务域隔离（阶段四 4.5）：run 级注入用户 domain，工具按需读取
        domain = await _get_user_domain(uid)
        if domain:
            config["configurable"]["domain"] = domain

        if resume_command is not None:
            await consume_graph_stream(
                graph, run_id, thread_id, config, resume_command=resume_command,
            )
        else:
            await consume_graph_stream(
                graph, run_id, thread_id, config,
                initial_input={"messages": [HumanMessage(content=query)]},
            )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        from datadeck import logger
        logger.error(f"run {run_id} executor crashed: {exc}")
        async with async_session_factory() as db:
            await db.execute(sa_text(
                "UPDATE agent_runs SET status='failed', error_type=:et, error_message=:em, "
                "finished_at=:now WHERE id=:rid "
                "AND status NOT IN ('completed','failed','cancelled')"
            ), {"et": type(exc).__name__, "em": str(exc)[:500],
                "now": utc_now(), "rid": run_id})
            await db.commit()
        await append_event(run_id, "error", {
            "error": {"message": str(exc)[:500], "type": type(exc).__name__},
        })
        await append_event(run_id, "end", {"run": {"id": run_id, "status": "failed"}})


def build_resume_command(tool_approval: dict | None) -> Command:
    """前端审批结果 → Command(resume={"decisions": [...]})。

    HITL 协议（已验证）：resume payload 是 {"decisions": [...]}，
    decisions 数量须与 action_requests 一一对应。
    """
    approved = bool((tool_approval or {}).get("approved", True))
    if approved:
        decisions: list[dict] = [{"type": "approve"}]
    else:
        decisions = [{
            "type": "reject",
            "message": (tool_approval or {}).get("reason") or "用户拒绝执行该工具",
        }]
    return Command(resume={"decisions": decisions})


async def request_cancel(run_id: str, uid: str) -> str:
    """取消 run：置 cancel_requested → 终态化 cancelled + 终态事件。"""
    async with async_session_factory() as db:
        r = await db.execute(sa_text(
            "SELECT status FROM agent_runs WHERE id=:rid AND uid=:uid"
        ), {"rid": run_id, "uid": uid})
        row = r.fetchone()
        if not row:
            raise ValueError("Run 不存在")
        if row[0] not in ("running", "pending", "cancel_requested"):
            raise ValueError(f"Run 当前状态不可取消: {row[0]}")

    task = _running.get(run_id)
    if task is not None and not task.done():
        task.cancel()

    async with async_session_factory() as db:
        await db.execute(sa_text(
            "UPDATE agent_runs SET status='cancelled', finished_at=:now WHERE id=:rid "
            "AND status NOT IN ('completed','failed','cancelled')"
        ), {"now": utc_now(), "rid": run_id})
        await db.commit()
    await append_event(run_id, "finished", {"run": {"id": run_id, "status": "cancelled"}})
    await append_event(run_id, "end", {"run": {"id": run_id, "status": "cancelled"}})
    return "cancelled"
