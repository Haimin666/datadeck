# cline 计划：A 类缺失端点补齐路线图

> 背景：前后端 API 对齐审查发现 A 类（前端调用、后端 404）缺失端点共 8 组。
> 本计划按决策划分两批：**第一批实施**（model-providers / projects / workspace / viewer /
> knowledge / graph / evaluation），其余列入文末 TODO。
> 原则：对齐上游 Yuxi-Know 的接口契约（前端组件消费什么字段，后端就返回什么形状），
> 但实现尽量复用 datadeck 已有基础设施（EnvModelProvider、rag_store、PG `server/db.py`），
> 不移植上游的完整子系统。验收以"前端页面可用、无 404/422"为准。

---

## 第一批实施

### M1. model-providers（最高优先级，主聊天页模型选择器依赖）

**现状**
- 前端契约（`system_api.js` + 3 个选择器组件 + `useModelStatus.js`）：
  - `GET /api/system/model-providers/models/v2` → `data` 形如
    `{ [providerId]: { name, display_name, models: [{ spec, display_name }] } }`
  - `GET /api/system/model-providers/status?spec=` → `{ status: available|unavailable|error, message }`
  - `POST /api/system/model-providers/cache/refresh`
  - provider CRUD：`GET/POST/PUT/DELETE /api/system/model-providers[/{id}]`
  - `GET /api/system/model-providers/remote-models?provider=&base_url=&api_key=`
- 后端现状：`EnvModelProvider`（`src/datadeck/adapters/model_provider.py`）只从
  `DATADECK_MODEL*` 环境变量解析**单个**聊天模型。

**任务**
| # | 任务 | 要点 |
|---|---|---|
| 1.1 | 新建 `server/routers/model_provider_router.py` | 读环境变量组织为多 provider 分组返回 `models/v2`（按 kind 参数支持 chat/embedding/rerank）；embedding 组可直接映射 `DATADECK_EMBEDDING_*`（rag_store 已在用） |
| 1.2 | `GET /status?spec=` | 轻量连通性检查（调 provider `/models` 或 1 次最小调用，60s 超时缓存） |
| 1.3 | `POST /cache/refresh`、`GET /new/...` | refresh 返回成功即可；remote-models 用 base_url+api_key 现场请求 `{base_url}/models` 转 v2 形状 |
| 1.4 | provider CRUD | datadeck 无 DB 供应商表 → **v1 内存存储 + 环境变量合并**（写操作进程内生效，标注实验性） |
| 1.5 | 环境变量扩展 | `DATADECK_MODELS_EXTRA`（JSON/分号分隔多模型 spec），让 `get_all_specs` 返回多模型，选择器才有得选 |

**验收**：聊天页模型下拉列出 ≥1 个可用模型；检查按钮返回真实状态；管理面板 providers tab 无报错。
**预估**：1.5–2 天

### M2. projects（AgentPanel 项目选择 / 新建会话流程依赖）

**现状**
- 前端契约（`project_api.js` + `stores/projects.js` + `ProjectSelectionSection.vue`）：
  - `GET /api/projects`、`GET /api/projects/history-candidates`（预取）
  - `POST /api/projects` body `{ request_id, name, mode, path }`
  - `PUT/DELETE /api/projects/{id}`
- datadeck 语义映射：**一个 project = 一个工作目录（workdir）**。后端 `p1_router.py` 注释已明确
  workspace 概念不存在，需新建。

**任务**
| # | 任务 | 要点 |
|---|---|---|
| 2.1 | 新建 `projects` 表 + repository（沿用 `server/db.py`/models 风格） | 字段：id, name, mode, path, owner_id, created_at |
| 2.2 | `server/routers/project_router.py` | 5 个端点按契约实现；`path` 安全校验（限定配置根目录白名单内） |
| 2.3 | 与 agent run 打通 | 前端 `createThread` 本就传 `project_id`（现被 pydantic 静默丢弃）→ `ThreadCreateRequest` 增加可选 `project_id`，run 上下文注入 workdir |

**验收**：面板可新建/选择项目；新会话关联项目；删除项目不级联误删数据。
**预估**：1.5–2 天

### M3. workspace / viewer 文件系统（M2 的延伸）

**前端契约**
- `workspace_api.js`：`GET /api/workspace/tree?path=`、读/写/删除文件、上传、搜索、`GET /api/workspace/knowledge/tree`
- `viewer_filesystem.js`：`GET /api/viewer/filesystem/{threadId}/tree?path=`、`GET .../file?path=`（AgentPanel 文件树/预览，thread 级沙箱）

**任务**
| # | 任务 | 要点 |
|---|---|---|
| 3.1 | 新建 `workspace_router.py` + `viewer_router.py` | 基于 `DATADECK_WORKDIR`（默认 `./data/workspace`）根目录约束 + 路径穿越防护（resolve 后必须在根内） |
| 3.2 | viewer 按 thread 隔离 | 目录=`{workdir}/threads/{threadId}`，仅暴露该子树；读取扩展名白名单（md/txt/csv/json/图片） |
| 3.3 | 上传/保存/删除 | 原子写（临时文件+rename）；大小上限 10MB |
| 3.4 | `workspace/knowledge/tree` | 返回目录树 JSON，与 M4 knowledge 上传流程对接 |

**验收**：AgentPanel 文件树可浏览 thread 产物并预览；/workspace 页可浏览、编辑、上传。
**预估**：2–3 天

---

### M4. knowledge（自有 RAG 之上的管理面，砍掉上游多余面）

**现状**：后端已有 `/api/rag/*`（add/search/delete，Qdrant+BM25 混合检索），`system/discovery`
返回 `knowledge: false` 隐藏入口。前端 `knowledge_api.js` 是上游完整知识库 API（kb CRUD、文件夹树、
mindmap、workspaces）——**只对齐实际使用的子集**，其余不做。

**实施范围（新 `kb_router.py`，前缀 `/api/knowledge`）**
| # | 端点组 | 说明 |
|---|---|---|
| 4.1 | `GET/POST /databases`、`GET/PUT/DELETE /databases/{id}` | kb 元数据表（name, description, domain, doc_count）；kb 的 domain 作为 rag_store 检索过滤键 |
| 4.2 | `POST/GET .../documents`、`DELETE .../documents/{doc_id}` | 落到 `rag_store.add_document/delete_document`；文档元数据存 PG（chunk 细节仍在 Qdrant/BM25 内） |
| 4.3 | `POST .../documents/search` | 直通 `rag_store.search(domain=kb.domain)`，返回 `{items, total}` |
| 4.4 | `POST /files/upload?kb_id=`、`GET /files/supported-types` | multipart 上传 → 抽文本（txt/md/csv/json 原生；pdf 可选）→ chunk → 入库；`documentExists` 按 filename 查重 |
| 4.5 | 能力位翻转 | `system/discovery` 的 `knowledge` 改为动态：Qdrant 或 BM25 可用即 `true`，前端入口自然恢复 |

**明确不做**（前端调用保留 catch 降级）：mindmap、folders 文件夹树、workspaces、chunk-presets、
批量导入 workspace 文件。
**验收**：知识库页可建库→传文档→检索命中；agent 的 rag_search 工具能按 kb 过滤取数。
**预估**：3–4 天（4.4 文本抽取是变量）

### M5. knowledge graph（kb 关联功能，依赖 M4）

**前端契约**（`graph_api.js`，仅 4 个调用）：`GET /api/graph/list`、`/subgraph`、`/stats`、`/labels`
（`KnowledgeGraphSection.vue` 可视化）。

**任务**
| # | 任务 | 要点 |
|---|---|---|
| 5.1 | 元数据与关系抽取 | M4 入库 pipeline 追加：LLM（复用 EnvModelProvider）做实体/关系抽取，存 PG `graph_nodes`/`graph_edges`（kb_id, entity, type, source_doc_id） |
| 5.2 | `graph_router.py` | list/subgraph/stats/labels 四个只读端点，返回前端 ECharts 期望的 `{nodes, links}` 形状（实现时对照组件字段再定 schema） |
| 5.3 | 降级 | 无图谱数据返回空集，前端已有空态 |

**验收**：知识库详情页图谱 tab 展示实体关系图；无数据不报错。
**预估**：2 天（含 LLM 抽取 prompt 调试）

### M6. evaluation（对齐前端 `/api/evaluation/*`，复用自有 RAG 做评测素材）

**前端契约**（`knowledge_api.js` 内 `evaluationApi`，9 个端点，全部按 kb 组织）：
- datasets：`upload / list / get(分页) / delete / download / generate / resume`
- runs：`POST runs`、`GET runs`、`GET runs/{id}`（分页+result_filter）、`DELETE runs/{id}`

**任务**
| # | 任务 | 要点 |
|---|---|---|
| 6.1 | 建表 | `eval_datasets` / `eval_dataset_items`（question, answer, metadata）/ `eval_runs` / `eval_run_items`（期望/实际/得分/错误） |
| 6.2 | `evaluation_router.py` 9 端点 | upload=解析上传文件（xlsx/csv/jsonl）生成 items；download=导出 csv；generate/resume=基于 kb 文档 LLM 批量生成 QA 对（后台任务+status 轮询） |
| 6.3 | run 执行器 | 复用 agent chat 入口逐条跑 question（可带 kb 检索）；规则评分（关键词/包含/完全匹配）先做，LLM 评分留接口 |
| 6.4 | 清理旧 `/api/eval/*` | `eval_router.py`（cases/runs 简版）未被前端调用 → 标记 deprecated，M6 完成后删除 |

**验收**：评测页可上传/生成数据集、发起评测、查看逐条结果与得分。
**预估**：3–4 天

---

## 第一批顺序与总量
- 顺序：**M1 → M2 → M3 → M4 → M5 → M6**（M1 独立收益最大先做；M3 依赖 M2 的 workdir；M5/M6 依赖 M4）
- 预估总量：**13–17 人天**
- 每模块完成即页面实测验收，不跨模块并行。

---

## TODO（暂不实施，仅记录）

| 模块 | 缺失端点 | 备注 |
|---|---|---|
| mcp-servers | CRUD/test/status/tools/toggle | 前端已有静默降级；真 MCP 集成工作量 ≫ 第一批单模块，暂缓 |
| skills | `/api/system/skills/*` + `/api/skills/*` | 同上，降级为空列表 |
| departments | `/api/departments` 组 | 用户管理页 catch 降级；datadeck 无组织概念 |
| OIDC 登录 | `oidc/config`、`oidc/login-url`、`exchange-code` | 前端已隐藏按钮，主登录正常；按需再补 |
| CLI 认证授权 | `cli/sessions/{code}` + approve | 调试功能，可删页面代替 |
| OCR | `ocr/options`、`ocr/health` | 附件上传弹窗内调用，catch 降级 |
| impersonate / check-uid | 调试用 | Debug 页可删 |
| **active_run 路径 404** | 前端 `/api/agent/thread/{id}/active_run` | **TODO 中最优先**：一行改路径为 `/api/chat/thread/...`，主流程 bug |
| **dashboard 统计 422** | type/time_range 枚举失配 | `getCallTimeseries`/`getThreadStats` 参数对齐，半天内 |
| rag_store 无 KB 分域 | `domain` 参数已有但无 kb 层 | M4 落地后自动解决 |

---

## 附：B 类（参数失配）遗留清单
端点存在但字段被忽略/形状不符，修对应端点时顺手对齐：
- `getFeedbacks` 的 `agent_id` 未参与 SQL 过滤（伪参数）
- `apikeyApi.get(id)` 无对应端点；`list(skip,limit)` 被忽略
- `createThread` 的 `request_id/project_id`、`createAgentRun` 的 `created_by_run_id` 被 pydantic 丢弃（M2 补 `project_id`）
- `searchThreads`/`getAgents`/`getThreadStats` 的 `include_subagents`/`agent_id` 被忽略
- vite 代理 `http://api:5050` vs uvicorn 8000，开发环境端口需统一
