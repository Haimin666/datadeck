from fastapi import APIRouter
from server.routers.auth_router import auth
from server.routers.agent_router import agent
from server.routers.chat_router import chat
from server.routers.apikey_router import apikey
from server.routers.dashboard_router import dashboard
from server.routers.system_router import system
from server.routers.rag_router import rag
from server.routers.knowledge_router import knowledge
from server.routers.eval_router import eval_router
from server.routers.config_router import config_router
from server.routers.attachment_router import attachments
from server.routers.project_router import projects
from server.routers.workspace_router import workspace
from server.routers.filesystem_router import filesystem_router
from server.routers.model_provider_router import model_providers
from server.routers.operation_log_router import operation_logs
from server.routers.skill_router import skills, user_skills
from server.routers.mcp_router import mcp
from server.routers.tool_router import tools
from server.routers.task_router import tasks
from server.routers.scheduled_task_router import scheduled_tasks

router = APIRouter()
router.include_router(system)
router.include_router(auth)
router.include_router(agent)
router.include_router(chat)
router.include_router(apikey)
router.include_router(dashboard)
router.include_router(rag)
router.include_router(knowledge)
router.include_router(eval_router)
router.include_router(config_router)
router.include_router(attachments)
router.include_router(projects)
router.include_router(workspace)
router.include_router(filesystem_router)
router.include_router(model_providers)
router.include_router(operation_logs)
router.include_router(skills)
router.include_router(user_skills)
router.include_router(mcp)
router.include_router(tools)
router.include_router(tasks)
router.include_router(scheduled_tasks)
