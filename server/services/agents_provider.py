"""Agent 组装单例（server 侧宿主实现）：ChatbotAgent + AsyncPostgresSaver。

抽离边界：PG checkpointer 是平台依赖，实现在宿主（server），经
datadeck.ports.CheckpointerProvider 注入——src/datadeck 不感知 PG。
"""
from __future__ import annotations

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from datadeck.adapters.model_provider import EnvModelProvider
from datadeck.agents.buildin.chatbot.graph import ChatbotAgent
from datadeck.ports.checkpointer import CheckpointerProvider
from server.config import settings

# AsyncPostgresSaver 自身维护连接池；单例持有使 checkpointer 跨请求复用
# （线程内多轮记忆 + interrupt 恢复）。
_saver_cm = None
_saver = None
_agent: ChatbotAgent | None = None


class PgCheckpointerProvider(CheckpointerProvider):
    """把 AsyncPostgresSaver 适配进 datadeck CheckpointerProvider 端口。"""

    def __init__(self, saver: AsyncPostgresSaver) -> None:
        self._saver = saver

    def get_checkpointer(self):
        return self._saver


async def _init_saver() -> AsyncPostgresSaver:
    global _saver_cm, _saver
    if _saver is not None:
        return _saver
    # AsyncPostgresSaver 吃 psycopg 连接串（无 +asyncpg driver 前缀）
    dsn = settings.database_url.replace("+asyncpg", "")
    _saver_cm = AsyncPostgresSaver.from_conn_string(dsn)
    _saver = await _saver_cm.__aenter__()
    await _saver.setup()
    return _saver


async def get_chatbot_agent() -> ChatbotAgent:
    """进程级单例（graph 缓存由 BaseAgent 自管）。"""
    global _agent
    if _agent is None:
        saver = await _init_saver()
        from server.services.pg_memory_store import PgMemoryStore

        _agent = ChatbotAgent(
            model_provider=EnvModelProvider(),
            checkpointer_provider=PgCheckpointerProvider(saver),
            memory_store=PgMemoryStore(),
        )
    return _agent


async def close_agent() -> None:
    """进程退出时释放 saver 连接池。"""
    global _saver_cm, _saver, _agent
    _agent = None
    if _saver_cm is not None:
        await _saver_cm.__aexit__(None, None, None)
        _saver_cm = None
        _saver = None
