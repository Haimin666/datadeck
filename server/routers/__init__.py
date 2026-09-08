from fastapi import APIRouter
from server.routers.auth_router import auth
from server.routers.agent_router import agent
from server.routers.chat_router import chat
from server.routers.apikey_router import apikey
from server.routers.dashboard_router import dashboard
from server.routers.system_router import system

router = APIRouter()
router.include_router(system)
router.include_router(auth)
router.include_router(agent)
router.include_router(chat)
router.include_router(apikey)
router.include_router(dashboard)
