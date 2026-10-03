# VendorLens

面向项目方案与供应商资料的证据驱动评审 Agent 平台。它读取需求书、供应商方案、报价、排期和会议纪要，形成带版本、页码或单元格定位的对比结论，而不是做泛化文档聊天。

## 当前可运行能力

- 本地文件存储接口与基于 SHA-256 的幂等上传。
- 文档不可变版本；内容变化自动递增版本号。
- PDF、DOCX、XLSX、Markdown、TXT 统一解析，保留来源定位和原文哈希。
- 叙事文档采用父子 Chunk：最多 320 字的子块负责召回，父块负责提供完整上下文；XLSX 保持一行一个可定位块。
- EvidenceItem 的 `context_text` 将完整父块传给模型，`quote` 保留命中子块原文，`matched_chunk_id` 标识召回块；校验父块 SHA-256 与引用包含关系。旧快照缺少上下文时仍可读取，但不会自动补齐。
- BM25 索引落盘、ChromaDB 向量持久化、RRF 融合和 metadata 过滤；检索只使用每份文档最新的已索引版本。
- Review Project 可登记多家 Vendor；采购方资料与各供应商同名文件相互隔离，检索 metadata 固定供应商归属。
- 确定性推荐引擎支持需求符合性、相对 TCO、承诺可信度、50/30/20 权重、必选需求硬性淘汰和条件推荐。
- 单 Agent 工具箱只开放 11 个已登记的需求、成本和承诺工具；工具参数与证据范围由本地代码校验；成本输入先绑定所引原文的金额、币种、类别和周期，TCO 由确定性工具计算且覆盖模型数值。
- 单个 VendorReviewAgent 一次生成需求、成本和承诺三个结构化领域结果；执行器拒绝未知工具、跨项目/版本证据及输入包之外的证据引用，并允许单领域失败而保留其他结果。
- OpenAI 兼容 Chat Completions 模型适配器支持多轮 `tool_calls`、结构化 Draft、限流/鉴权/协议错误分类和密钥脱敏；离线测试使用假模型与假 HTTP 传输。
- Vendor 级检索输入包只冻结采购方公共材料和当前供应商各文档的最新已索引版本，内部按三个领域组织查询，竞争对手材料不会进入 Agent 输入。
- 数字事实门禁逐条核验需求说明、承诺陈述和成本付款条款中的金额、完整日期、百分比、时长及数量；只使用该条实际引用的证据，失败仅影响所属领域。费用来源片段与字符范围随 `cost_sources` 保存，月费年化、父块重复命中去重、TCO 派生复算复用于降级。
- 可恢复 LangGraph 工作流：Vendor 间并行检索与分析、领域级校验、最多两次局部返工、确定性降级、评分、报告和部分完成语义。
- 内存/SQLite/PostgreSQL Checkpointer、持久化运行事件与支持 `Last-Event-ID` 续读的 SSE HTTP 端点。
- 任务 API、RQ 后台执行、需求/配置快照、领域结果与报告事务落库；重复投递不会重复执行已领取任务。
- 数据库父子 Trace 覆盖工作流节点、供应商检索、Agent 尝试、模型请求、工具与降级；记录耗时、错误类型和已报告 Token，用属性白名单排除提示词、工具原始参数/结果与密钥。
- 项目、供应商、需求与配置管理 API；文档上传、版本列表、来源 Chunk、下载和显式重建索引，支持从空库建立评审输入。
- 数据库执行租约与心跳、写入所有权校验、过期任务扫描与手动恢复；保留原任务快照并继续持久化 checkpoint。
- 可选共享 Bearer Key、上传大小限制、必需费用类别/承诺清单；Token 单价估算与聚合 Prometheus 指标。
- 本地中文 Embedding/CrossEncoder 接入口与检索失败降级；不自动下载权重，默认保持哈希基线。
- PostgreSQL 业务模型、Alembic 事务迁移和 FastAPI 健康检查。
- 完全合成的本地检索标注集与 Recall@K、Precision@K、MRR、NDCG 评测。
- 三层会话记忆：最近消息、带版本依赖的项目评审摘要与显式偏好，支持 Redis 工作缓存、可选 Chroma 摘要检索及本地回退，创建任务时冻结上下文。
- 行业 Skills 文件热加载、按领域注入与任务内容快照；模型端点路由根据请求耗时、错误和领域校验结果更新健康分，支持冷却与受控恢复探测。

VendorReviewWorkflow 和正式模型执行器已接入 Worker 与任务 API。真实模型调用需要配置支持 Chat Completions `tools`/`tool_calls` 的兼容模型；默认测试不访问网络。确定性哈希向量继续作为可复现检索基线，本地模型质量评测、复杂报价语义核验按[实施计划](docs/plans/2026-09-30-implementation-plan.md)继续迭代。完整边界见[供应商评审 Agent 设计](docs/superpowers/specs/2026-10-01-domain-agent-redesign.md)、[模型适配器设计](docs/superpowers/specs/2026-10-02-model-adapter-tools-design.md)与[设计说明](docs/design/2026-09-30-vendorlens-design.md)。

## 前端工作台

`apps/web` 已实现项目资料、评审任务、评审报告、证据核验与运行记录，采用 Next.js、TypeScript、Tailwind CSS 和 Radix/shadcn 组件。默认进入独立示例模式，点击“连接真实项目”使用后端接口。

```bash
cd apps/web
npm ci
npm run dev
```

打开 `http://127.0.0.1:3000`。服务端代理默认连接 `http://127.0.0.1:8000`；其他地址及共享 Key 在 `apps/web/.env.local` 中设置，字段见该目录 `.env.example`。配置不会进入浏览器包。现有 API/Worker 需更新到当前代码，才可读取新增的领域结果与冻结引用接口。

已验证资料上传、评审讨论、任务提交、SSE 进度、报告与固定版本原文回查，以及 Skills 重载、偏好保存。导出支持 JSON 与浏览器打印。视觉与接口验收见[工作台验收记录](docs/design/2026-10-03-frontend-verification.md)。

## 本地启动

要求 Python 3.12+ 与 Docker Desktop。

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
docker compose up -d postgres redis chroma
.venv/bin/alembic upgrade head
.venv/bin/uvicorn vendorlens.api:app --reload
```

API 健康检查：`GET http://localhost:8000/health`。本机 ChromaDB 使用 `http://localhost:8100`；容器内服务通过 `http://chroma:8000` 访问。

注意：`.env.example` 的依赖地址适用于 Compose 内的 API/Worker。若在 Mac 终端启动服务，将 `.env` 中依赖地址改成本机映射地址：数据库 `postgresql+psycopg://vendorlens:vendorlens@localhost:5433/vendorlens`、Redis `redis://localhost:6380/0`、Chroma `http://localhost:8100`，文件目录改为 `data/files`。保留已有模型密钥，不要再次用示例覆盖 `.env`。

真实模型执行器使用 `.env.example` 中的 `VENDORLENS_MODEL_*` 配置；不要把真实 API Key 提交到仓库。

配置 `.env` 后，可运行真实模型与工具调用链的合成冒烟测试：

```bash
.venv/bin/python -m vendorlens.model_smoke
```

重新执行 `pip install -e '.[dev]'` 后也可以使用 `.venv/bin/vendorlens-model-smoke`。该命令只读取内置合成供应商材料，打印三个领域的结构化结果，不写数据库，也不输出模型密钥。

使用 DeepSeek 时配置为：

```env
VENDORLENS_MODEL_PROVIDER=deepseek
VENDORLENS_MODEL_BASE_URL=https://api.deepseek.com
VENDORLENS_MODEL_API_KEY=你的本地密钥
VENDORLENS_MODEL_NAME=deepseek-flash
```

DeepSeek 适配会省略未在其 Chat Completions 文档中声明的 `store` 参数、关闭思考模式并启用 JSON Output，避免多轮工具调用要求回传 `reasoning_content`。密钥只写入被 Git 忽略的 `.env`。

## 后台评审任务

在另一个终端启动 Worker：`.venv/bin/python -m vendorlens.worker`。通过 API 文档 `http://localhost:8000/docs` 可操作以下接口：

Swagger 已提供中文分组、请求示例、响应模型、错误状态和 Try it out 调试。阅读版为 `/redoc`，机器可读规范为 `/openapi.json`。操作说明见[接口文档](docs/api.md)。Try it out 会调用真实接口，创建任务可能产生模型费用；示例 ID 需替换。

| 接口 | 用途 |
| --- | --- |
| `POST /projects/{project_id}/tasks` | 创建任务并入队，返回 HTTP 202 与 task_id/run_id/trace_id |
| `GET /tasks/{task_id}` | 查询 queued/running/completed/partially_completed/failed/enqueue_failed |
| `GET /tasks/{task_id}/report` | 读取报告；未生成时返回 409 |
| `GET /tasks/{task_id}/events` | SSE 进度；支持 Last-Event-ID 或 after 查询参数 |
| `GET /tasks/{task_id}/trace` | 父子执行记录、调用次数与已报告 Token 汇总 |

创建请求示例（替换已有项目和供应商 ID）：

```json
{"objective": "比较供应商方案与三年成本", "vendor_ids": ["供应商ID"]}
```

前置条件：通过管理 API 创建项目和供应商，上传采购方需求书并登记需求，再上传供应商材料并索引。完整路由见[接口文档](docs/api.md)。Worker 从 `storage_root/indexes/bm25.json` 和配置的 Chroma 集合读取索引；摄取流程需要使用同一路径和集合。

提交时冻结需求、项目上下文与 ReviewConfig；没有配置时使用 CNY、36 个月和 50/30/20 权重，必需成本类别为 software/implementation/maintenance。可通过 `queries_by_domain` 覆盖三个领域查询。文档版本在 Worker 检索节点执行时冻结。

事件包括 run.queued、run.started、node.completed、run.finished/run.failed/run.enqueue_failed。终态后事件连接关闭；刷新页面不会重新执行任务。队列失败返回 503 和已创建 task_id；Worker 异常记录脱敏失败事件。Worker 领取时写入执行租约，心跳续租。启动 `python -m vendorlens.recovery --watch` 扫描过期 running 任务；也可调用 `POST /tasks/{task_id}/retry` 恢复执行异常或入队失败任务。有效租约及已有报告的任务返回 409。恢复保留 run_id/trace_id 和快照，旧未结束跨度标记 interrupted；新执行产生新根跨度。共享 Bearer Key 可选，未配置时限可信本地使用；它不是多租户权限系统。

Trace 的 parent_span_id 构成执行树；并行供应商通过独立 ContextVar 上下文保持父子关系。模型接口未提供完整 usage 时，usage_reported=false；Trace 汇总只累加已报告调用，并通过 usage_complete 标明是否完整，没有已报告调用时 Token 为 null。仅在完整 usage 和输入/输出单价均配置时估算金额，按币种汇总，并用 cost_complete 表示完整性；这是配置单价估算，不是供应商账单。耗时包含嵌套子操作，父子 duration_ms 不能简单相加。详细代码学习见[后台任务与 Trace 讲解](docs/learning/2026-10-02-task-runtime-and-trace.md)。

## 当前数据流

```text
上传文件
  → SHA-256 幂等判断与不可变 Version
  → 解析并保存父子 Chunk
  → 子 Chunk 写入 BM25 + ChromaDB
  → 冻结采购方与当前 Vendor 的最新已索引 Version
  → BM25 / Vector 召回并用 RRF 融合
  → 根据 parent_id 回查父 Chunk
  → 生成带父块全文、子块引用、版本、位置和父块原文哈希的 EvidenceItem
  → Vendor 间并行调用单 Agent
  → 成本原文绑定与权威计算、逐条数字门禁
  → 带失败原因的领域级返工与同规则确定性降级
  → 确定性评分并生成报告
```

## 验证

父块上下文接线与代码讲解见[父块上下文与子块引用](docs/learning/2026-10-02-parent-context-and-quotes.md)。成本抽取和数字门禁优先使用经哈希校验的 `context_text`，旧快照仅用 `quote`；需求/承诺工具现读取完整父块；需求判断使用保守的相关子句、否定作用域和限制条件规则，不等于通用语义蕴含校验。接线、示例与边界见[数字事实与成本证据门禁](docs/learning/2026-10-02-numeric-evidence-gate.md)。

```bash
.venv/bin/pytest --cov=vendorlens
.venv/bin/ruff check .
.venv/bin/alembic check
.venv/bin/python -m vendorlens.eval_cli --top-k 3
```

当前提交的实测检索结果来自 7 个合成 chunk、4 个标注查询。样本仅用于打通可复现评测链路，不代表生产质量，也不会直接写入简历。详见[基线说明](docs/results/2026-09-30-retrieval-baseline.md)与[原始结果](docs/results/retrieval-baseline.json)。

费用门禁当前接受明确币种的独立一次性、月费、年费和一次性可选费用；`元/人民币` 按 CNY 解释，支持 USD/EUR 明示代码或币种文字，不自动换汇。含糊的汇总与明细关系、估价、税费、单价、数量乘法、多年打包与周期性可选费用进入缺失信息；无可用费用项时成本领域失败，不能用零 TCO 参与评分。零类别值表示本次已知费用中没有该类别，不证明供应商免费。数字匹配不证明语义蕴含或检索完整性；必需费用类别检查已按项目配置实现，缺项进入 missing_costs 并限制推荐。完整语义核验与复杂报价解析仍待扩展。此次离线测试不调用真实模型。

## 可复现性

所有后续量化结果必须由本仓库的版本化数据、脚本和原始产物复现。

## 会话与运行策略

先创建 `/projects/{project_id}/sessions`，通过 messages 接口保存讨论，在创建评审任务时传入 session_id、user_id 和可选 industry。任务完成后写回评审摘要；偏好通过 `/projects/{project_id}/preferences` 显式设置。

Skills 存放于 skills 目录，支持 JSON 和带 JSON 元数据头的 SKILL.md，文件更新后新任务自动加载，也可调用 `/skills/reload`。在途任务和恢复任务使用提交时的内容快照。

已有 MODEL_* 配置支持单端点运行；配置 VENDORLENS_MODEL_ENDPOINTS JSON 数组后支持多个兼容工具调用端点的健康路由。查看 `/routing/health` 和 `/tasks/{task_id}/routing` 可核对健康分与每次选择。Redis/Chroma 记忆适配开关和窗口参数见 .env.example。

更新现有部署前执行 `.venv/bin/alembic upgrade head` 并重启 API、Worker。结构与边界见[会话记忆、Skills 与模型路由](docs/learning/2026-10-02-conversation-skills-routing.md)，接口使用见[接口文档](docs/api.md)。

## 后端补齐与离线演示

后端切片含管理/上传、恢复、聚合指标与费用估算。租户权限、复杂报价/OCR 和真实模型质量验证仍需后续实现或专项验证。

```bash
.venv/bin/python -m vendorlens.backend_demo --output /tmp/vendorlens-demo-new-folder
```

输出目录必须不存在；演示使用独立 SQLite、真实本地 Chroma/BM25 和持久化 checkpoint，保存 `result.json`，不会调用付费模型或连接业务数据库。演示 Agent 使用确定性替身，不能据此宣称真实模型效果。

本地语义模型需要安装 `.[retrieval]` 并提供已下载目录：`VENDORLENS_EMBEDDING_PROVIDER=local`、`VENDORLENS_EMBEDDING_MODEL_PATH`、可选 `VENDORLENS_RERANKER_MODEL_PATH`。加载使用 local_files_only，依据官方 [SentenceTransformer](https://sbert.net/docs/package_reference/sentence_transformer/model.html) 和 [CrossEncoder](https://sbert.net/docs/package_reference/cross_encoder/model.html) 接口；本次只验证适配行为，没有真实权重质量结果。切换 Embedding 后使用新集合，须重新索引；同目录更换权重也须使用新目录并重新索引。上传与 Worker 必须共享配置和文件目录。

核心学习顺序见[证据门禁与 Trace](docs/learning/2026-10-02-evidence-gate-and-trace.md)。

## 强指标测试与简历

[修订测试报告](docs/results/2026-10-03-challenge-metrics/测试报告.md)记录固定挑战集、真实模型调用、计算口径和失败样例。[简历文本](docs/resume/VendorLens-强指标简历.txt)按已验证的实现与测试范围描述项目。旧模板集的满分结果仅保留作功能回归记录，已撤出简历效果指标；当前诊断暴露了语义判断和复杂报价处理的不足。

任务创建现保存事务 outbox，恢复扫描同时处理未发送记录。升级到 `e72f01b7c391` 后更新 API/Worker/recovery；新增表不改变已有报告。
