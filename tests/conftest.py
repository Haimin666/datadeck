"""后端测试共享 fixture：PG 测试库 + 假模型 + app client。

测试库红线（DEVELOPMENT.md §4）：必须 datadeck_test。
关键：环境变量须在 import server.* 之前设置（server.db 的 engine 是模块级创建）；
表由 lifespan 在 TestClient 的 portal loop 内创建（asyncpg 连接绑 loop，不可跨用）。
"""
from __future__ import annotations

import os

TEST_DATABASE_URL = "postgresql+asyncpg://localhost:5432/datadeck_test"

os.environ["DATABASE_URL"] = TEST_DATABASE_URL
assert "datadeck_test" in os.environ["DATABASE_URL"], "测试库防线：必须 datadeck_test"

# 测试环境与真实数据源隔离：data_selfcheck/sql_executor 不做真实外呼
os.environ.pop("DATADECK_SQL_DSN", None)

import pytest  # noqa: E402
from langchain_core.language_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402

# ── 假模型：脚本化 AIMessage 序列（可含 tool_calls），支持流式 token ──


class ScriptedAsyncModel(BaseChatModel):
    """按脚本逐轮返回 AIMessage；支持 bind_tools 与 token 流。

    脚本存实例属性（不走模块级全局），避免 conftest 双模块实例
    （pytest 裸名 conftest vs tests.conftest）导致脚本分裂。
    """

    script: list[dict] = None  # type: ignore[assignment]

    def __init__(self, script: list[dict] | None = None, **kw):
        super().__init__(**kw)
        self.script = script or [{"content": "你好，我是测试助手。"}]
        self._i = 0

    def bind_tools(self, tools, **kw):
        return self

    @property
    def _llm_type(self):
        return "scripted"

    def reset(self, script: list[dict] | None = None):
        self.script = script or [{"content": "你好，我是测试助手。"}]
        self._i = 0

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        i = self._i
        self._i += 1
        script = self.script
        msg = script[i] if i < len(script) else script[-1]
        content = msg.get("content", "")
        if run_manager and content:
            from langchain_core.messages import AIMessageChunk
            for piece in content.split(" "):
                token = (piece + " ") if piece else ""
                if token:
                    await run_manager.on_llm_new_token(
                        token, chunk=AIMessageChunk(content=token))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(
            content=content,
            tool_calls=msg.get("tool_calls", []),
        ))])

    def _generate(self, *a, **kw):
        raise NotImplementedError("async-only")


# 进程级假模型单例：load_chat_model 桩与测试引用同一实例（避免 conftest 双实例分裂）
SCRIPTED_MODEL_SINGLETON = ScriptedAsyncModel()


@pytest.fixture
def scripted_model():
    SCRIPTED_MODEL_SINGLETON.reset()
    return SCRIPTED_MODEL_SINGLETON


def _drop_test_schema() -> None:
    """同步清库（psycopg；portal loop 已关，不能用 asyncpg）。"""
    import psycopg

    dsn = TEST_DATABASE_URL.replace("+asyncpg", "")
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")


@pytest.fixture
def app_client(monkeypatch):
    """FastAPI TestClient：lifespan 建表（测试库），假模型驱动真图。"""
    from fastapi.testclient import TestClient

    import server.main as server_main
    import server.services.agents_provider as ap
    import server.services.run_service as rs
    import datadeck.agents.buildin.chatbot.graph as graph_mod

    # server.main import 时 load_dotenv 会把 .env 的 DATADECK_SQL_DSN 塞回环境；
    # 测试不外呼真实数仓，import 后再次剥离（graph 中间件按需惰性读取该 env）。
    os.environ.pop("DATADECK_SQL_DSN", None)
    import datadeck.agents.middlewares.data_selfcheck as _dsc
    _dsc._schema_cache["map"] = None
    _dsc._schema_cache["at"] = 0.0

    # 假模型单例替换（测试经 scripted_model fixture 设脚本）
    monkeypatch.setattr(graph_mod, "load_chat_model",
                        lambda *a, **k: SCRIPTED_MODEL_SINGLETON)
    SCRIPTED_MODEL_SINGLETON.reset()

    # agent 走内存 checkpointer（SSE 契约聚焦事件形状，不依赖 PG langgraph 表）
    # 单例 provider：同测试内多轮（含 resume）共享记忆
    from datadeck.adapters.checkpointer import MemoryCheckpointerProvider
    from datadeck.adapters.model_provider import EnvModelProvider
    from datadeck.agents.buildin.chatbot.graph import ChatbotAgent

    shared_checkpointer = MemoryCheckpointerProvider()

    async def fake_get_agent():
        return ChatbotAgent(
            model_provider=EnvModelProvider(),
            checkpointer_provider=shared_checkpointer,
        )

    monkeypatch.setattr(ap, "get_chatbot_agent", fake_get_agent)
    monkeypatch.setattr(rs, "get_chatbot_agent", fake_get_agent)
    monkeypatch.setattr(ap, "_agent", None)

    _drop_test_schema()
    with TestClient(server_main.app) as client:
        yield client
    _drop_test_schema()
