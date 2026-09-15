"""Agent 策略：把循环实现与业务能力边界分开。"""

from __future__ import annotations

from dataclasses import dataclass

from datadeck.agents.toolkits.packages import expand_tool_selection


@dataclass(frozen=True, slots=True)
class AgentPolicy:
    """单个 Agent 后端的静态能力策略。"""

    backend_id: str
    fixed_tools: tuple[str, ...] = ()
    fixed_packages: tuple[str, ...] = ()
    data_workflow: bool = False
    allow_delegation: bool = True

    @property
    def fixed_tool_selection(self) -> tuple[str, ...]:
        """固定工具的实际 slug；能力包只用于管理端展示。"""
        return tuple(dict.fromkeys((*self.fixed_tools, *expand_tool_selection(self.fixed_packages))))

    def merge_tools(self, configured: object) -> list[str] | None:
        """将固定能力叠加到用户配置，None 仍表示使用 Agent 默认策略。"""
        fixed = list(self.fixed_tool_selection)
        if configured is None:
            return fixed or None
        configured_tools = list(configured) if isinstance(configured, (list, tuple, set)) else []
        return list(dict.fromkeys([*fixed, *configured_tools]))


# 两类内置 Agent 都默认具备平台基础能力；数据分析专属能力仍只由
# DATA_AGENT_POLICY 固定挂载，用户可配置工具只负责在此基础上追加能力。
COMMON_PLATFORM_PACKAGE = "package:platform"

CHATBOT_POLICY = AgentPolicy(
    backend_id="ChatbotAgent",
    fixed_packages=(COMMON_PLATFORM_PACKAGE,),
)

# DataAgent 的数据能力不可从 Agent 编辑页移除；真实运行工具保持明确顺序，
# 便于提示词、工作流门控和回归测试稳定。
DATA_AGENT_POLICY = AgentPolicy(
    backend_id="DataAgent",
    fixed_packages=(
        COMMON_PLATFORM_PACKAGE,
        "package:knowledge",
        "package:omd",
        "package:sql",
    ),
    data_workflow=True,
    allow_delegation=False,
)


__all__ = [
    "AgentPolicy", "CHATBOT_POLICY", "COMMON_PLATFORM_PACKAGE", "DATA_AGENT_POLICY",
]
