"""Agent 运行服务：run 生命周期 + 后台执行器。

架构（对齐统一运行时装配方案）：
- create_agent_run: 落库（新 run=pending；resume 复用 interrupted 原行）
- dispatch_run: 派发后台 task 执行真图，事件写 run_events；SSE 只轮询表
- 进程内 registry 记录运行中 task（取消用）；多实例部署由 run_events 轮询兜底
"""
from __future__ import annotations

import asyncio
import os
import re
import uuid
from datetime import timedelta

from langgraph.types import Command
from sqlalchemy import select as sa_select, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from server.config import settings
from server.db import async_session_factory
from server.event_translator import append_event, consume_graph_stream
from server.models import AgentRun, User
from server.services.agents_provider import get_agent
from server.services.agent_runtime_contract import RuntimeAssemblyError
from server.utils.datetime_utils import utc_now_naive
from datadeck import logger

# 进程内运行注册表：run_id → asyncio.Task
_running: dict[str, asyncio.Task] = {}
_shutting_down = False

_MENTION_PATTERN = re.compile(
    r'@(?P<kind>knowledge|skill):(?:"(?P<quoted>(?:\\.|[^"\\])*)"|(?P<plain>[^\s，。！？；：、]+))'
)


def _runtime_resource_mentions(query: str) -> tuple[list[str], list[str]]:
    """解析消息中的知识库/Skill 引用，作为本次运行的临时资源选择。"""
    knowledges: list[str] = []
    skills: list[str] = []
    for match in _MENTION_PATTERN.finditer(str(query or "")):
        value = match.group("quoted") if match.group("quoted") is not None else match.group("plain")
        value = re.sub(r'\\(["\\])', r'\1', value or "").strip()
        if not value:
            continue
        target = knowledges if match.group("kind") == "knowledge" else skills
        if value not in target:
            target.append(value)
    return knowledges, skills


def _remove_running_task(run_id: str, task: asyncio.Task) -> None:
    """只移除仍指向当前 task 的注册项，避免旧回调删掉新任务。"""
    if _running.get(run_id) is task:
        _running.pop(run_id, None)


def _agent_run_timeout_seconds() -> float:
    """限制单次 Agent 图执行时长，避免后台任务永久占用并让前端无限等待。"""
    try:
        value = float(os.getenv("DATADECK_AGENT_RUN_TIMEOUT", "180"))
    except (TypeError, ValueError):
        value = 180.0
    return max(value, 1.0)


async def _claim_pending_run(run_id: str) -> bool:
    """按线程串行抢占 pending run，避免多实例并发执行同一线程。"""
    async with async_session_factory() as db:
        run_result = await db.execute(sa_text(
            "SELECT thread_id, uid FROM agent_runs WHERE id=:rid FOR UPDATE"
        ), {"rid": run_id})
        run_row = run_result.fetchone()
        if run_row is None:
            await db.commit()
            return False

        # HTTP 层的 active 检查不是并发安全的；使用事务级 advisory lock
        # 将同一 uid/thread 的“检查 + 抢占”序列化，锁不会跨请求泄漏。
        await db.execute(sa_text(
            "SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"
        ), {"lock_key": f"datadeck:agent-run:{run_row[1]}:{run_row[0]}"})
        now = utc_now_naive()
        result = await db.execute(sa_text(
            "UPDATE agent_runs AS candidate "
            "SET status='running', started_at=:now, updated_at=:now "
            "WHERE candidate.id=:rid AND candidate.status='pending' "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM agent_runs AS active "
            "  WHERE active.thread_id=candidate.thread_id AND active.uid=candidate.uid "
            "    AND active.id<>candidate.id "
            "    AND active.status IN ('running','cancel_requested','interrupted')"
            ") RETURNING candidate.id"
        ), {"rid": run_id, "now": now})
        claimed = result.fetchone() is not None
        await db.commit()
    return claimed


async def recover_orphaned_agent_runs() -> int:
    """回收没有心跳的运行记录，避免误伤其他实例的活跃 Run。

    ``updated_at`` 由运行实例心跳维护；只回收超过一次运行超时并留有
    宽限期的记录。这样多副本部署时，一个实例启动不会立即终止另一个
    实例正在执行的任务。
    """
    now = utc_now_naive()
    stale_after = max(60.0, _agent_run_timeout_seconds() + 30.0)
    stale_before = now - timedelta(seconds=stale_after)
    async with async_session_factory() as db:
        result = await db.execute(sa_text(
            "UPDATE agent_runs SET status='failed', error_type='ServiceRestart', "
            "error_message='服务重启导致 Agent 运行中断', finished_at=:now, updated_at=:now "
            "WHERE status IN ('running','cancel_requested') AND updated_at < :stale_before"
        ), {"now": now, "stale_before": stale_before})
        await db.commit()
        return int(result.rowcount or 0)


async def _heartbeat_agent_run(run_id: str) -> None:
    """为本进程持有的运行租约续期。"""
    interval = min(15.0, max(1.0, _agent_run_timeout_seconds() / 3.0))
    try:
        while True:
            await asyncio.sleep(interval)
            async with async_session_factory() as db:
                await db.execute(sa_text(
                    "UPDATE agent_runs SET updated_at=:now "
                    "WHERE id=:rid AND status='running'"
                ), {"now": utc_now_naive(), "rid": run_id})
                await db.commit()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("Agent run %s heartbeat stopped: %s", run_id, exc)


async def resume_pending_agent_runs() -> int:
    """服务启动后恢复每个线程队首的 pending run。

    pending 表示尚未开始执行的排队请求，不能在重启时标记失败；同一线程
    仍然遵循串行规则，存在审批中断时也必须等待用户恢复。
    """
    async with async_session_factory() as db:
        result = await db.execute(sa_text(
            "SELECT p.id FROM agent_runs p "
            "WHERE p.status='pending' "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM agent_runs active "
            "  WHERE active.thread_id=p.thread_id AND active.uid=p.uid "
            "    AND active.status IN ('running','cancel_requested','interrupted')"
            ") "
            "AND NOT EXISTS ("
            "  SELECT 1 FROM agent_runs earlier "
            "  WHERE earlier.thread_id=p.thread_id AND earlier.uid=p.uid "
            "    AND earlier.status='pending' "
            "    AND (earlier.created_at < p.created_at "
            "      OR (earlier.created_at = p.created_at AND earlier.id < p.id))"
            ") "
            "ORDER BY p.created_at ASC"
        ))
        run_ids = [row[0] for row in result.fetchall()]
    for run_id in run_ids:
        await dispatch_run(run_id)
    return len(run_ids)


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
            "UPDATE agent_runs SET status='pending', started_at=NULL, finished_at=NULL, "
        "error_type=NULL, error_message=NULL, updated_at=:now "
        "WHERE id=:rid AND uid=:uid AND status='interrupted' "
        "RETURNING id"
        ), {"rid": resume, "uid": uid, "now": utc_now_naive()})
        if not r.fetchone():
            raise ValueError(f"待恢复的 run 不存在或状态不可恢复: {resume}")
        # 必须在 dispatch 前提交状态，避免并发 resume 再次启动同一条执行链。
        await db.commit()
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
    if _shutting_down:
        return
    existing = _running.get(run_id)
    if existing is not None and not existing.done():
        return
    task = asyncio.create_task(_execute_run(run_id, resume_command=resume_command))
    _running[run_id] = task
    task.add_done_callback(lambda completed: _remove_running_task(run_id, completed))


async def shutdown_running_agent_runs() -> None:
    """停止并等待进程内 Agent Run，确保数据库引擎关闭前归还连接。"""
    global _shutting_down
    _shutting_down = True
    try:
        tasks = [task for task in _running.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        _running.clear()
    finally:
        _shutting_down = False


async def _mark_run_failed_after_shutdown(run_id: str) -> None:
    """将本实例因停机取消的运行立即收敛为失败并写入终态事件。"""
    async with async_session_factory() as db:
        result = await db.execute(sa_text(
            "UPDATE agent_runs SET status='failed', error_type='ServiceRestart', "
            "error_message='服务重启导致 Agent 运行中断', finished_at=:now, updated_at=:now "
            "WHERE id=:rid AND status IN ('running','cancel_requested') "
            "RETURNING thread_id"
        ), {"now": utc_now_naive(), "rid": run_id})
        row = result.fetchone()
        await db.commit()
    if row is None:
        return
    thread_id = row[0]
    error_payload = {
        "error": {
            "message": "服务重启导致 Agent 运行中断",
            "type": "ServiceRestart",
        },
    }
    await append_event(run_id, "error", error_payload, thread_id)
    await append_event(
        run_id, "end", {"run": {"id": run_id, "status": "failed"}}, thread_id,
    )


async def _execute_run(run_id: str, *, resume_command: Command | None = None) -> None:
    """后台执行器：组装 agent + 真图流式翻译（细节在 event_translator）。"""
    from langchain_core.messages import HumanMessage

    heartbeat_task: asyncio.Task | None = None
    try:
        # dispatch_run 仅负责创建 task，真正执行前必须再次由数据库原子抢占。
        # 这样多个应用实例同时恢复/派发时，只有一个实例能进入 Agent 图。
        if not await _claim_pending_run(run_id):
            logger.info("run %s was already claimed or is no longer pending", run_id)
            return
        heartbeat_task = asyncio.create_task(
            _heartbeat_agent_run(run_id), name=f"agent-run-heartbeat:{run_id}"
        )
        async with async_session_factory() as db:
            r = await db.execute(sa_text(
                "SELECT r.thread_id, r.uid, r.agent_slug, r.input_payload, r.request_id, "
                "t.tool_approval_mode, t.project_id, p.workdir_path, a.backend_id, a.config_json "
                "FROM agent_runs r "
                "JOIN threads t ON t.id=r.thread_id AND t.uid=r.uid "
                "JOIN agents a ON a.slug=r.agent_slug "
                "LEFT JOIN projects p ON p.id=t.project_id AND p.uid=t.uid "
                "WHERE r.id=:rid"
            ), {"rid": run_id})
            row = r.fetchone()
        if not row:
            async with async_session_factory() as db:
                await db.execute(sa_text(
                    "UPDATE agent_runs SET status='failed', error_type='RunNotFound', "
                    "error_message='Agent 运行上下文不存在', finished_at=:now, updated_at=:now "
                    "WHERE id=:rid AND status NOT IN ('completed','failed','cancelled')"
                ), {"now": utc_now_naive(), "rid": run_id})
                await db.commit()
            await append_event(run_id, "error", {
                "error": {"message": "Agent 运行上下文不存在", "type": "RunNotFound"},
            })
            await append_event(run_id, "end", {"run": {"id": run_id, "status": "failed"}})
            return
        (thread_id, uid, stored_agent_slug, input_payload, request_id, tool_approval_mode,
         project_id, workdir_path, agent_backend_id, agent_config) = row
        agent_slug = str(stored_agent_slug)
        query = (input_payload or {}).get("query", "")
        image_content = (input_payload or {}).get("image_content")
        model_spec = (input_payload or {}).get("model_spec") or ""
        logger.info(f"run {run_id} executor started: thread={thread_id}, model={model_spec or 'default'}")

        # 任务分类路由提示（阶段二 2.2）：仅注入内部运行上下文，不能拼入用户消息。
        from datadeck.agents.middlewares.task_router import classify_query, routing_hint
        hint = routing_hint(query)
        task_kind = classify_query(query) or ""

        # 所有正式运行都通过同一个 Agent 选择入口；数据 Agent 与通用 Agent
        # 只在 provider 内部按 backend_id 选择不同实现，运行服务不再分叉。
        agent = await get_agent(agent_slug, agent_backend_id)
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
            "agent_backend_id": agent_backend_id,
            "routing_hint": hint,
            "task_kind": task_kind,
        })
        mentioned_knowledges, mentioned_skills = _runtime_resource_mentions(query)
        if mentioned_knowledges:
            configured_knowledges = context.get("knowledges")
            context.knowledges = list(dict.fromkeys([
                *(configured_knowledges or []), *mentioned_knowledges,
            ]))
        if mentioned_skills:
            configured_skills = context.get("skills")
            context.skills = list(dict.fromkeys([
                *(configured_skills or []), *mentioned_skills,
            ]))
        run_meta = (input_payload or {}).get("meta") or {}
        if isinstance(run_meta, dict):
            attachment_ids = run_meta.get("attachment_file_ids")
            if isinstance(attachment_ids, (list, tuple)):
                context.attachment_file_ids = [
                    str(item).strip() for item in attachment_ids if str(item).strip()
                ]
            try:
                context.subagent_depth = max(0, int(run_meta.get("subagent_depth", 0)))
            except (TypeError, ValueError):
                context.subagent_depth = 0
        context.tool_approval_mode = (
            "none"
            if settings.agent_allow_all_actions
            else (tool_approval_mode or "default")
        )
        context.project_id = project_id
        context.workdir_path = workdir_path
        if workdir_path:
            from server.services.workdir_service import resolve_authorized_workdir

            # 上面的上下文查询 session 已经离开 async with；工作目录授权必须
            # 使用独立 session，不能把已关闭的 session 传入 Repository。
            async with async_session_factory() as workdir_db:
                context.workdir = (await resolve_authorized_workdir(
                    thread_id=thread_id, uid=uid, db=workdir_db
                )).workdir
        else:
            from server.workspace.temp_workdir import open_temporary_workdir

            context.workdir = open_temporary_workdir(str(uid), str(thread_id))
            context.workdir_path = context.workdir.relative_path
        # 运行级资源只由宿主装配器解析一次；Agent 核心只消费 Context 快照。
        from server.services.agent_runtime_assembler import assemble_agent_runtime
        async with async_session_factory() as runtime_db:
            runtime_user = await runtime_db.scalar(
                sa_select(User).where(User.uid == uid, User.is_deleted == 0)
            )
            if runtime_user is None:
                raise RuntimeError("Agent 运行用户不存在或已被禁用")
            from server.deps import require_agent_access

            await require_agent_access(runtime_db, runtime_user, agent_slug)
            snapshot = await assemble_agent_runtime(
                context, db=runtime_db, user=runtime_user, agent_slug=agent_slug
            )
            # 快照本身是 trace 的第一等事件，诊断事件单独发送便于前端筛选。
            await append_event(run_id, "runtime_snapshot", {
                "run_id": run_id,
                "thread_id": thread_id,
                "snapshot": snapshot.to_dict(),
            }, thread_id)
            for diagnostic in getattr(context, "_runtime_diagnostics", []) or []:
                await append_event(run_id, "runtime_diagnostic", diagnostic, thread_id)
        graph = await agent.get_graph(context=context)
        logger.info(f"run {run_id} graph ready")
        try:
            max_execution_steps = int(getattr(context, "max_execution_steps", 300))
        except (TypeError, ValueError):
            max_execution_steps = 300
        config = {
            "configurable": {"thread_id": thread_id, "uid": uid},
            "recursion_limit": max(1, max_execution_steps),
        }
        if model_spec:
            config["configurable"]["model"] = model_spec

        # 业务域隔离（阶段四 4.5）：run 级注入用户 domain，工具按需读取
        domain = await _get_user_domain(uid)
        if domain:
            config["configurable"]["domain"] = domain

        if resume_command is not None:
            await asyncio.wait_for(
                consume_graph_stream(
                    graph, run_id, thread_id, config, resume_command=resume_command,
                    context=context,
                ),
                timeout=_agent_run_timeout_seconds(),
            )
        else:
            human_content = query
            if image_content:
                human_content = [
                    {"type": "text", "text": query},
                    {"type": "image_url", "image_url": {"url": image_content}},
                ]
            await asyncio.wait_for(
                consume_graph_stream(
                    graph, run_id, thread_id, config,
                    initial_input={"messages": [HumanMessage(content=human_content)]},
                    context=context,
                ),
                timeout=_agent_run_timeout_seconds(),
            )
    except asyncio.CancelledError:
        if _shutting_down:
            try:
                await _mark_run_failed_after_shutdown(run_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not finalize Agent run %s during shutdown: %s", run_id, exc)
        raise
    except asyncio.TimeoutError:
        error_message = "Agent 运行超时：模型或工具在限定时间内没有返回"
        logger.error(f"run {run_id} executor timed out after {_agent_run_timeout_seconds()}s")
        async with async_session_factory() as db:
            await db.execute(sa_text(
                "UPDATE agent_runs SET status='failed', error_type='TimeoutError', "
                "error_message=:em, finished_at=:now, updated_at=:now WHERE id=:rid "
                "AND status NOT IN ('completed','failed','cancelled')"
            ), {"em": error_message, "now": utc_now_naive(), "rid": run_id})
            await db.commit()
        await append_event(run_id, "error", {
            "error": {"message": error_message, "type": "TimeoutError"},
        })
        await append_event(run_id, "end", {"run": {"id": run_id, "status": "failed"}})
    except RuntimeAssemblyError as exc:
        diagnostic = exc.diagnostic.to_dict()
        error_message = diagnostic["message"]
        logger.error("run %s runtime assembly failed: %s", run_id, error_message)
        async with async_session_factory() as db:
            await db.execute(sa_text(
                "UPDATE agent_runs SET status='failed', error_type=:et, error_message=:em, "
                "finished_at=:now, updated_at=:now WHERE id=:rid "
                "AND status NOT IN ('completed','failed','cancelled')"
            ), {"et": diagnostic["code"], "em": error_message[:500],
                "now": utc_now_naive(), "rid": run_id})
            await db.commit()
        await append_event(run_id, "runtime_diagnostic", diagnostic)
        await append_event(run_id, "error", {
            "error": {"message": error_message[:500], "type": diagnostic["code"]},
        })
        await append_event(run_id, "end", {"run": {"id": run_id, "status": "failed"}})
    except Exception as exc:  # noqa: BLE001
        logger.error(f"run {run_id} executor crashed: {exc}")
        async with async_session_factory() as db:
            await db.execute(sa_text(
                "UPDATE agent_runs SET status='failed', error_type=:et, error_message=:em, "
                "finished_at=:now, updated_at=:now WHERE id=:rid "
                "AND status NOT IN ('completed','failed','cancelled')"
            ), {"et": type(exc).__name__, "em": str(exc)[:500],
                "now": utc_now_naive(), "rid": run_id})
            await db.commit()
        await append_event(run_id, "error", {
            "error": {"message": str(exc)[:500], "type": type(exc).__name__},
        })
        await append_event(run_id, "end", {"run": {"id": run_id, "status": "failed"}})
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
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
    if not isinstance(tool_approval, dict):
        raise ValueError("恢复 Run 必须提供审批决定或用户回答")
    supplied_decisions = tool_approval.get("decisions")
    if isinstance(supplied_decisions, list) and supplied_decisions:
        return Command(resume={"decisions": supplied_decisions})
    approved = tool_approval.get("approved")
    if not isinstance(approved, bool):
        raise ValueError("审批结果必须包含 approved 布尔值或 decisions 列表")
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
    if _shutting_down:
        return
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
            "SELECT thread_id, status FROM agent_runs WHERE id=:rid AND uid=:uid"
        ), {"rid": run_id, "uid": uid})
        row = r.fetchone()
        if not row:
            raise ValueError("Run 不存在")
        thread_id, status = row
        if status not in ("running", "pending", "cancel_requested", "interrupted"):
            raise ValueError(f"Run 当前状态不可取消: {status}")

        # 初始状态检查与终态迁移之间可能发生完成/失败；只有真正更新了
        # 记录才允许发布 cancelled 事件，避免终态竞态制造矛盾 Trace。
        updated = await db.execute(sa_text(
            "UPDATE agent_runs SET status='cancelled', finished_at=:now, updated_at=:now "
            "WHERE id=:rid AND uid=:uid AND status IN ('running','pending','cancel_requested','interrupted') "
            "RETURNING status"
        ), {"now": utc_now_naive(), "rid": run_id, "uid": uid})
        if updated.fetchone() is None:
            current = await db.scalar(sa_text(
                "SELECT status FROM agent_runs WHERE id=:rid AND uid=:uid"
            ), {"rid": run_id, "uid": uid})
            await db.commit()
            if current:
                return str(current)
            raise ValueError("Run 不存在")
        await db.commit()

    task = _running.get(run_id)
    if task is not None and not task.done():
        task.cancel()

    await append_event(
        run_id, "finished", {"run": {"id": run_id, "status": "cancelled"}}, thread_id,
    )
    await append_event(
        run_id, "end", {"run": {"id": run_id, "status": "cancelled"}}, thread_id,
    )
    await _dispatch_next_queued(run_id)
    return "cancelled"
