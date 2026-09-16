"""datadeck FastAPI 应用入口。"""
from __future__ import annotations

import os
import asyncio
from contextlib import asynccontextmanager
import traceback

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# .env 必须先于 server.db（engine 模块级创建）与 EnvModelProvider（os.getenv）加载
load_dotenv()

from server.config import settings  # noqa: E402
from datadeck import logger  # noqa: E402
from server.db import async_session_factory, close_db  # noqa: E402
from server.models import Agent, Role, User  # noqa: E402
from server.deps import BUILTIN_ROLE_AGENT_SLUGS, BUILTIN_ROLE_PERMISSIONS  # noqa: E402
from server.routers import router  # noqa: E402
from server.routers.run_router import run_router  # noqa: E402
from server.utils.auth import hash_password  # noqa: E402


async def _temporary_workdir_cleanup_loop():
    """定期清理过期临时会话目录；清理失败不影响主服务。"""
    from server.workspace.temp_workdir import cleanup_expired_temporary_workdirs

    while True:
        await asyncio.sleep(60 * 60)
        try:
            removed = cleanup_expired_temporary_workdirs()
            if removed:
                logger.info("Cleaned %d expired temporary Agent workdirs", removed)
        except Exception as exc:
            logger.warning(f"Temporary Agent workdir cleanup failed: {exc}")


async def _agent_run_recovery_loop():
    """定期回收无心跳的 Agent Run，避免进程异常退出后永久 loading。"""
    from server.services.run_service import recover_orphaned_agent_runs

    while True:
        await asyncio.sleep(30)
        try:
            recovered = await recover_orphaned_agent_runs()
            if recovered:
                logger.warning("Recovered %d stale Agent runs", recovered)
                from server.services.run_service import resume_pending_agent_runs
                resumed = await resume_pending_agent_runs()
                if resumed:
                    logger.info("Resumed %d queued Agent runs after recovery", resumed)
        except Exception as exc:
            logger.warning(f"Agent run recovery failed: {exc}")


async def _rebuild_rag_indexes_in_background():
    """在服务就绪后恢复 RAG 派生索引，不阻塞 HTTP 启动。"""
    from server.services.knowledge_service import rebuild_rag_indexes

    try:
        async with async_session_factory() as index_db:
            repaired_documents = await rebuild_rag_indexes(index_db)
            # async_session_factory 不会自动提交；恢复出的 chunk 和向量状态必须
            # 在 session 关闭前提交，否则下次启动仍会重复恢复并继续回滚。
            await index_db.commit()
        if repaired_documents:
            logger.info("Repaired persisted RAG chunks: %d documents", repaired_documents)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # Qdrant/embedding 暂时不可用不能影响已经启动的对话服务；关键词检索
        # 仍然直接读取 PostgreSQL，下一次启动或手动重试可继续恢复派生索引。
        logger.warning(f"Background RAG index recovery failed: {exc}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema 由部署入口的 Alembic 迁移负责；应用启动只初始化默认数据和运行服务。
    async with async_session_factory() as db:
        from sqlalchemy import select as sa_select

        builtin_roles = (
            ("superadmin", "超级管理员", "拥有全部模块及系统管理权限", []),
            ("admin", "管理员", "可管理业务模块和用户", []),
            ("user", "普通用户", "使用简化对话入口和内置运营 Agent", BUILTIN_ROLE_AGENT_SLUGS["user"]),
        )
        for slug, name, description, agent_slugs in builtin_roles:
            role = await db.get(Role, slug)
            if role is None:
                db.add(Role(
                    slug=slug,
                    name=name,
                    description=description,
                    permissions=BUILTIN_ROLE_PERMISSIONS[slug],
                    agent_slugs=agent_slugs,
                    is_builtin=True,
                ))
            else:
                role.name = name
                role.description = description
                role.permissions = BUILTIN_ROLE_PERMISSIONS[slug]
                role.agent_slugs = agent_slugs
                role.is_builtin = True

        # 确保内置 Agent 存在：通用对话、运营入口、数据分析。
        builtin_agents = (
            ("default-chatbot", "对话助手", "内置通用对话智能体，按实际挂载能力完成问答和任务协作", "ChatbotAgent"),
            ("operations-agent", "运营助手", "面向普通用户的内置运营智能体，使用简化对话入口", "ChatbotAgent"),
            ("data-agent", "数据分析助手", "按知识库口径、元数据和只读 SQL 完成企业数据分析", "DataAgent"),
        )
        for slug, name, description, backend_id in builtin_agents:
            agent = await db.scalar(sa_select(Agent).where(Agent.slug == slug))
            if agent is None:
                db.add(Agent(
                    id=slug, slug=slug, name=name, description=description,
                    backend_id=backend_id, is_builtin=True,
                ))
            else:
                agent.is_builtin = True

        # 确保至少有一个用户（开发环境）
        r = await db.execute(sa_select(User).where(User.is_deleted == 0).limit(1))
        if not r.scalar_one_or_none():
            db.add(User(
                username="admin", uid="admin", password_hash=hash_password("admin123456"),
                role="superadmin",
            ))

        from server.services.model_providers.service import (
            ensure_builtin_model_providers_in_db,
            get_all_model_providers,
        )
        await ensure_builtin_model_providers_in_db(db)
        await db.commit()

        try:
            from server.services.model_providers.cache import model_cache
            model_cache.rebuild(await get_all_model_providers(db))
        except Exception as exc:
            logger.warning(f"Redis model cache rebuild failed: {exc}")

        from server.services.skills.service import init_builtin_skills
        try:
            await init_builtin_skills(db, created_by="system")
        except Exception as exc:
            logger.warning(f"Built-in skills initialization failed: {exc}")
            traceback.print_exc()

    try:
        from server.services.mcp.service import ensure_builtin_mcp_servers_in_db
        await ensure_builtin_mcp_servers_in_db()
    except Exception as exc:
        logger.warning(f"Built-in MCP servers initialization failed: {exc}")

    from server.services.task_service import tasker
    from server.workspace.temp_workdir import cleanup_expired_temporary_workdirs
    from server.services.run_service import recover_orphaned_agent_runs, resume_pending_agent_runs
    try:
        removed_temp_sessions = cleanup_expired_temporary_workdirs()
        if removed_temp_sessions:
            logger.info("Cleaned %d expired temporary Agent workdirs", removed_temp_sessions)
    except Exception as exc:
        logger.warning(f"Temporary Agent workdir cleanup failed: {exc}")
    recovered = await recover_orphaned_agent_runs()
    if recovered:
        logger.warning("Recovered %d orphaned Agent runs after service restart", recovered)
    resumed = await resume_pending_agent_runs()
    if resumed:
        logger.info("Resumed %d pending Agent runs after service restart", resumed)
    await tasker.start()
    await tasker.start_scheduler()
    rag_recovery_task = asyncio.create_task(_rebuild_rag_indexes_in_background())
    temporary_cleanup_task = asyncio.create_task(_temporary_workdir_cleanup_loop())
    run_recovery_task = asyncio.create_task(_agent_run_recovery_loop())

    yield

    # 关闭：释放 agent checkpointer 连接池。
    try:
        rag_recovery_task.cancel()
        await rag_recovery_task
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.warning(f"RAG index recovery task shutdown failed: {exc}")
    try:
        temporary_cleanup_task.cancel()
        await temporary_cleanup_task
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.warning(f"Temporary Agent cleanup task shutdown failed: {exc}")
    try:
        run_recovery_task.cancel()
        await run_recovery_task
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.warning(f"Agent run recovery task shutdown failed: {exc}")
    try:
        await tasker.shutdown()
    except Exception as exc:
        logger.warning(f"Tasker shutdown failed: {exc}")
    from server.services.run_service import shutdown_running_agent_runs
    await shutdown_running_agent_runs()
    from server.services.agents_provider import close_agent
    await close_agent()
    await close_db()


app = FastAPI(
    title=settings.app_name,
    description="DataDeck - 知识库问答与 Text2SQL 平台",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "X-Lock-Remaining"],
)

# 注册路由
app.include_router(router, prefix="/api")
app.include_router(run_router, prefix="/api")

# 前端静态文件（SPA：非 /api、/uploads 的未匹配路径回退 index.html）
frontend_dist = os.path.join(os.path.dirname(__file__), "..", "web", "dist")
if os.path.isdir(frontend_dist):
    app.mount("/assets", StaticFiles(directory=os.path.join(frontend_dist, "assets")), name="assets")

    @app.exception_handler(404)
    async def spa_fallback(request, exc):  # noqa: ANN001
        from fastapi.responses import JSONResponse

        if request.url.path.startswith(("/api", "/uploads")):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return FileResponse(os.path.join(frontend_dist, "index.html"))

# 用户上传文件（头像/图片）静态服务
uploads_dir = os.path.join(os.path.dirname(__file__), "..", "uploads")
os.makedirs(uploads_dir, exist_ok=True)
for upload_kind in ("avatars", "images"):
    canonical_kind_dir = os.path.join(uploads_dir, upload_kind)
    os.makedirs(canonical_kind_dir, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=uploads_dir), name="uploads")

# Vite public 资源（logo、登录背景等）需要在生产 API 进程中直接提供。
if os.path.isdir(frontend_dist):
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")


@app.get("/")
async def root():
    return FileResponse(os.path.join(frontend_dist, "index.html"))


if __name__ == "__main__":
    uvicorn.run(
        "server.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
    )
