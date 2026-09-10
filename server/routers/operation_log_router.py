"""操作日志 HTTP 适配层。"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.deps import get_admin_user
from server.services.operation_log_service import list_operation_logs

operation_logs = APIRouter(prefix="/system/operation-logs", tags=["operation-logs"])


@operation_logs.get("")
async def list_logs(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _admin=Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    return await list_operation_logs(db=db, limit=limit, offset=offset)
