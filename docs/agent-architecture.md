# DataDeck Agent 架构文档

## 一、核心范式

### 1. ReAct（思考-行动循环）

DataDeck 基于 **LangGraph + LangChain `create_agent`** 实现 ReAct 循环。Agent 的核心运行路径：

```
用户输入 → [路由提示注入] → 模型思考 → 调用工具 → 观察结果 → 模型继续思考 → ... → 最终回答
```

**具体实现在 `graph.py:99-107`**：

```python
# src/datadeck/agents/buildin/chatbot/graph.py
return create_agent(
    model=model,
    tools=tools,
    system_prompt=system_prompt,
    middleware=middlewares,
    state_schema=ChatBotState,
    context_schema=ChatBotContext,
    checkpointer=await self._get_checkpointer(),
)
```

`create_agent` 来自 `langchain.agents`，内部编译为 LangGraph 的 `CompiledStateGraph`。循环由 `recursion_limit` 控制（默认 300 步），直到模型产出不含 tool_calls 的最终回答。

**调用入口**（`base.py:81-93`）：
- `invoke_messages()` — 一次性运行，返回最终状态
- `stream_values()` — 值流式，逐轮产出 `{messages: [...]}`

---

### 2. Tool（工具）

工具是 Agent 与外部世界交互的唯一方式。DataDeck 通过 **装饰器 + 全局注册表** 实现工具注册。

**注册机制**（`toolkits/registry.py:38-74`）：

```python
@tool(category="buildin", tags=["计算"], display_name="计算器")
def calculator(a: float, b: float, operation: str) -> float:
    ...
```

`@tool` 装饰器在 `langchain.tools.tool` 基础上扩展，自动：
1. 注册元数据（category/tags/display_name/icon）到 `_extra_registry`
2. 收集工具实例到 `_all_tool_instances` 全局列表
3. 设置 `handle_tool_error = True`（异常不中断循环）

**工具解析**（`toolkits/service.py`）：根据 `context.tools` 过滤可用工具，`None` 表示全部启用。

**DataDeck 内置工具**：

| 工具名 | 分类 | 功能 |
|--------|------|------|
| `sql_execute_query` | data | 执行 SQL（PG/Doris），熔断器保护 |
| `omd_list_databases` | data | 列出 OpenMetadata 数据库 |
| `omd_list_tables` | data | 列出指定库的表 |
| `omd_get_table_schema` | data | 获取表结构（列名/类型/注释） |
| `omd_get_table_lineage` | data | 获取表血缘关系 |
| `rag_search` | knowledge | BM25 + 向量混合检索知识库 |
| `echo` / `add` | buildin | 内置调试工具 |
| `sql_validate` | buildin | SQL 静态校验 |
| `remember_memory` | memory | 新增/纠正用户长期记忆 |
| `search_thread_messages` | memory | 搜索历史消息 |
| `read_thread_messages` | memory | 读取线程历史 |

**熔断器**（`toolkits/circuit_breaker.py`）：每个工具独立计数，连续失败 3 次 → 熔断 open（60s 冷却）→ 半开试探 → 成功关闭。业务失败（如"表不存在"）不触发熔断。

---

### 3. State（状态）

状态是 Agent 记忆与流转的核心，全程保存对话上下文、中间结果、工具返回、任务进度。

**基础状态**（`agents/state.py:19-26`）：

```python
class BaseState(AgentState):
    artifacts: Annotated[list[str], merge_artifacts]  # 产出文件路径（去重合并）
    sql_retry_attempts: int      # SQL 自检：当前轮打回次数
    sql_turn_base: int           # SQL 自检：本轮消息基线索引
    sql_validation: dict | None  # SQL 自检：最终验证状态
```

**每个中间件可声明自己的 state schema**，运行时自动合并到图状态中：

| 中间件 | State 字段 | 用途 |
|--------|-----------|------|
| `SqlSelfCheckMiddleware` | `sql_retry_attempts`, `sql_validation` | SQL 打回计数与验证状态 |
| `DataSelfCheckMiddleware` | `data_retry_attempts` | 数据自检打回计数 |
| `TokenBudgetMiddleware` | `token_budget_trimmed` | 累计被裁消息数 |
| `TokenUsageMiddleware` | `token_usage` | Token 消耗统计 |
| `TodoListMiddleware` | `todos` | 任务待办列表 |

**状态持久化**：通过 `langgraph-checkpoint-postgres`（`AsyncPostgresSaver`）将状态写入 PostgreSQL，支持会话恢复。

---

### 4. Memory（记忆）

解决多轮对话、上下文遗忘问题。分为短期会话记忆和长期用户记忆。

**短期会话记忆**：由 LangGraph Checkpointer 提供，每次 `ainvoke`/`astream` 自动读写 PG `checkpoints` 表，支持按 `thread_id` 恢复历史。

**长期用户记忆**（`middlewares/memory.py` + `pg_memory_store.py`）：

```python
# 端口定义（ports/memory.py）
class MemoryStore(Protocol):
    async def load_prompt(self, uid: str) -> str | None: ...
    async def remember(self, *, uid, content, replaces=None) -> dict: ...
    async def search(self, *, uid, query, limit=10) -> dict: ...
    async def read(self, *, uid, thread_id=None, limit=20) -> dict: ...
```

**PG 实现**（`pg_memory_store.py`）：
- 表 `agent_memories`：`uid + content + replaces + created_at`
- `load_prompt`：取最近 20 条记忆，拼接为 `<memory_data>` 注入 system prompt
- `remember`：用户说"记住 X"时调用，支持 `replaces` 幂等覆盖旧记忆
- `search`：ILIKE 关键词搜索（记忆量小，够用）

**注入方式**（`middlewares/memory.py:46-57`）：`MemoryMiddleware.wrap_model_call` 在每次模型调用前，将记忆文本追加到 system message。

---

## 二、Harness 组件

### 1. Middleware（中间件）

中间件是 DataDeck 的核心架构模式。所有中间件继承 `AgentMiddleware`，可挂载到以下钩子：

| 钩子 | 执行时机 | 典型用途 |
|------|---------|---------|
| `before_agent` | Agent 循环开始前 | 工具补丁（PatchToolCalls） |
| `before_model` | 模型调用前 | 摘要压缩、Token 预算裁剪 |
| `wrap_model_call` | 包裹模型调用 | Token 用量记录、Memory 注入 |
| `after_model` | 模型返回后 | SQL 自检、数据自检、HITL 审批 |
| `after_agent` | Agent 循环结束后 | 结果后处理 |

**完整中间件链**（`graph.py:34-67`）：

```
PatchToolCalls.before_agent
    → SqlSelfCheck.after_model (jump_to=model)
    → DataSelfCheck.after_model (jump_to=model)
    → Memory.wrap_model_call (注入记忆 + 工具)
    → Summary.before_model (100K tokens 触发压缩)
    → TokenBudget.wrap_model_call (60K 软顶/100K 硬顶)
    → TodoList.after_model (任务跟踪)
    → ModelRetry.wrap (最多 2 次重试)
    → TokenUsage.wrap (消耗统计)
    → HumanInTheLoop.after_model (敏感工具审批)
```

---

### 2. Self-Reflection（自我反思）

DataDeck 实现了**两层确定性自我反思**，无需 LLM 参与：

#### 2.1 SQL 自检（`middlewares/sql_selfcheck.py`）

```python
@hook_config(can_jump_to=["model"])  # 关键：不声明则框架静默忽略跳转
def after_model(self, state, runtime):
    return self._check(dict(state))
```

**流程**：
1. 从模型回答中提取 SQL（优先 ````sql` 代码块，其次裸 SELECT/WITH + 解析门控）
2. 调用 `sqlglot` 确定性校验（语法、只读性、表数量、JOIN 数）
3. **通过** → 记录验证结果到 state
4. **失败** → 注入结构化反馈 + `jump_to="model"` 打回重写
5. **封顶**（默认 2 次）→ 保留原始 SQL + 追加警示，state 标记 `gave_up`

**关键协议**：
- 必须声明 `@hook_config(can_jump_to=["model"])`
- 打回预算记在 state（`sql_retry_attempts`），按"本轮用户消息"切轮重置
- 控制语义一律走 state 字段，禁止写入自由文本再 regex 回读

#### 2.2 数据自检（`middlewares/data_selfcheck.py`）

在 SQL 自检之上增加**真实数据源对照**：
- 查 `information_schema` 获取表清单（带 120s TTL 缓存）
- SQL 引用的表不存在 → 打回重写（同样 `jump_to=model`）
- DSN 不可用时静默放行（不阻塞链路）

---

### 3. Summary（上下文压缩）

超 100K tokens 时自动压缩历史上下文，防止上下文窗口溢出。

**实现**（`middlewares/summary.py`）：使用 LangChain 官方 `SummarizationMiddleware`。

```python
SummarizationMiddleware(
    model=model,
    trigger=("tokens", 100 * 1024),   # 100K tokens 触发
    keep=("messages", 10),             # 保留最近 10 条
    token_counter=count_tokens_approximately,
    summary_prompt=DEFAULT_DATADECK_SUMMARY_PROMPT,  # 中文压缩 prompt
)
```

**压缩 prompt 要求保留**：
- `SESSION INTENT` — 用户目标
- `USER REQUIREMENTS` — 用户要求
- `PROGRESS AND DECISIONS` — 已完成步骤
- `ARTIFACTS AND REFERENCES` — 文件/路径/标识
- `NEXT STEPS` — 后续步骤

---

### 4. TokenBudget（上下文裁剪）

作为 Summary 的**保险丝**，两层防护：

| 层级 | 阈值 | 策略 |
|------|------|------|
| 软顶（budget） | 60K tokens | 从中段裁最老消息（保留 system + 最近 6 条） |
| 硬顶（hard_limit） | 100K tokens | 激进裁剪，只保 system + 最近 4 条 |

**实现**（`middlewares/token_budget.py:67-83`）：

```python
def _trim(self, messages):
    before = self.token_counter(messages)
    if before <= self.budget:
        return None, payload  # 未超限，直通
    # 软顶超限：从中段裁剪
    trimmed_msgs, count = self._trim_middle(messages, self.budget)
    # 硬顶仍超：激进裁剪
    if after > self.hard_limit:
        trimmed_msgs, count2 = self._trim_hard(trimmed_msgs)
    return trimmed_msgs, payload
```

裁剪记录写入 state（`token_budget_trimmed`），供前端审计展示。

---

### 5. Approval（人类介入/HITL）

敏感工具（`write_file`/`edit_file`/`execute`）在执行前请求人工确认。

**实现**（`tool_approval.py`）：

```python
HumanInTheLoopMiddleware(
    interrupt_on={
        "write_file": {"allowed_decisions": ["approve", "reject"], "when": write_requires_approval},
        "edit_file": {"allowed_decisions": ["approve", "reject"], "when": write_requires_approval},
        "execute": {"allowed_decisions": ["approve", "reject"]},
    }
)
```

**审批谓词**：`_project_write_requires_approval` 创建闭包，豁免当前 Project 内写入，Project 外写入始终请求确认。

**恢复流程**：
1. Agent 运行中断，run 状态置为 `interrupted`
2. 前端展示审批卡片（tool_calls + tool_names + actionRequests）
3. 用户点击同意/拒绝 → `POST /api/agent/runs` 带 `resume` + `tool_approval`
4. 后端构建 `Command(resume={"decisions": [{"type": "approve|reject"}]})`
5. 图从中断点恢复执行

---

### 6. ModelRetry（模型重试）

模型调用异常（如 API 超时、限流）时自动重试。

**实现**：使用 LangChain 内置 `ModelRetryMiddleware`：

```python
ModelRetryMiddleware(max_retries=2)  # 可通过 context.model_retry_times 配置
```

与 SQL 自检的"打回重写"不同——ModelRetry 是对模型调用本身的容错，不涉及业务逻辑。

---

### 7. Loop（Agent 循环）

循环由 LangGraph 运行时管理：

```
while 未产出最终回答 且 steps < recursion_limit:
    1. 中间件 before_model 链
    2. 模型推理
    3. 中间件 after_model 链（可能 jump_to=model 打回）
    4. 若有 tool_calls → 执行工具 → 将结果追加到 messages → 回到 1
    5. 若无 tool_calls → 产出最终回答 → 退出循环
```

**配置**（`context.py`）：
- `max_execution_steps = 300` — `recursion_limit`，防止无限循环
- 中间件的 `jump_to="model"` 实现内环打回（不计入外环步数）

---

### 8. Prompt（提示词）

**分层 prompt 体系**（`buildin/chatbot/prompt.py`）：

```python
def build_prompt_with_context(context):
    parts = [
        f"当前日期：{today_str()}\n\n{PROMPT}",  # 基础人设
    ]
    if context.sql_guard_enabled:
        parts.append(SQL_GUARD_PROMPT)     # SQL 产物约定（```sql 代码块）
    if context.system_prompt:
        parts.append(context.system_prompt) # 用户自定义追加
    return "\n\n".join(parts)
```

**数据 Agent prompt**（`data_prompt.py`）：当启用 SQL/OMD/RAG 工具时，注入工具选择引导（few-shot 示例）。

**任务路由提示**（`middlewares/task_router.py`）：确定性关键词预分类，注入到用户 query 前缀：

| 分类 | 关键词 | 路由提示 |
|------|--------|---------|
| metric | 口径/怎么算/定义/指标 | → 优先 `rag_search` |
| schema | 表结构/字段/血缘/元数据 | → 优先 `omd_*` |
| data | 查询/统计/汇总/count/sum | → 先 `omd_get_table_schema` 再 `sql_execute_query` |
| chat | 你好/hi/谢谢 | → 直接回答，不调工具 |

---

### 9. Context（上下文配置）

`BaseContext`（`context.py:54-181`）是 Agent 的可配置参数中心，dataclass 声明 + 元数据驱动：

```python
@dataclass(kw_only=True)
class BaseContext:
    thread_id: str          # 线程 ID
    uid: str                # 用户 ID
    model: str              # 模型 spec (provider:model_id)
    system_prompt: str      # 自定义系统提示
    tools: list[str] | None # 启用工具列表
    summary_threshold: int  # 摘要触发阈值 (K tokens) = 100
    summary_keep_messages: int  # 摘要后保留消息数 = 10
    max_execution_steps: int    # 最大执行步数 = 300
    model_retry_times: int      # 模型重试次数 = 2
    ...
```

**ChatBotContext 扩展**（`buildin/chatbot/context.py`）：增加 `sql_guard_enabled`、`sql_dialect`、`sql_max_reflect`、`sql_max_tables`、`sql_max_joins` 等 SQL 相关配置。

**配置优先级**：运行时输入（`update_from_dict`）> 类默认值。

---

## 三、Agent 评估

### 本地评测体系

DataDeck 实现了自建评测体系（`server/services/eval_service.py`），替代 LangSmith 的评测功能。

**评测集**（PG `evaluation_cases` 表）：

```python
class EvaluationCase:
    dataset: str           # 评测集名
    question: str          # 测试问题
    expect_class: str      # 期望分类: metric/schema/data/chat
    expect_tools: list     # 期望被调用的工具名（任一命中）
    expect_keywords: list  # 回答必须含的关键词（忠实度）
```

**跑分流程**：
1. 对每个 case 走真实 Agent 链路（create run → SSE 收集）
2. 自动判定：
   - **工具选择准确率**：实际调用工具 ∩ 期望工具
   - **回答忠实度**：期望关键词命中率（≥50% 即通过）
3. 结果写 `evaluation_runs` 表，支持多次跑分对比

**判定逻辑**（`eval_service.py:53-66`）：

```python
def judge_case(answer_text, called_tools, expect_tools, expect_keywords):
    tool_hit = any(t in called for called in called_tools for t in expect_tools)
    kw_hits = [k for k in expect_keywords if k in answer_text]
    kw_hit = len(kw_hits) >= max(1, len(expect_keywords) // 2)
    return {"passed": tool_hit and kw_hit, ...}
```

### 可观测性

- **structlog**：结构化日志，全链路追踪
- **LangSmith/Langfuse**：预留占位接口（`GET /api/agent/runs/{run_id}/langfuse` 返回 `{url: null}`），后续可接入系统性评测（prompt 版本对比、数据集回归）
- **Token 用量**：`TokenUsageMiddleware` 记录每次调用的 input/output tokens
- **SQL 验证状态**：`sql_validation` 字段记录验证结果，前端可展示"通过/未通过/gave_up"

---

## 四、架构总览

```
┌─────────────────────────────────────────────────────┐
│                    FastAPI Server                     │
│  run_router → run_service → event_translator → SSE   │
│  pg_memory_store / eval_service / metric_registry    │
└────────────────────────┬────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────┐
│              ChatbotAgent (BaseAgent)                 │
│  graph = create_agent(model, tools, middleware, ...)  │
│  checkpointer: AsyncPostgresSaver (PG)               │
└────────────────────────┬────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────┐
│              Middleware Chain (10+ 中间件)             │
│  ┌─ Self-Reflection 内环 ─────────────────────────┐ │
│  │  SqlSelfCheck.after_model → jump_to=model       │ │
│  │  DataSelfCheck.after_model → jump_to=model      │ │
│  └─────────────────────────────────────────────────┘ │
│  Summary → TokenBudget → TodoList → ModelRetry       │
│  → TokenUsage → Memory → HumanInTheLoop              │
│  → PatchToolCalls → Steer                             │
└────────────────────────┬────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────┐
│              Tool Registry                            │
│  @tool 装饰器 → _all_tool_instances 全局注册          │
│  sql_execute_query / omd_* / rag_search / ...         │
│  circuit_breaker (每工具独立熔断)                      │
└─────────────────────────────────────────────────────┘
```
