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


class BaseState(AgentState):
    """Shared state fields for datadeck agents."""

    artifacts: Annotated[list[str], merge_artifacts]
    sql_retry_attempts: int       # SqlSelfCheckMiddleware：当前轮打回次数
    sql_turn_base: int            # SqlSelfCheckMiddleware：本轮消息基线索引
    sql_validation: dict | None   # SqlSelfCheckMiddleware：最终验证状态


class AgentStatePayload(TypedDict):
    """Serialized agent state payload consumed by the frontend."""

    todos: list
    artifacts: list[str]
    token_usage: dict | None
    sql_validation: dict | None