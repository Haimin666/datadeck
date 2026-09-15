"""在实际应用运行环境中验证 Agent 能力包，不调用模型、不保留测试数据。"""

# 项目根路径必须在宿主模块导入前加入 sys.path；这些导入因此有意不在
# 文件首部，保留其余 Ruff 检查。
# ruff: noqa: E402

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

# Docker Compose 已通过 env_file 注入 .env.docker；直接在项目目录执行时，
# 仅使用开发环境 .env 作为本地探针兜底，避免把部署文件再次加载并覆盖配置。
load_dotenv(dotenv_path=".env", override=False)

from sqlalchemy import select

from datadeck.agents.buildin.chatbot.context import ChatBotContext
from datadeck.agents.toolkits import buildin as _buildin_tools  # noqa: F401
from datadeck.agents.toolkits.packages import expand_tool_selection
from datadeck.agents.toolkits.service import get_tool_instances_for_context
from server.db import close_db, session_context
from server.models import Agent, KnowledgeBase, MCPServer, MetricRegistry, User
from server.services.agent_runtime_tools import build_agent_runtime_tools, build_knowledge_search_tool
from server.services.attachment_service import remove_thread_storage
from server.services.mcp.service import get_mcp_tools
from server.services.skills.service import list_accessible_skills
from server.services.skills.runtime import build_runtime_skills
from server.services.skills.virtual_paths import VIRTUAL_PERSONAL_SKILLS_PATH, VIRTUAL_SKILLS_PATH
from server.workspace.temp_workdir import TemporaryWorkdir


def _safe_error(error: BaseException) -> str:
    message = str(error).replace("\n", " ").strip()
    message = re.sub(
        r"(?i)(authorization|token|api[_-]?key|password|secret)\s*[:=]\s*[^,;]+",
        r"\1=<redacted>", message,
    )
    return f"{type(error).__name__}: {message[:300]}"


async def _invoke(tool_map: dict, name: str, args: dict) -> tuple[bool, object]:
    tool = tool_map.get(name)
    if tool is None:
        return False, "工具未装配"
    try:
        return True, await tool.ainvoke(args)
    except Exception as exc:  # noqa: BLE001
        return False, _safe_error(exc)


def _summary(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _summary(item)
            for key, item in value.items()
            if key not in {"rows", "tables", "columns", "events", "results", "content"}
        }
    if isinstance(value, list):
        return [_summary(item) for item in value[:3]]
    if isinstance(value, str):
        return value[:180]
    return value


async def _run() -> int:
    probe_uid = os.getenv("PROBE_UID", "admin").strip() or "admin"
    table_name = os.getenv("PROBE_TABLE_NAME", "pangu.sys_flow_s_cap_h").strip()
    failures: dict[str, object] = {}
    checks: dict[str, object] = {}

    async with session_context() as db:
        user = await db.scalar(select(User).where(User.uid == probe_uid))
        if user is None:
            print(f"[ERROR] 用户不存在: {probe_uid}")
            return 1
        agents = (await db.execute(select(Agent).order_by(Agent.slug))).scalars().all()
        skills = await list_accessible_skills(db, user)
        knowledge_bases = (await db.execute(select(KnowledgeBase).order_by(KnowledgeBase.name))).scalars().all()
        mcps = (await db.execute(select(MCPServer).where(MCPServer.enabled == 1).order_by(MCPServer.slug))).scalars().all()
        metric = await db.scalar(
            select(MetricRegistry).where(MetricRegistry.status == "approved")
            .order_by(MetricRegistry.id).limit(1)
        )

    root = Path(tempfile.mkdtemp(prefix="datadeck-agent-probe-"))
    context = ChatBotContext(
        uid=user.uid,
        thread_id=f"probe-{uuid.uuid4().hex}",
        run_id=f"probe-run-{uuid.uuid4().hex}",
        agent_backend_id="ChatbotAgent",
        tools=["package:core", "package:platform", "package:sql", "package:omd", "package:knowledge"],
        skills=[item.slug for item in skills],
        knowledge_base_collections=[item.collection_name for item in knowledge_bases],
        workdir_path="/",
    )
    context.workdir = TemporaryWorkdir(relative_path="tmp/probe", host_root=root)
    context._effective_skill_slugs = [item.slug for item in skills]
    context._runtime_skills = build_runtime_skills(skills)

    try:
        core_tools = {tool.name: tool for tool in get_tool_instances_for_context(context)}
        platform_tools = {tool.name: tool for tool in build_agent_runtime_tools(context, user)}
        tool_map = {**core_tools, **platform_tools}
        expected = set(expand_tool_selection(context.tools))
        missing = sorted(expected - set(tool_map))
        if missing:
            failures["tool_package_mount"] = {"missing": missing}
        else:
            checks["tool_package_mount"] = {"count": len(tool_map)}

        for name, args in (
            ("echo", {"text": "probe"}),
            ("add", {"a": 2, "b": 3}),
            ("sql_validate", {"sql": "SELECT 1"}),
            ("sql_execute_query", {"sql": "SELECT 1"}),
            ("omd_list_databases", {}),
        ):
            ok, value = await _invoke(tool_map, name, args)
            (checks if ok else failures)[name] = _summary(value)

        ok, search = await _invoke(tool_map, "omd_search_tables", {"table_name": table_name, "limit": 20})
        (checks if ok else failures)["omd_search_tables"] = _summary(search)
        candidates = search.get("candidates", []) if ok and isinstance(search, dict) else []
        candidate = next((item for item in candidates if item.get("database") == "default" and item.get("schema") == "lion_dw_ods"), candidates[0] if candidates else None)
        if candidate:
            table_args = {
                "service_name": candidate.get("service", ""),
                "database_name": candidate.get("database", ""),
                "schema_name": candidate.get("schema", ""),
                "table": candidate.get("name", ""),
            }
            for name, args in (
                ("omd_get_table_schema", table_args),
                ("omd_get_table_lineage", {**table_args, "direction": "both", "depth": 1}),
                ("omd_list_tables", {key: table_args[key] for key in ("service_name", "database_name", "schema_name")}),
            ):
                ok, value = await _invoke(tool_map, name, args)
                (checks if ok else failures)[name] = _summary(value)
        else:
            failures["omd_table_followup"] = "没有命中可用于结构/血缘验证的表"

        rag_base = core_tools.get("rag_search")
        if rag_base is not None:
            rag_tool = build_knowledge_search_tool(context, user, rag_base)
            ok, value = await _invoke({"rag_search": rag_tool}, "rag_search", {"query": "逾期率 口径", "top_k": 5})
            (checks if ok else failures)["rag_search"] = _summary(value)
        if metric is not None:
            review_status = (metric.ai_context or {}).get("review_status")
            if review_status != "approved":
                failures["metric_registry_consistency"] = {
                    "metric_id": metric.id,
                    "status": metric.status,
                    "review_status": review_status,
                }
            ok, value = await _invoke(tool_map, "metric_lookup", {"query": metric.canonical_name, "limit": 1})
            (checks if ok else failures)["metric_lookup"] = _summary(value)
        else:
            ok, value = await _invoke(tool_map, "metric_lookup", {"query": "不存在的探针指标", "limit": 1})
            (checks if ok else failures)["metric_lookup"] = _summary(value)

        for name, args in (
            ("workspace_list_directory", {"path": "/"}),
            ("workspace_write_file", {"path": "/probe.txt", "content": "DataDeck UTF-8 probe"}),
            ("workspace_read_file", {"path": "/probe.txt"}),
            ("workspace_search_files", {"query": "probe"}),
            ("execute", {"command": "python3 -c 'print(\"execute-ok\")'"}),
            ("read_attachment", {}),
        ):
            ok, value = await _invoke(tool_map, name, args)
            (checks if ok else failures)[name] = _summary(value)

        output_file = root / "outputs" / "probe.json"
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text('{"probe": true}', encoding="utf-8")
        ok, value = await _invoke(tool_map, "present_artifacts", {"filepaths": ["/outputs/probe.json"]})
        (checks if ok else failures)["present_artifacts"] = _summary(value)

        # 对已声明脚本执行依赖的 DBA Skill 跑一次真实查库与 SQL/JSON 产物生成。
        # 其它脚本的业务参数不由探针猜测，只验证依赖工具已装配。
        dba_skill = next((item for item in skills if item.slug == "dba"), None)
        if dba_skill and "run_skill_script" in (dba_skill.tool_dependencies or []):
            probe_table = table_name.rsplit(".", 1)[-1]
            ok, lookup = await _invoke(tool_map, "run_skill_script", {
                "skill_slug": "dba", "script_path": "scripts/dba.py",
                "script_args": ["lookup", table_name.split(".", 1)[0]],
            })
            (checks if ok else failures)["run_skill_script:dba.lookup"] = _summary(lookup)
            dbid_match = re.search(r"dbid=(\d+)", str(lookup.get("output", ""))) if ok and isinstance(lookup, dict) else None
            if not dbid_match:
                failures["run_skill_script:dba.db2hive"] = "lookup 未返回 dbid"
            else:
                ok, generated = await _invoke(tool_map, "run_skill_script", {
                    "skill_slug": "dba", "script_path": "scripts/dba.py",
                    "script_args": ["db2hive", dbid_match.group(1), probe_table, "/outputs"],
                })
                (checks if ok else failures)["run_skill_script:dba.db2hive"] = _summary(generated)
                sql_path = root / "outputs" / f"{probe_table}.sql"
                json_path = root / "outputs" / f"{probe_table}.json"
                if not ok or not sql_path.is_file() or not json_path.is_file():
                    failures["run_skill_script:dba.artifacts"] = "未生成 SQL 和 JSON 文件"
                else:
                    ok, value = await _invoke(tool_map, "present_artifacts", {
                        "filepaths": [f"/outputs/{probe_table}.sql", f"/outputs/{probe_table}.json"],
                    })
                    (checks if ok else failures)["run_skill_script:dba.present_artifacts"] = _summary(value)

        mysql_skill = next((item for item in skills if item.slug == "mysql-reporter"), None)
        if mysql_skill and "run_skill_script" in (mysql_skill.tool_dependencies or []):
            ok, value = await _invoke(tool_map, "run_skill_script", {
                "skill_slug": "mysql-reporter", "script_path": "scripts/list_tables.py",
                "script_args": [],
            })
            output = str(value.get("output", "")) if ok and isinstance(value, dict) else ""
            if ok and isinstance(value, dict) and value.get("ok"):
                checks["run_skill_script:mysql-reporter.list_tables"] = _summary(value)
            elif "missing required key" in output:
                checks["run_skill_script:mysql-reporter.list_tables"] = {
                    "ok": False, "expected": "MYSQL_* 未配置时给出明确提示",
                }
            else:
                failures["run_skill_script:mysql-reporter.list_tables"] = _summary(value)

        for skill in skills:
            skill_root = (
                VIRTUAL_PERSONAL_SKILLS_PATH
                if getattr(skill, "source_scope", None) == "personal"
                else VIRTUAL_SKILLS_PATH
            )
            skill_path = f"{skill_root}/{skill.slug}/SKILL.md"
            ok, value = await _invoke(tool_map, "read_file", {"file_path": skill_path})
            (checks if ok else failures)[f"read_file:{skill.slug}"] = _summary(value)
            missing_dependencies = sorted(set(skill.tool_dependencies or []) - set(tool_map))
            if missing_dependencies:
                failures[f"skill_dependencies:{skill.slug}"] = {"missing": missing_dependencies}
        if not skills:
            failures["read_file"] = "没有启用 Skill 可用于读取验证"

        ok, value = await _invoke(tool_map, "scheduled_task_list", {})
        (checks if ok else failures)["scheduled_task_list"] = _summary(value)

        created, created_value = await _invoke(tool_map, "scheduled_task_create", {
            "name": "agent-probe-temporary",
            "cron": "0 0 1 1 *",
            "prompt": "probe",
            "agent_slug": "default-chatbot",
            "enabled": False,
        })
        if not created or not isinstance(created_value, dict) or not created_value.get("id"):
            failures["scheduled_task_create"] = _summary(created_value)
        else:
            checks["scheduled_task_create"] = _summary(created_value)
            task_id = created_value["id"]
            try:
                ok, value = await _invoke(tool_map, "scheduled_task_update", {
                    "task_id": task_id,
                    "name": "agent-probe-temporary-updated",
                    "cron": "5 0 1 1 *",
                    "prompt": "probe-updated",
                    "agent_slug": "default-chatbot",
                    "enabled": False,
                })
                (checks if ok else failures)["scheduled_task_update"] = _summary(value)
                ok, value = await _invoke(tool_map, "scheduled_task_delete", {"task_id": task_id})
                (checks if ok else failures)["scheduled_task_delete"] = _summary(value)
            finally:
                # 删除失败时再调用一次，确保探针不会留下启用任务。
                await _invoke(tool_map, "scheduled_task_delete", {"task_id": task_id})

        with patch("langgraph.types.interrupt", return_value={"answer": "继续"}):
            ok, value = await _invoke(tool_map, "ask_user_question", {
                "questions": [{"question": "请选择测试答案", "options": ["继续", "停止"]}],
            })
            (checks if ok else failures)["ask_user_question"] = _summary(value)

        for server in mcps:
            loaded = await get_mcp_tools(server.slug, force_refresh=True)
            if loaded:
                checks[f"mcp:{server.slug}"] = {"tool_count": len(loaded)}
            else:
                failures[f"mcp:{server.slug}"] = "未发现工具；请检查容器网络、URL、认证和 MCP 服务状态"

        for agent in agents:
            assembly_root = root / "assembly" / agent.slug
            assembly_root.mkdir(parents=True, exist_ok=True)
            probe_context = ChatBotContext(
                uid=user.uid,
                thread_id=f"assembly-probe-{agent.slug}",
                agent_backend_id=agent.backend_id,
                tools=None,
                skills=None,
                knowledges=None,
                workdir_path=f"tmp/probe-assembly/{agent.slug}",
            )
            probe_context.workdir = TemporaryWorkdir(
                relative_path=probe_context.workdir_path,
                host_root=assembly_root,
            )
            configured_context = (agent.config_json or {})
            configured_context = (
                configured_context.get("context", configured_context)
                if isinstance(configured_context, dict) else {}
            )
            if isinstance(configured_context, dict):
                probe_context.update(configured_context)
            try:
                from server.services.agent_runtime_assembler import AgentRuntimeAssembler

                async with session_context() as assembly_db:
                    snapshot = await AgentRuntimeAssembler().prepare_context(
                        probe_context, db=assembly_db, user=user, agent_slug=agent.slug,
                    )
                checks[f"assembly:{agent.slug}"] = {
                    "resources": len(snapshot.resources),
                    "runtime_tools": len(probe_context.runtime_tools),
                }
            except Exception as exc:  # noqa: BLE001
                failures[f"assembly:{agent.slug}"] = _safe_error(exc)

        print(json.dumps({
            "user": user.uid,
            "agents": [item.slug for item in agents],
            "skills": [item.slug for item in skills],
            "knowledge_bases": len(knowledge_bases),
            "enabled_mcps": [item.slug for item in mcps],
            "checks": checks,
            "failures": failures,
        }, ensure_ascii=False, indent=2))
        return 1 if failures else 0
    finally:
        shutil.rmtree(root, ignore_errors=True)
        remove_thread_storage(context.thread_id)
        await close_db()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
