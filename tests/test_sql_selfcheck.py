"""SqlSelfCheckMiddleware 测试：单元（helper）+ 链路级端到端（脚本化假模型）。"""

import pytest
from langchain.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from datadeck.adapters.checkpointer import MemoryCheckpointerProvider
from datadeck.adapters.model_provider import EnvModelProvider
from datadeck.agents.buildin.chatbot import ChatbotAgent
from datadeck.agents.middlewares.sql_selfcheck import (
    SQL_FEEDBACK_PREFIX,
    extract_first_sql,
    has_sql_content,
    sql_feedback_message,
)

# ── 脚本化假模型（模块级计数器，规避 pydantic 字段拦截）──────────────
SCRIPTS: dict[str, list[str]] = {"lines": ["ok"]}
CALLS = {"n": 0}


class ScriptedModel(BaseChatModel):
    @property
    def _llm_type(self):
        return "fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        n = CALLS["n"]
        CALLS["n"] += 1
        lines = SCRIPTS["lines"]
        content = lines[n] if n < len(lines) else lines[-1]
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])

    def bind_tools(self, tools, **kwargs):
        return self


@pytest.fixture(autouse=True)
def _reset_script():
    CALLS["n"] = 0
    SCRIPTS["lines"] = ["ok"]
    yield


def _make_agent() -> ChatbotAgent:
    import datadeck.agents.buildin.chatbot.graph as graph_mod

    graph_mod.load_chat_model = lambda *a, **k: ScriptedModel()
    return ChatbotAgent(
        model_provider=EnvModelProvider(),
        memory_store=None,
        checkpointer_provider=MemoryCheckpointerProvider(),
    )


async def _run(agent: ChatbotAgent, text: str = "查一下用户数", thread_id: str = "t1"):
    return await agent.invoke_messages(
        [{"role": "user", "content": text}],
        input_context={"uid": "u1", "thread_id": thread_id, "sql_guard_enabled": True},
    )


# ── helper 单元测试 ──────────────────────────────────────────────


class TestHelpers:
    def test_extract_from_sql_fence(self):
        assert extract_first_sql("```sql\nSELECT 1```") == "SELECT 1"

    def test_extract_fence_case_insensitive(self):
        assert extract_first_sql("结果如下 ```SQL\n SELECT 1 ``` 完毕") == "SELECT 1"

    def test_extract_bare_select(self):
        assert extract_first_sql("SELECT 1 FROM t") == "SELECT 1 FROM t"

    def test_extract_none_for_plain_text(self):
        assert extract_first_sql("普通中文回答，没有代码") is None

    def test_extract_empty_fence_is_none(self):
        assert extract_first_sql("```sql\n```") is None

    def test_has_sql_content(self):
        assert has_sql_content("```sql\nSELECT 1```")
        assert has_sql_content("SELECT * FROM t")
        assert not has_sql_content("你好")
        assert not has_sql_content("```json\n{\"a\": 1}```")

    def test_feedback_message_shape(self):
        msg = sql_feedback_message([{"code": "SQL_PARSE_ERROR", "message": "解析失败"}], 1, 2)
        assert msg.startswith(SQL_FEEDBACK_PREFIX)
        assert "SQL_PARSE_ERROR" in msg
        assert "1/2" in msg


# ── 链路级端到端（真实 create_agent 图 + middleware 链）──────────────


class TestEndToEnd:
    async def test_bounce_then_pass_then_answer(self):
        SCRIPTS["lines"] = ["```sql\nSELEC bad```", "```sql\nSELECT 1 AS ok```"]
        agent = _make_agent()
        r = await _run(agent)
        assert CALLS["n"] == 2, f"坏SQL打回1次+好SQL通过即终态 = 2 次调用，实际 {CALLS['n']}"
        msgs = r["messages"]
        feedbacks = [m for m in msgs if SQL_FEEDBACK_PREFIX in str(m.content)]
        assert len(feedbacks) == 1, "应恰好注入一条校验反馈"
        assert r["sql_validation"]["status"] == "passed"
        assert r["sql_validation"]["attempts"] == 1
        assert "SELECT 1 AS ok" in str(msgs[-1].content)

    async def test_always_bad_capped_with_warning(self):
        SCRIPTS["lines"] = ["```sql\nSELEC bad```"]
        agent = _make_agent()
        r = await _run(agent)
        assert CALLS["n"] == 3, f"初始1次+打回2次 = 3 次调用（封顶），实际 {CALLS['n']}"
        final = str(r["messages"][-1].content)
        assert "SELEC bad" in final, "最终消息应保留模型原始 SQL"
        assert "未通过" in final, "封顶后应在最终消息上追加警示"
        assert r["sql_validation"]["status"] == "gave_up"
        assert r["sql_validation"]["attempts"] == 2

    async def test_disabled_passthrough(self):
        SCRIPTS["lines"] = ["```sql\nSELEC bad```"]
        agent = _make_agent()
        r = await agent.invoke_messages(
            [{"role": "user", "content": "q"}],
            input_context={"uid": "u1", "thread_id": "t1", "sql_guard_enabled": False},
        )
        assert CALLS["n"] == 1, "关闭守卫时应只调用一次模型"
        assert "sql_validation" not in r
        assert str(r["messages"][-1].content) == "```sql\nSELEC bad```"

    async def test_no_sql_single_call(self):
        SCRIPTS["lines"] = ["知识问答的直接回答"]
        agent = _make_agent()
        r = await _run(agent)
        assert CALLS["n"] == 1
        assert r["sql_validation"]["status"] == "skipped_no_sql"

    async def test_valid_sql_single_call(self):
        SCRIPTS["lines"] = ["```sql\nSELECT 1 AS ok```"]
        agent = _make_agent()
        r = await _run(agent)
        assert CALLS["n"] == 1, "合法 SQL 不应打回"
        assert r["sql_validation"]["status"] == "passed"
        assert r["sql_validation"]["tables"] == []

    async def test_budget_resets_on_new_turn(self):
        SCRIPTS["lines"] = ["```sql\nSELEC bad```", "```sql\nSELECT 1 AS ok```",
                            "```sql\nSELEC worse```", "```sql\nSELECT 2 AS ok```"]
        agent = _make_agent()
        await _run(agent, text="第一问", thread_id="t9")
        r2 = await _run(agent, text="第二问", thread_id="t9")
        # 第二问再次经历 坏→好：打回预算应重置（否则 attempts=1 只能打回一次）
        assert CALLS["n"] == 4, f"两问各 2 次调用，实际 {CALLS['n']}"
        assert r2["sql_validation"]["status"] == "passed"
        assert r2["sql_validation"]["attempts"] == 1
