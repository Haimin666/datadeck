"""Agent 组装单例（server 侧宿主实现）：ChatbotAgent + AsyncPostgresSaver。

抽离边界：PG checkpointer 是平台依赖，实现在宿主（server），经
datadeck.ports.CheckpointerProvider 注入——src/datadeck 不感知 PG。
"""
from __future__ import annotations

import asyncio
import sys

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from datadeck.adapters.platform_model_provider import PlatformModelProvider
from datadeck.agents.buildin.chatbot.graph import ChatbotAgent
from datadeck.agents.buildin.dataagent import DataAgent
from datadeck.ports.checkpointer import CheckpointerProvider
from server.config import settings
from server.services.model_providers.cache import model_cache

# AsyncPostgresSaver 自身维护连接池；单例持有使 checkpointer 跨请求复用
# （线程内多轮记忆 + interrupt 恢复）。
_saver_cm = None
_saver = None
_saver_lock = asyncio.Lock()
_agent: ChatbotAgent | None = None
_data_agent: DataAgent | None = None


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
    async with _saver_lock:
        if _saver is not None:
            return _saver
        # AsyncPostgresSaver 吃 psycopg 连接串（无 +asyncpg driver 前缀）
        dsn = settings.database_url.replace("+asyncpg", "")
        saver_cm = AsyncPostgresSaver.from_conn_string(dsn)
        saver = await saver_cm.__aenter__()
        try:
            await saver.setup()
        except BaseException:
            await saver_cm.__aexit__(*sys.exc_info())
            raise
        _saver_cm = saver_cm
        _saver = saver
        return saver


async def get_chatbot_agent() -> ChatbotAgent:
    """进程级单例（graph 缓存由 BaseAgent 自管）。"""
    global _agent
    if _agent is None:
        saver = await _init_saver()
        from server.services.pg_memory_store import PgMemoryStore

        _agent = ChatbotAgent(
            model_provider=PlatformModelProvider(model_cache),
            checkpointer_provider=PgCheckpointerProvider(saver),
            memory_store=PgMemoryStore(),
        )
    return _agent


async def get_agent(agent_slug: str = "default-chatbot", backend_id: str | None = None):
    """按数据库中的 Agent slug 选择运行时；默认 Agent 保持原通用行为。"""
    if agent_slug == "data-agent" or backend_id == "DataAgent":
        global _data_agent
        if _data_agent is None:
            saver = await _init_saver()
            from server.services.pg_memory_store import PgMemoryStore

            _data_agent = DataAgent(
                model_provider=PlatformModelProvider(model_cache),
                checkpointer_provider=PgCheckpointerProvider(saver),
                memory_store=PgMemoryStore(),
            )
        return _data_agent
    return await get_chatbot_agent()


async def close_agent() -> None:
    """进程退出时释放 saver 连接池。"""
    global _saver_cm, _saver, _agent, _data_agent
    _agent = None
    _data_agent = None
    if _saver_cm is not None:
        await _saver_cm.__aexit__(None, None, None)
        _saver_cm = None
        _saver = None
