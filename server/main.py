"""datadeck FastAPI 应用入口。"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# .env 必须先于 server.db（engine 模块级创建）与 EnvModelProvider（os.getenv）加载
load_dotenv()

from server.config import settings  # noqa: E402
from server.db import engine, async_session_factory  # noqa: E402
from server.models import Base, User, Agent  # noqa: E402
from server.routers import router  # noqa: E402
from server.routers.run_router import run_router  # noqa: E402
from server.utils.auth import hash_password  # noqa: E402
from server.utils.datetime_utils import utc_now  # noqa: E402

# 新表模型：导入即注册进 Base.metadata（lifespan create_all 自动建表）
from server.services.eval_service import EvaluationCase, EvaluationRun  # noqa: F401,E402
from server.services.pg_memory_store import AgentMemory  # noqa: F401,E402
from server.services.metric_registry import MetricRegistry  # noqa: F401,E402
from server.routers.config_router import SystemConfig, UserConfig  # noqa: F401,E402


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

    yield

    # 关闭：释放 agent checkpointer 连接池 + 销毁引擎
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

# 前端静态文件
frontend_dist = os.path.join(os.path.dirname(__file__), "..", "web", "dist")
if os.path.isdir(frontend_dist):
    app.mount("/assets", StaticFiles(directory=os.path.join(frontend_dist, "assets")), name="assets")
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

# 用户上传文件（头像/图片）静态服务
uploads_dir = os.path.join(os.path.dirname(__file__), "..", "uploads")
if os.path.isdir(uploads_dir):
    app.mount("/uploads", StaticFiles(directory=uploads_dir), name="uploads")


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
