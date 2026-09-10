"""数据自检中间件（阶段三 3.2）：Self-Reflection 扩展。

在 sql_selfcheck（语法/只读校验）之上增加真实数据源对照：
- 表存在性：SQL 引用的表不存在于数据源 → 打回重写（jump_to=model）
- 打回封顶：重试 N 次仍失败 → 保留原文 + 警示标记（不阻塞链路）
对照来源：DATADECK_SQL_DSN 的 information_schema（PG 测试 / Doris 生产同理）。
DSN 不可用时静默放行（占位原则：跑不通不阻塞链路）。

协议细节（对齐 sql_selfcheck，已验证）：
- after_model/aafter_model 签名 (self, state, runtime)，返回 dict update 或 None
- 必须 @hook_config(can_jump_to=["model"])，否则 jump_to 被静默忽略
"""

from __future__ import annotations

import re
import time

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain.agents.middleware.types import AgentState
from langchain_core.messages import AIMessage

from datadeck import logger
from datadeck.agents.sql_guard import validate_sql
from datadeck.agents.toolkits.sql_executor import sql_dialect, sql_dsn

__all__ = [
    "DataSelfCheckMiddleware",
    "DataSelfCheckState",
    "create_data_selfcheck_middleware",
    "extract_sql",
    "check_sql_against_schema",
]

FEEDBACK_PREFIX = "[数据自检未通过]"
DEFAULT_MAX_REFLECT = 2
_SCHEMA_CACHE_TTL = 120.0  # schema map 进程级缓存，避免每轮查库

_SQL_FENCE_RE = re.compile(r"```sql\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)

_GAVE_UP_SUFFIX = "\n\n> ⚠️ 数据自检未通过：SQL 引用的表与真实数据源不符（已自动重试仍失败），请人工核对。"

_schema_cache: dict = {"map": None, "at": 0.0}


def extract_sql(text: str) -> str | None:
    m = _SQL_FENCE_RE.search(text)
    return m.group(1).strip() if m else None


def _fetch_schema_map(force: bool = False) -> dict[str, set[str]] | None:
    """同步 hook 不访问异步数据库；schema 查询由 ``_async_schema_map`` 完成。"""
    return None


def check_sql_against_schema(sql: str, schema_map: dict[str, set[str]]) -> list[str]:
    """表存在性校验，返回问题列表（中文、面向模型）。"""
    problems: list[str] = []
    check = validate_sql(sql, dialect=sql_dialect())
    if not check.ok:
        return [f"SQL 未通过只读校验: {'; '.join(i.message for i in check.issues)}"]
    for table in check.tables:
        if schema_map.get(table.lower()) is None:
            problems.append(
                f"表 `{table}` 在数据源中不存在，请先用 omd_get_table_schema 确认真实表名")
    return problems


class DataSelfCheckState(AgentState):
    data_retry_attempts: int


class DataSelfCheckMiddleware(AgentMiddleware):
    state_schema = DataSelfCheckState

    def __init__(self, max_reflect: int = DEFAULT_MAX_REFLECT) -> None:
        super().__init__()
        self._max_reflect = int(max_reflect)

    @hook_config(can_jump_to=["model"])
    def after_model(self, state, runtime):  # noqa: ARG002
        return self._check(dict(state))

    async def aafter_model(self, state, runtime):  # noqa: ARG002
        return await self._acheck(dict(state))

    async def _acheck(self, state: dict):
        """异步 middleware 路径，直接使用 asyncpg，不嵌套事件循环。"""
        messages = list(state.get("messages") or [])
        if not messages:
            return None
        content = getattr(messages[-1], "content", None)
        if not isinstance(content, str) or not content.strip():
            return None
        sql = extract_sql(content)
        if not sql:
            return {"data_retry_attempts": 0} if state.get("data_retry_attempts") else None
        schema_map = await self._async_schema_map()
        return self._check_with_schema(state, content, sql, schema_map)

    async def _async_schema_map(self) -> dict[str, set[str]] | None:
        if not sql_dsn():
            return None
        now = time.monotonic()
        if _schema_cache["map"] is not None and now - _schema_cache["at"] < _SCHEMA_CACHE_TTL:
            return _schema_cache["map"]
        try:
            import asyncpg
            conn = await asyncpg.connect(sql_dsn().replace("postgresql://", "postgres://", 1))
            try:
                rows = await conn.fetch(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema NOT IN ('pg_catalog','information_schema')")
            finally:
                await conn.close()
            schema_map: dict[str, set[str]] = {}
            for row in rows:
                schema_map.setdefault(row["table_name"].lower(), set()).add(row["column_name"].lower())
            _schema_cache.update({"map": schema_map, "at": now})
            return schema_map
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"数据自检：异步元数据获取失败，跳过 ({str(exc)[:120]})")
            return None

    def _check_with_schema(self, state: dict, content: str, sql: str,
                           schema_map: dict[str, set[str]] | None):
        if schema_map is None:
            return None
        attempts = int(state.get("data_retry_attempts") or 0)
        problems = check_sql_against_schema(sql, schema_map)
        if not problems:
            return {"data_retry_attempts": 0}
        if attempts >= self._max_reflect:
            return {"data_retry_attempts": 0, "messages": [AIMessage(content=content + _GAVE_UP_SUFFIX)]}
        body = "\n".join(f"- {p}" for p in problems)
        return {"jump_to": "model", "data_retry_attempts": attempts + 1,
                "messages": [AIMessage(content=f"{FEEDBACK_PREFIX} SQL 与真实数据源不符，请修正后重新作答：\n{body}")]}

    def _check(self, state: dict):
        messages = list(state.get("messages") or [])
        if not messages:
            return None
        content = getattr(messages[-1], "content", None)
        if not isinstance(content, str) or not content.strip():
            return None

        sql = extract_sql(content)
        if not sql:
            # 无 ```sql``` 块：非 SQL 回答路径，直通并复位计数
            update: dict = {}
            if state.get("data_retry_attempts"):
                update["data_retry_attempts"] = 0
            return update or None

        attempts = int(state.get("data_retry_attempts") or 0)
        schema_map = _fetch_schema_map()
        if schema_map is None:
            return None  # 元数据不可用：静默放行（不阻塞链路）

        problems = check_sql_against_schema(sql, schema_map)
        if not problems:
            update = {"data_retry_attempts": 0}
            return update

        if attempts >= self._max_reflect:
            logger.warning("data_selfcheck: 重试 %d 次仍未通过，保留原文并标记警示", attempts)
            return {
                "data_retry_attempts": 0,
                "messages": [AIMessage(content=content + _GAVE_UP_SUFFIX)],
            }

        body = "\n".join(f"- {p}" for p in problems)
        feedback = AIMessage(content=(
            f"{FEEDBACK_PREFIX} SQL 与真实数据源不符，请修正后重新作答：\n{body}"))
        return {
            "jump_to": "model",
            "data_retry_attempts": attempts + 1,
            "messages": [feedback],
        }


def create_data_selfcheck_middleware(context=None) -> AgentMiddleware | None:
    enabled = bool(getattr(context, "data_selfcheck_enabled", True)) if context else True
    if not enabled or not sql_dsn():
        return None
    return DataSelfCheckMiddleware()
