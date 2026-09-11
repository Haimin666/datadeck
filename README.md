# DataDeck

DataDeck 是一个面向企业数据问答的 Agent 平台，包含通用对话 Agent、独立 DataAgent、知识库/RAG、OMD 元数据查询、只读 SQL、Skill、MCP、工作区和定时任务。

## 当前状态

- `default-chatbot`：通用对话 Agent，保持通用问答和工具扩展能力。
- `data-agent`：内置数据分析 Agent，默认加载 RAG、OMD 和只读 SQL 工具，并开启 SQL 自检。
- 知识库：支持 TXT、Markdown、CSV、JSON；按文档切片，持久化到 PostgreSQL，关键词索引启动恢复，配置 embedding 后使用 Qdrant 向量检索。
- Agent 绑定：Agent 配置中的 `knowledges` 绑定用户知识库；运行时校验用户归属并注入对应 collection。
- 运行状态：Agent run、事件流、审批、错误和重启恢复均落库。
- Docker：Compose 使用国内镜像源，默认暴露应用 `8000`、Qdrant `6333`。

DataAgent 已加入运行时流程门控：指标取数先检索 RAG，SQL 执行前必须完成表结构确认和只读校验；缺少前置步骤时不会执行 SQL。
字段级权限和完整业务评测集仍需按实际数仓规则配置。

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
cp .env.docker.example .env.docker
# 编辑 .env.docker，至少配置模型供应商；需要向量检索时配置 embedding key
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:8000/api/system/health
```

服务数据保存在以下位置，不要删除这些目录或 Compose volume：

- PostgreSQL：`datadeck-postgres`
- Qdrant：`datadeck-qdrant`
- Redis：`datadeck-redis`
- 应用文件：`./data`、`./uploads`、`./server/uploads`

重启只需：

```bash
docker compose up -d --build
```

不要使用 `docker compose down -v`，它会删除数据库和向量库数据。

## Agent 选择

Agent 通过数据库中的 `backend_id` 选择运行时：

- `ChatbotAgent`：通用 Agent，不强制数据流程。
- `DataAgent`：数据分析 Agent，默认工具链为：

```text
RAG → OMD 元数据 → SQL 只读查询 → 结果回答
```

DataAgent 仍允许加载 Skill、MCP、工作区等宿主能力，但不改变通用 Agent 的行为。

## 知识库/RAG

前端知识库是 RAG 引擎的管理层：

1. 知识库和文档元数据写入 PostgreSQL。
2. 文档按基础文本规则切片，chunk 写入 `knowledge_chunks`。
3. BM25 索引保存在进程内，并在服务启动时从 PostgreSQL 恢复。
4. 配置 embedding 后，切片同步写入每个知识库独立的 Qdrant collection。
5. Agent 运行时通过 `knowledges` 选择知识库，并使用授权后的 collection 检索。

目前不处理 PDF、图片、OCR、Word 和 Excel。

## 开发验证

```bash
python -m compileall -q server src
pytest

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
- `DATADECK_AGENT_RUN_TIMEOUT`：单次 Agent 最大运行时间，默认 180 秒。

所有密钥只放在 `.env` 或 `.env.docker`，不要提交到 Git。
