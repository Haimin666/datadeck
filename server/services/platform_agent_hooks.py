"""平台 Agent 组装钩子：Skills 运行时解析 + SkillsMiddleware 注入。

datadeck 核心环（src/datadeck）保持平台无关；本模块在宿主侧把
Skills 授权解析与 SkillsMiddleware 挂进 ChatbotAgent（对应 Yuxi
context.py prepare_agent_context 的 skills 段 + graph middleware 组装）。
"""

from __future__ import annotations

from datadeck import logger


async def platform_context_skills_resolver(context) -> None:
    """按 context.uid 解析用户可访问 Skills，写入运行时 scope 快照。

    对应 Yuxi prepare_agent_context 的 skills 段（_effective_skill_slugs 等）。
    Skills 未启用 / 无用户时保持空快照，不影响主链路。
    """
    uid = str(getattr(context, "uid", "") or "").strip()
    if not uid:
        setattr(context, "_effective_skill_slugs", [])
        setattr(context, "_runtime_skills", {})
        setattr(context, "_preloaded_skills", [])
        setattr(context, "_preloaded_skill_contents", {})
        return

    try:
        from server.db import session_context
        from server.models import User
        from server.repositories.user_repository import UserRepository
        from server.services.skills.runtime import resolve_runtime_skills_for_context

        from sqlalchemy import select

        async with session_context() as db:
            result = await db.execute(select(User).where(User.uid == uid, User.is_deleted == 0))
            user = result.scalar_one_or_none()
            if user is None:
                setattr(context, "_effective_skill_slugs", [])
                setattr(context, "_runtime_skills", {})
                setattr(context, "_preloaded_skills", [])
                setattr(context, "_preloaded_skill_contents", [])
                return
            snapshot = await resolve_runtime_skills_for_context(context, db=db, user=user)
            context.skills = snapshot["context_skills"]
            context.preload_skills = snapshot["context_preload_skills"]
            setattr(context, "_effective_skill_slugs", snapshot["effective_skills"])
            setattr(context, "_runtime_skills", snapshot["runtime_skills"])
            setattr(context, "_preloaded_skills", snapshot["preloaded_skills"])
            setattr(context, "_preloaded_skill_contents", snapshot["preloaded_skill_contents"])
    except Exception as e:
        # Skills 面板故障不应阻断对话主链路
        logger.warning(f"resolve skills for context failed, fallback to empty: {e}")
        setattr(context, "_effective_skill_slugs", [])
        setattr(context, "_runtime_skills", {})
        setattr(context, "_preloaded_skills", [])
        setattr(context, "_preloaded_skill_contents", {})


async def platform_extra_middlewares(context) -> list:
    """宿主平台 middleware 工厂：SkillsMiddleware（提示注入 + 依赖门控 + 动态激活）。"""
    effective_skills = getattr(context, "_effective_skill_slugs", None)
    if not isinstance(effective_skills, list) or not effective_skills:
        # 无可用 Skill 时跳过，避免空提示注入
        return []
    try:
        from server.services.skills.middleware import SkillsMiddleware

        return [SkillsMiddleware()]
    except Exception as e:
        logger.warning(f"SkillsMiddleware unavailable, skip: {e}")
        return []
