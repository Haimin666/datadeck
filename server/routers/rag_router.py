"""RAG 知识库路由：文档录入、检索、连通性自检。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.deps import get_required_user

rag = APIRouter(prefix="/rag", tags=["rag"])


class DocumentIn(BaseModel):
    doc_id: str = Field(default="", description="文档唯一 ID，缺省自动生成")
    title: str = Field(min_length=1, max_length=256, description="文档标题")
    content: str = Field(min_length=1, description="文档正文")
    metadata: dict = Field(default_factory=dict, description="业务域等附加信息")


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=512)
    top_k: int = Field(default=5, ge=1, le=20)
    domain: str = Field(default="", description="业务域过滤，空=全部")


def _store():
    from datadeck.agents.toolkits import rag_store

    return rag_store


def _admin_check(user) -> None:
    if user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="需要管理员权限")


@rag.post("/documents")
async def add_document(
    payload: DocumentIn,
    current_user=Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """录入知识库文档（管理员）：切片 → embedding → Qdrant。"""
    _admin_check(current_user)
    doc_id = payload.doc_id or f"doc-{abs(hash(payload.title)) % 10**10}"
    result = _store().add_document(doc_id, payload.title, payload.content, payload.metadata)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "入库失败"))
    return result


@rag.post("/search")
async def search(payload: SearchIn, current_user=Depends(get_required_user)):
    """知识库混合检索（登录用户，可按业务域过滤）。"""
    return _store().search(payload.query, top_k=payload.top_k, domain=payload.domain or None)


@rag.delete("/documents/{doc_id}")
async def delete_document(doc_id: str, current_user=Depends(get_required_user)):
    """增量删除文档（管理员）：Qdrant + BM25 同步摘除。"""
    _admin_check(current_user)
    return _store().delete_document(doc_id)


@rag.get("/health")
async def rag_health():
    """RAG 组件连通性（embedding/Qdrant/BM25）。"""
    return _store().test_connection()
