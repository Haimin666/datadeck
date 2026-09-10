
import io
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from server.db import get_db
from server.deps import get_required_user
from server.models import KnowledgeDocument, User
from server.services.knowledge_service import get_knowledge_base
from server.services.workspace_service import (
    create_workspace_directory,
    delete_workspace_path,
    download_workspace_file,
    list_workspace_tree,
    read_workspace_file_content,
    search_workspace_files,
    upload_workspace_files,
    write_workspace_file_content,
)
workspace = APIRouter(prefix="/workspace", tags=["workspace"])


class CreateWorkspaceDirectoryRequest(BaseModel):
    parent_path: str
    name: str


class UpdateWorkspaceFileContentRequest(BaseModel):
    path: str
    content: str


async def _knowledge_document(db: AsyncSession, uid: str, kb_id: str, file_id: str) -> KnowledgeDocument:
    try:
        await get_knowledge_base(db, uid, kb_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="知识库不存在或无权访问") from exc
    document = (await db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == file_id,
            KnowledgeDocument.kb_id == kb_id,
        )
    )).scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=404, detail="知识库文件不存在")
    return document


@workspace.get("/knowledge/tree", response_model=dict)
async def get_workspace_knowledge_tree(
    kb_id: str = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """把基础文本知识库文档适配为 Workspace 文件树的只读条目。"""
    try:
        await get_knowledge_base(db, current_user.uid, kb_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="知识库不存在或无权访问") from exc
    documents = (await db.execute(
        select(KnowledgeDocument)
        .where(KnowledgeDocument.kb_id == kb_id)
        .order_by(KnowledgeDocument.created_at.desc())
    )).scalars().all()
    start = (page - 1) * page_size
    items = documents[start:start + page_size]
    return {
        "entries": [
            {
                "name": item.filename,
                "path": f"/{item.filename}",
                "file_id": item.id,
                "kb_id": kb_id,
                "is_dir": False,
                "size": len(item.content.encode("utf-8")),
                "status": item.status,
                "source": "knowledge",
            }
            for item in items
        ],
        "page": page,
        "page_size": page_size,
        "total": len(documents),
        "has_more": start + page_size < len(documents),
        "parent_id": None,
        "path_prefix": "",
    }


@workspace.get("/knowledge/file")
async def get_workspace_knowledge_file(
    kb_id: str = Query(...),
    file_id: str = Query(...),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    document = await _knowledge_document(db, current_user.uid, kb_id, file_id)
    return StreamingResponse(
        io.BytesIO(document.content.encode("utf-8")),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(document.filename)}"},
    )


@workspace.get("/knowledge/download")
async def download_workspace_knowledge_file(
    kb_id: str = Query(...),
    file_id: str = Query(...),
    variant: str = Query("original"),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    document = await _knowledge_document(db, current_user.uid, kb_id, file_id)
    return StreamingResponse(
        io.BytesIO(document.content.encode("utf-8")),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(document.filename)}"},
    )


@workspace.get("/tree", response_model=dict)
async def get_workspace_tree(
    path: str = Query("/", description="工作区目录路径"),
    recursive: bool = Query(False, description="是否递归返回子目录文件"),
    files_only: bool = Query(False, description="是否仅返回文件"),
    include_unbound_project_dirs: bool = Query(False, description="Project 选目录时展示未绑定目录"),
    current_user: User = Depends(get_required_user),
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
    current_user: User = Depends(get_required_user),
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
    current_user: User = Depends(get_required_user),
):
    return await read_workspace_file_content(path=path, current_user=current_user)


@workspace.put("/file", response_model=dict)
async def update_workspace_file(
    payload: UpdateWorkspaceFileContentRequest,
    current_user: User = Depends(get_required_user),
):
    return await write_workspace_file_content(
        path=payload.path,
        content=payload.content,
        current_user=current_user,
    )


@workspace.delete("/file", response_model=dict)
async def delete_workspace_file_route(
    path: str = Query(..., description="工作区文件或目录路径"),
    current_user: User = Depends(get_required_user),
):
    return await delete_workspace_path(path=path, current_user=current_user)


@workspace.post("/directory", response_model=dict)
async def create_workspace_directory_route(
    payload: CreateWorkspaceDirectoryRequest,
    current_user: User = Depends(get_required_user),
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
    current_user: User = Depends(get_required_user),
):
    return await upload_workspace_files(parent_path=parent_path, files=files, current_user=current_user)


@workspace.get("/download")
async def download_workspace(
    path: str = Query(..., description="工作区文件路径"),
    current_user: User = Depends(get_required_user),
):
    return await download_workspace_file(path=path, current_user=current_user)
