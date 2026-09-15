# DataDeck 项目规则

## 0. 统一架构强制原则

- 所有 Agent 运行入口必须经过唯一的 `AgentRuntimeAssembler`，再创建本次运行的 Graph。
- 不保留旧路由、旧响应字段、旧存储、旧装配入口或双写逻辑。一次性数据迁移完成后，历史实现必须删除。
- `src/datadeck` 是独立核心层，不得导入 `server`，不得读取宿主数据库、环境服务或平台全局可变状态。
- 新增任何功能必须复用现有资源契约、工具 Schema 和运行时装配，不得增加旁路。
- [TODO.md](TODO.md) 是 1～8 项架构收敛的执行清单；每阶段未通过验收不得进入下一阶段。

## 1. Agent 边界

- `default-chatbot` 是通用 Agent，修改数据流程时不得改变它的默认行为。
- `data-agent` 是独立的数据分析 Agent；数据流程、RAG 强约束和 SQL 自检优先在它内部实现。
- `src/datadeck` 是核心层，不得直接依赖 `server`。平台能力通过 context、ports 或宿主 hook 注入。
- Agent 运行必须使用持久化 run 和事件流；不能只依赖进程内状态。

## 2. DataAgent 流程

数据类问题按以下顺序处理：

```text
识别问题 → RAG 获取口径 → OMD 获取表结构 → 生成 SQL → 只读校验 → 执行 → 结果校验 → 回答
```

- 口径问题优先检索知识库。
- 生成 SQL 前必须确认真实表结构。
- 只允许单条只读查询。
- `DataWorkflowMiddleware` 在运行时门控上述顺序；缺少前置步骤时不会执行 SQL。
- 没有检索依据或查询结果时，必须明确说明，不得编造。
- 数据答案应包含知识库文档、表名、查询时间或 SQL 等来源信息。

## 3. 知识库与 RAG

- 前端知识库数据以 PostgreSQL 为事实来源，Qdrant 是向量索引，不是业务元数据来源。
- 每个知识库使用独立 collection，不能跨用户直接接受 collection 参数。
- 新增、删除、重建文档时必须同步处理 PostgreSQL、chunk 和 Qdrant；平台 RAG 的关键词检索直接读取 PostgreSQL，独立核心的 BM25 仅用于 standalone。
- 服务重启后必须能够依据数据库恢复文档与 Qdrant 索引状态。
- RAG 结果必须保留 `knowledge_base_id`、`document_id`、`chunk_id` 和文件名。
- 目前仅支持 TXT、Markdown、CSV、JSON；不得在基础文本流程中假装支持 PDF 或图片。

## 4. 工具和扩展

- 新工具必须通过工具注册器注册，并提供稳定 slug、描述和分类。
- 工具列表接口只能有一个权威路由和一种响应格式。
- Skill、MCP、工作区和定时任务必须通过统一 Agent Runtime 装配。
- Skill 脚本只能通过 `run_skill_script` 执行已挂载 Skill 的 `scripts/` 文件；脚本产物必须通过 `present_artifacts` 交付，禁止模型直接访问宿主路径。
- 对话附件必须按 `thread_id + uid` 装配到本次 Runtime Context，并通过受限的 `read_attachment` 工具读取；禁止模型自行拼接或访问宿主磁盘路径。
- MCP 在 Agent 配置中使用动态工具包 `package:mcp:<server_slug>`，不再存在独立的 `context.mcps` 配置项。
- 每次 Agent 运行必须生成请求级 `RuntimeResourceSnapshot`；用户授权状态不得放入跨用户共享的可变对象。
- 资源选择中 `None` 表示默认策略，空列表表示明确不挂载，不能把两者混用。
- 资源实例化必须消费快照；任何未经过装配器的实例化或资源解析都视为错误。
- 工具审批、失败、超时和恢复都必须产生可追踪状态。
- 写文件、执行外部动作和其他高风险工具必须经过审批策略。

## 5. 数据和权限

- 所有知识库、项目、文件、任务和 Agent 配置查询都必须校验当前用户权限。
- 禁止相信前端传入的 collection、路径、Agent 或项目归属。
- SQL 默认只读，必须经过语法、危险操作、表数量和查询范围校验。
- 日志不得输出 API Key、JWT、密码、文件内容或完整隐私数据。

## 6. 数据库和持久化

- 新表、新字段和索引必须进入 Alembic migration。
- 启动代码不得继续增加临时 ALTER TABLE。
- 不得通过删除 Compose volume 解决 schema 或数据问题。
- 运行时缓存必须能够从 PostgreSQL、Redis 或 Qdrant 重建。

## 7. 修改和验证

- 修改前先确认实际调用链和接口契约，不以旧文档为准。
- 保持改动最小，不顺手重构无关代码。
- 每次后端改动至少执行：`./.venv/bin/python -m compileall -q server src`、`git diff --check`。
- 前端改动至少执行：`pnpm test:unit` 和 `pnpm build`。
- Agent、RAG、队列或数据库改动必须增加对应回归测试。
- Docker 验证必须确认 app、PostgreSQL、Qdrant、Redis 健康，并检查应用 health endpoint。

## 8. 部署

- 国内环境使用 Compose 中配置的国内镜像源。
- 代码更新后使用 `./docker-manage.sh restart`，确保镜像包含最新源码；需要排查缓存时使用 `./docker-manage.sh rebuild`。
- 发布验收可使用 `FAULT_DRILL_CONFIRM=YES ./docker-manage.sh fault-drill` 做依赖重启、应用恢复和可选 SSE 断流演练；该命令不得删除数据卷。
- 部署后先检查 `docker compose ps`、健康接口和应用日志，再进行页面测试。
- 不得提交 `.env`、密钥、上传文件、数据库备份或运行时缓存。
