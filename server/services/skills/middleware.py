"""Skills 中间件 - 处理 skills 提示词注入、依赖展开、动态激活"""

from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import PurePosixPath
from typing import Annotated, Any, NotRequired

from deepagents.middleware._utils import append_to_system_message
from deepagents.middleware.skills import SKILLS_SYSTEM_PROMPT
from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import ToolMessage
from langchain.tools.tool_node import ToolCallRequest
from langgraph.types import Command

from server.services.skills.virtual_paths import VIRTUAL_PERSONAL_SKILLS_PATH, VIRTUAL_SKILLS_PATH
from server.services.agent_runtime_assembler import record_runtime_diagnostic
from server.services.skills.runtime import RuntimeSkill, build_dependency_bundle
from server.services.skills.service import is_valid_skill_slug, normalize_string_list
from datadeck.agents.toolkits.packages import expand_tool_selection
from datadeck import logger


def _activated_skills_reducer(left: list[str] | None, right: list[str] | None) -> list[str]:
    """合并 activated_skills 列表"""
    return normalize_string_list([*(left or []), *(right or [])])


class SkillsState(AgentState):
    """Skills 状态定义"""

    activated_skills: NotRequired[Annotated[list[str], _activated_skills_reducer]]


class SkillsMiddleware(AgentMiddleware):
    """Skills 中间件 - 处理提示词注入、预加载、依赖展开和动态激活

    职责：
    - Skills 摘要提示与预加载完整说明注入
    - 依赖展开（预加载配置 + 动态激活）
    - 本地/MCP 依赖工具的模型可见性门控
    """

    state_schema = SkillsState

    def __init__(
        self,
        *,
        enable_skills_prompt: bool = True,
        skills_sources_for_prompt: list[str] | None = None,
    ):
        """初始化中间件

        Args:
            enable_skills_prompt: 是否启用 skills 提示段注入（默认 True）
            skills_sources_for_prompt: skills 来源路径（默认展示共享投影与个人 Workspace）
        """
        super().__init__()
        self.enable_skills_prompt = enable_skills_prompt
        self.skills_sources_for_prompt = skills_sources_for_prompt or [
            f"{VIRTUAL_SKILLS_PATH}/",
            f"{VIRTUAL_PERSONAL_SKILLS_PATH}/",
        ]

    @staticmethod
    def _tool_failure_key(request: ToolCallRequest) -> str:
        """按工具名和参数识别重复失败，不把不同参数误判为死循环。"""
        tool_call = request.tool_call or {}
        return json.dumps(
            [tool_call.get("name", ""), tool_call.get("args", {})],
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )

    def _record_tool_failure(self, request: ToolCallRequest, message: str) -> None:
        """同一 Run 同一调用连续失败两次后终止，避免模型无限重试。"""
        context = request.runtime.context
        failures = getattr(context, "_tool_failure_counts", None)
        if not isinstance(failures, dict):
            failures = {}
            setattr(context, "_tool_failure_counts", failures)
        key = self._tool_failure_key(request)
        count = int(failures.get(key, 0)) + 1
        failures[key] = count
        # 请求级状态有界，防止模型生成大量不同错误参数导致上下文膨胀。
        if len(failures) > 64:
            failures.pop(next(iter(failures)))
        if count >= 2:
            name = request.tool_call.get("name", "未知工具")
            raise RuntimeError(
                f"工具 {name} 对相同参数已连续失败 {count} 次，已终止本次运行。"
                f"最后错误：{message[:400]}"
            )

    def _clear_tool_failure(self, request: ToolCallRequest) -> None:
        failures = getattr(request.runtime.context, "_tool_failure_counts", None)
        if isinstance(failures, dict):
            failures.pop(self._tool_failure_key(request), None)

    @staticmethod
    def _result_failure_message(result: Any) -> str:
        """识别工具节点已转换成结果的错误，覆盖 handle_tool_error 场景。"""
        if not isinstance(result, ToolMessage):
            return ""
        content = result.content
        text = content if isinstance(content, str) else str(content)
        status = getattr(result, "status", None)
        if status == "error" or text.startswith(("工具执行失败", "无法读取该 Skill", "无法读取该文件")):
            return text
        return ""

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        """包装模型调用，处理 skills 提示词注入、动态激活和依赖展开"""
        runtime_context = request.runtime.context

        if self.enable_skills_prompt:
            effective_skills = getattr(runtime_context, "_effective_skill_slugs", None)
            if isinstance(effective_skills, list):
                effective_skills = normalize_string_list(effective_skills)
                preloaded_skills = self._get_preloaded_skills(runtime_context)
                preloaded_set = set(preloaded_skills)
                prompt_sections: list[str] = []
                lazy_skills = [slug for slug in effective_skills if slug not in preloaded_set]
                if lazy_skills:
                    skills_meta = self._collect_prompt_metadata(lazy_skills, runtime_context)
                    if skills_meta:
                        prompt_sections.append(self._build_skills_section(skills_meta))
                if preloaded_skills:
                    prompt_sections.append(self._build_preloaded_skills_section(preloaded_skills, runtime_context))
                if prompt_sections:
                    system_message = append_to_system_message(
                        getattr(request, "system_message", None), "\n\n".join(prompt_sections)
                    )
                    request = request.override(system_message=system_message)

        state = request.state if isinstance(request.state, dict) else {}
        activated = state.get("activated_skills", []) or []
        if not isinstance(activated, list):
            activated = []

        effective_skills = self._get_effective_skills(runtime_context)
        activated = [slug for slug in normalize_string_list(activated) if slug in effective_skills]
        activated = _activated_skills_reducer(self._get_preloaded_skills(runtime_context), activated)

        deps_bundle = build_dependency_bundle(activated, self._get_runtime_skills(runtime_context))
        activated_tool_names = set(deps_bundle["tools"])

        # 门控：未激活 Skill 的依赖工具对模型不可见（保持按需加载）。
        # 所有工具已在运行时装配阶段进入同一 ToolNode，剔除只影响模型可见性，
        # 不会改变本次运行的工具实例。
        # 排除基础工具集中的工具（如 present_artifacts），它们始终可见、不受 Skill 激活影响。
        gated_tool_names = self._resolve_gated_tool_names(runtime_context) - activated_tool_names
        model_tools = list(request.tools or [])
        if gated_tool_names:
            model_tools = [t for t in model_tools if t.name not in gated_tool_names]

        # 追加已激活或预加载 Skill 的依赖工具。
        runtime_tools = {
            tool.name: tool
            for tool in (getattr(runtime_context, "runtime_tools", ()) or ())
            if getattr(tool, "name", "")
        }
        enabled_tools = [
            runtime_tools[name] for name in activated_tool_names
            if name in runtime_tools
        ]

        mcp_tools_by_server = getattr(runtime_context, "runtime_mcp_tools", {}) or {}
        runtime_snapshot = getattr(runtime_context, "_runtime_snapshot", None)
        configured_mcp_names = normalize_string_list(
            runtime_snapshot.selected("mcps") if runtime_snapshot else ()
        )
        active_mcp_names = set(configured_mcp_names) | set(deps_bundle["mcps"])
        active_mcp_tools = [
            tool
            for server_name in active_mcp_names
            for tool in mcp_tools_by_server.get(server_name, ())
        ]
        all_mcp_tool_names = {
            tool.name
            for tools in mcp_tools_by_server.values()
            for tool in tools
        }
        model_tools = [
            tool for tool in model_tools
            if tool.name not in (all_mcp_tool_names - {tool.name for tool in active_mcp_tools})
        ]
        active_mcp_tools_by_name = {}
        for tool in active_mcp_tools:
            existing = active_mcp_tools_by_name.get(tool.name)
            if existing is not None and existing is not tool:
                # MCP 工具名冲突不能击穿整个 Run。保留先发现的实例，并把
                # 冲突写进运行诊断，模型仍可使用其余工具继续工作。
                record_runtime_diagnostic(runtime_context, {
                    "severity": "warning",
                    "code": "MCP_TOOL_NAME_CONFLICT",
                    "message": f"多个 MCP 暴露了同名工具，已保留先加载的实例：{tool.name}",
                    "resource_kind": "mcps",
                    "resource_key": tool.name,
                    "recoverable": True,
                })
                continue
            active_mcp_tools_by_name[tool.name] = tool
        setattr(runtime_context, "_active_skill_mcp_tools", active_mcp_tools_by_name)

        existing_tool_names = {t.name for t in model_tools}
        for t in enabled_tools:
            if t.name in existing_tool_names:
                continue
            model_tools.append(t)
            existing_tool_names.add(t.name)
        for t in active_mcp_tools:
            if t.name in existing_tool_names:
                record_runtime_diagnostic(runtime_context, {
                    "severity": "warning",
                    "code": "MCP_TOOL_NAME_CONFLICT",
                    "message": f"MCP 工具与当前 Agent 工具同名，已跳过 MCP 实例：{t.name}",
                    "resource_kind": "mcps",
                    "resource_key": t.name,
                    "recoverable": True,
                })
                continue
            model_tools.append(t)
            existing_tool_names.add(t.name)

        if gated_tool_names or enabled_tools or active_mcp_tools:
            request = request.override(tools=model_tools)

        return await handler(request)

    def _resolve_gated_tool_names(self, runtime_context) -> set[str]:
        """所有可见 Skill 依赖、且不属于基础工具集的工具名集合（即「仅经 Skill 激活才放出」的工具）。"""
        runtime_skills = self._get_runtime_skills(runtime_context)
        effective_skills = self._get_effective_skills(runtime_context)
        base_tool_names = set(expand_tool_selection(getattr(runtime_context, "tools", None)))
        gated: set[str] = set()
        for slug in effective_skills:
            gated.update(runtime_skills.get(slug, {}).get("tools", []))
        return gated - base_tool_names

    def _collect_prompt_metadata(self, slugs: list[str], runtime_context) -> list[RuntimeSkill]:
        """收集指定 slugs 的提示词元数据"""
        runtime_skills = self._get_runtime_skills(runtime_context)
        result: list[RuntimeSkill] = []
        for slug in slugs:
            item = runtime_skills.get(slug)
            if not item:
                logger.debug(f"Skill slug not found in prompt metadata, skip: {slug}")
                continue
            result.append(dict(item))
        return result

    def _process_tool_call_result(self, result: Any, request: ToolCallRequest) -> Any:
        """处理工具调用结果，检查并处理 skill 动态激活"""
        if request.tool_call.get("name") != "read_file":
            return result

        args = request.tool_call.get("args") or {}
        file_path = args.get("file_path") if isinstance(args, dict) else None
        slug = self._extract_skill_slug_from_skill_md_path(file_path)

        if not slug:
            return result

        if slug not in self._get_effective_skills(request.runtime.context):
            logger.warning(f"SkillsMiddleware: deny skill activation for invisible slug: {slug}")
            return result

        logger.debug(f"SkillsMiddleware: activated skill by read_file: {slug}")
        return self._merge_activated_skill_update(result, slug)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ):
        """包装工具调用，处理 skill 动态激活"""
        request = self._bind_active_mcp_tool(request)
        try:
            result = await handler(request)
        except Exception as exc:  # noqa: BLE001
            # 工具参数错误、路径错误、MCP 连接错误都应回传给模型自行纠正；
            # 只有取消信号需要继续向上抛出，不能让单个工具击穿整个 Run。
            message = f"{type(exc).__name__}: {str(exc)[:500]}"
            self._record_tool_failure(request, message)
            logger.warning("SkillsMiddleware: tool call failed, returning recoverable result: %s: %s",
                           request.tool_call.get("name"), exc)
            result = ToolMessage(
                content=f"工具执行失败（{message}）",
                tool_call_id=request.tool_call.get("id", ""),
            )
        else:
            failure_message = self._result_failure_message(result)
            if failure_message:
                self._record_tool_failure(request, failure_message)
            else:
                self._clear_tool_failure(request)
        return self._process_tool_call_result(result, request)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ):
        """同步版本的工具调用包装"""
        request = self._bind_active_mcp_tool(request)
        try:
            result = handler(request)
        except Exception as exc:  # noqa: BLE001
            message = f"{type(exc).__name__}: {str(exc)[:500]}"
            self._record_tool_failure(request, message)
            logger.warning("SkillsMiddleware: sync tool call failed, returning recoverable result: %s: %s",
                           request.tool_call.get("name"), exc)
            result = ToolMessage(
                content=f"工具执行失败（{message}）",
                tool_call_id=request.tool_call.get("id", ""),
            )
        else:
            failure_message = self._result_failure_message(result)
            if failure_message:
                self._record_tool_failure(request, failure_message)
            else:
                self._clear_tool_failure(request)
        return self._process_tool_call_result(result, request)

    def _bind_active_mcp_tool(self, request: ToolCallRequest) -> ToolCallRequest:
        """为当前模型轮次已开放的动态 MCP 调用绑定真实工具。"""

        if request.tool is not None:
            return request
        context = request.runtime.context
        active_tools = getattr(context, "_active_skill_mcp_tools", {})
        if not isinstance(active_tools, dict):
            return request
        tool = active_tools.get(request.tool_call.get("name"))
        return request.override(tool=tool) if tool is not None else request

    def _extract_skill_slug_from_skill_md_path(self, file_path: Any) -> str | None:
        """从共享投影或个人 UserWorkspace 的 SKILL.md 路径中提取 slug。"""
        if not isinstance(file_path, str):
            return None
        raw = file_path.strip()
        if not raw:
            return None
        pure = PurePosixPath(raw if raw.startswith("/") else f"/{raw}")
        for root in (VIRTUAL_SKILLS_PATH, VIRTUAL_PERSONAL_SKILLS_PATH):
            try:
                relative = pure.relative_to(PurePosixPath(root))
            except ValueError:
                continue
            if (
                (len(relative.parts) == 1 or (len(relative.parts) == 2 and relative.name == "SKILL.md"))
                and is_valid_skill_slug(relative.parts[0])
            ):
                return relative.parts[0]
        return None

    def _get_effective_skills(self, runtime_context) -> set[str]:
        selected = getattr(runtime_context, "_effective_skill_slugs", [])
        return set(normalize_string_list(selected if isinstance(selected, list) else []))

    def _get_runtime_skills(self, runtime_context) -> dict[str, RuntimeSkill]:
        runtime_skills = getattr(runtime_context, "_runtime_skills", {})
        return runtime_skills if isinstance(runtime_skills, dict) else {}

    def _get_preloaded_skills(self, runtime_context) -> list[str]:
        selected = getattr(runtime_context, "_preloaded_skills", [])
        effective = self._get_effective_skills(runtime_context)
        return [
            slug for slug in normalize_string_list(selected if isinstance(selected, list) else []) if slug in effective
        ]

    def _build_preloaded_skills_section(self, slugs: list[str], runtime_context) -> str:
        """构建已预加载 Skill 的完整系统提示段。"""

        contents = getattr(runtime_context, "_preloaded_skill_contents", {})
        if not isinstance(contents, dict):
            contents = {}
        sections = ["# Preloaded Skills", "The following Skill instructions are already loaded and active."]
        for slug in slugs:
            content = contents.get(slug)
            if isinstance(content, str):
                sections.append(f'<preloaded_skill slug="{slug}">\n{content}\n</preloaded_skill>')
        return "\n\n".join(sections)

    def _merge_activated_skill_update(self, result: Any, slug: str):
        """合并动态激活的 skill 更新"""
        from langchain_core.messages import ToolMessage

        if isinstance(result, Command):
            update = dict(result.update or {})
            current = update.get("activated_skills") or []
            update["activated_skills"] = _activated_skills_reducer(current, [slug])
            return Command(graph=result.graph, update=update, resume=result.resume, goto=result.goto)

        if isinstance(result, ToolMessage):
            return Command(update={"messages": [result], "activated_skills": [slug]})

        return result

    def _format_skills_locations(self, sources: list[str]) -> str:
        """格式化 skills 位置信息"""
        locations = []
        for i, source_path in enumerate(sources):
            name = PurePosixPath(source_path.rstrip("/")).name.capitalize()
            suffix = " (higher priority)" if i == len(sources) - 1 else ""
            locations.append(f"**{name} Skills**: `{source_path}`{suffix}")
        return "\n".join(locations)

    def _format_skills_list(self, skills_meta: list[dict[str, str]]) -> str:
        """格式化 skills 列表"""
        if not skills_meta:
            return f"(No skills available yet. You can create skills in {' or '.join(self.skills_sources_for_prompt)})"

        lines = []
        for skill in skills_meta:
            lines.append(f"- **{skill['name']}**: {skill['description']}")
            lines.append(f"  -> Read `{skill['path']}` for full instructions")
        return "\n".join(lines)

    def _build_skills_section(self, skills_meta: list[dict[str, str]]) -> str:
        """构建 skills 提示段"""
        skills_locations = self._format_skills_locations(self.skills_sources_for_prompt)
        skills_list = self._format_skills_list(skills_meta)
        return SKILLS_SYSTEM_PROMPT.format(
            skills_locations=skills_locations,
            skills_load_warnings="",
            skills_list=skills_list,
        )
