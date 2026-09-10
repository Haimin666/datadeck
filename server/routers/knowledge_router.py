"""知识库构建全流程 API（基础文本版本）。"""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.deps import get_required_user
from server.services.knowledge_service import (
    add_document, create_knowledge_base, delete_document, delete_knowledge_base, get_knowledge_base,
    list_documents, list_knowledge_bases, search,
)

knowledge = APIRouter(prefix="/knowledge", tags=["knowledge"])


class KnowledgeBaseIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000)


class QueryIn(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=20)


def _admin(user) -> None:
    if user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="需要管理员权限")


@knowledge.get("/databases")
async def databases(current_user=Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    _admin(current_user)
    return {"databases": await list_knowledge_bases(db, current_user.uid)}


@knowledge.get("/databases/accessible")
async def accessible_databases(current_user=Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    """供 Agent 配置和工作区选择使用的用户可访问知识库列表。"""
    return {"databases": await list_knowledge_bases(db, current_user.uid)}


@knowledge.post("/databases")
async def create_database(payload: KnowledgeBaseIn, current_user=Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    _admin(current_user)
    try:
        item = await create_knowledge_base(db, current_user.uid, payload.name, payload.description)
        return item.to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@knowledge.get("/databases/{kb_id}")
async def database_detail(kb_id: str, current_user=Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    try:
        item = await get_knowledge_base(db, current_user.uid, kb_id)
        documents = await list_documents(db, kb_id, current_user.uid)
        return {**item.to_dict(document_count=len(documents), chunk_count=sum(d["chunk_count"] for d in documents)),
                "documents": documents}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@knowledge.delete("/databases/{kb_id}")
async def remove_database(kb_id: str, current_user=Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    _admin(current_user)
    try:
        await delete_knowledge_base(db, current_user.uid, kb_id)
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@knowledge.get("/databases/{kb_id}/documents")
async def documents(kb_id: str, current_user=Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    try:
        return {"documents": await list_documents(db, kb_id, current_user.uid)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@knowledge.post("/databases/{kb_id}/documents/upload")
async def upload_document(kb_id: str, file: UploadFile = File(...), current_user=Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    _admin(current_user)
    try:
        document = await add_document(db, current_user.uid, kb_id, file.filename or "document.txt", await file.read())
        return document.to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@knowledge.delete("/databases/{kb_id}/documents/{document_id}")
async def remove_document(kb_id: str, document_id: str, current_user=Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    _admin(current_user)
    try:
        await delete_document(db, current_user.uid, kb_id, document_id)
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@knowledge.post("/databases/{kb_id}/query")
async def query_database(kb_id: str, payload: QueryIn, current_user=Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    try:
        return await search(db, current_user.uid, kb_id, payload.query, payload.top_k)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
