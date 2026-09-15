
import io
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from server.db import get_db
from server.deps import get_required_user, get_role_permissions, require_module_access
from server.models import User
from server.services.workspace_service import (
    create_workspace_directory,
    delete_workspace_path,
    download_workspace_file,
    list_workspace_tree,
    read_workspace_file_content,
    search_workspace_files,
    upload_workspace_files,
    write_workspace_file_content,
    is_public_knowledge_path,
)
workspace = APIRouter(prefix="/workspace", tags=["workspace"])


async def require_workspace_or_public_knowledge(
    path: str = Query("/"),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    """普通工作区仍需 workspace 权限；公共知识物料允许 knowledge 用户只读访问。"""
    permissions = await get_role_permissions(db, current_user.role)
    required = "knowledge" if is_public_knowledge_path(path) else "workspace"
    if required not in permissions:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="没有访问该模块的权限")
    return current_user


class CreateWorkspaceDirectoryRequest(BaseModel):
    parent_path: str
    name: str


class UpdateWorkspaceFileContentRequest(BaseModel):
    path: str
    content: str


@workspace.get("/tree", response_model=dict)
async def get_workspace_tree(
    path: str = Query("/", description="工作区目录路径"),
    recursive: bool = Query(False, description="是否递归返回子目录文件"),
    files_only: bool = Query(False, description="是否仅返回文件"),
    include_unbound_project_dirs: bool = Query(False, description="Project 选目录时展示未绑定目录"),
    current_user: User = Depends(require_workspace_or_public_knowledge),
    db: AsyncSession = Depends(get_db),
):
    return await list_workspace_tree(
        path=path,
        recursive=recursive,
        files_only=files_only,
        include_unbound_project_dirs=include_unbound_project_dirs,
        current_user=current_user,
        db=db,
    )


@workspace.get("/search", response_model=dict)
async def search_workspace_files_route(
    query: str = Query(..., description="搜索关键词"),
    current_user: User = Depends(require_module_access("workspace")),
):
    return await search_workspace_files(query=query, current_user=current_user)


def _binary_preview_response(data: dict) -> StreamingResponse:
    filename = data.get("filename") or "preview"
    preview_type = data.get("preview_type") or "unsupported"
    return StreamingResponse(
        io.BytesIO(data.get("content") or b""),
        media_type=data.get("media_type") or "application/octet-stream",
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
            "X-Yuxi-Preview-Type": preview_type,
            "X-Yuxi-Preview-Filename": quote(filename),
        },
    )


def _preview_response(data):
    if isinstance(data, dict) and data.get("binary"):
        return _binary_preview_response(data)
    return data


@workspace.get("/file")
async def get_workspace_file(
    path: str = Query(..., description="工作区文件路径"),
    current_user: User = Depends(require_workspace_or_public_knowledge),
    db: AsyncSession = Depends(get_db),
):
    return await read_workspace_file_content(path=path, current_user=current_user, db=db)


@workspace.put("/file", response_model=dict)
async def update_workspace_file(
    payload: UpdateWorkspaceFileContentRequest,
    current_user: User = Depends(require_module_access("workspace")),
):
    return await write_workspace_file_content(
        path=payload.path,
        content=payload.content,
        current_user=current_user,
    )


@workspace.delete("/file", response_model=dict)
async def delete_workspace_file_route(
    path: str = Query(..., description="工作区文件或目录路径"),
    current_user: User = Depends(require_module_access("workspace")),
):
    return await delete_workspace_path(path=path, current_user=current_user)


@workspace.post("/directory", response_model=dict)
async def create_workspace_directory_route(
    payload: CreateWorkspaceDirectoryRequest,
    current_user: User = Depends(require_module_access("workspace")),
):
    return await create_workspace_directory(
        parent_path=payload.parent_path,
        name=payload.name,
        current_user=current_user,
    )


@workspace.post("/upload", response_model=dict)
async def upload_workspace_files_route(
    parent_path: str = Form(..., description="父目录路径"),
    files: list[UploadFile] = File(..., description="上传文件列表"),
    current_user: User = Depends(require_module_access("workspace")),
):
    return await upload_workspace_files(parent_path=parent_path, files=files, current_user=current_user)


@workspace.get("/download")
async def download_workspace(
    path: str = Query(..., description="工作区文件路径"),
    current_user: User = Depends(require_module_access("workspace")),
    db: AsyncSession = Depends(get_db),
):
    return await download_workspace_file(path=path, current_user=current_user, db=db)
