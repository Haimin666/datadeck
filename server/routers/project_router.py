"""Project HTTP 适配层。"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.deps import get_required_user
from server.services.project_service import (
    create_project_view,
    delete_project_view,
    list_history_candidates_view,
    list_projects_view,
    rename_project_view,
)
from server.models import User
from server.services.operation_log_service import log_operation

projects = APIRouter(prefix="/projects", tags=["projects"])


class ProjectWorkdirCreate(BaseModel):
    """Project Workdir 创建意图。"""

    model_config = ConfigDict(extra="forbid")

    mode: str = "managed"
    path: str | None = None


class ProjectCreate(BaseModel):
    """独立 Project 创建请求。"""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(..., min_length=1, max_length=128)
    name: str
    workdir: ProjectWorkdirCreate


class ProjectUpdate(BaseModel):
    """Project 可修改字段。"""

    model_config = ConfigDict(extra="forbid")

    name: str


@projects.get("")
async def list_projects(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """列出当前用户可选择的 Project。"""
    return await list_projects_view(uid=str(current_user.uid), db=db)


@projects.post("")
async def create_project(
    payload: ProjectCreate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """独立创建 managed 或 linked Project。"""
    project = await create_project_view(
        uid=str(current_user.uid),
        request_id=payload.request_id,
        name=payload.name,
        directory_mode=payload.workdir.mode,
        workdir_path=payload.workdir.path,
        db=db,
    )
    await log_operation(
        db,
        current_user.id,
        "project.create",
        f"project_id={project.get('id')};name={payload.name};directory_mode={payload.workdir.mode}",
    )
    await db.commit()
    return project


@projects.get("/history-candidates")
async def list_history_candidates(
    q: str = Query("", max_length=200),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """列出可作为目录快捷选择的历史 Conversation。"""
    return await list_history_candidates_view(uid=str(current_user.uid), db=db, query=q, limit=limit, offset=offset)


@projects.put("/{project_id}")
async def rename_project(
    project_id: str,
    payload: ProjectUpdate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """重命名当前用户的 Project。"""
    project = await rename_project_view(uid=str(current_user.uid), project_id=project_id, name=payload.name, db=db)
    await log_operation(db, current_user.id, "project.rename", f"project_id={project_id};name={payload.name}")
    await db.commit()
    return project


@projects.delete("/{project_id}")
async def delete_project(
    project_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """软删除当前用户的 Project 及其中对话。"""
    result = await delete_project_view(uid=str(current_user.uid), project_id=project_id, db=db)
    await log_operation(
        db,
        current_user.id,
        "project.delete",
        f"project_id={project_id};deleted_conversations={result.get('deleted_conversations', 0)}",
    )
    await db.commit()
    return result
