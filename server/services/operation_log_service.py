from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from server.models import OperationLog


async def log_operation(
    db: AsyncSession,
    user_id: int | None,
    operation: str,
    details: str | None = None,
    request: Request | None = None,
) -> None:
    ip_address = request.client.host if request and request.client else None
    db.add(
        OperationLog(
            user_id=user_id,
            operation=operation,
            details=details,
            ip_address=ip_address,
        )
    )
    await db.flush()


async def list_operation_logs(
    *,
    db: AsyncSession,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    from sqlalchemy import func, select

    rows = await db.execute(
        select(OperationLog).order_by(OperationLog.id.desc()).offset(offset).limit(limit)
    )
    total = await db.scalar(select(func.count(OperationLog.id)))
    return {
        "items": [item.to_dict() for item in rows.scalars().all()],
        "total": int(total or 0),
        "limit": limit,
        "offset": offset,
    }
