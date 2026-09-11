"""指标注册表（阶段四 4.4，Ossie 指标规范适配）：口径统一 + 别名消解。

表 metric_registry：
- canonical_name: 官方指标名（如 M1逾期率）
- aliases: 别名 JSON（"不良率"、"首逾率"…）→ 用户口语统一映射到官方名
- definition/formula/unit/owner: 口径四要素（Ossie 语义）
- domain: 业务域

链路：用户问"不良率"→ resolve_alias → "M1逾期率" → rag_search/SQL 都用官方名。
"""

from __future__ import annotations

from sqlalchemy import select

from server.db import async_session_factory
from server.models import MetricRegistry


async def resolve_alias(name: str) -> str:
    """别名消解：命中别名/官方名返回官方名，否则原样返回。"""
    async with async_session_factory() as db:
        result = await db.execute(select(MetricRegistry).where(
            MetricRegistry.status == "approved"))
        wanted = str(name or "").strip().casefold()
        for item in result.scalars().all():
            names = [item.canonical_name, item.ossie_name, *(item.aliases or [])]
            if any(str(value or "").strip().casefold() == wanted for value in names):
                return item.canonical_name
    return name


async def get_metric(canonical_name: str) -> dict | None:
    async with async_session_factory() as db:
        from sqlalchemy import text as sa_text

        r = await db.execute(sa_text(
            "SELECT canonical_name, aliases, definition, formula, unit, owner, domain, "
            "ossie_name, ossie_expression, datatype, ai_context, source_evidence, status "
            "FROM metric_registry WHERE canonical_name ILIKE :n AND status='approved' LIMIT 1"),
            {"n": canonical_name})
        row = r.fetchone()
    if not row:
        return None
    return {
        "name": row[0], "aliases": row[1] or [], "definition": row[2],
        "formula": row[3], "unit": row[4], "owner": row[5], "domain": row[6],
        "ossie_name": row[7], "ossie_expression": row[8], "datatype": row[9],
        "ai_context": row[10] or {}, "source_evidence": row[11] or [], "status": row[12],
    }


async def search_metrics(query: str, limit: int = 5) -> list[dict]:
    """按官方名、别名和定义检索已审核指标，供 DataAgent 做口径识别。"""
    needle = f"%{str(query or '').strip()}%"
    if needle == "%%":
        return []
    async with async_session_factory() as db:
        result = await db.execute(select(MetricRegistry).where(MetricRegistry.status == "approved"))
        items = result.scalars().all()
    matches = []
    query_text = str(query or "").strip().casefold()
    for item in items:
        names = [item.canonical_name, item.ossie_name, *(item.aliases or [])]
        haystack = " ".join(str(value or "") for value in [*names, item.definition]).casefold()
        if query_text in haystack:
            matches.append(item.to_dict())
    return matches[:min(max(int(limit), 1), 20)]
