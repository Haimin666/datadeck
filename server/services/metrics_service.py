"""监控统计层（阶段四 4.1）：自建 run_events/agent_runs 报表。

指标（LangSmith 替代，数据本地可查）：
- 运行：总数/成功率/失败率/平均耗时（P50/P95）
- 工具：调用频次/失败分布（run_events.tool_call 事件）
- 熔断器与缓存：进程内快照
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import text as sa_text

from server.db import async_session_factory
from server.utils.datetime_utils import utc_now


async def run_stats(hours: int = 24) -> dict:
    """近 N 小时运行统计。"""
    since = utc_now() - timedelta(hours=hours)  # 传 datetime 对象（asyncpg 不收 str）
    async with async_session_factory() as db:
        r = await db.execute(sa_text("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE status = 'completed') AS completed,
                COUNT(*) FILTER (WHERE status = 'failed') AS failed,
                COUNT(*) FILTER (WHERE status = 'cancelled') AS cancelled,
                COUNT(*) FILTER (WHERE status = 'interrupted') AS interrupted,
                AVG(EXTRACT(EPOCH FROM (finished_at - started_at))) AS avg_seconds
            FROM agent_runs
            WHERE created_at >= :since
        """), {"since": since})
        row = r.fetchone()
        total = row.total or 0
        completed = row.completed or 0
        failed = row.failed or 0

        # 耗时分位（completed runs）
        r2 = await db.execute(sa_text("""
            SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (finished_at - started_at))) AS p50,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (finished_at - started_at))) AS p95
            FROM agent_runs
            WHERE created_at >= :since AND status = 'completed' AND finished_at IS NOT NULL
        """), {"since": since})
        q = r2.fetchone()

        return {
            "window_hours": hours,
            "total": total,
            "completed": completed,
            "failed": failed,
            "cancelled": row.cancelled or 0,
            "interrupted": row.interrupted or 0,
            "success_rate": round(completed / total, 4) if total else None,
            "failure_rate": round(failed / total, 4) if total else None,
            "avg_seconds": round(float(row.avg_seconds), 2) if row.avg_seconds else None,
            "p50_seconds": round(float(q.p50), 2) if q and q.p50 is not None else None,
            "p95_seconds": round(float(q.p95), 2) if q and q.p95 is not None else None,
        }


async def tool_stats(hours: int = 24) -> dict:
    """工具调用频次与错误分布（基于 stream_event.tool_call + error 事件）。"""
    since = utc_now() - timedelta(hours=hours)  # 传 datetime 对象（asyncpg 不收 str）
    async with async_session_factory() as db:
        r = await db.execute(sa_text("""
            SELECT payload->'tool_call'->>'name' AS tool, COUNT(*) AS calls
            FROM run_events
            WHERE event_type = 'stream_event'
              AND payload->>'type' = 'tool_call'
              AND created_at >= :since
            GROUP BY 1 ORDER BY calls DESC
        """), {"since": since})
        by_tool = [{"tool": t, "calls": c} for t, c in r.fetchall()]

        r2 = await db.execute(sa_text("""
            SELECT payload->'error'->>'type' AS err_type, COUNT(*) AS cnt
            FROM run_events
            WHERE event_type = 'error' AND created_at >= :since
            GROUP BY 1 ORDER BY cnt DESC LIMIT 10
        """), {"since": since})
        errors = [{"type": t, "count": c} for t, c in r2.fetchall()]

        return {"window_hours": hours, "calls_by_tool": by_tool, "errors_by_type": errors}


def component_stats() -> dict:
    """熔断器 + 缓存进程内快照。"""
    from datadeck.agents.toolkits.cache import stats as cache_stats
    from datadeck.agents.toolkits.circuit_breaker import default_breaker

    return {
        "circuit_breakers": default_breaker.snapshot(),
        "caches": cache_stats(),
    }
