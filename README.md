# DataDeck

DataDeck 是一个面向企业数据问答的 Agent 平台，包含通用对话 Agent、独立 DataAgent、知识库/RAG、OMD 元数据查询、只读 SQL、Skill、MCP、工作区和定时任务。

当前架构收敛任务以 [TODO.md](TODO.md) 为唯一执行清单，严格按 1～8 阶段推进；新功能不得增加历史旁路或兼容入口。

## 当前状态

- `default-chatbot`：通用对话 Agent，保持通用问答和工具扩展能力。
- `data-agent`：内置数据分析 Agent，默认加载 RAG、OMD 和只读 SQL 工具，并开启 SQL 自检。
- 两个内置 Agent 都固定挂载 `package:platform`（文件、Skill、定时任务、追问和产物等平台能力）；该能力包在编辑器中可见但不可移除。
- OMD Service 查询区分数据库类（Hive、Doris、Mysql、Oracle）和看板类（Looker、CustomDashboard）；数据库/表搜索不会把看板 Service 当作数据源。
- 知识库：支持 TXT、Markdown、CSV、JSON；按文档切片，持久化到 PostgreSQL，启动时恢复 chunk 并按需补齐 Qdrant 向量索引，未配置 embedding 时使用关键词检索。
- Agent 绑定：Agent 配置中的 `knowledges` 绑定用户知识库；运行时校验用户归属并注入对应 collection。
- 运行状态：Agent run、事件流、审批、错误和重启恢复均落库；运行中 Run 通过数据库心跳续租，多副本不会互相误回收，失联 Run 会自动收敛为失败。
- Docker：Compose 使用国内镜像源，默认暴露应用 `8000`、Qdrant `6333`。

DataAgent 已加入运行时流程门控：指标取数先检索 RAG，SQL 执行前必须完成表结构确认和只读校验；缺少前置步骤时不会执行 SQL。
字段级权限和完整业务评测集仍需按实际数仓规则配置。

模型和 MCP 的网络代理彼此隔离：模型使用 `DATADECK_MODEL_HTTP_PROXY`，MCP 默认直连，只有明确需要代理时配置 `DATADECK_MCP_HTTP_PROXY`。

## 目录

```text
src/datadeck/                 Agent 核心与工具
  agents/buildin/chatbot/     通用对话 Agent
  agents/buildin/dataagent.py 独立 DataAgent
  agents/toolkits/            RAG、OMD、SQL 和工具注册
server/                       FastAPI 宿主、数据库、权限和运行时适配
web/                          Vue 前端
migrations/                   Alembic 数据库迁移
docker-compose.yml            本地/服务器 Compose 部署
RULES.md                      项目开发与运行规则
```

## Docker 启动

```bash
# 首次部署：自动检查 Docker、创建挂载目录并生成 .env.docker
./docker-manage.sh init
# 编辑 .env.docker，至少配置模型供应商；JWT 和 PostgreSQL 密码已由 init 自动生成；需要向量检索时配置 embedding key
./docker-manage.sh up
./docker-manage.sh status
curl http://127.0.0.1:8000/api/system/health
```

部署完成后可使用 smoke 检查容器健康和核心 API。只检查公开健康接口时直接运行；
需要验证登录、Agent、工具包、知识库、指标和定时任务接口时，通过环境变量传入临时测试凭据：

```bash
./docker-manage.sh smoke
# 完整受保护接口检查建议使用 superadmin/admin 测试账号
SMOKE_USERNAME=admin SMOKE_PASSWORD='你的密码' ./docker-manage.sh smoke
```

服务数据保存在以下位置，不要删除这些目录或 Compose volume：

- PostgreSQL：`datadeck-postgres`
- Qdrant：`datadeck-qdrant`
- Redis：`datadeck-redis`
- 应用文件：`./data`、`./uploads`、`./data_material`
- 临时会话目录：`./data/temp-sessions`（容器内仍位于 `/tmp/datadeck-sessions`，按 TTL 清理）

重启只需：

```bash
./docker-manage.sh restart
```

不要使用 `docker compose down -v`，它会删除数据库和向量库数据。

常用管理命令：

```bash
./docker-manage.sh logs       # 查看应用日志
./docker-manage.sh inject     # 迁移并幂等注入 DataAgent 物料
./docker-manage.sh smoke      # 检查容器健康和核心 API
./docker-manage.sh tool-probe # 在容器网络内验证 Agent 全工具链，不调用模型
FAULT_DRILL_CONFIRM=YES ./docker-manage.sh fault-drill  # 故障恢复演练，不删除数据卷
./docker-manage.sh rebuild    # 无缓存重建，排查构建缓存问题
./docker-manage.sh stop
./docker-manage.sh down       # 仅删除容器和网络，保留数据卷
```

## 本地启动

本地 PostgreSQL、Redis、Qdrant 已启动时，执行以下命令即可启动后端和 Vite 前端：

```bash
./local-manage.sh start
./local-manage.sh status
./local-manage.sh logs
./local-manage.sh stop
```

脚本会自动执行 `alembic upgrade head`，日志和 PID 保存在 `tmp/local-runtime/`；
不会操作本地 PostgreSQL、Redis、Qdrant。默认地址为后端 `8000`、前端 `5173`，
可通过 `DATADECK_LOCAL_BACKEND_PORT` 和 `DATADECK_LOCAL_FRONTEND_PORT` 覆盖。

## Agent 选择

Agent 通过数据库中的 `backend_id` 选择运行时：

- `ChatbotAgent`：通用 Agent，不强制数据流程。
- `DataAgent`：数据分析 Agent，默认工具链为：

```text
RAG → OMD 元数据 → SQL 只读查询 → 结果回答
```

DataAgent 仍允许加载 Skill、MCP、工作区等宿主能力，但数据工具包由策略固定挂载，不能通过普通工具白名单移除；通用 Agent 不会自动获得数据工具。

角色权限与 Agent 能力是两层边界：角色权限只决定前端模块和模块级 API 是否可见；用户被角色分配某个 Agent 后，该 Agent 已配置的工具包、Skill、MCP、知识库、工作区和定时任务能力由运行时统一装配，不再被用户是否打开对应管理模块重复限制。Agent 身份提示词默认为空，能力说明只根据本次实际挂载资源生成。

## Agent 架构与运行时装配

当前 Agent 采用“核心图 + 宿主装配”的分层结构：

```text
HTTP / 定时任务 / 子 Agent
          ↓
server.services.run_service
  加载 Agent 配置、用户、Thread、Project
          ↓
server.services.agents_provider
  选择 ChatbotAgent 或 DataAgent
          ↓
server.services.agent_runtime_assembler
  解析授权、能力包、知识库、Skill、MCP、工作区并生成快照
          ↓
ChatbotAgent/DataAgent.get_graph(context)
  只消费本次快照中的模型、中间件、工具和系统提示词
          ↓
LangGraph create_agent
  模型 → 工具 → 工具结果 → 模型，直到结束/审批/失败/达到步数上限
```

`src/datadeck` 是 Agent 核心层，负责 Context、Graph、Middleware 和工具注册接口；`server` 是宿主层，负责数据库、用户权限、工作区、Skill、MCP、知识库和定时任务。PG Checkpointer、模型供应商和平台工具通过宿主适配器注入核心层。统一资源契约定义在 [agent_runtime_contract.py](server/services/agent_runtime_contract.py)，统一装配入口定义在 [agent_runtime_assembler.py](server/services/agent_runtime_assembler.py)。

角色的模块权限和 Agent 授权在装配器内解析为本次运行的不可变快照：模块权限控制管理 API，Agent 授权控制其运行资源；工具实例化后还会进行一次服务端权限过滤，因此前端隐藏模块不是安全边界。没有 `workspace` 模块权限的用户只能创建临时会话，不绑定项目；临时目录按配置周期清理。

当前资源挂载方式：

| 资源 | 当前装配方式 |
|---|---|
| 内置工具 | 代码注册，按 `context.tools`/能力包选择；DataAgent 的数据工具由 `DATA_AGENT_POLICY` 固定保留 |
| 工作区工具 | 当前用户工作目录授权后动态创建 |
| 知识库 | `AgentRuntimeAssembler` 按用户可访问范围和 Agent 明确配置注入 collection；RAG 运行时按快照限制范围，前端 `@` 候选与快照一致 |
| 对话附件 | 线程附件先按 `thread_id + uid` 过滤后进入本次 Runtime Context，由 `read_attachment` 能力包读取文本；不向模型暴露磁盘路径 |
| Skill | 按用户/Agent 配置动态解析，提示词和依赖工具按需激活；声明 `run_skill_script` 后只能执行该 Skill `scripts/` 下的 `.py/.sh`，`/outputs` 自动映射到当前 Workdir |
| MCP | 以 `package:mcp:<server_slug>` 动态工具包挂载；Skill 依赖由装配器自动补齐，工具只能来自已挂载 Server |
| 定时任务工具 | 宿主侧注入，只有选择 `package:platform` 或明确配置后可见，调用后创建新的 AgentRun |
| 子 Agent 工具 | 宿主侧注入，创建独立 Thread/Run，限制单层派生；父 Agent 显式挂载后不要求用户重复分配子 Agent |
| 用户追问 | `ask_user_question` 通过 LangGraph interrupt 暂停，前端可按同一 `run_id` 恢复 |

通用 Agent 只注入中性基础提示和实际能力摘要；DataAgent 才注入数据问题工作流。通用 Agent 只有同时挂载 `dba` Skill 与 `run_skill_script` 时，才可以介绍或执行数据同步能力。

正式 AgentRun 必须先经过 `AgentRuntimeAssembler` 生成用户级 `RuntimeResourceSnapshot`；内置工具、知识库、Skill、MCP、工作区、定时任务和子 Agent 的授权边界以该快照为准。装配快照和运行期间的资源诊断会作为 `runtime_snapshot`/`runtime_diagnostic` 事件落库并进入对话 Trace。正式运行不复用绑定了上一轮上下文的 Graph；只有明确的无用户元信息场景可以使用无运行 Graph。详见 [TODO.md](TODO.md) 的 1～8 阶段。

删除会话时，服务端会同时清理该线程的运行事件、反馈、附件元数据、附件/制品目录和 LangGraph checkpoint；运行中的会话先返回冲突提示，避免删除与写入并发发生。

当前 Agent Loop 使用 LangGraph 标准循环，没有自定义主循环；正式运行按请求创建 Graph，工具错误/超时转换为可恢复的 ToolMessage，外部资源问题转换为结构化诊断。资源快照按请求生命周期创建，平台工具遵循显式白名单。

上下文按“系统约束 → 当前用户请求 → 结构化任务事实 → 工具证据 → 用户记忆 → 历史摘要 → 普通历史”的优先级处理。单工具结果默认限制为 300 token，可用 `DATADECK_TOOL_RESULT_TOKEN_LIMIT` 调整（64～8000 token）；裁剪、摘要失败和恢复状态会通过 `agent_state`/Trace 记录。

长期记忆按用户隔离，并支持 `project_id` 项目边界：全局记忆可在项目内复用，项目记忆只在当前项目可见；数据库迁移版本为 `20260916_0026`。

显式配置的 Skill 如果装配失败会直接终止本次 Run，不会伪装成“未挂载”；运行资源快照带有稳定 fingerprint，审批恢复和重启恢复会校验快照一致性。

Skill 脚本通过 `run_skill_script(skill_slug, script_path, script_args, timeout)` 进入统一运行时，不能直接执行任意宿主路径或 shell；生成到 `/outputs` 的文件再通过 `present_artifacts` 交付。默认审批模式下脚本执行仍需人工审批。

工具失败保护同时按“工具+参数”和“工具”两个维度计数：同一参数连续失败 2 次，或同一工具不同参数累计失败 4 次，运行会停止并返回最后错误，避免 Agent 在错误路径上无限尝试。

发布前验证：

```bash
./.venv/bin/python -m compileall -q server src tests
.venv/bin/python -m pytest -q
cd web && pnpm build
```

涉及数据库结构的发布使用 Alembic；当前索引迁移提供可回滚的 `downgrade`。完整审批流测试需要先准备并迁移独立的 `datadeck_test` 数据库。

## 知识库/RAG

前端知识库是 RAG 引擎的管理层：

1. 知识库和文档元数据写入 PostgreSQL。
2. 文档按基础文本规则切片，chunk 写入 `knowledge_chunks`。
3. PostgreSQL 是关键词检索事实来源；服务启动时校验并补齐持久化 chunk，并提交恢复事务。
4. 配置 embedding 后，切片同步写入每个知识库独立的 Qdrant collection；启动发现 collection 或文档向量缺失时按数据库事实重建，Qdrant 仅作为可重建索引。
5. Agent 运行时通过 `knowledges` 选择知识库，并使用授权后的 collection 检索。

### DataAgent 物料分层

业务问答 RAG 只接收 `upload`、`wiki`、`business_doc` 三类业务文档。数仓代码以 Git
快照为事实来源，通过 `code_search` 按需检索；OMD 的 Service、Schema、表结构、字段、
血缘和产出状态通过 OMD 工具实时查询。历史代码、OMD 快照、OSSIE 原始记录和
`wiki_extract` 仍保留在只读的 `/public/knowledge-materials` 公共知识物料路径，
但不会污染默认 RAG。可用 `python scripts/migrate_knowledge_layers.py` 生成盘点报告，
确认后使用 `--apply` 幂等归档原始物料并删除其 PostgreSQL 派生 chunk；原始文件不会删除。

数仓物料准备分为三步：`prepare_lion_dw_material.py` 扫描代码和注释，SQL 文件同时
执行 Hive SQL AST 解析；`build_dataagent_delivery.py` 生成业务文档草稿和 OSSIE
候选；`import_dataagent_materials.py` 幂等写入知识文档和指标注册表。所有代码候选、
Wiki 证据和冲突指标都保留来源、版本或行号，需人工审核后才能作为权威口径。

目前不处理 PDF、图片、OCR、Word 和 Excel。

## 开发验证

```bash
python -m compileall -q server src
../.venv/bin/python -m pytest
cd web
pnpm test:unit
pnpm build
pnpm lint:check
```

前端已有单元测试；后端测试应优先覆盖知识库、DataAgent、运行队列、审批恢复和定时任务。

## 配置重点

- `DATABASE_URL`：PostgreSQL 连接串。
- `DATADECK_QDRANT_URL`：Qdrant 地址，Compose 内为 `http://qdrant:6333`。
- `DATADECK_EMBEDDING_API_URL`、`DATADECK_EMBEDDING_API_KEY`、`DATADECK_EMBEDDING_MODEL`：向量模型配置。
- `DATADECK_SQL_DSN`、`DATADECK_SQL_DIALECT`：数据源和 SQL 方言。
- `DATADECK_SQL_ALLOWED_SCHEMAS`、`DATADECK_SQL_DENIED_TABLES`、`DATADECK_SQL_DENIED_COLUMNS`：可选的 SQL schema/表/字段边界，逗号分隔。
- `DATADECK_AGENT_RUN_TIMEOUT`：单次 Agent 最大运行时间，默认 600 秒（10 分钟）。超时会先输出已记录的 Todo、最近工具调用和终止原因。
- `DATADECK_CONTEXT_OPERATIONAL_CAP`：单次运行的上下文预算上限，默认 256K；Agent 会根据模型的 `context_length` 动态计算输出预留、软预算、摘要阈值和硬上限。未提供模型元数据时按 128K 保守估算。
- `DATADECK_MODEL_CONTEXT_WINDOW`、`DATADECK_MODEL_MAX_OUTPUT_TOKENS`：仅用于环境变量模型 Provider 的上下文和输出上限；数据库模型优先读取模型配置中的 `context_window/context_length`。
- 工具调用超时时间：当前 Context 默认 120 秒，运行时会将其限制在 1～600 秒。

所有密钥只放在 `.env` 或 `.env.docker`，不要提交到 Git。

故障演练只会短暂停止并恢复 PostgreSQL、Qdrant、Redis 或应用容器，不使用 `down -v`。
如需验证已有运行的 SSE 断线恢复，可额外传入 `FAULT_DRILL_RUN_ID` 和
`FAULT_DRILL_TOKEN`；脚本不会自动创建 Agent Run。模型超时需要在部署环境临时调整
`DATADECK_MODEL_TIMEOUT` 后单独发起测试，完成后恢复原值并重启应用。

`tool-probe` 使用 `PROBE_UID`（默认 `admin`）和 `PROBE_TABLE_NAME`（默认
`pangu.sys_flow_s_cap_h`）指定测试账号和 OMD 表。它只执行只读数据查询、临时禁用的
定时任务增改删以及临时工作区文件操作；不会调用模型，不会保留测试文件或任务。已配置
MCP 必须在应用容器网络内成功发现工具，否则探针会失败并给出连接原因。
