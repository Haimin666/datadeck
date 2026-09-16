"""Agent 状态结构（对应 Yuxi agents/state.py，原样迁移）。"""

from __future__ import annotations

from typing import Annotated, TypedDict

from langchain.agents import AgentState


def merge_artifacts(existing: list[str] | None, new: list[str] | None) -> list[str]:
    """Merge artifact file paths while preserving order and removing duplicates."""
    if existing is None:
        return new or []
    if new is None:
        return existing
    return list(dict.fromkeys(existing + new))


def merge_data_workflow(existing: dict | None, new: dict | None) -> dict:
    """合并并行工具调用的工作流状态，避免 LangGraph 多值更新冲突。"""
    merged = dict(existing or {})
    merged.update(new or {})
    return merged


def merge_string_list(existing: list[str] | None, new: list[str] | None) -> list[str]:
    """合并运行时字符串状态并去重，供 Skill 动态激活使用。"""
    return list(dict.fromkeys([*(existing or []), *(new or [])]))


class BaseState(AgentState):
    """Shared state fields for datadeck agents."""

    artifacts: Annotated[list[str], merge_artifacts]
    sql_retry_attempts: int       # SqlSelfCheckMiddleware：当前轮打回次数
    sql_turn_base: int            # SqlSelfCheckMiddleware：本轮消息基线索引
    sql_validation: dict | None   # SqlSelfCheckMiddleware：最终验证状态
    data_workflow: Annotated[dict, merge_data_workflow]  # DataWorkflowMiddleware：数据工具前置条件
    activated_skills: Annotated[list[str], merge_string_list]
    context_compression: dict | None
    token_budget: dict | None


class AgentStatePayload(TypedDict):
    """Serialized agent state payload consumed by the frontend."""

    todos: list
    artifacts: list[str]
    token_usage: dict | None
    sql_validation: dict | None
    data_workflow: dict
    context_compression: dict | None
    token_budget: dict | None
