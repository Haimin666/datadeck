"""宿主侧 Agent 工具：Skill 文件和当前用户的定时任务。"""
from __future__ import annotations

import uuid
import asyncio
import json
import os
import sys
from pathlib import Path, PurePosixPath

from fastapi import HTTPException
from langchain_core.tools import StructuredTool
from sqlalchemy import or_, select

from server.db import session_context
from server.deps import get_role_agent_slugs, require_agent_access
from server.models import Agent, AgentRun, CodeRepository, CodeRepositoryFile, KnowledgeBase, Project, RunEvent, ScheduledTask, Thread, User
from server.services.skills.service import read_personal_skill_file, read_skill_file
from server.services.skills.virtual_paths import VIRTUAL_PERSONAL_SKILLS_PATH, VIRTUAL_SKILLS_PATH
from server.utils.cron import validate_cron_expression
from server.workspace.paths import user_workdir_host_dir
from datadeck.agents.toolkits.packages import expand_tool_selection
from datadeck.agents.policy import DATA_AGENT_POLICY


TOOL_REQUIRED_MODULES = {
    "echo": "conversations",
    "add": "conversations",
    "execute": "workspace",
    "run_skill_script": "extensions",
    # 生成物通过当前对话展示/下载，不要求用户拥有个人空间模块权限。
    "present_artifacts": "conversations",
    "read_file": "extensions",
    "read_attachment": "conversations",
    "rag_search": "knowledge",
    "code_search": "knowledge",
    "metric_lookup": "metrics",
    "omd_list_services": "knowledge",
    "omd_list_databases": "knowledge",
    "omd_search_tables": "knowledge",
    "omd_list_tables": "knowledge",
    "omd_get_table_schema": "knowledge",
    "omd_get_table_lineage": "knowledge",
    "sql_validate": "knowledge",
    "sql_execute_query": "knowledge",
    "ask_user_question": "conversations",
    "workspace_list_directory": "workspace",
    "workspace_search_files": "workspace",
    "workspace_read_file": "workspace",
    "workspace_write_file": "workspace",
    "scheduled_task_list": "scheduled_tasks",
    "scheduled_task_create": "scheduled_tasks",
    "scheduled_task_update": "scheduled_tasks",
    "scheduled_task_delete": "scheduled_tasks",
    "subagent_start": "agents",
    "subagent_status": "agents",
    "subagent_events": "agents",
    "subagent_cancel": "agents",
    "subagent_await": "agents",
    "subagent_orchestrate": "agents",
}


def _selected_runtime_ids(context, kind: str) -> set[str] | None:
    """读取本次运行的资源选择；无快照时保留 standalone 上下文语义。"""
    snapshot = getattr(context, "_runtime_snapshot", None)
    selected = snapshot.selected(kind) if snapshot is not None else getattr(context, kind, None)
    if selected is None:
        return None
    return {str(item).strip() for item in selected if str(item).strip()}


def tool_allowed_by_runtime_permissions(context, tool_name: str) -> bool:
    """正式运行按请求级权限快照过滤工具；standalone 不伪造宿主权限。"""
    snapshot = getattr(context, "_runtime_snapshot", None)
    permissions = (
        getattr(snapshot, "permissions", None)
        if snapshot is not None
        else getattr(context, "runtime_permissions", None)
    )
    if permissions is None:
        return True
    required = TOOL_REQUIRED_MODULES.get(str(tool_name or "").strip())
    return required is None or required in set(permissions)


def _translate_skill_script_arg(value: str, cwd: str | os.PathLike[str]) -> str:
    """把 Skill 约定的 Workdir 虚拟输出路径映射为宿主路径。"""
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        and len(pure.parts) >= 2
        and pure.parts[1] == "outputs"
        and ".." not in pure.parts
        and "\\" not in value
    ):
        return os.fspath(Path(cwd) / Path(*pure.parts[1:]))
    return value


def build_knowledge_search_tool(context, user: User, base_tool):
    """创建宿主侧知识库工具；事实检索只读 PostgreSQL/Qdrant，不读进程文档缓存。"""
    allowed_collections = {
        str(item).strip()
        for item in (getattr(context, "knowledge_base_collections", []) or [])
        if str(item).strip()
    }
    runtime_snapshot = getattr(context, "_runtime_snapshot", None)
    # Agent 的显式挂载授权已经在宿主侧写入不可变快照。工具层必须使用这份
    # 白名单，不能再用当前用户 ACL 覆盖已授权的私有知识库。
    allowed_knowledge_base_ids = (
        {
            str(item.key)
            for item in runtime_snapshot.mounted_resources("knowledges")
            if str(item.key).strip()
        }
        if runtime_snapshot is not None else None
    )

    async def rag_search(
        query: str, top_k: int = 5, domain: str = "", collection_name: str = "",
    ) -> dict:
        if not tool_allowed_by_runtime_permissions(context, "rag_search"):
            return {"ok": False, "error": "当前角色没有知识库模块权限"}
        requested = [item.strip() for item in str(collection_name or "").split(",") if item.strip()]
        if requested and not set(requested).issubset(allowed_collections):
            return {"ok": False, "error": "指定知识库不在当前 Agent 授权范围内"}
        targets = requested or list(allowed_collections)
        if not targets:
            return {"ok": True, "results": [], "strategy": "empty",
                    "note": "当前 Agent 没有挂载可检索知识库"}

        from server.db import session_context
        from server.models import KnowledgeBase
        from server.services.knowledge_service import search as search_knowledge_base

        try:
            safe_top_k = min(max(int(top_k), 1), 20)
        except (TypeError, ValueError):
            safe_top_k = 5
        async with session_context() as db:
            db_query = select(KnowledgeBase).where(KnowledgeBase.collection_name.in_(targets))
            if allowed_knowledge_base_ids is not None:
                # targets 已通过 collection 白名单校验，ID 过滤用于防止历史
                # 重复 collection 或模型构造参数越过当前运行快照。
                db_query = db_query.where(KnowledgeBase.id.in_(allowed_knowledge_base_ids))
            else:
                # 没有宿主快照的 standalone 调用仍必须走用户 ACL。
                db_query = db_query.where(or_(
                    KnowledgeBase.uid == str(user.uid),
                    KnowledgeBase.access_scope.in_(("shared", "public")),
                ))
            rows = (await db.execute(db_query)).scalars().all()
            result_rows = []
            strategies = set()
            for knowledge_base in rows:
                result = await search_knowledge_base(
                    db, str(user.uid), knowledge_base.id, query, safe_top_k,
                    authorized_kb_ids=allowed_knowledge_base_ids,
                )
                strategies.add(result.get("strategy", "unknown"))
                for item in result.get("results", []):
                    result_rows.append({
                        **item,
                        "knowledge_base_id": knowledge_base.id,
                        "knowledge_base_name": knowledge_base.name,
                        "collection_name": knowledge_base.collection_name,
                        **({"domain": domain} if domain else {}),
                    })
        result_rows.sort(key=lambda item: float(item.get("score", 0) or 0), reverse=True)
        return {"ok": True, "results": result_rows[:safe_top_k],
                "strategy": "multi_knowledge_base" if len(targets) > 1 else next(iter(strategies), "empty")}

    return StructuredTool.from_function(
        coroutine=rag_search,
        name=base_tool.name,
        description=base_tool.description,
    )


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
    # 纯闲聊不需要宿主工具；即使模型误解提示词，也不会触发文件、
    # 定时任务或扩展调用。
    if getattr(context, "task_kind", "") == "chat":
        return []

    allowed_skills = set(getattr(context, "_effective_skill_slugs", []) or [])
    parent_thread_id = str(getattr(context, "thread_id", "") or "")
    parent_run_id = str(getattr(context, "run_id", "") or "")
    subagent_depth = int(getattr(context, "subagent_depth", 0) or 0)
    configured_subagents = getattr(context, "subagents", None)
    selected_scheduled_task_ids = _selected_runtime_ids(context, "scheduled_tasks")
    delegation_enabled = (
        bool(getattr(context, "delegation_enabled", False))
        and getattr(context, "agent_backend_id", "") != DATA_AGENT_POLICY.backend_id
    )
    delegation_allowed = (
        subagent_depth < 1
        and delegation_enabled
        and isinstance(configured_subagents, (list, tuple))
        and bool(configured_subagents)
    )

    async def execute(command: str, timeout: int = 60) -> dict:
        """在当前用户当前项目目录执行命令，支持常见 Shell 语法。"""
        if not tool_allowed_by_runtime_permissions(context, "execute"):
            raise ValueError("当前角色没有个人空间模块权限")
        raw_command = str(command or "").strip()
        if not raw_command:
            raise ValueError("command 不能为空")
        try:
            safe_timeout = min(max(int(timeout or 60), 1), 120)
        except (TypeError, ValueError):
            safe_timeout = 60
        workdir_path = str(getattr(context, "workdir_path", "") or "").strip()
        if not workdir_path:
            raise ValueError("当前会话没有授权项目工作目录")
        temporary_root = getattr(getattr(context, "workdir", None), "host_root", None)
        cwd = temporary_root or user_workdir_host_dir(str(user.uid), workdir_path)
        process = await asyncio.create_subprocess_shell(
            raw_command,
            cwd=os.fspath(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        try:
            output_bytes, _ = await asyncio.wait_for(process.communicate(), timeout=safe_timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            return {"ok": False, "exit_code": -1, "timed_out": True,
                    "output": f"命令超过 {safe_timeout} 秒，已终止"}
        output = output_bytes.decode("utf-8", errors="replace")
        return {"ok": process.returncode == 0, "exit_code": process.returncode,
                "timed_out": False, "output": output[-64 * 1024:]}

    async def run_skill_script(
        skill_slug: str,
        script_path: str,
        script_args: list[str] | str | None = None,
        timeout: int = 120,
    ) -> dict:
        """执行当前已激活 Skill 的 scripts/ 文件，不允许任意路径或 shell。"""
        if not tool_allowed_by_runtime_permissions(context, "run_skill_script"):
            raise ValueError("当前角色没有 Skill 模块权限")
        slug = str(skill_slug or "").strip()
        runtime_skills = getattr(context, "_runtime_skills", {}) or {}
        if slug not in set(allowed_skills) or slug not in runtime_skills:
            raise ValueError("该 Skill 未挂载到当前 Agent")
        raw_path = str(script_path or "").strip()
        relative = PurePosixPath(raw_path if raw_path.startswith("/") else f"/{raw_path}")
        if (
            not raw_path
            or ".." in relative.parts
            or "\\" in raw_path
            or not relative.is_absolute()
            or len(relative.parts) < 3
            or relative.parts[1] != "scripts"
        ):
            raise ValueError("script_path 必须是当前 Skill 的 /scripts/ 下相对路径")
        source_value = str((runtime_skills.get(slug) or {}).get("source_dir") or "").strip()
        source_dir = Path(source_value).expanduser().resolve()
        if not source_dir.is_absolute() or source_dir == Path("/") or not source_dir.is_dir():
            raise ValueError("Skill 来源目录不可用")
        candidate = source_dir / Path(*relative.parts[1:])
        if candidate.is_symlink():
            raise ValueError("Skill 脚本不允许使用符号链接")
        script = candidate.resolve()
        try:
            script.relative_to(source_dir)
        except ValueError as exc:
            raise ValueError("脚本路径超出 Skill 来源目录") from exc
        if not script.is_file():
            raise ValueError("Skill 脚本不存在或不是普通文件")
        suffix = script.suffix.lower()
        if suffix == ".py":
            command = [sys.executable, os.fspath(script)]
        elif suffix == ".sh":
            command = ["/bin/sh", os.fspath(script)]
        else:
            raise ValueError("仅支持执行 .py 和 .sh Skill 脚本")
        try:
            safe_timeout = min(max(int(timeout or 120), 1), 300)
        except (TypeError, ValueError):
            safe_timeout = 120
        workdir_path = str(getattr(context, "workdir_path", "") or "").strip()
        if not workdir_path:
            raise ValueError("当前会话没有授权项目工作目录")
        temporary_root = getattr(getattr(context, "workdir", None), "host_root", None)
        cwd = temporary_root or user_workdir_host_dir(str(user.uid), workdir_path)
        if isinstance(script_args, str):
            try:
                script_args = json.loads(script_args)
            except json.JSONDecodeError as exc:
                raise ValueError("script_args 字符串必须是 JSON 数组") from exc
        if script_args is not None and (
            not isinstance(script_args, list)
            or len(script_args) > 32
            or any(not isinstance(item, str) for item in script_args)
        ):
            raise ValueError("script_args 必须是最多 32 个字符串的列表")
        command.extend(
            _translate_skill_script_arg(str(item), cwd)
            for item in (script_args or [])
        )
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=os.fspath(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        try:
            output_bytes, _ = await asyncio.wait_for(process.communicate(), timeout=safe_timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            return {"ok": False, "exit_code": -1, "timed_out": True,
                    "output": f"Skill 脚本超过 {safe_timeout} 秒，已终止"}
        output = output_bytes.decode("utf-8", errors="replace")
        return {"ok": process.returncode == 0, "exit_code": process.returncode,
                "timed_out": False, "output": output[-64 * 1024:]}

    async def present_artifacts(filepaths: list[str]) -> dict:
        """把当前 Workdir 的 outputs 文件登记到本线程制品目录。"""
        if not tool_allowed_by_runtime_permissions(context, "present_artifacts"):
            raise ValueError("当前角色没有个人空间模块权限")
        if not isinstance(filepaths, list) or not filepaths or len(filepaths) > 20:
            raise ValueError("filepaths 必须是 1-20 个文件路径的列表")
        workdir = getattr(context, "workdir", None)
        thread_id = str(getattr(context, "thread_id", "") or "").strip()
        if workdir is None or not thread_id:
            raise ValueError("当前运行没有可用的 Workdir 或线程")

        from server.services.attachment_service import _safe_join

        presented: list[str] = []
        errors: list[dict[str, str]] = []
        for value in filepaths:
            raw = str(value or "").strip()
            pure = PurePosixPath(raw if raw.startswith("/") else f"/{raw}")
            path = pure.as_posix()
            if (
                not raw
                or ".." in pure.parts
                or "\\" in raw
                or pure == PurePosixPath("/")
                or len(pure.parts) < 2
                or pure.parts[1] != "outputs"
            ):
                errors.append({"path": raw, "error": "制品路径必须位于当前 Workdir 的 /outputs 目录"})
                continue
            try:
                data, truncated = await asyncio.to_thread(
                    workdir.read_file_prefix, path, 32 * 1024 * 1024,
                )
                if truncated:
                    raise ValueError("制品超过 32MB 限制")
                target = _safe_join(thread_id, path.lstrip("/"))
                os.makedirs(os.path.dirname(target), exist_ok=True)
                temporary = f"{target}.tmp-{uuid.uuid4().hex}"
                try:
                    with open(temporary, "wb") as handle:
                        handle.write(data)
                    os.replace(temporary, target)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
                presented.append(path)
            except (OSError, ValueError) as exc:
                errors.append({"path": path, "error": str(exc)[:300]})
        result = {"ok": bool(presented) and not errors, "filepaths": presented, "errors": errors}
        presented_paths = getattr(context, "_presented_artifact_paths", None)
        if presented_paths is not None:
            presented_paths.update(presented)
        run_id = str(getattr(context, "run_id", "") or "").strip()
        if presented and run_id:
            # 文件已复制到线程制品目录后再发送独立事件，简化版对话可以
            # 直接把它渲染为消息附件；完整页面继续使用原有文件栏。
            from server.event_translator import append_event

            await append_event(
                run_id,
                "artifact",
                {"run_id": getattr(context, "run_id", ""),
                 "request_id": getattr(context, "request_id", ""),
                 "message_id": f"{run_id}-ai", "artifacts": presented},
                thread_id,
            )
        return result

    async def metric_lookup(query: str, limit: int = 5) -> dict:
        """从已审核 Ossie 指标中按名称/别名/定义识别业务指标。"""
        if not tool_allowed_by_runtime_permissions(context, "metric_lookup"):
            raise ValueError("当前角色没有指标口径模块权限")
        from server.services.metric_registry import search_metrics
        return {"metrics": await search_metrics(query, limit=limit),
                "instruction": "仅 approved 指标可作为权威口径；无结果时继续检索知识库。"}

    async def code_search(query: str, repository_id: str = "", path: str = "", commit: str = "", limit: int = 8) -> dict:
        """按需检索授权 Git 快照，不把原始代码自动写入 RAG。"""
        if not tool_allowed_by_runtime_permissions(context, "code_search"):
            raise ValueError("当前角色没有知识库模块权限")
        needle = str(query or "").strip().lower()
        if not needle:
            return {"ok": False, "error": "query 不能为空"}
        try:
            safe_limit = min(max(int(limit or 8), 1), 20)
        except (TypeError, ValueError):
            return {"ok": False, "error": "limit 必须是整数"}
        suffixes = {".sql", ".hql", ".py", ".sh", ".yaml", ".yml", ".json", ".md", ".txt", ".java", ".scala", ".js", ".ts"}
        async with session_context() as db:
            stmt = select(CodeRepository).join(KnowledgeBase, CodeRepository.kb_id == KnowledgeBase.id)
            runtime_snapshot = getattr(context, "_runtime_snapshot", None)
            if runtime_snapshot is not None:
                allowed_kb_ids = {
                    str(item.key)
                    for item in runtime_snapshot.mounted_resources("knowledges")
                    if str(item.key).strip()
                }
                stmt = stmt.where(CodeRepository.kb_id.in_(allowed_kb_ids))
            else:
                # 没有宿主快照的 standalone 调用仍必须走用户 ACL。
                stmt = stmt.where(or_(
                    KnowledgeBase.uid == str(user.uid),
                    KnowledgeBase.access_scope.in_(("shared", "public")),
                ))
            if repository_id:
                stmt = stmt.where(CodeRepository.id == str(repository_id))
            repositories = (await db.execute(stmt)).scalars().all()
            repo_ids = [repo.id for repo in repositories]
            file_stmt = select(CodeRepositoryFile).where(CodeRepositoryFile.repository_id.in_(repo_ids)) if repo_ids else None
            if file_stmt is not None and commit:
                file_stmt = file_stmt.where(CodeRepositoryFile.commit_sha == str(commit))
            indexed_files = (await db.execute(file_stmt)).scalars().all() if file_stmt is not None else []
        indexed_by_repo: dict[str, list[CodeRepositoryFile]] = {}
        for item in indexed_files:
            indexed_by_repo.setdefault(item.repository_id, []).append(item)
        results = []
        for repo in repositories:
            base = (Path(repo.local_path) / (repo.subdir or "")).resolve()
            if not base.is_dir():
                continue
            indexed_candidates = indexed_by_repo.get(repo.id)
            if commit and not indexed_candidates:
                continue
            file_paths = [base / item.path for item in indexed_candidates] if indexed_candidates else base.rglob("*")
            for file_path in file_paths:
                if len(results) >= safe_limit:
                    break
                if file_path.is_symlink() or not file_path.is_file() or file_path.suffix.lower() not in suffixes:
                    continue
                relative = file_path.relative_to(base)
                if any(part in {".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build"} for part in relative.parts):
                    continue
                try:
                    if file_path.stat().st_size > 2 * 1024 * 1024:
                        continue
                except OSError:
                    continue
                if path and str(relative) != str(path).strip("/"):
                    continue
                try:
                    text = file_path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                lower = text.lower()
                indexed_match = next((item for item in (indexed_candidates or []) if item.path == relative.as_posix()), None)
                if indexed_match and needle not in " ".join(indexed_match.tables_json or []).lower() and needle not in " ".join(indexed_match.columns_json or []).lower() and needle not in lower and needle not in str(relative).lower():
                    continue
                index = lower.find(needle) if needle in lower else 0
                start = max(0, index - 400)
                results.append({"repository_id": repo.id, "repository_name": repo.name,
                                "path": str(relative), "line": text.count("\n", 0, start) + 1,
                                "commit": repo.last_commit or "", "snippet": text[start:start + 1200]})
        return {"ok": True, "query": query, "results": results}

    async def read_file(file_path: str) -> str:
        if not tool_allowed_by_runtime_permissions(context, "read_file"):
            return "当前角色没有 Skill 模块权限。"
        raw = str(file_path or "").strip()
        pure = PurePosixPath(raw if raw.startswith("/") else f"/{raw}")
        for root, personal in ((VIRTUAL_SKILLS_PATH, False), (VIRTUAL_PERSONAL_SKILLS_PATH, True)):
            try:
                relative = pure.relative_to(PurePosixPath(root))
            except ValueError:
                continue
            if not relative.parts or relative.parts[0] not in allowed_skills:
                # 工具调用参数来自模型，权限校验失败属于可恢复的工具错误。
                # 返回 ToolMessage 让 Agent 自己纠正路径或说明无法访问，
                # 不应因一次错误路径把整个 Run 置为 failed。
                return "无法读取该 Skill 文件：Skill 不在当前 Agent 的授权范围内。请先使用当前可见 Skill 的路径。"
            slug = relative.parts[0]
            # 模型有时先读取 Skill 目录。目录本身没有可读正文，按约定读取根级 SKILL.md。
            path = "/".join(relative.parts[1:]) or "SKILL.md"
            if personal:
                result = await read_personal_skill_file(str(user.uid), slug, path)
            else:
                async with session_context() as db:
                    result = await read_skill_file(db, slug=slug, relative_path=path, operator=user)
            return result["content"]
        return "无法读取该文件：仅允许读取当前 Agent 已授权 Skill 的路径。请使用系统提示中提供的 Skill 路径。"

    async def read_attachment(file_id: str = "", file_name: str = "") -> str:
        """读取本次运行已授权的对话附件，不允许模型自行拼接磁盘路径。"""
        if not tool_allowed_by_runtime_permissions(context, "read_attachment"):
            return "当前角色没有对话附件访问权限。"
        requested_id = str(file_id or "").strip()
        requested_name = str(file_name or "").strip()
        attachments = list(getattr(context, "attachments", []) or [])
        matches = [
            item for item in attachments
            if (requested_id and str(item.get("file_id") or item.get("id") or "") == requested_id)
            or (requested_name and str(item.get("file_name") or "") == requested_name)
        ]
        if not requested_id and not requested_name and len(attachments) == 1:
            matches = attachments
        if not matches:
            if len(attachments) > 1 and not requested_id and not requested_name:
                names = ", ".join(str(item.get("file_name") or item.get("id")) for item in attachments)
                return f"当前对话有多个附件，请使用 file_id 或 file_name 指定：{names}"
            return "未找到指定的对话附件；请先查看当前运行上下文中的附件列表。"
        if len(matches) > 1:
            names = ", ".join(str(item.get("file_name") or item.get("id")) for item in matches)
            return f"附件名称不唯一，请改用 file_id 指定：{names}"
        item = matches[0]
        if str(item.get("file_name") or "").lower().endswith((".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp")):
            return "当前基础 Agent 只支持读取文本附件，暂不解析 PDF 或图片。"
        from server.services.attachment_service import read_thread_attachment

        try:
            return await asyncio.to_thread(
                read_thread_attachment,
                str(context.thread_id),
                str(item.get("object_name") or ""),
            )
        except ValueError as exc:
            return f"读取附件失败：{exc}"

    async def ask_user_question(questions: list[dict]) -> dict:
        """暂停当前 Run，请用户补充无法安全猜测的关键信息。"""
        from langgraph.types import interrupt

        if not isinstance(questions, list) or not questions or len(questions) > 3:
            raise ValueError("questions 必须是 1-3 个问题的列表")
        normalized = []
        for item in questions:
            if not isinstance(item, dict) or not str(item.get("question") or "").strip():
                raise ValueError("每个问题都必须包含 question")
            normalized.append({
                "question": str(item["question"]).strip()[:500],
                "header": str(item.get("header") or "").strip()[:80],
                "options": [str(option).strip()[:200] for option in (item.get("options") or [])
                            if str(option).strip()][:5],
            })
        answer = interrupt({"kind": "ask_user_question", "questions": normalized})
        return {"ok": True, "answers": answer}

    def validate_cron(value: str) -> str:
        return validate_cron_expression(value)

    async def list_tasks() -> dict:
        if not tool_allowed_by_runtime_permissions(context, "scheduled_task_list"):
            raise ValueError("当前角色没有定时任务模块权限")
        async with session_context() as db:
            query = select(ScheduledTask).where(ScheduledTask.uid == str(user.uid))
            if selected_scheduled_task_ids is not None:
                query = query.where(
                    ScheduledTask.id.in_(selected_scheduled_task_ids)
                    if selected_scheduled_task_ids else False
                )
            rows = (await db.execute(
                query.order_by(ScheduledTask.updated_at.desc())
            )).scalars().all()
            return {"tasks": [item.to_dict() for item in rows]}

    async def create_task(name: str, cron: str, prompt: str,
                          agent_slug: str = "default-chatbot", project_id: str = "",
                          enabled: bool = True) -> dict:
        if not tool_allowed_by_runtime_permissions(context, "scheduled_task_create"):
            raise ValueError("当前角色没有定时任务模块权限")
        async with session_context() as db:
            if await db.scalar(select(Agent.id).where(Agent.slug == agent_slug)) is None:
                raise ValueError("指定的智能体不存在")
            try:
                await require_agent_access(db, user, agent_slug)
            except HTTPException as exc:
                raise ValueError(str(exc.detail)) from exc
            if project_id and await db.scalar(select(Project.id).where(
                    Project.id == project_id, Project.uid == str(user.uid), Project.status == "active")) is None:
                raise ValueError("指定的项目不存在或无权访问")
            item = ScheduledTask(id=str(uuid.uuid4()), uid=str(user.uid), name=name.strip(),
                                 cron=validate_cron(cron), prompt=prompt.strip(), agent_slug=agent_slug,
                                 project_id=project_id or None, enabled=bool(enabled))
            db.add(item)
            await db.flush()
            if selected_scheduled_task_ids is not None:
                selected_scheduled_task_ids.add(item.id)
            return item.to_dict()

    async def update_task(task_id: str, name: str, cron: str, prompt: str,
                          agent_slug: str = "default-chatbot", project_id: str = "",
                          enabled: bool = True) -> dict:
        if not tool_allowed_by_runtime_permissions(context, "scheduled_task_update"):
            raise ValueError("当前角色没有定时任务模块权限")
        if selected_scheduled_task_ids is not None and str(task_id) not in selected_scheduled_task_ids:
            raise ValueError("该定时任务未挂载到当前运行")
        async with session_context() as db:
            item = await db.scalar(select(ScheduledTask).where(
                ScheduledTask.id == task_id, ScheduledTask.uid == str(user.uid)))
            if item is None:
                raise ValueError("定时任务不存在或无权访问")
            if await db.scalar(select(Agent.id).where(Agent.slug == agent_slug)) is None:
                raise ValueError("指定的智能体不存在")
            try:
                await require_agent_access(db, user, agent_slug)
            except HTTPException as exc:
                raise ValueError(str(exc.detail)) from exc
            if project_id and await db.scalar(select(Project.id).where(
                    Project.id == project_id, Project.uid == str(user.uid), Project.status == "active")) is None:
                raise ValueError("指定的项目不存在或无权访问")
            item.name, item.cron, item.prompt = name.strip(), validate_cron(cron), prompt.strip()
            item.agent_slug, item.project_id, item.enabled = agent_slug, project_id or None, bool(enabled)
            await db.flush()
            return item.to_dict()

    async def delete_task(task_id: str) -> dict:
        if not tool_allowed_by_runtime_permissions(context, "scheduled_task_delete"):
            raise ValueError("当前角色没有定时任务模块权限")
        if selected_scheduled_task_ids is not None and str(task_id) not in selected_scheduled_task_ids:
            raise ValueError("该定时任务未挂载到当前运行")
        async with session_context() as db:
            item = await db.scalar(select(ScheduledTask).where(
                ScheduledTask.id == task_id, ScheduledTask.uid == str(user.uid)))
            if item is None:
                raise ValueError("定时任务不存在或无权访问")
            await db.delete(item)
            return {"ok": True, "task_id": task_id}

    async def start_subagent(subagent_slug: str, task: str, description: str = "") -> dict:
        """创建并启动一个独立子线程，结果通过 subagent_await 获取。"""
        if not tool_allowed_by_runtime_permissions(context, "subagent_start"):
            raise ValueError("当前角色没有智能体模块权限")
        if not delegation_allowed:
            raise ValueError("当前 Agent 未启用任务委派或未配置子智能体")
        slug = str(subagent_slug or "").strip()
        prompt = str(task or "").strip()
        if not slug or not prompt:
            raise ValueError("subagent_slug 和 task 不能为空")
        if slug not in configured_subagents:
            raise ValueError("该子智能体未被当前 Agent 授权")
        async with session_context() as db:
            role_agents = await get_role_agent_slugs(db, user.role)
            if (
                role_agents is not None
                and slug not in role_agents
                and slug not in configured_subagents
            ):
                raise ValueError("当前角色未分配该子智能体")
            agent = await db.scalar(select(Agent).where(
                Agent.slug == slug,
                Agent.execution_role == "subagent",
            ))
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
        if not tool_allowed_by_runtime_permissions(context, "subagent_orchestrate"):
            raise ValueError("当前角色没有智能体模块权限")
        if not delegation_allowed:
            raise ValueError("当前 Agent 未启用任务委派或未配置子智能体")
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
        if not tool_allowed_by_runtime_permissions(context, "subagent_status"):
            raise ValueError("当前角色没有智能体模块权限")
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
        if not tool_allowed_by_runtime_permissions(context, "subagent_events"):
            raise ValueError("当前角色没有智能体模块权限")
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
        if not tool_allowed_by_runtime_permissions(context, "subagent_cancel"):
            raise ValueError("当前角色没有智能体模块权限")
        from server.services.run_service import request_cancel
        status = await request_cancel(str(run_id), str(user.uid))
        return {"status": status, "run_id": str(run_id)}

    async def await_subagent(run_id: str, timeout: int = 120) -> dict:
        if not tool_allowed_by_runtime_permissions(context, "subagent_await"):
            raise ValueError("当前角色没有智能体模块权限")
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

    configured_tools = getattr(context, "tools", None)
    configured_tool_names = (
        set(expand_tool_selection(configured_tools))
        if configured_tools is not None else set()
    )

    tools = [
        StructuredTool.from_function(coroutine=execute, name="execute",
                                     description="在当前项目工作目录执行命令（例如 python script.py）；支持管道、重定向、&& 和 here-doc，默认需要人工审批。"),
        StructuredTool.from_function(coroutine=run_skill_script, name="run_skill_script",
                                     description="执行当前已挂载 Skill 的 scripts/ 下 Python 或 Shell 脚本；不允许任意路径，默认需要人工审批。"),
        StructuredTool.from_function(coroutine=present_artifacts, name="present_artifacts",
                                     description="登记当前 Workdir /outputs 下的文件，供对话中预览和下载；只能提交已存在的文件。"),
        StructuredTool.from_function(coroutine=read_file, name="read_file",
                                     description="读取当前 Agent 已授权 Skill 的文本文件。file_path 可填写 Skill 目录或目录下的文件；传入目录时自动读取根级 SKILL.md。遇到工具错误时先根据可见 Skill 列表修正路径，最多重试一次。"),
        StructuredTool.from_function(coroutine=read_attachment, name="read_attachment",
                                     description="读取当前对话已上传的文本附件。可传 file_id 或 file_name；不支持 PDF 和图片。"),
        StructuredTool.from_function(coroutine=ask_user_question, name="ask_user_question",
                                     description="遇到多个 OMD 表命中、缺少必要范围或其他无法安全判断的歧义时，向用户提出 1-3 个选择问题并暂停当前运行。"),
        StructuredTool.from_function(coroutine=list_tasks, name="scheduled_task_list",
                                     description="列出当前用户的定时任务。"),
        StructuredTool.from_function(coroutine=create_task, name="scheduled_task_create",
                                     description="创建定时执行的 Agent 任务，仅在用户明确要求时调用。"),
        StructuredTool.from_function(coroutine=update_task, name="scheduled_task_update",
                                     description="修改当前用户的定时任务，仅在用户明确要求时调用。"),
        StructuredTool.from_function(coroutine=delete_task, name="scheduled_task_delete",
                                     description="删除当前用户的定时任务，仅在用户明确要求时调用。"),
        StructuredTool.from_function(coroutine=code_search, name="code_search",
                                     description="按需检索已授权 Git 数仓代码和注释，返回文件路径、行号、commit 与代码片段；不会自动把原始代码写入知识库。"),
    ]
    if not getattr(context, "workdir_path", None):
        tools = [tool for tool in tools if tool.name not in {"execute", "run_skill_script"}]
    if delegation_allowed:
        tools.extend([
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
        ])
    if (
        getattr(context, "agent_backend_id", "") == "DataAgent"
        or "metric_lookup" in configured_tool_names
    ):
        tools.append(StructuredTool.from_function(
            coroutine=metric_lookup, name="metric_lookup",
            description="识别用户问题中的业务指标，按官方名/别名查询已审核的 Ossie 指标口径。"))
    # 与内置工具相同，显式配置工具列表时平台工具也必须受白名单控制。
    # 未配置时不默认暴露执行命令和定时任务；Skill 仍需要 read_file 作为按需激活入口。
    if configured_tools is None:
        allowed = set()
    else:
        allowed = set(expand_tool_selection(configured_tools))
    if getattr(context, "_effective_skill_slugs", None):
        allowed.add("read_file")
        runtime_skills = getattr(context, "_runtime_skills", {}) or {}
        for slug in getattr(context, "_effective_skill_slugs", []) or []:
            skill = runtime_skills.get(slug) or {}
            allowed.update(skill.get("tools", []) or [])
    if getattr(context, "attachments", None):
        # 附件是本次用户请求的显式输入，读取能力不依赖用户额外打开整套平台包。
        allowed.add("read_attachment")
    if delegation_allowed:
        # 委派开关和子 Agent 白名单共同构成该能力的显式授权。
        allowed.update({
            "subagent_start", "subagent_status", "subagent_events", "subagent_cancel",
            "subagent_await", "subagent_orchestrate",
        })
    is_data_agent = getattr(context, "agent_backend_id", "") == "DataAgent"
    code_search_enabled = (
        getattr(context, "task_kind", "") == "code"
        if is_data_agent
        else "code_search" in configured_tool_names
    )
    if is_data_agent or "metric_lookup" in configured_tool_names:
        # DataAgent 固定能力或显式选择 package:knowledge 时启用指标口径查询。
        allowed.add("metric_lookup")
        if code_search_enabled:
            allowed.add("code_search")
    elif "code_search" in configured_tool_names:
        allowed.add("code_search")
    tools = [
        tool for tool in tools
        if tool.name in allowed and tool_allowed_by_runtime_permissions(context, tool.name)
    ]
    # 与注册表内置工具保持一致：工具异常转成 ToolMessage，交给 Agent 继续解释，
    # 不让一次 Skill/任务操作失败直接终止整条运行。
    for tool in tools:
        tool.handle_tool_error = True
    return tools
