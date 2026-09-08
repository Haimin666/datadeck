"""系统路由：健康检查、运行时能力发现、品牌信息。"""
from fastapi import APIRouter

from server.config import settings

system = APIRouter(prefix="/system", tags=["system"])


@system.get("/health")
async def health():
    return {"status": "ok"}


@system.get("/ready")
async def ready():
    return {"status": "ok", "components": {"db": "ok"}}


@system.get("/discovery")
async def discovery():
    """运行时能力发现：前端 runtimeCapabilities store 消费。

    datadeck 当前无知识库后端，knowledge 关闭（前端隐藏知识库入口）。
    """
    return {
        "service": settings.app_name,
        "version": "0.2.0",
        "capabilities": {
            "features": {
                "knowledge": False,
            },
        },
    }


@system.get("/info")
async def info():
    """系统信息配置（公开）：前端 info store 消费，品牌/页脚文案。"""
    return {
        "organization": {
            "name": "DataDeck",
            "logo": "",
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
