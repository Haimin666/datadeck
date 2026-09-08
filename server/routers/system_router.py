"""系统路由：健康检查、首次运行等。"""
from fastapi import APIRouter

system = APIRouter(prefix="/system", tags=["system"])


@system.get("/health")
async def health():
    return {"status": "ok"}


@system.get("/ready")
async def ready():
    return {"status": "ok", "components": {"db": "ok"}}
