"""宿主侧运行时 middleware 装配。

资源查询、授权和 Skill 解析统一由 AgentRuntimeAssembler 负责；本模块只把
宿主 middleware 实例放入本次运行 Context，不向核心 Agent 提供回调入口。
"""

from __future__ import annotations

from datadeck import logger


async def build_runtime_middlewares(context) -> list:
    """构建本次运行需要的宿主 middleware。"""
    effective_skills = getattr(context, "_effective_skill_slugs", None)
    runtime_snapshot = getattr(context, "_runtime_snapshot", None)
    if runtime_snapshot and "extensions" not in runtime_snapshot.permissions:
        return []
    configured_mcps = runtime_snapshot.selected("mcps") if runtime_snapshot else ()
    if (not isinstance(effective_skills, list) or not effective_skills) and not configured_mcps:
        return []
    try:
        from server.services.skills.middleware import SkillsMiddleware

        return [SkillsMiddleware()]
    except Exception as exc:  # noqa: BLE001
        logger.warning("SkillsMiddleware unavailable, skip: %s", exc)
        return []


__all__ = ["build_runtime_middlewares"]
