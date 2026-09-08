"""datadeck CLI 样例：python -m datadeck

组装 ChatbotAgent（核心环）+ 内存适配者，接受用户输入并运行 agent。
模型从环境变量读取（DATADECK_MODEL/DATADECK_API_KEY，见 .env.example）；
context.model 为空时由 ChatbotAgent 回落到 ModelProvider 的默认 spec。
"""

from __future__ import annotations

import asyncio

from dotenv import load_dotenv

from datadeck.adapters.checkpointer import MemoryCheckpointerProvider
from datadeck.adapters.memory import MemoryMemoryStore
from datadeck.adapters.model_provider import EnvModelProvider
from datadeck.agents.buildin.chatbot import ChatbotAgent


async def run_once(agent: ChatbotAgent, question: str, *, uid: str, thread_id: str) -> str:
    context = {"uid": uid, "thread_id": thread_id}  # model 留空 → 默认 spec 兜底
    result = await agent.invoke_messages(
        [{"role": "user", "content": question}], input_context=context)
    messages = result.get("messages", []) if isinstance(result, dict) else getattr(result, "messages", [])
    last = messages[-1] if messages else None
    content = getattr(last, "content", None)
    if isinstance(content, list):
        content = "".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return content or str(last)


async def main() -> None:
    agent = ChatbotAgent(
        model_provider=EnvModelProvider(),
        memory_store=MemoryMemoryStore(),
        checkpointer_provider=MemoryCheckpointerProvider(),
    )
    print("datadeck 助手 就绪。输入 q 退出。")
    uid, thread_id = "u1", "t-cli"
    while True:
        try:
            question = input(">>> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question or question.lower() in {"q", "quit", "退出"}:
            break
        try:
            answer = await run_once(agent, question, uid=uid, thread_id=thread_id)
            print(f"\n{answer}\n")
        except Exception as exc:  # noqa: BLE001
            print(f"[error] {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(main())