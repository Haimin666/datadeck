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
from server.utils.datetime_utils import utc_now_naive
from datadeck import logger

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
    model_spec: str | None = None,
    meta: dict | None = None,
    image_content: str | None = None,
    queue_policy: str = "enqueue",
    request_id: str | None = None,
    db: AsyncSession,
) -> AgentRun:
    """创建 run 行（pending）；resume 场景复用原 interrupted 行。

    request_id 优先采用前端传入值（对齐 optimistic 消息与队列语义），缺省自生成。
    """
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
        request_id=str(request_id) if request_id else str(uuid.uuid4()),
        input_payload={
            "query": query,
            "agent_slug": agent_slug,
            "thread_id": thread_id,
            **({"model_spec": model_spec} if model_spec else {}),
            **({"image_content": image_content} if image_content else {}),
            "meta": meta or {},
            "queue_policy": queue_policy,
        },
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
                "SELECT r.thread_id, r.uid, r.input_payload, r.request_id, "
                "t.tool_approval_mode, p.workdir_path, a.config_json "
                "FROM agent_runs r "
                "JOIN threads t ON t.id=r.thread_id AND t.uid=r.uid "
                "JOIN agents a ON a.slug=r.agent_slug "
                "LEFT JOIN projects p ON p.id=t.project_id AND p.uid=t.uid "
                "WHERE r.id=:rid"
            ), {"rid": run_id})
            row = r.fetchone()
        if not row:
            return
        (thread_id, uid, input_payload, request_id, tool_approval_mode,
         workdir_path, agent_config) = row
        query = (input_payload or {}).get("query", "")
        image_content = (input_payload or {}).get("image_content")
        model_spec = (input_payload or {}).get("model_spec") or ""
        logger.info(f"run {run_id} executor started: thread={thread_id}, model={model_spec or 'default'}")

        # 任务分类路由提示（阶段二 2.2）：确定性预分类，辅助模型选工具；不确定则无提示
        from datadeck.agents.middlewares.task_router import routing_hint
        hint = routing_hint(query)
        if hint:
            query = f"[路由提示] {hint}\n\n{query}"

        agent = await get_chatbot_agent()
        context = agent.context_schema()
        configured_context = (agent_config or {}).get("context", agent_config or {})
        if isinstance(configured_context, dict):
            context.update(configured_context)
        context.update({
            "thread_id": thread_id,
            "uid": uid,
            "run_id": run_id,
            "request_id": request_id,
            "model": model_spec or context.model,
        })
        context.tool_approval_mode = tool_approval_mode or "default"
        context.workdir_path = workdir_path
        if workdir_path:
            from server.services.workdir_service import resolve_authorized_workdir

            context.workdir = (await resolve_authorized_workdir(
                thread_id=thread_id, uid=uid, db=db
            )).workdir
        graph = await agent.get_graph(context=context)
        logger.info(f"run {run_id} graph ready")
        config = {"configurable": {"thread_id": thread_id, "uid": uid}}
        if model_spec:
            config["configurable"]["model"] = model_spec

        # 业务域隔离（阶段四 4.5）：run 级注入用户 domain，工具按需读取
        domain = await _get_user_domain(uid)
        if domain:
            config["configurable"]["domain"] = domain

        if resume_command is not None:
            await consume_graph_stream(
                graph, run_id, thread_id, config, resume_command=resume_command,
                context=context,
            )
        else:
            human_content = query
            if image_content:
                human_content = [
                    {"type": "text", "text": query},
                    {"type": "image_url", "image_url": {"url": image_content}},
                ]
            await consume_graph_stream(
                graph, run_id, thread_id, config,
                initial_input={"messages": [HumanMessage(content=human_content)]},
                context=context,
            )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(f"run {run_id} executor crashed: {exc}")
        async with async_session_factory() as db:
            await db.execute(sa_text(
                "UPDATE agent_runs SET status='failed', error_type=:et, error_message=:em, "
                "finished_at=:now WHERE id=:rid "
                "AND status NOT IN ('completed','failed','cancelled')"
            ), {"et": type(exc).__name__, "em": str(exc)[:500],
                "now": utc_now_naive(), "rid": run_id})
            await db.commit()
        await append_event(run_id, "error", {
            "error": {"message": str(exc)[:500], "type": type(exc).__name__},
        })
        await append_event(run_id, "end", {"run": {"id": run_id, "status": "failed"}})
    finally:
        await _dispatch_next_queued(run_id)


def build_resume_command(
    tool_approval: dict | None = None,
    *,
    resume_payload: object | None = None,
) -> Command:
    """前端审批结果 → Command(resume={"decisions": [...]})。

    HITL 协议（已验证）：resume payload 是 {"decisions": [...]}，
    decisions 数量须与 action_requests 一一对应。
    """
    if resume_payload is not None:
        return Command(resume=resume_payload)
    supplied_decisions = (tool_approval or {}).get("decisions")
    if isinstance(supplied_decisions, list) and supplied_decisions:
        return Command(resume={"decisions": supplied_decisions})
    approved = bool((tool_approval or {}).get("approved", True))
    if approved:
        decisions: list[dict] = [{"type": "approve"}]
    else:
        decisions = [{
            "type": "reject",
            "message": (tool_approval or {}).get("reason") or "用户拒绝执行该工具",
        }]
    return Command(resume={"decisions": decisions})


async def _dispatch_next_queued(completed_run_id: str) -> None:
    """同一线程串行执行 pending run；steer 请求优先于普通 enqueue。"""
    async with async_session_factory() as db:
        r = await db.execute(sa_text(
            "SELECT thread_id, uid, status FROM agent_runs WHERE id=:rid"
        ), {"rid": completed_run_id})
        current = r.fetchone()
        if not current:
            return
        thread_id, uid, status = current
        if status not in {"completed", "failed", "cancelled"}:
            return
        r = await db.execute(sa_text(
            "SELECT 1 FROM agent_runs "
            "WHERE thread_id=:tid AND uid=:uid AND id<>:rid "
            "AND status IN ('running','cancel_requested','interrupted') LIMIT 1"
        ), {"tid": thread_id, "uid": uid, "rid": completed_run_id})
        if r.fetchone():
            return
        r = await db.execute(sa_text(
            "SELECT id FROM agent_runs "
            "WHERE thread_id=:tid AND uid=:uid AND status='pending' AND id<>:rid "
            "ORDER BY CASE WHEN input_payload->>'queue_policy'='steer' THEN 0 ELSE 1 END, "
            "created_at ASC LIMIT 1"
        ), {"tid": thread_id, "uid": uid, "rid": completed_run_id})
        row = r.fetchone()
    if row:
        await dispatch_run(row[0])


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
        ), {"now": utc_now_naive(), "rid": run_id})
        await db.commit()
    await append_event(run_id, "finished", {"run": {"id": run_id, "status": "cancelled"}})
    await append_event(run_id, "end", {"run": {"id": run_id, "status": "cancelled"}})
    await _dispatch_next_queued(run_id)
    return "cancelled"
