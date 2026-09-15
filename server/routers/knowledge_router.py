"""知识库构建全流程 API（基础文本版本）。"""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, or_, select

from server.db import get_db
from server.deps import require_module_access
from server.models import CodeRepository, KnowledgeBase, KnowledgeDocument
from server.services.knowledge_service import (
    add_document, create_knowledge_base, delete_document, delete_knowledge_base, get_knowledge_base,
    list_documents, list_knowledge_bases, search,
)
from server.services.code_repository_service import (
    create_code_repository, delete_code_repository, list_code_repositories,
    pull_code_repository, update_code_repository,
)

knowledge = APIRouter(prefix="/knowledge", tags=["knowledge"])


class KnowledgeBaseIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000)
    access_scope: str = Field(default="shared", pattern="^(private|shared|public)$")


class QueryIn(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=5, ge=1, le=20)


class CodeRepositoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    repo_url: str = Field(min_length=1, max_length=1024)
    branch: str = Field(default="main", max_length=256)
    subdir: str = Field(default="", max_length=512)
    ssh_key: str = Field(default="", max_length=20000)
    access_token: str = Field(default="", max_length=4096)


def _material_summary(item: KnowledgeDocument) -> dict:
    data = item.to_dict(include_content=True)
    content = data.pop("content", "")
    data["title"] = data.get("filename", "")
    data["content_preview"] = content[:300]
    return data


@knowledge.get("/materials")
async def list_materials(
    q: str = "", source_type: str = "", layer: str = "",
    limit: int = 10000, offset: int = 0,
    current_user=Depends(require_module_access("knowledge")),
    db: AsyncSession = Depends(get_db),
):
    """知识库内统一 RAG 物料列表；KnowledgeDocument 是唯一事实来源。"""
    if limit < 1 or limit > 10000 or offset < 0:
        raise HTTPException(status_code=422, detail="limit 必须在 1-10000，offset 不能为负数")
    access_filter = or_(KnowledgeBase.uid == current_user.uid,
                        KnowledgeBase.access_scope.in_(("shared", "public")))
    filters = [access_filter]
    if source_type:
        filters.append(KnowledgeDocument.source_type == source_type)
    if layer:
        if layer not in {"hot", "cold"}:
            raise HTTPException(status_code=422, detail="layer 只能是 hot 或 cold")
        filters.append(KnowledgeDocument.layer == layer)
    if q:
        needle = f"%{q.strip()}%"
        filters.append(or_(KnowledgeDocument.filename.ilike(needle), KnowledgeDocument.source_id.ilike(needle)))
    # 共享知识库由知识库模块权限保护；逐库 uid 过滤会破坏业务知识库共享语义。
    query = select(KnowledgeDocument).join(KnowledgeBase, KnowledgeBase.id == KnowledgeDocument.kb_id).where(*filters).order_by(KnowledgeDocument.updated_at.desc())
    total = int(await db.scalar(select(func.count(KnowledgeDocument.id)).join(KnowledgeBase, KnowledgeBase.id == KnowledgeDocument.kb_id).where(*filters)) or 0)
    rows = (await db.execute(query.offset(offset).limit(limit))).scalars().all()
    return {"items": [_material_summary(item) for item in rows], "total": total,
            "limit": limit, "offset": offset, "has_more": offset + len(rows) < total}


@knowledge.get("/materials/{document_id}")
async def get_material(
    document_id: str,
    current_user=Depends(require_module_access("knowledge")),
    db: AsyncSession = Depends(get_db),
):
    item = await db.scalar(select(KnowledgeDocument).join(KnowledgeBase, KnowledgeBase.id == KnowledgeDocument.kb_id).where(
        KnowledgeDocument.id == document_id,
        or_(KnowledgeBase.uid == current_user.uid, KnowledgeBase.access_scope.in_(("shared", "public"))),
    ))
    if item is None:
        raise HTTPException(status_code=404, detail="RAG 物料不存在")
    return item.to_dict(include_content=True)


@knowledge.get("/databases")
async def databases(current_user=Depends(require_module_access("knowledge")), db: AsyncSession = Depends(get_db)):
    return {"databases": await list_knowledge_bases(db, current_user.uid)}


@knowledge.get("/databases/accessible")
async def accessible_databases(current_user=Depends(require_module_access("knowledge")), db: AsyncSession = Depends(get_db)):
    """供 Agent 配置和工作区选择使用的用户可访问知识库列表。"""
    return {"databases": await list_knowledge_bases(db, current_user.uid)}


@knowledge.post("/databases")
async def create_database(payload: KnowledgeBaseIn, current_user=Depends(require_module_access("knowledge")), db: AsyncSession = Depends(get_db)):
    try:
        item = await create_knowledge_base(
            db, current_user.uid, payload.name, payload.description, payload.access_scope,
        )
        return item.to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@knowledge.get("/databases/{kb_id}")
async def database_detail(kb_id: str, current_user=Depends(require_module_access("knowledge")), db: AsyncSession = Depends(get_db)):
    try:
        item = await get_knowledge_base(db, current_user.uid, kb_id)
        documents = await list_documents(db, kb_id, current_user.uid)
        return {**item.to_dict(document_count=len(documents), chunk_count=sum(d["chunk_count"] for d in documents)),
                "documents": documents}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@knowledge.delete("/databases/{kb_id}")
async def remove_database(kb_id: str, current_user=Depends(require_module_access("knowledge")), db: AsyncSession = Depends(get_db)):
    try:
        await delete_knowledge_base(db, current_user.uid, kb_id)
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@knowledge.get("/databases/{kb_id}/documents")
async def documents(kb_id: str, current_user=Depends(require_module_access("knowledge")), db: AsyncSession = Depends(get_db)):
    try:
        return {"documents": await list_documents(db, kb_id, current_user.uid)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@knowledge.get("/databases/{kb_id}/repositories")
async def repositories(kb_id: str, current_user=Depends(require_module_access("knowledge")), db: AsyncSession = Depends(get_db)):
    try:
        return {"repositories": await list_code_repositories(db, current_user.uid, kb_id)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@knowledge.post("/databases/{kb_id}/repositories")
async def create_repository(kb_id: str, payload: CodeRepositoryIn, current_user=Depends(require_module_access("knowledge")), db: AsyncSession = Depends(get_db)):
    try:
        item = await create_code_repository(db, current_user.uid, kb_id, payload.model_dump())
        await db.commit()
        return item.to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@knowledge.put("/databases/{kb_id}/repositories/{repo_id}")
async def update_repository(kb_id: str, repo_id: str, payload: CodeRepositoryIn, current_user=Depends(require_module_access("knowledge")), db: AsyncSession = Depends(get_db)):
    try:
        data = payload.model_dump()
        item = await update_code_repository(db, current_user.uid, repo_id, data)
        if item.kb_id != kb_id:
            raise ValueError("代码仓库不属于当前知识库")
        await db.commit()
        return item.to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@knowledge.delete("/databases/{kb_id}/repositories/{repo_id}")
async def remove_repository(kb_id: str, repo_id: str, current_user=Depends(require_module_access("knowledge")), db: AsyncSession = Depends(get_db)):
    try:
        item = await db.get(CodeRepository, repo_id)
        if item is None or item.kb_id != kb_id:
            raise ValueError("代码仓库不存在或无权访问")
        await delete_code_repository(db, current_user.uid, repo_id)
        await db.commit()
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@knowledge.post("/databases/{kb_id}/repositories/{repo_id}/pull")
async def pull_repository(kb_id: str, repo_id: str, current_user=Depends(require_module_access("knowledge")), db: AsyncSession = Depends(get_db)):
    try:
        item = await db.get(CodeRepository, repo_id)
        if item is None or item.kb_id != kb_id:
            raise ValueError("代码仓库不存在或无权访问")
        result = await pull_code_repository(db, current_user.uid, repo_id)
        await db.commit()
        return result
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@knowledge.post("/databases/{kb_id}/documents/upload")
async def upload_document(kb_id: str, file: UploadFile = File(...), current_user=Depends(require_module_access("knowledge")), db: AsyncSession = Depends(get_db)):
    try:
        document = await add_document(db, current_user.uid, kb_id, file.filename or "document.txt", await file.read())
        return document.to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@knowledge.delete("/databases/{kb_id}/documents/{document_id}")
async def remove_document(kb_id: str, document_id: str, current_user=Depends(require_module_access("knowledge")), db: AsyncSession = Depends(get_db)):
    try:
        await delete_document(db, current_user.uid, kb_id, document_id)
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@knowledge.post("/databases/{kb_id}/query")
async def query_database(kb_id: str, payload: QueryIn, current_user=Depends(require_module_access("knowledge")), db: AsyncSession = Depends(get_db)):
    try:
        return await search(db, current_user.uid, kb_id, payload.query, payload.top_k)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
