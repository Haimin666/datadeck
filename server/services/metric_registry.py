"""指标注册表（阶段四 4.4，Ossie 指标规范适配）：口径统一 + 别名消解。

表 metric_registry：
- canonical_name: 官方指标名（如 M1逾期率）
- aliases: 别名 JSON（"不良率"、"首逾率"…）→ 用户口语统一映射到官方名
- definition/formula/unit/owner: 口径四要素（Ossie 语义）
- domain: 业务域

链路：用户问"不良率"→ resolve_alias → "M1逾期率" → rag_search/SQL 都用官方名。
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Index, Integer, JSON, String, Text

from server.db import async_session_factory
from server.models import Base
from server.utils.datetime_utils import utc_now


class MetricRegistry(Base):
    __tablename__ = "metric_registry"
    __table_args__ = (Index("ix_metric_registry_domain", "domain"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    canonical_name = Column(String(128), nullable=False, unique=True)
    aliases = Column(JSON, nullable=False, default=list)
    definition = Column(Text, nullable=False)   # 口径定义
    formula = Column(Text, nullable=True)        # 计算公式
    unit = Column(String(32), nullable=True)     # 单位
    owner = Column(String(64), nullable=True)    # 归属团队
    domain = Column(String(64), nullable=True)   # 业务域
    created_at = Column(DateTime, default=utc_now, nullable=False)


async def resolve_alias(name: str) -> str:
    """别名消解：命中别名/官方名返回官方名，否则原样返回。"""
    async with async_session_factory() as db:
        from sqlalchemy import text as sa_text

        r = await db.execute(sa_text(
            "SELECT canonical_name FROM metric_registry WHERE canonical_name ILIKE :n "
            "OR :n = ANY(SELECT json_array_elements_text(aliases)) LIMIT 1"),
            {"n": name})
        row = r.fetchone()
    return row[0] if row else name


async def get_metric(canonical_name: str) -> dict | None:
    async with async_session_factory() as db:
        from sqlalchemy import text as sa_text

        r = await db.execute(sa_text(
            "SELECT canonical_name, aliases, definition, formula, unit, owner, domain "
            "FROM metric_registry WHERE canonical_name ILIKE :n LIMIT 1"),
            {"n": canonical_name})
        row = r.fetchone()
    if not row:
        return None
    return {
        "name": row[0], "aliases": row[1] or [], "definition": row[2],
        "formula": row[3], "unit": row[4], "owner": row[5], "domain": row[6],
    }
