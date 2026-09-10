"""datadeck FastAPI 应用入口。"""
from __future__ import annotations

import os
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
from server.db import engine, async_session_factory  # noqa: E402
from server.models import Agent, Base, User  # noqa: E402
from server.routers import router  # noqa: E402
from server.routers.run_router import run_router  # noqa: E402
from server.utils.auth import hash_password  # noqa: E402
from server.utils.datetime_utils import utc_now  # noqa: E402

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动：建表 + 初始化默认数据
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 确保内置默认 agent 存在
    async with async_session_factory() as db:
        from sqlalchemy import select as sa_select
        r = await db.execute(sa_select(Agent).where(Agent.slug == "default-chatbot"))
        if not r.scalar_one_or_none():
            agent = Agent(
                id="default-chatbot",
                slug="default-chatbot",
                name="对话助手",
                description="内置对话智能体，支持 Text2SQL",
                backend_id="ChatbotAgent",
                is_builtin=True,
            )
            db.add(agent)
            await db.commit()

        # 确保至少有一个用户（开发环境）
        r = await db.execute(sa_select(User).where(User.is_deleted == 0).limit(1))
        if not r.scalar_one_or_none():
            user = User(
                username="admin",
                uid="admin",
                password_hash=hash_password("admin123456"),
                role="superadmin",
            )
            db.add(user)
            await db.commit()

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
    await tasker.start()
    await tasker.start_scheduler()

    yield

    # 关闭：释放 agent checkpointer 连接池 + 销毁引擎
    try:
        await tasker.shutdown()
    except Exception as exc:
        logger.warning(f"Tasker shutdown failed: {exc}")
    from server.services.agents_provider import close_agent
    await close_agent()
    await engine.dispose()


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
legacy_uploads_dir = os.path.join(os.path.dirname(__file__), "uploads")
if os.path.isdir(legacy_uploads_dir):
    # 兼容早期版本写入 server/uploads 的头像和图片。
    for upload_kind in ("avatars", "images"):
        legacy_kind_dir = os.path.join(legacy_uploads_dir, upload_kind)
        if os.path.isdir(legacy_kind_dir):
            app.mount(
                f"/uploads/{upload_kind}",
                StaticFiles(directory=legacy_kind_dir),
                name=f"legacy_uploads_{upload_kind}",
            )
if os.path.isdir(uploads_dir):
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
