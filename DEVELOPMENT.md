# DEVELOPMENT.md — datadeck 开发文档

> datadeck = 从 Yuxi 抽离的 LangGraph 智能体核心环（`src/datadeck/`）+ 复刻 Yuxi 前端体验的精简后端（`server/`）。
> 定位：公司内网知识问答 + Text2SQL（只生成不执行），单 Agent，Web UI 唯一入口（后续扩展外部调用通道）。

## 1. 已完成

### 1.1 Agent 核心环（src/datadeck/）
- BaseAgent + middleware 链（create_agent，langchain 1.x），图结构：
  `PatchToolCalls.before_agent → Summarization.before_model → model → TokenUsage(wrap) → ModelRetry(wrap) → HumanInTheLoop.after_model → TodoList.after_model → tools 循环`
- ports/adapters：ModelProvider（env）、CheckpointerProvider（InMemorySaver）、MemoryStore
- toolkits 注册机制（@tool 自动收集 + extra metadata）；CLI 入口 `python -m datadeck`
- 迁移期教训：middleware 适配层必须断言 Y 侧产物形状；mock 掉的正是唯一能暴露 bug 的地方

### 1.2 ReAct + 程序化 Self-Reflection（Phase 1，已验证）
- `sql_guard.py`：确定性只读 SQL 校验器（sqlglot Doris；禁 DML/DDL/多语句/SELECT INTO；表/JOIN 超限告警；永不抛异常）
- `sql_selfcheck.py` middleware：after_model 织入，坏 SQL 注入结构化反馈 + `jump_to="model"` 打回重写，
  封顶 2 次（best-keep：封顶保留原 SQL + 追加警示，state 标记 gave_up）
- **关键协议事实（勿凭直觉改动）**：
  - `@hook_config(can_jump_to=["model"])` 不声明则框架**静默忽略** jump_to
  - 反思状态全走结构化 state（`sql_retry_attempts`/`sql_validation`），控制语义禁止进自由文本
  - 好 SQL 通过即终态（SQL 消息就是最终回答）；裸 SELECT 词频不触发打回（须解析成功）
- 配置：`ChatBotContext(sql_guard_enabled=False 默认, sql_dialect, sql_max_reflect, sql_max_tables, sql_max_joins)`
- 工具：`sql_validate` 已注册 toolkits；测试 tests/ 31 个

## 2. 后端方案总览（简化 Yuxi，完整支撑前端复刻）

### 2.1 功能范围（定稿 2026-09-08）
- **纳入计划**：auth(JWT)、threads、runs+SSE 事件流、工具审批 interrupt/resume、附件+artifacts、
  消息反馈、`/api/chat/call`、model backends、agent state（含 sql_validation）、
  **API Key 管理（M4）、IM/外部调用通道（M5）、Dashboard（M6）**
- **Todo/Backlog（§5）**：Subagent、Langfuse、Knowledge（文档 RAG）、Skill
- **不做**：OIDC/部门/头像/CLI 会话、Project、Neo4j graph、Redis 强依赖、MinIO（本地目录替代）

### 2.2 基础设施选型
| 项 | 选型 | 说明 |
|---|---|---|
| Web | FastAPI + uvicorn | REST + SSE |
| DB | PostgreSQL 16 + SQLAlchemy 2 async + asyncpg + Alembic | 线程搜索 pg_trgm，不引 ES |
| 会话持久化 | langgraph 官方 **PostgresSaver** | langgraph>=1.0.1 已满足；history/state 零自研 |
| Run 事件流 | PG 表 `run_events(seq bigserial)` + 轮询式 SSE（心跳/Last-Event-ID 续传/终态补发 end，照抄 Yuxi 循环结构）；抽象 `RunEventStore` port | 多实例时插 Redis Streams 不改协议 |
| 对象存储 | 本地目录 DATADECK_DATA_DIR + UploadFile；port 预留 S3 兼容 | 附件仅 CSV/Excel/SQL/图片 |
| 认证 | python-jose JWT + bcrypt；**principal 双通道预留**（user \| api_key），M1 只实现 JWT | M4 加 API Key 不重构 |
| 可观测 | structlog + runs.usage jsonb | Langfuse 留 optional |

### 2.3 目录结构
```
datadeck/
├── src/datadeck/            # agent 核心（§1）——后端开发零改动
├── server/
│   ├── main.py / deps.py / config.py / sse.py
│   ├── routers/  auth / agent / chat / apikey(M4) / channel(M5) / dashboard(M6)
│   ├── services/ run / thread / attachment / title / apikey(M4) / channel(M5)
│   ├── repositories/       # SQLAlchemy async
│   └── event_translator.py # ★ datadeck 图事件 → Yuxi 前端协议
├── migrations/              # alembic
└── web/                     # 复刻 Yuxi 前端（apis/composables 移植）
```

### 2.4 数据模型
核心 6 张：users / threads / runs / run_events / message_feedback / attachments。
M4 增 api_keys；M5 增 invocation_channels；
runs 自 M1 带 `invocation_source` 列（web\|channel\|api_key）——dashboard 与通道的地基，加列成本为零。

### 2.5 事件翻译层（成败关键）
前端消费 event：`init / loading / stream_event(message_delta|tool_call|tool_call_delta) / error /
human_approval_required(interrupt) / agent_state(todos|artifacts|token_usage|sql_validation) /
context_compression / finished / interrupted / warning / end`。
实现：消费 `graph.astream_values()`；AIMessageChunk→message_delta；interrupt→human_approval_required；
super-step→agent_state；结束→finished+end。envelope `{run_id, thread_id, event_type, payload, seq, created_at}`。

### 2.6 前端兼容三坑
① seq 游标用 bigserial 数字字符串（Yuxi 是 Redis "0-0" 格式，前端 normalizeRunSeq 已兼容，需验证纯数字比较路径）；
② 前端 localStorage 存 active_run 快照 + `Last-Event-ID` 续传——轮询 SSE 天然支持；
③ 会话 ID 失效宽容：stream/history 查不到 thread 静默处理，不 4xx 打断（老 data-agent 教训）。

## 3. 开发计划（里程碑）

### M1 最小聊天闭环（前端主界面可跑）
- auth：`/api/auth/token`(form) `/me` `/initialize` `/check-first-run`
- threads CRUD + history + state（agent_state 含 sql_validation）
- runs：POST `/api/agent/runs`、GET run、cancel、active_run
- SSE：GET `/api/agent/runs/{run_id}/events`（心跳/断线续传/终态补发）
- `/api/chat/call` 非流式（标题生成依赖）；PostgresSaver 接入 + alembic 初版
- agent/backends 静态模型清单即可
- 测试：SSE 端到端契约测试先行（假模型驱动真图）；runs 状态机；auth

### M2 体验补齐
- attachments 两段式：tmp 上传 → parse → confirm；artifacts 下载/预览（**path 防穿越**）+ save
- feedback（like/dislike+reason）；threads 搜索（pg_trgm，带 snippet）、置顶、viewed 未读清除

### M3 领域增强
- sql_validation 预览卡数据面（已随 agent_state，补前端契约字段）
- OMD mention 搜索端点（先抽表名 token 再搜——整句搜必 500 的老教训；FQN 中文服务名 URL 编码）
- model backends 完整化（多模型清单 + model_spec 校验）

### M4 API Key 管理（程序化身份）
- 表 api_keys(id, uid, name, key_hash, prefix, last_used_at, created_at, revoked_at)；明文仅创建时展示一次
- 端点 `/api/user/apikey` CRUD（照 Yuxi apikey_api.js 五端点）
- 认证 principal 双通道生效：`Bearer JWT`（浏览器）/ `X-API-Key`（程序化）→ 映射 uid，权限=所属用户
- runs.invocation_source=api_key；key_hash bcrypt/argon2；限流留接缝
- 前端：API Key 管理页（Yuxi 有对应页）

### M5 IM/外部调用通道
- 参照 Yuxi agent_invocation_channel_router 最小形态：
  表 invocation_channels(id, name, type, secret, agent_id, enabled, config jsonb)；
  `POST /agent_invocation/channel/messages`（secret 鉴权 → slash command 解析 → 建/复用 thread → 建 run → 返回 run_id + events 轮询 URL）
- slash 解析直接移植 Yuxi `parse_slash_command`（纯函数）；调用记录复用 runs（source=channel），不建独立表
- 平台适配层留缝：飞书/钉钉 bot = channel adapter，未有时不实现
- 前端：channel 配置管理页

### M6 Dashboard（管理端用量观测）
- 端点照 Yuxi dashboard_router 十端点裁剪：stats 概览 / users / tools / calls/timeseries（PG generate_series）/
  threads / feedbacks / conversations 列表+详情；聚合全跑在 runs/threads/feedback 上，无新业务表；admin 权限
- 前端：dashboard 页（ECharts，Yuxi 有对应页）；上线跑两周后按实际想看的指标增删

### M7 加固上线
- docker compose（pg + api + web）、structlog、限流、备份策略
- interrupt/resume 全链路测试、SSE 断线重连压测
- 可选接缝启用：Langfuse（optional 依赖）、Redis RunEventStore

## 4. 测试红线（继承）
- TDD 强制；链路级测试必配单测（缩进类死代码教训）
- 外部依赖全 mock；PG 测试库必须 `data_agent_test`（conftest URL 防线）
- SSE 测试断言 Y 侧产物形状（event 序列/envelope），不 mock 事件翻译层
- 提交前：pytest 全绿 + compileall + git diff --check；单任务单提交

## 5. Todo / Backlog（不排期，记录触发条件）

| 项 | 说明 | 触发条件 |
|---|---|---|
| Subagent | 子代理/run 树/级联取消。**与单 Agent 红线冲突**，仅在红线修订后评估 | 出现隔离长任务场景且推翻红线 |
| Langfuse | LLM 追踪/评测平台。usage 数据 M1 起积累于 runs.usage | text2sql 需系统化评测（数据集回归/prompt 版本对比） |
| Knowledge（文档 RAG） | 与 OMD 结构化知识源是两套知识体系，违反单一知识源约束 | 出现"上传业务文档、对着文档提问"真实需求 |
| Skill | agent 变体编排（依赖闭包 DFS+环检测）。单 Agent 无第二消费者 | 出现第二种 agent 形态或自定义 agent 需求 |

## 6. 已知问题 / 注意事项
- （持续追加）
- 2026-09-08：M1 后端落地（详见 §8）；agnes 网关 key 认证可过 /models（200）但所有
  chat/completions 返回 401"无效的令牌"——平台侧 key 权限/配额问题，非代码问题。
  本机 shell 代理 127.0.0.1:7890 未运行会阻断 ChatOpenAI（已在 .env 配 NO_PROXY 绕过）。
- pytest 环境陷阱：tests/conftest.py 会被 pytest 以裸名 `conftest` 加载，测试文件若
  `from tests.conftest import X` 会得到第二个模块实例（脚本/全局分裂）——共享状态一律
  走 fixture 或实例属性（ScriptedAsyncModel 单例模式）。
- TestClient portal 与 SSE 无限流（interrupted 心跳）互斥：审批事件断言直查 run_events
  表，SSE 传输层由终态 run 的契约测试覆盖。

## 8. M1 后端落地（2026-09-08）

### 8.1 架构（本次重构）
- **执行与消费解耦**：run 创建即 dispatch 后台 task（asyncio.create_task + 进程内
  registry），真图 `astream(["messages","updates"])` → event_translator 翻译 →
  逐事件写 run_events；SSE 端点只轮询 run_events 表（Last-Event-ID/bigserial 游标、
  心跳 15s、终态补发 end、interrupted 保持连接等审批）——浏览器断开不影响运行。
- **事件信封**：`data: {"event": type, "payload": {...}}`（前端 handleSSEEvent/
  processSSEEvent 解构契约）；stream_event{type: message_delta|tool_call}。
- **PostgresSaver**：server/services/agents_provider.py 单例（AsyncPostgresSaver +
  setup()，经 ports.CheckpointerProvider 注入 ChatbotAgent）；history/state 端点读
  checkpointer 真状态，不再从 run_events 重建。
- **审批 resume**：POST /api/agent/runs {resume, tool_approval:{approved}} →
  Command(resume={"decisions":[{"type":"approve|reject"}]})（HITL 协议已验证）。

### 8.2 本次修复的既有 bug
- auth.py：PyJWT+passlib → jose+bcrypt（passlib 与 bcrypt5 不兼容且停止维护）
- models.py：Thread/AgentRun 重复同名索引（create_all 撞名）；无 FK 的 relationship
  （NoForeignKeysError）；DateTime 全列 timezone=True（utc_now aware vs naive 列）；
  RunEvent.seq → BigInteger+Identity()（ORM 显式 NULL 插入 NotNullViolation）
- toolkits/__init__.py：未导入 buildin → @tool 注册不生效（图绑定 0 工具）
- server/main.py：load_dotenv() 先于 engine/EnvModelProvider 导入

### 8.3 测试（tests/ 61 全绿）
- test_sse_contract.py：信封/序列/message_delta 契约/终态/重放（假模型驱动真图，
  不 mock 翻译层）
- test_approval_flow.py：interrupt→human_approval_required（前端 approvalState 契约）
  →resume approve/reject→completed；取消状态机
- test_auth_runs_api.py：auth 401/初始化互斥/thread CRUD/用户隔离/history 静默空（§2.6 坑③）
- conftest：PG 测试库红线（datadeck_test 断言）+ ScriptedAsyncModel 单例

## 7. 前端完成（2026-09-08）

### 7.1 已完成
- 在 `web/` 目录创建精简版 Vue 3 前端（vite 8.2.2 + vue 3.5.41 + pinia + ant-design-vue）
- 与 Yuxi 视觉风格一致：base.css / base.dark.css / shorts.css / code-highlight.less / animations.less 完整移植
- 5 个路由：`/` (Home) · `/login` · `/agent` (聊天主界面) · `/agent/:thread_id` · `/dashboard`
- API 层：`base.js` / `auth_api.js` / `agent_api.js` / `apikey_api.js` / `dashboard_api.js`（适配 datadeck 后端契约）
- Pinia stores：`user.js` / `agent.js`（精简版）/ `thread.js`（新建，管理 threads 列表）/ `theme.js`
- 核心组件：`AgentChatComponent.vue`（侧边栏对话列表 + 聊天区 + 状态面板）、`AgentInputArea.vue`、`AgentMessageComponent.vue`（markdown 渲染 + 复制/赞/踩/重试）、`ToolCallsGroupComponent.vue`、`AgentArtifactsCard.vue`、`HumanApprovalModal.vue`、`ConversationNavItem.vue`、`ConversationNavSection.vue`、`RefsComponent.vue`
- SSE 消费：`useAgentRunStream.js` + `messageProcessor.js`（适配 datadeck `event_translator.py` 事件协议）
- seq 格式兼容：`runStreamResume.js` 支持纯数字 bigserial 格式（datadeck）和 "major-minor" 格式（Yuxi）
- 登录/首页/Lite 模式：去掉 OIDC、Project、Knowledge、SubAgent、Skills、MCP、CLI 授权页
- `pnpm build` 成功，无错误

### 7.2 待后端就绪后验证
- SSE 事件类型对齐：`event_translator.py` 已定义协议，前端 `messageProcessor.js` 已实现消费
- seq 格式兼容性需通过真实 SSE 流验证（纯数字比较路径）
- thread_id 失效宽容：后端需对缺失 thread 静默处理，不返回 4xx
