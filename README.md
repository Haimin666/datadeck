# datadeck

把 Yuxi 的 LangGraph 智能体**核心环**抽离成独立库：`BaseAgent` + middleware 链 + 状态 schema + 工具审批 + 工具注册器。

## 设计原则（抽离边界）

- **决策归 LLM，控制归 framework（langchain.agents）+ middleware（环内）。**
- 一切平台依赖（PostgreSQL checkpointer、Redis、MinIO、Neo4j、workspace、模型供应商缓存、服务层）**不进入本库**，
  统一收敛为 `datadeck.ports` 里的适配者接口，由宿主用 `adapters` 注入。
- 只依赖公共第三方：`langchain` / `langgraph` / `deepagents` / `langchain-openai`。

## 结构

```text
src/datadeck/
├── ports/         适配者接口（ModelProvider / Checkpointer / MemoryStore / Workdir 等）
├── adapters/      默认内存/进程内实现（样例用）
├── agents/
│   ├── base.py        BaseAgent（图构建 + 调用/流式/恢复/历史）
│   ├── context.py     BaseContext（可配置参数 + 通用工具）
│   ├── state.py       BaseState + reducer
│   ├── models.py      load_chat_model / resolve_chat_model_spec（经 ModelProvider）
│   ├── tool_approval.py  敏感工具人工审批（HumanInTheLoop）
│   ├── toolkits/      工具注册器（@tool 装饰器 + 实例收集）
│   ├── middlewares/   middleware 链（summary/memory/token_usage/model_input...）
│   └── buildin/chatbot/  内置对话智能体（graph/prompt/state/context）
└── main.py         CLI 样例：python -m datadeck
```

## 快速开始

```bash
conda create -n datadeck python=3.12 -y
conda run -n datadeck uv sync --active    # 或 pip install -e .
export DATADECK_API_KEY=sk-xxx
export DATADECK_BASE_URL=https://api.deepseek.com/v1
python -m datadeck
```

## 许可

MIT（继承自 Yuxi；仅抽取核心环，保留原架构与注释出处）。