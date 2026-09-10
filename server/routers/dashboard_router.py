"""Dashboard 管理端统计（阶段 P0-7：对齐前端 DashboardView 全套消费）。"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from server.db import get_db
from server.deps import get_required_user
from server.models import User
from server.utils.datetime_utils import utc_now_naive

dashboard = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _require_admin(user: User) -> None:
    if user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="需要管理员权限")


async def _fetch_one(db: AsyncSession, sql: str, params: dict | None = None):
    r = await db.execute(sa_text(sql), params or {})
    row = r.fetchone()
    return row[0] if row else None


@dashboard.get("/stats")
async def get_stats(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """总览卡：对齐前端 StatsOverviewComponent 字段。"""
    _require_admin(current_user)
    total_users = await _fetch_one(db, "SELECT COUNT(*) FROM users WHERE is_deleted=0")
    total_threads = await _fetch_one(db, "SELECT COUNT(*) FROM threads")
    total_runs = await _fetch_one(db, "SELECT COUNT(*) FROM agent_runs")
    total_feedbacks = await _fetch_one(db, "SELECT COUNT(*) FROM message_feedback")

    # 近 7 天环比（对话=thread 维度）
    week_ago = (utc_now_naive() - timedelta(days=7))
    prev_week = (utc_now_naive() - timedelta(days=14))
    recent = await _fetch_one(
        db, "SELECT COUNT(*) FROM threads WHERE created_at >= :w", {"w": week_ago})
    prev = await _fetch_one(
        db, "SELECT COUNT(*) FROM threads WHERE created_at >= :p AND created_at < :w",
        {"p": prev_week, "w": week_ago})
    trend = None
    if prev and recent is not None:
        trend = round(100 * (recent - prev) / prev, 1) if prev else None

    return {
        "total_conversations": total_threads,
        "total_messages": total_runs,
        "total_users": total_users,
        "total_threads": total_threads,
        "total_runs": total_runs,
        "total_feedbacks": total_feedbacks,
        "conversation_trend": trend,
        "feedback_stats": {"total_feedbacks": total_feedbacks},
    }


@dashboard.get("/stats/users")
async def get_user_stats(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """用户活跃统计（近 30 天登录/新增 + 角色分布）。"""
    _require_admin(current_user)
    r = await db.execute(sa_text(
        "SELECT role, COUNT(*) FROM users WHERE is_deleted=0 GROUP BY role ORDER BY 2 DESC"))
    role_dist = [{"role": row[0], "count": row[1]} for row in r.fetchall()]
    month_ago = (utc_now_naive() - timedelta(days=30))
    active = await _fetch_one(
        db, "SELECT COUNT(*) FROM users WHERE is_deleted=0 AND last_login >= :m", {"m": month_ago})
    new_users = await _fetch_one(
        db, "SELECT COUNT(*) FROM users WHERE is_deleted=0 AND created_at >= :m", {"m": month_ago})
    return {
        "total": sum(x["count"] for x in role_dist),
        "active_30d": active or 0,
        "new_30d": new_users or 0,
        "role_distribution": role_dist,
    }


@dashboard.get("/stats/tools")
async def get_tool_stats(
    hours: int = Query(24 * 7, ge=1, le=24 * 90),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """工具调用统计（复用 metrics_service 口径，按 tool_call 事件聚合）。"""
    from server.services.metrics_service import tool_stats

    _require_admin(current_user)
    data = await tool_stats(hours=hours)
    return {
        "calls_by_tool": data["calls_by_tool"],
        "errors_by_type": data["errors_by_type"],
        "window_hours": hours,
    }


@dashboard.get("/stats/agents")
async def get_agent_stats(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """Agent 维度运行量/成功率。"""
    _require_admin(current_user)
    r = await db.execute(sa_text("""
        SELECT agent_slug,
               COUNT(*) AS total,
               COUNT(*) FILTER (WHERE status='completed') AS completed,
               COUNT(*) FILTER (WHERE status='failed') AS failed
        FROM agent_runs GROUP BY agent_slug ORDER BY total DESC
    """))
    agents = [
        {"agent_slug": row[0], "total": row[1], "completed": row[2],
         "failed": row[3], "success_rate": round(row[2] / row[1], 4) if row[1] else None}
        for row in r.fetchall()
    ]
    return {"agents": agents}


@dashboard.get("/stats/threads")
async def get_thread_stats(
    time_range: str = Query("30days", pattern="^(7days|14days|30days|90days)$"),
    agent_id: str = Query(""),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """线程趋势：按天新增 thread / run 数（threads Tab 消费）。"""
    _require_admin(current_user)
    days = {"7days": 7, "14days": 14, "30days": 30, "90days": 90}[time_range]
    since = utc_now_naive() - timedelta(days=days)
    params: dict = {"since": since}
    agent_cond = ""
    if agent_id:
        agent_cond = " AND agent_id=:aid"
        params["aid"] = agent_id
    r = await db.execute(sa_text(f"""
        SELECT to_char(created_at, 'YYYY-MM-DD') AS day, COUNT(*)
        FROM threads WHERE created_at >= :since{agent_cond}
        GROUP BY day ORDER BY day
    """), params)
    return {"time_range": time_range,
            "daily_new": [{"date": d, "count": c} for d, c in r.fetchall()]}


@dashboard.get("/stats/knowledge")
async def get_knowledge_stats(
    current_user: User = Depends(get_required_user),
):
    """知识库统计（datadeck RAG 口径：文档/分块/组件健康）。"""
    _require_admin(current_user)
    from datadeck.agents.toolkits import rag_store
    from datadeck.agents.toolkits.cache import stats as cache_stats

    health = rag_store.test_connection()
    return {
        "documents_indexed": len({v.get("doc_id") for v in rag_store._chunk_store.values()}),
        "chunks": health.get("checks", {}).get("bm25", {}).get("chunks", 0),
        "components": health.get("checks", {}),
        "caches": cache_stats(),
    }


@dashboard.get("/feedbacks")
async def get_feedbacks(
    rating: str = Query(""),
    agent_id: str = Query(""),
    limit: int = Query(100, ge=1, le=500),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """反馈明细列表。"""
    _require_admin(current_user)
    conds, params = ["1=1"], {"limit": limit}
    if rating:
        conds.append("f.rating = :rating")
        params["rating"] = rating
    r = await db.execute(sa_text(f"""
        SELECT f.id, f.run_id, f.message_id, f.rating, f.reason, f.uid, f.created_at,
               ar.agent_slug
        FROM message_feedback f
        LEFT JOIN agent_runs ar ON ar.id = f.run_id
        WHERE {' AND '.join(conds)}
        ORDER BY f.created_at DESC LIMIT :limit
    """), params)
    return [
        {"id": row[0], "run_id": row[1], "message_id": row[2], "rating": row[3],
         "reason": row[4], "uid": row[5], "created_at": str(row[6]),
         "agent_slug": row[7] or ""}
        for row in r.fetchall()
    ]


@dashboard.get("/conversations")
async def get_conversations(
    uid: str = Query(""),
    agent_id: str = Query(""),
    status: str = Query(""),
    search: str = Query(""),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """全站对话列表（管理员审计视角，分页）。"""
    _require_admin(current_user)
    conds, params = ["1=1"], {"limit": limit, "offset": offset}
    if uid:
        conds.append("t.uid = :uid"); params["uid"] = uid
    if agent_id:
        conds.append("t.agent_id = :aid"); params["aid"] = agent_id
    if search:
        conds.append("t.title ILIKE :q"); params["q"] = f"%{search}%"
    where = " AND ".join(conds)
    rows = await db.execute(sa_text(f"""
        SELECT t.id, t.uid, t.agent_id, t.title, t.is_pinned, t.created_at, t.updated_at,
               (SELECT COUNT(*) FROM agent_runs r WHERE r.thread_id = t.id) AS run_count,
               (SELECT MAX(r.status) FROM agent_runs r WHERE r.thread_id = t.id) AS last_status
        FROM threads t WHERE {where}
        ORDER BY t.updated_at DESC LIMIT :limit OFFSET :offset
    """), params)
    total = await _fetch_one(db, f"SELECT COUNT(*) FROM threads t WHERE {where}", params)
    return {
        "items": [
            {"id": r[0], "uid": r[1], "agent_id": r[2], "title": r[3], "is_pinned": r[4],
             "created_at": str(r[5]), "updated_at": str(r[6]),
             "run_count": r[7], "last_status": r[8]}
            for r in rows.fetchall()
        ],
        "total": total or 0, "limit": limit, "offset": offset,
    }


@dashboard.get("/conversations/options")
async def get_conversation_options(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """对话过滤下拉：用户/Agent 选项。"""
    _require_admin(current_user)
    users = await db.execute(sa_text(
        "SELECT DISTINCT uid FROM threads ORDER BY uid LIMIT 200"))
    agents_r = await db.execute(sa_text(
        "SELECT DISTINCT agent_id FROM threads ORDER BY agent_id LIMIT 200"))
    return {
        "users": [u[0] for u in users.fetchall()],
        "agents": [a[0] for a in agents_r.fetchall()],
    }


@dashboard.get("/conversations/{thread_id}")
async def get_conversation_detail(
    thread_id: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """对话明细：线程 + run 列表（审计视角）。"""
    _require_admin(current_user)
    t = await db.execute(sa_text(
        "SELECT id, uid, agent_id, title, is_pinned, created_at, updated_at "
        "FROM threads WHERE id=:tid"), {"tid": thread_id})
    row = t.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="对话不存在")
    runs = await db.execute(sa_text(
        "SELECT id, status, agent_slug, created_at, finished_at, error_type "
        "FROM agent_runs WHERE thread_id=:tid ORDER BY created_at DESC LIMIT 100"),
        {"tid": thread_id})
    return {
        "thread": {"id": row[0], "uid": row[1], "agent_id": row[2], "title": row[3],
                    "is_pinned": row[4], "created_at": str(row[5]), "updated_at": str(row[6])},
        "runs": [
            {"id": r[0], "status": r[1], "agent_slug": r[2], "created_at": str(r[3]),
             "finished_at": str(r[4]) if r[4] else None, "error_type": r[5]}
            for r in runs.fetchall()
        ],
    }


@dashboard.get("/users")
async def get_dashboard_users(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """用户列表（保留旧入口）。"""
    _require_admin(current_user)
    r = await db.execute(sa_text(
        "SELECT id, username, uid, role, avatar, domain, last_login, is_deleted "
        "FROM users WHERE is_deleted=0 ORDER BY id"))
    return {"users": [
        {"id": x[0], "username": x[1], "uid": x[2], "role": x[3], "avatar": x[4],
         "domain": x[5], "last_login": str(x[6]) if x[6] else None}
        for x in r.fetchall()
    ]}


@dashboard.get("/stats/calls/timeseries")
async def get_call_timeseries(
    type: str = Query("models", pattern="^(models|agents|tokens|tools)$"),
    time_range: str = Query("14days", pattern="^(14hours|14days|14weeks)$"),
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """调用时间序列（前端 CallStatsComponent 契约）。

    返回 {data:[{date, data:{<category>:count}}], categories:[...], agent_names:{}}。
    datadeck 单 agent 栈：按 agent_slug 聚合 agent_runs（models/tokens 无独立维度时同样
    以 agent_slug 呈现，保证前端图表可用）。
    """
    _require_admin(current_user)
    if time_range == "14hours":
        unit, step, count = "hour", "1 hour", 14
    elif time_range == "14weeks":
        unit, step, count = "week", "1 week", 14
    else:
        unit, step, count = "day", "1 day", 14

    fmt = {"hour": "YYYY-MM-DD HH24:00", "day": "YYYY-MM-DD", "week": "IYYY-IW"}[unit]
    since = utc_now_naive() - timedelta(
        hours=count - 1 if unit == "hour" else 0,
        days=(count - 1) if unit == "day" else (7 * (count - 1) if unit == "week" else 0),
    )
    if unit == "week":
        since = since - timedelta(days=since.weekday())

    status_cond = " AND status='failed'" if type == "tools" else ""
    r = await db.execute(sa_text(f"""
        SELECT to_char(d.bucket, :fmt) AS date,
               COALESCE(b.category, 'default-chatbot') AS category,
               COALESCE(b.cnt, 0) AS count
        FROM generate_series(date_trunc(:unit, CAST(:since AS timestamp)),
                             date_trunc(:unit, CURRENT_TIMESTAMP), interval '{step}') AS d(bucket)
        LEFT JOIN (
            SELECT date_trunc(:unit, created_at) AS bucket, agent_slug AS category, COUNT(*) AS cnt
            FROM agent_runs WHERE created_at >= :since{status_cond}
            GROUP BY bucket, category
        ) b ON b.bucket = d.bucket
        ORDER BY d.bucket
    """), {"fmt": fmt, "unit": unit, "since": since})

    categories: list[str] = []
    buckets: dict[str, dict] = {}
    order: list[str] = []
    for date, category, cnt in r.fetchall():
        if date not in buckets:
            buckets[date] = {}
            order.append(date)
        buckets[date][category] = int(cnt or 0)
        if category not in categories:
            categories.append(category)

    return {
        "data": [{"date": d, "data": buckets[d]} for d in order],
        "categories": categories,
        "agent_names": {c: c for c in categories},
        "type": type,
        "time_range": time_range,
    }
