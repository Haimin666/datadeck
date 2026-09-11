# DataAgent 数据物料准备方案

## 1. 探查结论

本次只做了只读探查，没有向知识库、OMD 或数仓写入数据。

### 1.1 Apache Ossie

`../ossie` 是 Apache Ossie（当前仓库标注为 `0.2.0.dev0` 草案），定位是语义模型交换标准，不是简单的指标导出表。顶层模型包含：

- `datasets`：逻辑数据集及其物理表来源
- `fields`：字段、类型、表达式、维度和别名
- `relationships`：语义数据集之间的关联关系
- `metrics`：指标名称、SQL 表达式、描述、类型和 AI 上下文
- `custom_extensions`：放置厂商或项目特有元数据

指标的标准表达式形态为：

```yaml
metrics:
  - name: total_revenue
    expression:
      dialects:
        - dialect: ANSI_SQL
          expression: SUM(orders.amount)
    description: 总收入
    datatype: Decimal
    ai_context:
      synonyms: [收入, 销售额]
```

因此，OMD 的物理血缘不能直接全部塞进 Ossie 的 `relationships`：Ossie `relationships` 表达的是语义关联，OMD/ELT 的 `source -> job/transform -> target` 应作为独立血缘物料，或放入 `custom_extensions` 引用。

### 1.2 本地数仓代码

`../lion_dw/app` 当前约有 429 个 Python 文件和 447 个 SQL 文件，Python/SQL 通常成对出现。已抽样看到以下可用于口径沉淀的内容：

- 建表 SQL 含表注释、字段中文注释、类型和分区定义
- Python 中包含实际 SQL、运行日期参数 `run_date`、输入分区和目标分区
- SQL 内含大量中文行内注释，描述业务含义、统计口径、异常排除和时间范围
- 有日表、月表、周表、拉链表等不同产出周期，不能统一按“每日产出”处理
- 存在多层库表引用，例如 `lion_dw_ods`、`lion_dw_dwd`、`lion_dw_dim`、`lion_dw_app`

典型样本：

- `app_bu_loan_amt_month_df`：事业部每月投放表，含事业部、投放时间、本金合计、预算金额、车辆数等字段
- `app_csh_loan_overdue_OUG_AMT_DF`：小贷应收款逾期表，含逾期期次、逾期天数、逾期金额、罚息金额
- `app_gps_wired_dianhuan_monitor_di`：GPS 有线设备垫还监控基础数据表，注释中明确运动时长、停留时长、离线时长和统计日期
- `app_fast_ratify_rule_target_dt`：速批需求指标，注释中包含“当前逾期支付表数”“历史逾期 30+/60+ 次数”等指标候选

### 1.3 Wiki 和 OMD 接入状态

- Wiki 已通过账号密码直连认证获取当前用户信息（HTTP 200），但页面 `70331927` 仍返回 HTTP 404；递归子页面因此无法开始。当前服务的 `/rest/auth/1/session` 也返回 404，暂保留 Basic Auth 兼容访问，待确认正确 Wiki 地址或页面权限
- 已从项目 `.env` 读取 `OMD_BASE_URL` 和 `OMD_TOKEN`（token 未写入日志）并直连成功，已返回 `数仓Doris.default`；后续可继续查询 schema、表描述和血缘
- 项目 `.env` 中 JWT 值未加 shell 引号，直接 `source .env` 会失败；探查时采用按键读取方式绕过，后续应改用 dotenv 加载或补齐安全引号
- 后续方案保留这两类来源，但在连接恢复前不伪造 Wiki/OMD 资产清单

本轮连接恢复后的实际采集结果：

- Wiki 页面 `70331927` 及递归子页面共下载 32 页，落在 `data_material/wiki/70331927/`
- OMD 已切换为 Hive 服务 `数仓HIVE`，仅采集 schema 名以 `lion_dw_` 开头的库表：10 个 schema、5344 张表；其中 647 张热层表已取字段详情，4697 张冷层表保留目录和描述，落在 `data_material/omd/table_metadata.jsonl`
- OMD Hive 共发现 118 个 schema，但本项目只纳入上述 10 个 `lion_dw_*` schema；`lion_dw`（无下划线后缀）明确排除
- 数仓代码盘点已生成 876 个文件、1124 个表引用和 389 个指标候选，落在 `data_material/lion_dw/`
- Wiki 关键词增强提取生成 167 条证据记录，覆盖 28 个页面

## 2. 物料职责边界

| 来源 | 采集内容 | 最终用途 | 权威性 |
|---|---|---|---|
| Wiki `70331927` 及子页面 | 业务术语、指标口径、业务规则、例外和生效版本 | 进入 RAG；生成指标候选和证据 | 业务口径首选 |
| OMD | 服务、库、schema、表、字段、描述、负责人、标签、上下游关系 | 通过 OMD 工具查询；必要时建立短期引用索引 | 线上库表/血缘首选 |
| `../lion_dw` | Python/SQL、注释、建表定义、输入输出、转换逻辑、分区和产出周期 | RAG 中的代码解释；生成指标候选；与 OMD 做差异核验 | 实现证据，不自动等同业务口径 |
| 数仓运行/分区统计 | 实际最新分区、行数、更新时间、成功/失败、延迟 | 每日数据产出情况和质量摘要 | 运行事实 |

关键原则：

1. Wiki 说“业务上怎么算”，代码说“当前实现怎么算”，二者冲突时进入人工修正队列。
2. OMD 说“线上有哪些库表和血缘”，本地代码说“仓库代码声明了什么”，二者不一致时标记差异，不自动覆盖。
3. OMD 描述信息与每日产出事实是两类数据：描述属于元数据，产出情况需要结合分区/行数/调度运行结果采集。
4. Ossie 的语义关系和 OMD 的 ELT 边分开建模，避免把物理加工链误当成业务实体关系。

## 3. 目标物料模型

### 3.1 指标候选对象

指标从 Wiki、代码和建表注释合并生成，但初始状态必须是 `candidate`，不能直接视为已确认口径。

```json
{
  "metric_id": "metric.overdue_payment_schedule.count",
  "name": "当前逾期支付表数",
  "aliases": [],
  "definition": "待业务确认",
  "formula": "SUM(CASE WHEN ... THEN 1 ELSE 0 END)",
  "grain": "待确认",
  "time_window": "待确认",
  "filters": [],
  "dimensions": [],
  "source_tables": [],
  "source_fields": [],
  "ossie_expression": {
    "dialects": [{"dialect": "ANSI_SQL", "expression": "..."}]
  },
  "provenance": [
    {"type": "wiki", "id": "70331927", "location": "待采集"},
    {"type": "code", "path": "../lion_dw/app/...py", "lines": "..."}
  ],
  "status": "candidate",
  "review": {"owner": null, "reviewed_at": null, "comment": null}
}
```

人工确认后才生成/更新 Ossie `metrics`，并在 `ai_context` 中保留别名、使用限制和证据引用。内部模型中的 `definition`、`grain`、`time_window`、审核状态等字段可通过 `custom_extensions` 扩展，不强行塞进 Ossie 核心字段。

### 3.2 Ossie 语义模型

第一版不为全量 400 多张表生成完整语义模型，而是按主题域建立小模型：

```yaml
version: "0.2.0.dev0"
semantic_model:
  - name: loan_risk_semantic_model
    description: 贷款及风险分析语义模型
    ai_context:
      instructions: 仅使用已审核指标；无法确认口径时返回待确认状态
    datasets: []
    relationships: []
    metrics: []
    custom_extensions:
      - vendor_name: DATADECK
        data: '{"source_manifest":"...","review_status":"draft"}'
```

每个 dataset 至少需要物理来源、描述、字段、类型和别名；只有确认了语义主外键后才生成 Ossie `relationships`，不能从 SQL JOIN 直接推断为业务关系。

### 3.3 OMD/ELT 边对象

```json
{
  "edge_id": "elt:<source>:<job>:<target>",
  "source": {"fqn": "db.schema.source_table", "columns": []},
  "transform": {
    "job": "app_xxx_df",
    "kind": "sql",
    "expression_evidence": "../lion_dw/app/app_xxx_df.py",
    "confidence": "observed"
  },
  "target": {"fqn": "db.schema.target_table", "columns": []},
  "observed_by": ["omd", "code"],
  "status": "active",
  "last_seen_at": null
}
```

边的 `confidence` 至少区分 `observed`、`inferred`、`conflict`、`unverified`。OMD 没有完整依赖时，以代码解析结果补边，但必须标记为推断，并进入核验清单。

### 3.4 每日数据产出对象

每日产出不能只从表注释推断，建议单独记录：

```json
{
  "table_fqn": "db.schema.table",
  "schedule_type": "daily",
  "partition_field": "dt",
  "expected_partition": "2026-09-11",
  "latest_partition": "2026-09-10",
  "row_count": null,
  "last_success_at": null,
  "delay_minutes": null,
  "quality_status": "unknown",
  "evidence": {"omd": null, "runtime": null, "warehouse_query": null}
}
```

日、周、月、实时和不定期表分别建模；`dt` 分区存在不代表当天任务成功，还要核对行数、更新时间和调度运行状态。

## 4. 数据准备阶段实施计划

### 当前进度快照

截至本轮：Wiki 下载、关键词证据提取、数仓代码盘点和 Hive `lion_dw_*` OMD 元数据采集已完成；指标尚未人工审核，尚未写入 RAG，Ossie 已生成 draft 并通过结构校验，尚未生成 approved 版本。当前可直接进入指标人工修正和 RAG 入库准备。

### P0：恢复连接和确定边界

- [ ] 修复 Wiki 访问配置，验证页面 `70331927`、子页面、版本和附件可读
- [ ] 修复 OMD TLS/网络/Token 配置，验证 service/database/schema 查询
- [ ] 确认 OMD 目标服务、库、schema 和血缘最大深度
- [ ] 确认每日产出事实的可用来源：调度平台、分区查询、表统计或任务日志
- [ ] 确认业务负责人、敏感字段和允许进入 RAG 的范围

验收：获得真实的 Wiki 页面清单和 OMD 服务清单，失败项有明确原因，不以空结果代替成功。

### P1：本地代码资产盘点

- [ ] 扫描所有 Python/SQL 文件、Git commit、更新时间和文件配对关系
- [ ] 提取建表表名、表注释、字段名、字段注释、类型和分区字段
- [ ] 提取 Python/SQL 的目标表、输入表、JOIN、过滤、聚合、窗口函数和 `run_date`
- [ ] 识别日/周/月/拉链/临时表命名和产出模式
- [ ] 先产出代码资产清单和解析失败清单，不修改原数仓代码

验收：每个可解析任务都能形成“任务—输入—转换—输出—分区”记录，不能解析的文件可定位到路径和原因。

### P2：Wiki 与代码生成指标候选

- [ ] 从 Wiki 章节识别术语、指标、规则、别名、口径版本和例外
- [ ] 从 SQL 注释、字段注释和聚合表达式识别指标候选
- [ ] 按名称、别名、字段和来源表进行候选合并，但保留所有证据
- [ ] 对同名不同公式、同公式不同名称、单位不一致和时间窗口不一致建立冲突项
- [ ] 生成待人工修正清单，人工审核后再转换为 Ossie metrics

验收：每个候选指标都有来源、公式、粒度、时间范围、输入字段和审核状态；缺任一关键项不得标记为 approved。

### P3：OMD 元数据和 ELT 边

- [ ] 获取表/字段描述、负责人、标签、类型、更新时间和已登记血缘
- [ ] 获取上下游血缘，保存节点和边的原始响应摘要以及采集时间
- [ ] 将本地代码解析的输入输出与 OMD 边做匹配、补充和冲突比对
- [ ] 对 OMD 缺失但代码明确的边标记 `inferred`，不直接伪装成 OMD 事实
- [ ] 对表描述缺失、负责人缺失和血缘断点生成治理清单

验收：指定表可以输出 OMD 事实、代码证据、差异和未确认项四部分结果。

### P4：每日产出快照

- [ ] 根据 OMD 表元数据和代码分区模式识别期望产出周期
- [ ] 每日采集最新分区、行数、更新时间、任务成功状态和延迟
- [ ] 区分“有分区但数据为空”“分区未更新”“任务失败”“非每日表”
- [ ] 生成表级每日产出摘要，不把动态运行数据永久写成静态 RAG 事实
- [ ] 为 DataAgent 提供“最近产出情况”和“数据截止日期”工具结果

验收：Agent 回答“某表今天是否产出、数据到哪天”时，能给出检查时间、分区、状态和证据。

### P5：RAG、Ossie 和 OMD 联动

- [ ] Wiki 业务规则、审核后的指标和代码解释进入 RAG
- [ ] Ossie 文件作为指标/语义模型规范产物保存，并通过 `../ossie/validation/validate.py` 校验
- [ ] 库表、字段、实时描述和血缘查询优先走 OMD 工具
- [ ] DataAgent 路由：口径问题先 RAG；库表/血缘问题先 OMD；取数问题先 OMD 校验后生成 SQL
- [ ] 回答中区分业务定义、代码实现、OMD 观测和每日运行事实

验收：三类问题不会串源：业务口径有 Wiki/审核指标证据，血缘有 OMD 证据，代码逻辑有文件定位，产出情况有运行快照。

## 5. 人工修正闭环

人工修正不是一次性导入步骤，而是指标物料的正式生命周期：

1. 自动抽取生成 `candidate`
2. 规则合并和冲突检测生成 `needs_review`
3. 业务人员确认定义、粒度、时间窗、过滤条件、单位和负责人
4. 生成 `approved` Ossie 指标及 RAG 文档
5. 后续 Wiki/代码/OMD 变化触发重新审核，而不是静默覆盖
6. 保留版本、审核人、审核时间和变更原因

建议首批只选一个主题域和 10～20 个高频指标做闭环，验证抽取、人工修正、Ossie 校验、RAG 引用和 Agent 回答后再扩展全量。

## 6. 首批交付物

- `data_material_manifest.json`：Wiki、OMD、代码和运行事实的来源清单
- `code_inventory.jsonl`：数仓任务、输入输出、分区和解析状态
- `metric_candidates.jsonl`：指标候选、公式、来源、冲突和审核状态
- `semantic_model.yaml`：审核后的 Apache Ossie 语义模型
- `elt_edges.jsonl`：OMD 观测边、代码推断边和差异状态
- `table_production_daily.jsonl`：每日产出快照
- RAG 知识库文档：业务规则、审核指标和代码解释，带来源与版本
- DataAgent 评测集：口径、血缘、代码逻辑、每日产出和冲突场景

当前建议先做 P0 和 P1。Wiki/OMD 连接恢复并确定范围后，再开始 P2/P3，避免在来源不可读时批量生成不可追溯的“知识”。
