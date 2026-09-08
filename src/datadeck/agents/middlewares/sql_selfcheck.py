"""SqlSelfCheckMiddleware：确定性 SQL 自检/反思中间件（Phase 1 程序化 self-reflection）。

织入点 after_model：
- 开关关闭 / 回答无 SQL → 零成本直通（知识问答路径不受影响）
- 回答含 SQL → 跑确定性校验器（sqlglot，毫秒级，无 LLM）
  - 通过 → 记录验证结果到 state（供宿主/前端展示）
  - 失败 → 注入结构化反馈消息 + jump_to="model" 打回重写（次数封顶）
- 封顶后放行：保留模型原始 SQL 并追加警示，state 标记 gave_up（预览卡显示"未通过静态校验"）

关键协议细节（已用探针验证，勿凭直觉改动）：
- 必须声明 @hook_config(can_jump_to=["model"])，否则框架静默忽略跳转
- 打回预算记在结构化 state（sql_retry_attempts），按"本轮用户消息"切轮重置
- 控制语义一律走 state 字段，禁止写入自由文本再 regex 回读（注入面）
"""

from __future__ import annotations

import re

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain.agents.middleware.types import AgentState
from langchain_core.messages import AIMessage

from datadeck import logger
from datadeck.agents.sql_guard import validate_sql

__all__ = [
    "SQL_FEEDBACK_PREFIX",
    "SqlSelfCheckMiddleware",
    "SqlSelfCheckState",
    "create_sql_selfcheck_middleware",
    "extract_first_sql",
    "has_sql_content",
    "sql_feedback_message",
]

SQL_FEEDBACK_PREFIX = "[SQL 校验未通过]"
# 仅认 ```sql 标记的代码块（```json 等其他围栏不算 SQL）
SQL_FENCE_RE = re.compile(r"```sql\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
# 裸语句：关键词后至少 4 个字符（能否算 SQL 由"能否解析"门控决定）
_BARE_SQL_RE = re.compile(r"\b(?:SELECT|WITH)\b[\s\S]{4,}", re.IGNORECASE)

PARSE_FAIL_CODES = frozenset({"SQL_PARSE_ERROR", "SQL_PARSE_EMPTY"})

DEFAULT_MAX_REFLECT = 2

_GAVE_UP_SUFFIX = (
    "\n\n> ⚠️ 该 SQL 未通过只读静态校验（已自动重试仍失败），请人工核对后再使用。"
)


def extract_first_sql(text: str) -> str | None:
    """提取回答中的 SQL 候选。

    双通道（防误伤也防漏检，parse 门控保证散文提及 SELECT 不算 SQL）：
    - 优先 ```sql 代码块（约定产物形态）
    - 其次裸 SELECT/WITH 语句——还须该片段能被解析（散文后半句混入会解析失败→不算 SQL）
    """
    if not text:
        return None
    for fence in SQL_FENCE_RE.finditer(text):
        inner = fence.group(1).strip()
        if inner:
            return inner
    bare = _BARE_SQL_RE.search(text)
    if not bare:
        return None
    candidate = bare.group(0).strip()
    result = validate_sql(candidate, dialect="doris")
    if result.issues and result.issues[0].code in PARSE_FAIL_CODES:
        return None
    return candidate


def has_sql_content(text: str) -> bool:
    """回答是否包含 SQL 内容（= 能提取出 SQL 候选）。"""
    return extract_first_sql(text) is not None


def sql_feedback_message(issues: list[dict], attempt: int, max_attempts: int) -> str:
    """把校验 issues 转成模型可读的结构化反馈消息。"""
    lines = [
        f"{SQL_FEEDBACK_PREFIX} 第 {attempt}/{max_attempts} 次生成未通过只读校验，"
        "请修正后重新给出完整 SQL。",
        "修正要求：",
        "1. 只允许单条只读 SELECT 查询（含 UNION/CTE），禁止 INSERT/UPDATE/DELETE/DDL/多语句；",
        "2. 逐条解决下列问题：",
    ]
    for item in issues:
        lines.append(f"   - [{item['code']}] {item['message']}")
    lines.append("把修正后的完整 SQL 放在 ```sql 代码块中。")
    return "\n".join(lines)
class SqlSelfCheckState(AgentState):
    """self-check 结构化状态（控制语义只走这些字段，不进自由文本）。"""

    sql_retry_attempts: int      # 当前轮已打回次数（同轮重置）
    sql_turn_base: int           # 本轮消息基线索引（切轮检测用）
    sql_validation: dict | None  # 最终验证状态 {status, attempts, tables, issues, dialect}


class SqlSelfCheckMiddleware(AgentMiddleware):
    state_schema = SqlSelfCheckState

    def __init__(
        self,
        *,
        dialect: str = "doris",
        max_reflect: int = DEFAULT_MAX_REFLECT,
        max_tables: int = 8,
        max_joins: int = 6,
        enabled: bool = True,
    ) -> None:
        super().__init__()
        self._dialect = dialect
        self._max_reflect = int(max_reflect)
        self._max_tables = int(max_tables)
        self._max_joins = int(max_joins)
        self._enabled = bool(enabled)

    @hook_config(can_jump_to=["model"])  # 不声明则框架静默忽略 jump_to
    def after_model(self, state, runtime):  # noqa: ARG002
        return self._check(dict(state))

    async def aafter_model(self, state, runtime):  # noqa: ARG002
        return self._check(dict(state))

    def _check(self, state: dict):
        """一次性判定：直通 / 通过 / 打回 / 封顶放行。"""
        if not self._enabled:
            return None
        messages = list(state.get("messages") or [])
        if not messages:
            return None

        attempts = int(state.get("sql_retry_attempts") or 0)
        last = messages[-1]
        content = getattr(last, "content", None)
        if not isinstance(content, str):
            return None

        if not has_sql_content(content):
            update: dict = {"sql_validation": {"status": "skipped_no_sql", "attempts": 0}}
            if attempts:
                update["sql_retry_attempts"] = 0
            return update

        sql = extract_first_sql(content)
        result = validate_sql(
            sql, dialect=self._dialect,
            max_tables=self._max_tables, max_joins=self._max_joins,
        )

        if result.ok:
            payload = result.to_payload()
            payload["status"] = "passed"
            payload["attempts"] = attempts
            update = {"sql_validation": payload}
            if attempts:
                update["sql_retry_attempts"] = 0
            return update

        if attempts >= self._max_reflect:
            logger.warning("sql_selfcheck: 重试 %d 次仍未通过，保留最近版本并标记警示", attempts)
            return {
                "sql_retry_attempts": 0,
                "sql_validation": {
                    "status": "gave_up",
                    "attempts": attempts,
                    "issues": [i.to_payload() for i in result.issues],
                    "dialect": result.dialect,
                },
                # best-keep：保留模型原始 SQL，仅追加警示（系统补写，非模型生成）
                "messages": [AIMessage(content=content + _GAVE_UP_SUFFIX)],
            }

        feedback = sql_feedback_message(
            [i.to_payload() for i in result.issues], attempts + 1, self._max_reflect)
        return {
            "jump_to": "model",
            "sql_retry_attempts": attempts + 1,
            "messages": [AIMessage(content=feedback)],
        }


def create_sql_selfcheck_middleware(context) -> SqlSelfCheckMiddleware | None:
    """按 context 配置构建 middleware（未启用返回 None）。"""
    if not bool(getattr(context, "sql_guard_enabled", False)):
        return None
    return SqlSelfCheckMiddleware(
        dialect=str(getattr(context, "sql_dialect", "doris") or "doris"),
        max_reflect=int(getattr(context, "sql_max_reflect", DEFAULT_MAX_REFLECT)),
        max_tables=int(getattr(context, "sql_max_tables", 8)),
        max_joins=int(getattr(context, "sql_max_joins", 6)),
        enabled=True,
    )

