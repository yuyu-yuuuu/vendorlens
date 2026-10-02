# VendorLens

面向项目方案与供应商资料的证据驱动评审 Agent 平台。它读取需求书、供应商方案、报价、排期和会议纪要，形成带版本、页码或单元格定位的对比结论，而不是做泛化文档聊天。

## 当前可运行能力

- 本地文件存储接口与基于 SHA-256 的幂等上传。
- 文档不可变版本；内容变化自动递增版本号。
- PDF、DOCX、XLSX、Markdown、TXT 统一解析，保留来源定位和原文哈希。
- 叙事文档采用父子 Chunk：最多 320 字的子块负责召回，父块负责提供完整上下文；XLSX 保持一行一个可定位块。
- BM25 索引落盘、ChromaDB 向量持久化、RRF 融合和 metadata 过滤；检索只使用每份文档最新的已索引版本。
- Review Project 可登记多家 Vendor；采购方资料与各供应商同名文件相互隔离，检索 metadata 固定供应商归属。
- 确定性推荐引擎支持需求符合性、相对 TCO、承诺可信度、50/30/20 权重、必选需求硬性淘汰和条件推荐。
- 单 Agent 工具箱只开放 11 个已登记的需求、成本和承诺工具；工具参数与证据范围由本地代码校验，TCO 由确定性工具计算且覆盖模型数值。
- 单个 VendorReviewAgent 一次生成需求、成本和承诺三个结构化领域结果；执行器拒绝未知工具、跨项目/版本证据及输入包之外的证据引用，并允许单领域失败而保留其他结果。
- OpenAI 兼容 Chat Completions 模型适配器支持多轮 `tool_calls`、结构化 Draft、限流/鉴权/协议错误分类和密钥脱敏；离线测试使用假模型与假 HTTP 传输。
- Vendor 级检索输入包只冻结采购方公共材料和当前供应商各文档的最新已索引版本，内部按三个领域组织查询，竞争对手材料不会进入 Agent 输入。
- 可恢复 LangGraph 工作流：Vendor 间并行检索与分析、领域级校验、最多两次局部返工、确定性降级、评分、报告和部分完成语义。
- 内存/PostgreSQL Checkpointer、持久化运行事件与支持 `Last-Event-ID` 续读的 SSE HTTP 端点。
- 任务 API、RQ 后台执行、需求/配置快照、领域结果与报告事务落库；重复投递不会重复执行已领取任务。
- 数据库父子 Trace 覆盖工作流节点、供应商检索、Agent 尝试、模型请求、工具与降级；记录耗时、错误类型和已报告 Token，用属性白名单排除提示词、工具原始参数/结果与密钥。
- PostgreSQL 业务模型、Alembic 事务迁移和 FastAPI 健康检查。
- 完全合成的本地检索标注集与 Recall@K、Precision@K、MRR、NDCG 评测。

VendorReviewWorkflow 和正式模型执行器已接入 Worker 与任务 API。真实模型调用需要配置支持 Chat Completions `tools`/`tool_calls` 的兼容模型；默认测试不访问网络。确定性哈希向量继续作为可复现检索基线，真实中文 Embedding、模型成本计费统计、Worker 中断恢复和前端工作台按[实施计划](docs/plans/2026-09-30-implementation-plan.md)继续迭代。完整边界见[供应商评审 Agent 设计](docs/superpowers/specs/2026-10-01-domain-agent-redesign.md)、[模型适配器设计](docs/superpowers/specs/2026-10-02-model-adapter-tools-design.md)与[设计说明](docs/design/2026-09-30-vendorlens-design.md)。

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

前置条件：项目、供应商、需求目录已通过现有数据层创建，材料已摄取并建立索引。项目/上传管理 API 尚未实现，不能只靠以上四个接口从空库开始。Worker 从 `storage_root/indexes/bm25.json` 和配置的 Chroma 集合读取索引；摄取流程需要使用同一路径和集合。

提交时冻结需求、项目上下文与 ReviewConfig；没有配置时使用 CNY、36 个月和 50/30/20 权重，必需成本类别为 software/implementation/maintenance。可通过 `queries_by_domain` 覆盖三个领域查询。文档版本在 Worker 检索节点执行时冻结。

事件包括 run.queued、run.started、node.completed、run.finished/run.failed/run.enqueue_failed。终态后事件连接关闭；刷新页面不会重新执行任务。队列失败返回 503 和已创建 task_id；Worker 异常记录脱敏失败事件。首版使用原子领取防止重复执行，但硬中断可能使任务停在 running；尚无自动恢复/重试入队端点。API 暂无鉴权，仅用于可信本地环境。

Trace 的 parent_span_id 构成执行树；并行供应商通过独立 ContextVar 上下文保持父子关系。模型接口未提供完整 usage 时，usage_reported=false；Trace 汇总只累加已报告调用，并通过 usage_complete 标明是否完整，没有已报告调用时 Token 为 null。不根据 Token 猜测账单金额。耗时包含嵌套子操作，父子 duration_ms 不能简单相加。详细代码学习见[后台任务与 Trace 讲解](docs/learning/2026-10-02-task-runtime-and-trace.md)。

## 当前数据流

```text
上传文件
  → SHA-256 幂等判断与不可变 Version
  → 解析并保存父子 Chunk
  → 子 Chunk 写入 BM25 + ChromaDB
  → 冻结采购方与当前 Vendor 的最新已索引 Version
  → BM25 / Vector 召回并用 RRF 融合
  → 根据 parent_id 回查父 Chunk
  → 生成带版本、位置和原文哈希的 EvidenceItem
  → Vendor 间并行调用单 Agent
  → 领域级校验、局部返工与确定性降级
  → 确定性评分并生成报告
```

## 验证

```bash
.venv/bin/pytest --cov=vendorlens
.venv/bin/ruff check .
.venv/bin/alembic check
.venv/bin/python -m vendorlens.eval_cli --top-k 3
```

当前提交的实测检索结果来自 7 个合成 chunk、4 个标注查询。样本仅用于打通可复现评测链路，不代表生产质量，也不会直接写入简历。详见[基线说明](docs/results/2026-09-30-retrieval-baseline.md)与[原始结果](docs/results/retrieval-baseline.json)。

## 可复现性

所有后续量化结果必须由本仓库的版本化数据、脚本和原始产物复现。
