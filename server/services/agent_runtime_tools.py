"""宿主侧 Agent 工具：Skill 文件和当前用户的定时任务。"""
from __future__ import annotations

import uuid
import asyncio
from pathlib import PurePosixPath

from langchain_core.tools import StructuredTool
from sqlalchemy import select

from server.db import session_context
from server.models import Agent, AgentRun, Project, RunEvent, ScheduledTask, Thread, User
from server.services.skills.service import read_personal_skill_file, read_skill_file
from server.services.skills.virtual_paths import VIRTUAL_PERSONAL_SKILLS_PATH, VIRTUAL_SKILLS_PATH
from server.utils.cron import validate_cron_expression


def summarize_subagent_results(results: dict[str, dict]) -> dict:
    """统一判定编排结果，禁止部分失败被包装成成功。"""
    statuses = [str(item.get("status") or "unknown") for item in results.values()]
    completed = statuses.count("completed")
    return {
        "status": "completed" if statuses and completed == len(statuses) else "failed",
        "completed": completed,
        "failed": len(statuses) - completed,
        "total": len(statuses),
    }


def build_agent_runtime_tools(context, user: User) -> list:
    allowed_skills = set(getattr(context, "_effective_skill_slugs", []) or [])
    parent_thread_id = str(getattr(context, "thread_id", "") or "")
    parent_run_id = str(getattr(context, "run_id", "") or "")
    subagent_depth = int(getattr(context, "subagent_depth", 0) or 0)
    configured_subagents = getattr(context, "subagents", None)

    async def metric_lookup(query: str, limit: int = 5) -> dict:
        """从已审核 Ossie 指标中按名称/别名/定义识别业务指标。"""
        from server.services.metric_registry import search_metrics
        return {"metrics": await search_metrics(query, limit=limit),
                "instruction": "仅 approved 指标可作为权威口径；无结果时继续检索知识库。"}

    async def read_file(file_path: str) -> str:
        raw = str(file_path or "").strip()
        pure = PurePosixPath(raw if raw.startswith("/") else f"/{raw}")
        for root, personal in ((VIRTUAL_SKILLS_PATH, False), (VIRTUAL_PERSONAL_SKILLS_PATH, True)):
            try:
                relative = pure.relative_to(PurePosixPath(root))
            except ValueError:
                continue
            if len(relative.parts) < 2 or relative.parts[0] not in allowed_skills:
                raise ValueError("Skill 不在当前 Agent 的授权范围内")
            slug, path = relative.parts[0], "/".join(relative.parts[1:])
            if personal:
                result = await read_personal_skill_file(str(user.uid), slug, path)
            else:
                async with session_context() as db:
                    result = await read_skill_file(db, slug=slug, relative_path=path, operator=user)
            return result["content"]
        raise ValueError("仅允许读取已授权 Skill 路径")

    def validate_cron(value: str) -> str:
        return validate_cron_expression(value)

    async def list_tasks() -> dict:
        async with session_context() as db:
            rows = (await db.execute(select(ScheduledTask).where(
                ScheduledTask.uid == str(user.uid)).order_by(ScheduledTask.updated_at.desc()))).scalars().all()
            return {"tasks": [item.to_dict() for item in rows]}

    async def create_task(name: str, cron: str, prompt: str,
                          agent_slug: str = "default-chatbot", project_id: str = "",
                          enabled: bool = True) -> dict:
        async with session_context() as db:
            if await db.scalar(select(Agent.id).where(Agent.slug == agent_slug)) is None:
                raise ValueError("指定的智能体不存在")
            if project_id and await db.scalar(select(Project.id).where(
                    Project.id == project_id, Project.uid == str(user.uid), Project.status == "active")) is None:
                raise ValueError("指定的项目不存在或无权访问")
            item = ScheduledTask(id=str(uuid.uuid4()), uid=str(user.uid), name=name.strip(),
                                 cron=validate_cron(cron), prompt=prompt.strip(), agent_slug=agent_slug,
                                 project_id=project_id or None, enabled=bool(enabled))
            db.add(item)
            await db.flush()
            return item.to_dict()

    async def update_task(task_id: str, name: str, cron: str, prompt: str,
                          agent_slug: str = "default-chatbot", project_id: str = "",
                          enabled: bool = True) -> dict:
        async with session_context() as db:
            item = await db.scalar(select(ScheduledTask).where(
                ScheduledTask.id == task_id, ScheduledTask.uid == str(user.uid)))
            if item is None:
                raise ValueError("定时任务不存在或无权访问")
            if await db.scalar(select(Agent.id).where(Agent.slug == agent_slug)) is None:
                raise ValueError("指定的智能体不存在")
            if project_id and await db.scalar(select(Project.id).where(
                    Project.id == project_id, Project.uid == str(user.uid), Project.status == "active")) is None:
                raise ValueError("指定的项目不存在或无权访问")
            item.name, item.cron, item.prompt = name.strip(), validate_cron(cron), prompt.strip()
            item.agent_slug, item.project_id, item.enabled = agent_slug, project_id or None, bool(enabled)
            await db.flush()
            return item.to_dict()

    async def delete_task(task_id: str) -> dict:
        async with session_context() as db:
            item = await db.scalar(select(ScheduledTask).where(
                ScheduledTask.id == task_id, ScheduledTask.uid == str(user.uid)))
            if item is None:
                raise ValueError("定时任务不存在或无权访问")
            await db.delete(item)
            return {"ok": True, "task_id": task_id}

    async def start_subagent(subagent_slug: str, task: str, description: str = "") -> dict:
        """创建并启动一个独立子线程，结果通过 subagent_await 获取。"""
        if subagent_depth >= 1:
            raise ValueError("子智能体不能继续派生子智能体")
        slug = str(subagent_slug or "").strip()
        prompt = str(task or "").strip()
        if not slug or not prompt:
            raise ValueError("subagent_slug 和 task 不能为空")
        if isinstance(configured_subagents, (list, tuple)) and configured_subagents and slug not in configured_subagents:
            raise ValueError("该子智能体未被当前 Agent 授权")
        async with session_context() as db:
            agent = await db.scalar(select(Agent).where(
                Agent.slug == slug, Agent.is_subagent == True))  # noqa: E712
            if agent is None:
                raise ValueError("指定的子智能体不存在或不是子智能体")
            child_thread_id = str(uuid.uuid4())
            child_thread = Thread(
                id=child_thread_id,
                uid=str(user.uid),
                agent_id=slug,
                title=(description or prompt[:80] or "子智能体任务")[:256],
                project_id=getattr(context, "project_id", None),
                extra_metadata={
                    "parent_thread_id": parent_thread_id,
                    "parent_run_id": parent_run_id,
                    "subagent_slug": slug,
                },
            )
            db.add(child_thread)
            await db.flush()
            from server.services.run_service import create_agent_run, dispatch_run
            child_run = await create_agent_run(
                query=prompt,
                agent_slug=slug,
                thread_id=child_thread_id,
                uid=str(user.uid),
                meta={"parent_thread_id": parent_thread_id, "parent_run_id": parent_run_id,
                      "subagent_depth": subagent_depth + 1},
                db=db,
            )
        await dispatch_run(child_run.id)
        return {
            "status": "started",
            "subagent_slug": slug,
            "subagent_thread_id": child_thread_id,
            "child_thread_id": child_thread_id,
            "run_id": child_run.id,
            "subagent_run_id": child_run.id,
            "description": description or prompt[:80],
        }

    async def orchestrate_subagents(tasks: list[dict]) -> dict:
        """按依赖关系分批并行执行子智能体，并汇总结果。"""
        if subagent_depth >= 1:
            raise ValueError("子智能体不能继续编排子智能体")
        if not isinstance(tasks, list) or not tasks or len(tasks) > 8:
            raise ValueError("tasks 必须是 1-8 个任务的列表")
        normalized = {}
        for index, item in enumerate(tasks):
            if not isinstance(item, dict):
                raise ValueError(f"第 {index + 1} 个任务格式非法")
            task_id = str(item.get("id") or f"task-{index + 1}").strip()
            if task_id in normalized:
                raise ValueError(f"任务 id 重复: {task_id}")
            dependencies = item.get("depends_on") or []
            if not isinstance(dependencies, list) or any(str(dep) not in {str(x.get("id") or f"task-{i + 1}") for i, x in enumerate(tasks)} for dep in dependencies):
                raise ValueError(f"任务 {task_id} 的依赖不存在")
            normalized[task_id] = {
                "id": task_id,
                "subagent_slug": str(item.get("subagent_slug") or "").strip(),
                "task": str(item.get("task") or "").strip(),
                "description": str(item.get("description") or ""),
                "depends_on": [str(dep) for dep in dependencies],
            }
        results: dict[str, dict] = {}
        remaining = set(normalized)
        while remaining:
            ready = [task_id for task_id in remaining
                     if all(dep in results for dep in normalized[task_id]["depends_on"])]
            if not ready:
                raise ValueError("子任务依赖存在循环")
            runnable = []
            for task_id in ready:
                item = normalized[task_id]
                failed_deps = [dep for dep in item["depends_on"]
                               if results[dep].get("status") not in {"completed", "success"}]
                if failed_deps:
                    results[task_id] = {"status": "skipped", "reason": "依赖任务失败",
                                        "depends_on": failed_deps}
                else:
                    dependency_context = ""
                    if item["depends_on"]:
                        dependency_context = "\n\n前置任务结果：\n" + "\n".join(
                            f"[{dep}] {str(results[dep].get('result') or results[dep].get('status', ''))[:4000]}"
                            for dep in item["depends_on"]
                        )
                    runnable.append((task_id, item, dependency_context))
            started = await asyncio.gather(*(
                start_subagent(item["subagent_slug"], item["task"] + dependency_context,
                               item["description"])
                for _, item, dependency_context in runnable
            ))
            completed = await asyncio.gather(*(
                await_subagent(started[index]["run_id"])
                for index in range(len(started))
            ))
            for index, (task_id, _, _) in enumerate(runnable):
                results[task_id] = {**started[index], **completed[index]}
            remaining -= set(ready)
        return {**summarize_subagent_results(results), "tasks": results,
                "summary": "；".join(f"{key}: {value.get('status')}" for key, value in results.items())}

    async def get_subagent_status(run_id: str) -> dict:
        async with session_context() as db:
            run = await db.scalar(select(AgentRun).where(
                AgentRun.id == str(run_id), AgentRun.uid == str(user.uid)))
            if run is None:
                raise ValueError("子智能体运行不存在或无权访问")
            return {
                "status": run.status,
                "run_id": run.id,
                "child_thread_id": run.thread_id,
                "subagent_run_id": run.id,
                "error": run.error_message or "",
            }

    async def get_subagent_events(run_id: str, after_seq: int = 0, limit: int = 100) -> dict:
        safe_limit = min(max(int(limit or 100), 1), 200)
        async with session_context() as db:
            run = await db.scalar(select(AgentRun).where(
                AgentRun.id == str(run_id), AgentRun.uid == str(user.uid)))
            if run is None:
                raise ValueError("子智能体运行不存在或无权访问")
            rows = (await db.execute(select(RunEvent).where(
                RunEvent.run_id == run.id, RunEvent.seq > int(after_seq or 0)
            ).order_by(RunEvent.seq.asc()).limit(safe_limit))).scalars().all()
            return {"run_id": run.id, "events": [item.to_envelope() for item in rows],
                    "next_seq": str(rows[-1].seq) if rows else str(after_seq or 0),
                    "has_more": len(rows) == safe_limit}

    async def cancel_subagent(run_id: str) -> dict:
        from server.services.run_service import request_cancel
        status = await request_cancel(str(run_id), str(user.uid))
        return {"status": status, "run_id": str(run_id)}

    async def await_subagent(run_id: str, timeout: int = 120) -> dict:
        deadline = asyncio.get_running_loop().time() + min(max(int(timeout or 120), 1), 300)
        while True:
            async with session_context() as db:
                run = await db.scalar(select(AgentRun).where(
                    AgentRun.id == str(run_id), AgentRun.uid == str(user.uid)))
                if run is None:
                    raise ValueError("子智能体运行不存在或无权访问")
                if run.status in {"completed", "failed", "cancelled", "interrupted"}:
                    rows = (await db.execute(select(RunEvent).where(
                        RunEvent.run_id == run.id, RunEvent.event_type == "messages"
                    ).order_by(RunEvent.seq.asc()))).scalars().all()
                    parts = []
                    for event in rows:
                        chunk = (event.payload or {}).get("chunk", {})
                        stream_event = chunk.get("stream_event", {}) if isinstance(chunk, dict) else {}
                        if stream_event.get("type") == "message_delta":
                            parts.append(str(stream_event.get("content") or ""))
                    last_seq = await db.scalar(select(RunEvent.seq).where(
                        RunEvent.run_id == run.id).order_by(RunEvent.seq.desc()).limit(1))
                    return {"status": run.status, "run_id": run.id,
                            "child_thread_id": run.thread_id, "result": "".join(parts),
                            "error": run.error_message or "",
                            "last_event_seq": str(last_seq) if last_seq is not None else "0"}
            if asyncio.get_running_loop().time() >= deadline:
                return {"status": "timeout", "run_id": str(run_id),
                        "message": "等待子智能体超时，可稍后使用 subagent_status 查询"}
            await asyncio.sleep(0.5)

    tools = [
        StructuredTool.from_function(coroutine=read_file, name="read_file",
                                     description="读取当前用户已授权 Skill 的文本文件。"),
        StructuredTool.from_function(coroutine=list_tasks, name="scheduled_task_list",
                                     description="列出当前用户的定时任务。"),
        StructuredTool.from_function(coroutine=create_task, name="scheduled_task_create",
                                     description="创建定时执行的 Agent 任务，仅在用户明确要求时调用。"),
        StructuredTool.from_function(coroutine=update_task, name="scheduled_task_update",
                                     description="修改当前用户的定时任务，仅在用户明确要求时调用。"),
        StructuredTool.from_function(coroutine=delete_task, name="scheduled_task_delete",
                                     description="删除当前用户的定时任务，仅在用户明确要求时调用。"),
        StructuredTool.from_function(coroutine=start_subagent, name="subagent_start",
                                     description="启动一个已配置的子智能体执行独立任务。"),
        StructuredTool.from_function(coroutine=get_subagent_status, name="subagent_status",
                                     description="查询子智能体运行状态。"),
        StructuredTool.from_function(coroutine=get_subagent_events, name="subagent_events",
                                     description="读取子智能体的结构化运行事件。"),
        StructuredTool.from_function(coroutine=cancel_subagent, name="subagent_cancel",
                                     description="取消一个正在运行的子智能体。"),
        StructuredTool.from_function(coroutine=await_subagent, name="subagent_await",
                                     description="等待子智能体完成并返回结果。"),
        StructuredTool.from_function(coroutine=orchestrate_subagents, name="subagent_orchestrate",
                                     description="按依赖关系并行编排多个子智能体并汇总结果。"),
    ]
    if getattr(context, "agent_backend_id", "") == "DataAgent":
        tools.append(StructuredTool.from_function(
            coroutine=metric_lookup, name="metric_lookup",
            description="识别用户问题中的业务指标，按官方名/别名查询已审核的 Ossie 指标口径。"))
    # 与注册表内置工具保持一致：工具异常转成 ToolMessage，交给 Agent 继续解释，
    # 不让一次 Skill/任务操作失败直接终止整条运行。
    for tool in tools:
        tool.handle_tool_error = True
    return tools
