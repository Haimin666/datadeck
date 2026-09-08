# DataDeck 开发计划（修订版）

> 基于 2026-09 进度复核修订。原计划四阶段不变，已完成项移出任务清单，剩余任务按断点优先重排。
> 执行约定：每阶段完成即验收，验收通过才推进下一阶段；跑不通的工具先占位，链路必须通。

## 进度基线（2026-09-09 复核）

| 阶段 | 完成度 | 说明 |
|---|---|---|
| 阶段一 MVP | 50% | 架构/调度/循环上限/SQL校验已有；缺三大工具 |
| 阶段二 工程化 | 35% | DDL拦截/语法校验/重试已超前完成；RAG升级待做 |
| 阶段三 Harness | 30% | 调度中心/审计/重试骨架已有 |
| 阶段四 产品化 | 25% | FastAPI+SSE+双通道认证已提前完成 |

---

## 阶段一：MVP 闭环

**已完成（不再列入）**：项目架构（ports+adapters）、LangGraph 调度（State/条件分支/recursion_limit）、多轮+SSE 流式、SQL 只读校验 sql_guard。

| # | 任务 | 方案 | 状态 |
|---|---|---|---|
| 1.1 | SQL 执行工具 | asyncpg 连 **本地 PG 测试**（`DATADECK_SQL_DSN` 可配，后续换 Doris 只改 env）；sql_guard 前置校验；自动行数限制 | ✅ |
| 1.2 | OMD 元数据工具 | 移植现有 omd-query skill → 工具集：databases/schemas/tables/**table_schema(字段)**/lineage；env 可配，未配置返回占位提示 | ✅ |
| 1.3 | Text2SQL 生成链 | 数据助手系统提示（工具选择指引 + fewshot）：口径→RAG、表结构→OMD、取数→SQL | ✅ |
| 1.4 | RAG 基础三件套 | **从零搭建**：chunker + BM25（纯本地，必可用）+ 可选 BGE/OpenAI 兼容 embedding（配置后混合检索）；PG 存储；rag_search 工具 + 文档入库 API | ✅ |

**验收标准**：
- [x] 纯口径问题（RAG 命中指标口径文档）
- [x] 纯表结构问题（OMD/PG information_schema 返回真实字段）
- [x] 简单 SQL 查询返回真实数据（PG 实测）
- [x] 自动判断调用哪个工具（真实 LLM E2E 验证）
- [x] 多轮简单循环（既有能力）

## 阶段二：工程化强化

**已完成（不再列入）**：DDL 全拦截、sqlglot 语法校验+格式化、SQL 自检重试+封顶警示、循环上限、多轮记忆、摘要压缩。

| # | 任务 | 状态 |
|---|---|---|
| 2.1 | RAG 生产级检索：BM25+向量 RRF 混合、Rerank、增量更新 | ✅ |
| 2.2 | 决策优化：数据助手 fewshot、任务分类路由辅助决策 | ✅ |
| 2.3 | SQL 加固收尾：自动 LIMIT、大表扫描预判（EXPLAIN 行数阈值拒绝） | ✅ |
| 2.4 | Token 预算控制：动态裁剪+硬顶（从"记录"升级为"控制"） | ✅ |

**验收标准**：
- [x] 混合检索召回提升（评测集对比 BM25 vs RRF）
- [x] 任务分类路由（口径/结构/取数/闲聊 四类判定）
- [x] SQL 强制 LIMIT + EXPLAIN 预判拒绝高开销
- [x] Token 硬顶裁剪，多轮不超限

## 阶段三：Harness + 高级能力

**已有骨架（复用）**：统一工具调度中心、run_events 全链路审计+SSE 回放、ModelRetry+SQL 自检、HITL 审批。

| # | 任务 | 状态 |
|---|---|---|
| 3.1 | 全局熔断/降级（工具级错误率熔断→友好降级） | ✅ |
| 3.2 | Self-Reflection 扩展：SQL 表存在性/字段对照真实元数据校验；RAG 答案引用核对 | ✅ |
| 3.3 | 复杂任务拆解分步执行（RAG→OMD→SQL 串联 fewshot+todo 引导） | ✅ |
| 3.4 | 缓存：元数据 TTL 缓存 + 高频答案缓存 | ✅ |

**验收标准**：
- [x] 工具连续失败触发熔断并降级
- [x] SQL 引用不存在表/字段被拦截并自动修正
- [x] RAG 答案无引用依据时被要求复核
- [x] 元数据/答案缓存命中
- [x] 三步串联任务可自主完成

## 阶段四：评估 + 产品化

**已完成（移出清单）**：FastAPI+SSE（心跳/续传/终态）、JWT+APIKey 双通道、uid 用户隔离。

| # | 任务 | 状态 |
|---|---|---|
| 4.1 | 监控：run_events 自建统计（耗时/失败率/工具选择准确率）+ LangSmith 可选接入 | ✅ |
| 4.2 | 评测体系：评测集 + 忠实度/召回率/工具选择指标，量化迭代 | ✅ |
| 4.3 | 长期记忆落地：MemoryStore 内存 stub → PG 持久化 | ✅ |
| 4.4 | Ossie 指标规范：指标注册表（名称/别名/口径/公式/单位），检索时别名消解 | ✅ |
| 4.5 | 业务域隔离：RAG 文档/OMD 查询按业务域过滤 | ✅ |

**验收标准**：
- [x] 可监控（统计报表接口）
- [x] 可评测（批量评测跑分）
- [x] 长期记忆跨会话生效
- [x] 指标别名可消解
- [x] 业务域数据隔离

---

## 技术决策记录

1. **SQL 执行**：阶段一用 asyncpg 连本地 PG 测试；切 Doris 只改 `DATADECK_SQL_DSN` + `DATADECK_SQL_DIALECT=doris`（sqlglot 已支持）。
2. **OMD**：移植 `~/.agents/skills/omd-query/scripts/omd_query.py` 的 API 调用（base/token/service env 可配）；token 过期时工具返回友好提示（占位），不影响链路。
3. **RAG**：自研 chunker + BM25（零依赖必可用）；embedding 走 OpenAI 兼容 `/embeddings`（`DATADECK_EMBEDDING_*` 配置），未配置自动降级纯 BM25；存储 PG（rag_documents/rag_chunks），后续可平滑换 Qdrant。
4. **新增 env**：见 `.env.example`（DATADECK_SQL_* / OMD_* / DATADECK_EMBEDDING_* / DATADECK_RAG_*）。
5. **RAG 栈**（用户指定）：Qdrant(localhost:6333) + SiliconFlow BGE-M3(1024维) + BGE-Reranker-V2-M3；BM25 本地兜底。
6. **OMD fallback**：OMD 未配置时自动降级 PG information_schema（真实元数据），生产切 Doris 同理。
7. **熔断语义**：业务性失败（如"表不存在"）不触发熔断计数，只有服务性失败（网络/认证）才计。
8. **评测体系**：evaluation_cases/evaluation_runs 表 + POST /api/eval/runs 批量跑分（工具准确率+忠实度）。
