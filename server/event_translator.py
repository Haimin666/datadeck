"""datadeck 事件翻译层：LangGraph 图产物 → run_events 表（SSE 消费的唯一来源）。

职责（对应 DEVELOPMENT.md §2.5，成败关键层）：
- consume_graph_stream: 消费 graph.astream(["messages", "updates"])，逐条翻译为
  envelope 事件并写入 run_events；interrupt 翻译为 human_approval_required；
  updates 中非 messages 的结构化 state（todos/artifacts/token_usage/sql_validation）
  翻译为 agent_state。
- poll_run_events: SSE 轮询循环（Last-Event-ID 续传 / 心跳 / 终态补发 end）。

envelope（前端 messageProcessor.js / AgentChatComponent.handleSSEEvent 契约）：
  data: {"event": <type>, "payload": {...}}  ← event 字段，非 event_type
事件类型：init / stream_event(message_delta|tool_call) / human_approval_required
  / agent_state / finished / end / error
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, AsyncIterator

from langchain_core.messages import AIMessage, AIMessageChunk
from langgraph.types import Command
from sqlalchemy import text as sa_text

from server.config import settings
from server.db import async_session_factory
from server.models import RunEvent
from server.utils.datetime_utils import utc_now_naive
from server.utils.sse_utils import format_sse, format_heartbeat

# updates 里非 messages 的结构化 state 字段（翻译为 agent_state）
_AGENT_STATE_KEYS = ("todos", "artifacts", "token_usage", "sql_validation")

# ── 进程内实时事件总线 ──────────────────────────────────────────
# append_event 落库后立即 publish，poll_run_events 优先消费内存事件、
# 空闲时才回退 DB 轮询。同一进程内实现真·流式；跨进程/重启由轮询兜底。
_bus_subscribers: dict[str, set[asyncio.Queue]] = {}


def subscribe_run_events(run_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    _bus_subscribers.setdefault(run_id, set()).add(q)
    return q


def unsubscribe_run_events(run_id: str, q: asyncio.Queue) -> None:
    subs = _bus_subscribers.get(run_id)
    if not subs:
        return
    subs.discard(q)
    if not subs:
        _bus_subscribers.pop(run_id, None)


def publish_run_event(run_id: str, seq: int, event_type: str, payload: dict) -> None:
    for q in list(_bus_subscribers.get(run_id, ())):
        q.put_nowait((seq, event_type, payload))


async def append_event(
    run_id: str, event_type: str, payload: dict, thread_id: str | None = None,
) -> None:
    """写入一条 run_events 行（seq 由 DB identity/DEFAULT 赋值，不预分配）。

    落库后立即 publish 到进程内总线（同一进程内的 SSE 可近实时消费）；
    无订阅者时 publish 是空操作，不影响落库语义。
    """
    async with async_session_factory() as session:
        ev = RunEvent(
            id=str(uuid.uuid4()),
            run_id=run_id,
            event_type=event_type,
            payload=payload,
            thread_id=thread_id,
        )
        session.add(ev)
        await session.commit()
        await session.refresh(ev)
        seq = int(ev.seq) if ev.seq is not None else 0
    publish_run_event(run_id, seq, event_type, payload)


def _human_approval_payload(interrupt_value: dict) -> dict:
    """HITLRequest → 前端 approvalState 契约（tool_calls + tool_names + actionRequests）。"""
    requests = interrupt_value.get("action_requests") or []
    tool_calls = [
        {"id": f"tc-{i}", "name": r.get("name", ""), "args": r.get("args", {})}
        for i, r in enumerate(requests)
    ]
    return {
        "kind": "tool_approval",
        "tool_calls": tool_calls,
        "tool_names": [r.get("name", "") for r in requests],
        "actionRequests": requests,
        "review_configs": interrupt_value.get("review_configs") or [],
    }


def _extract_agent_state(updates: dict) -> dict | None:
    """从一次 node update 提取结构化 state（todos/token_usage/sql_validation/artifacts）。"""
    state = {k: updates[k] for k in _AGENT_STATE_KEYS if updates.get(k) is not None}
    return state or None


async def _load_run_run_context(run_id: str) -> tuple[str | None, str | None]:
    """读取 run 的 request_id 与 thread_id（用于前端 chunk 关联）。"""
    async with async_session_factory() as session:
        r = await session.execute(sa_text(
            "SELECT request_id, thread_id FROM agent_runs WHERE id=:rid"
        ), {"rid": run_id})
        row = r.fetchone()
    if not row:
        return None, None
    return row[0], row[1]


def _message_delta_chunk(message_id: str, content: str, thread_id: str, request_id: str | None) -> dict:
    """Yuxi 前端契约：loading chunk 内嵌 stream_event(message_delta)。"""
    return {
        "status": "loading",
        "type": "ai",
        "id": message_id,
        "request_id": request_id,
        "thread_id": thread_id,
        "stream_event": {
            "type": "message_delta",
            "message_id": message_id,
            "thread_id": thread_id,
            "content": content,
        },
    }


def _tool_call_chunk(message_id: str, tc: dict, thread_id: str, request_id: str | None) -> dict:
    """工具调用 chunk（stream_event.type=tool_call，前端 tool_call_chunks 消费）。"""
    return {
        "status": "loading",
        "request_id": request_id,
        "thread_id": thread_id,
        "stream_event": {
            "type": "tool_call",
            "message_id": message_id,
            "tool_call_id": tc.get("id", ""),
            "name": tc.get("name", ""),
            "args": tc.get("args", {}),
            "thread_id": thread_id,
        },
    }


async def consume_graph_stream(
    graph, run_id: str, thread_id: str, config: dict,
    *,
    initial_input: dict | None = None,
    resume_command: Command | None = None,
    context: object | None = None,
) -> None:
    """驱动真图流式执行，翻译为前端可消费事件并落库；interrupt 时置 run=interrupted 并停止。

    事件契约对齐 1:1 迁移的 Yuxi 前端（useAgentRunStream / useAgentStreamHandler）：
      - message_delta / tool_call → payload={chunk:{status:'loading', stream_event}}
      - agent_state               → payload={name:'yuxi.agent_state', chunk:{status:'agent_state', agent_state}}
      - human_approval_required   → payload={reason:'human_approval', chunk:{status:..., approval}}
      - finished/error            → end / error 事件（前端按 end 收尾）

    initial_input: 首轮输入 {"messages": [HumanMessage(...)]}
    resume_command: 审批恢复 Command(resume={"decisions": [...]})
    """
    if initial_input is not None:
        graph_input: Any = initial_input
    elif resume_command is not None:
        graph_input = resume_command
    else:
        return
    interrupts: list = []
    error: Exception | None = None

    request_id, _ = await _load_run_run_context(run_id)
    # run 内稳定的 AI message_id：所有 message_delta 归入同一条前端消息
    ai_message_id = f"{run_id}-ai"

    async with async_session_factory() as db:
        await db.execute(sa_text(
            "UPDATE agent_runs SET status='running', started_at=:now WHERE id=:rid"
        ), {"now": utc_now_naive(), "rid": run_id})
        await db.commit()

    try:
        async for mode, payload in graph.astream(
            graph_input, config=config, context=context, stream_mode=["messages", "updates"],
        ):
            if mode == "messages":
                chunk, meta = payload
                # 流式模型产 AIMessageChunk，非流式模型产整条 AIMessage（都发 delta）
                if not isinstance(chunk, (AIMessage, AIMessageChunk)):
                    continue
                if meta.get("langgraph_node") != "model":
                    continue
                content = chunk.content if isinstance(chunk.content, str) else ""
                if content:
                    await append_event(run_id, "messages", {
                        "chunk": _message_delta_chunk(ai_message_id, content, thread_id, request_id),
                    }, thread_id)
            elif mode == "updates":
                for node, upd in payload.items():
                    if node == "__interrupt__":
                        interrupts = list(upd)
                        continue
                    if not isinstance(upd, dict):
                        continue
                    messages = upd.get("messages") or []
                    last = messages[-1] if messages else None
                    tool_calls = getattr(last, "tool_calls", None) if last is not None else None
                    if tool_calls:
                        for tc in tool_calls:
                            await append_event(run_id, "messages", {
                                "chunk": _tool_call_chunk(ai_message_id, tc, thread_id, request_id),
                            }, thread_id)
                    state = _extract_agent_state(upd)
                    if state:
                        await append_event(run_id, "custom", {
                            "name": "yuxi.agent_state",
                            "chunk": {"status": "agent_state", "agent_state": state,
                                      "thread_id": thread_id, "request_id": request_id},
                            "agent_state": state,
                        }, thread_id)
    except Exception as exc:  # noqa: BLE001
        error = exc

    async with async_session_factory() as db:
        if error is not None:
            await db.execute(sa_text(
                "UPDATE agent_runs SET status='failed', error_type=:et, error_message=:em, "
                "finished_at=:now WHERE id=:rid"
            ), {
                "et": type(error).__name__, "em": str(error)[:500],
                "now": utc_now_naive(), "rid": run_id,
            })
            await db.commit()
            await append_event(run_id, "error", {
                "chunk": {"status": "error", "message": str(error)[:500],
                          "error_type": type(error).__name__, "request_id": request_id},
            }, thread_id)
            await append_event(run_id, "end", {
                "status": "failed",
                "chunk": {"status": "finished", "request_id": request_id},
                "run": {"id": run_id, "status": "failed"},
            }, thread_id)
            return

        if interrupts:
            await db.execute(sa_text(
                "UPDATE agent_runs SET status='interrupted', finished_at=:now WHERE id=:rid"
            ), {"now": utc_now_naive(), "rid": run_id})
            await db.commit()
            for iv in interrupts:
                value = iv.value if hasattr(iv, "value") else iv
                approval = _human_approval_payload(value or {})
                requests = approval.get("actionRequests") or []
                review_configs = approval.get("review_configs") or []
                await append_event(run_id, "interrupt", {
                    "reason": "human_approval",
                    "chunk": {
                        "status": "human_approval_required",
                        "request_id": request_id,
                        "thread_id": thread_id,
                        "run_id": run_id,
                        # 前端 processApprovalInStream 读 chunk.approval.{action_requests,review_configs}
                        "approval": {
                            "action_requests": requests,
                            "review_configs": review_configs,
                        },
                        "tool_calls": approval.get("tool_calls") or [],
                        "tool_names": approval.get("tool_names") or [],
                        "actionRequests": requests,
                    },
                }, thread_id)
            return

        await db.execute(sa_text(
            "UPDATE agent_runs SET status='completed', finished_at=:now WHERE id=:rid"
        ), {"now": utc_now_naive(), "rid": run_id})
        await db.commit()
        await append_event(run_id, "end", {
            "status": "completed",
            "chunk": {"status": "finished", "request_id": request_id},
            "run": {"id": run_id, "status": "completed"},
        }, thread_id)


def parse_after_seq(raw: str | None) -> int:
    """Last-Event-ID / after_seq → 数字游标（兼容纯数字与 "major-minor"）。"""
    if not raw:
        return 0
    text = str(raw).strip()
    if not text:
        return 0
    head = text.split("-", 1)[0]
    try:
        return int(head)
    except ValueError:
        return 0


TERMINAL_STATUSES = ("completed", "failed", "cancelled")


async def poll_run_events(
    run_id: str, *, after_seq: int, current_uid: str,
) -> AsyncIterator[str]:
    """轮询式 SSE：补发历史事件 → 追新 → 终态补发 end 后关闭。

    浏览器断开只中断轮询，不影响 run 执行（由 run_service 后台 task 驱动）。
    interrupted 状态保持连接（心跳维持），审批 resume 后产生新事件继续追新。
    """
    loop = asyncio.get_event_loop()
    cursor = after_seq
    deadline = loop.time() + settings.sse_max_connection_minutes * 60
    last_beat = loop.time()

    # 鉴权 + thread 归属
    async with async_session_factory() as session:
        r = await session.execute(sa_text(
            "SELECT thread_id, status FROM agent_runs WHERE id=:rid AND uid=:uid"
        ), {"rid": run_id, "uid": current_uid})
        row = r.fetchone()
    if not row:
        yield format_sse(
            {"event": "error", "payload": {"error": {"message": "运行任务不存在"}}},
            event="error",
        )
        return
    thread_id, status = row

    yield format_sse(
        {"event": "init", "payload": {"run_id": run_id, "thread_id": thread_id}},
        event="init", event_id=f"{max(cursor - 1, 0)}-0",
    )

    sub = subscribe_run_events(run_id)
    try:
        while True:
            fetched: list = []
            async with async_session_factory() as session:
                r = await session.execute(sa_text(
                    "SELECT seq, event_type, payload FROM run_events "
                    "WHERE run_id=:rid AND seq > :seq ORDER BY seq ASC LIMIT 500"
                ), {"rid": run_id, "seq": cursor})
                fetched = r.fetchall()

            if fetched:
                for seq, event_type, payload in fetched:
                    cursor = int(seq)
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    yield format_sse(
                        {"event": event_type, "payload": payload or {}},
                        event=event_type, event_id=f"{seq}-0",
                    )
                    if event_type == "end":
                        return
                last_beat = loop.time()
                continue

            # 无新事件：查终态
            async with async_session_factory() as session:
                r = await session.execute(sa_text(
                    "SELECT status FROM agent_runs WHERE id=:rid"
                ), {"rid": run_id})
                row2 = r.fetchone()
            status = row2[0] if row2 else "failed"

            if status in TERMINAL_STATUSES:
                yield format_sse(
                    {
                        "event": "end",
                        "payload": {
                            "status": status,
                            "chunk": {"status": "finished"},
                            "run": {"id": run_id, "status": status},
                        },
                    },
                    event="end", event_id=f"{cursor + 1}-0",
                )
                return

            # 等待本进程总线信号（append_event 落库即 publish）→ 近实时；
            # 超时则回退下一次 DB 轮询（覆盖跨进程/重启前事件）。
            try:
                await asyncio.wait_for(sub.get(), timeout=settings.sse_poll_interval_seconds)
            except (asyncio.TimeoutError, TimeoutError):
                pass

            if loop.time() - last_beat >= settings.sse_heartbeat_seconds:
                last_beat = loop.time()
                yield format_heartbeat()
            if loop.time() > deadline:
                return
    finally:
        unsubscribe_run_events(run_id, sub)


def _has_more(fetched: list) -> bool:
    return len(fetched) >= 500
