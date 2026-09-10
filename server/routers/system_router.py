"""系统路由：健康检查、运行时能力发现、品牌信息、监控统计。"""
from fastapi import APIRouter, Depends, HTTPException

from server.config import settings
from server.deps import get_required_user

system = APIRouter(prefix="/system", tags=["system"])


@system.get("/health")
async def health():
    return {"status": "ok"}


@system.get("/ready")
async def ready():
    return {"status": "ok", "components": {"db": "ok"}}


def _admin_check(user) -> None:
    if getattr(user, "role", "") not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="需要管理员权限")


@system.get("/metrics/runs")
async def metrics_runs(hours: int = 24, current_user=Depends(get_required_user)):
    """运行统计：成功率/失败率/耗时分位（管理员）。"""
    _admin_check(current_user)
    from server.services.metrics_service import run_stats

    return await run_stats(hours=hours)


@system.get("/metrics/tools")
async def metrics_tools(hours: int = 24, current_user=Depends(get_required_user)):
    """工具调用频次与错误分布（管理员）。"""
    _admin_check(current_user)
    from server.services.metrics_service import tool_stats

    return await tool_stats(hours=hours)


@system.get("/metrics/components")
async def metrics_components(current_user=Depends(get_required_user)):
    """熔断器/缓存状态快照（管理员）。"""
    _admin_check(current_user)
    from server.services.metrics_service import component_stats

    return component_stats()


@system.get("/discovery")
async def discovery():
    """运行时能力发现：前端 runtimeCapabilities store 消费。

    基础文本知识库已启用；高级解析能力不在当前版本范围内。
    """
    return {
        "service": settings.app_name,
        "version": "0.2.0",
        "capabilities": {
            "features": {
                "knowledge": True,
                "skills": True,
                "mcp": True,
                "workspace": True,
                "scheduled_tasks": True,
            },
        },
    }


@system.get("/info")
async def info():
    """系统信息配置（公开）：前端 info store 消费，品牌/页脚文案。"""
    return {
        "organization": {
            "name": "DataDeck",
            "logo": "/logo.png",
            "avatar": "",
        },
        "branding": {
            "name": "DataDeck",
            "title": "DataDeck",
            "subtitle": "知识库问答与 Text2SQL 平台",
            "subtitles": [
                "你好，有什么可以帮你的？",
                "欢迎使用 DataDeck",
                "准备好开始了吗？",
                "今天想解决什么问题？",
            ],
        },
        "footer": {
            "copyright": "Powered by DataDeck",
            "user_agreement_url": "",
            "privacy_policy_url": "",
        },
    }
