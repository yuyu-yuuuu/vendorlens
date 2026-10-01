# VendorLens 设计说明

## 1. 产品边界

VendorLens 是“项目方案与供应商资料评审 Agent 平台”。用户上传项目需求书、供应商方案、报价单、项目排期和会议纪要，系统围绕一次明确的评审目标，完成结构化提取、横向比较、矛盾发现、证据核验，并生成可追溯报告。

首版只支持可复制文字的 PDF、DOCX、XLSX、Markdown 和 TXT。不提供扫描件 OCR、泛文档聊天、合同法律审查、互联网搜索或自由 Agent 讨论。

## 2. 核心不变量

1. 每条事实性 Claim 至少关联一个 EvidenceItem，否则只能输出“无法判断”和缺失资料。
2. 金额、日期、比例和周期必须精确定位到页码或工作表/单元格，并通过确定性数字匹配。
3. 引用固定到文档版本、原文哈希和位置；新版本不会静默改变旧报告证据。
4. 冲突并列展示，不自动选择“正确”版本。
5. 推荐意见必须公开评审维度、权重和证据。
6. Agent 只负责需要模型判断的工作；解析、检索、引用定位、数字核验、持久化和渲染均为确定性组件。

## 3. 系统架构

采用 monorepo：

- `apps/web`：Next.js、TypeScript、Tailwind CSS、shadcn/ui。
- `services/api`：FastAPI REST 与 SSE；创建分析任务后立即返回 `run_id`。
- `services/worker`：独立 Redis 队列消费者，执行解析、索引和 LangGraph 工作流。
- `packages/core`：领域模型、文档解析、Hybrid RAG、证据门禁、ModelProvider、Trace。
- `evals`：本地标注集、检索/Claim/端到端评测和消融脚本。
- `infra`：Docker Compose、数据库迁移与 Prometheus 配置。

PostgreSQL 保存业务对象、运行状态、LangGraph checkpoint 和 Trace；Redis 只承担队列及轻量通知；ChromaDB 保存向量；BM25 索引本地持久化；原文件经 `DocumentStorage` 接口写入本地目录，未来可替换 S3/MinIO。

## 4. 领域模型

- Project：一次采购或项目评审空间。
- Document：逻辑资料；DocumentVersion：不可变版本和文件哈希。
- ParsedChunk：保留标题层级、页码/段落或工作表/单元格坐标、原文及哈希。
- ResearchTask：一次评审运行，关联目标、状态、`run_id` 和 `trace_id`。
- ResearchPlan：维度、权重、子任务、查询和依赖。
- EvidenceItem：版本固定的原文、定位、哈希和检索分数。
- Claim：可核验陈述、类型、结构化值、置信度及证据引用。
- ReviewResult：确定性与语义校验结果、冲突、缺失资料和待返工 Claim。
- FinalReport：正文、对比矩阵、风险、冲突、缺失信息及 Claim-Evidence 矩阵。
- AgentRun/TraceSpan：节点、检索、模型、工具、重试、核验和状态变化的审计记录。

所有写操作使用事务。上传以项目、文件 SHA-256 和版本号实现幂等；后台任务以稳定 job key 防止重复执行；Claim 和 EvidenceLink 的修改记录审计事件。

## 5. 文档摄取与 Hybrid RAG

解析器输出统一 `ParsedChunk`，同时保留格式特有定位：PDF 页码，DOCX 标题/段落，XLSX 工作表/单元格范围，Markdown/TXT 行区间。空文本 PDF 明确标记为 `ocr_required`，不进入索引。

索引包含：

- `rank_bm25` 词法检索；中文使用字符二元组与词元混合分词。
- ChromaDB 向量检索；默认本地 Embedding，可通过接口替换。
- Reciprocal Rank Fusion：`score(d)=Σ 1/(k+rank_i(d))`，默认 `k=60`。
- 可选 reranker，默认关闭，确保基线可复现。

检索条件由 LangGraph 显式节点集中执行。每个 Planner 子任务生成多个领域子查询并行检索，使用 project、document、version、supplier、type 等 metadata 过滤；检索结果去重并缓存。评测分别运行 BM25、Vector 和 Hybrid，输出 Recall@K、Precision@K、MRR、NDCG。

## 6. 三 Agent 状态图

LangGraph 状态以 `ResearchPlan`、`EvidenceItem[]`、`Claim[]`、`ReviewResult` 和 `FinalReport` 为核心：

`load_context → plan → fan_out_retrieval → analyst_subgraphs → review → revise_claims | finalize → persist_report`

- PlannerAgent：生成维度、权重、可并行子任务和检索查询。
- AnalystAgent：每个子任务最多三轮 Agent→Tool→Observation，产出 Claim 与 EvidenceItem。
- ReviewerAgent：语义判断证据是否支持 Claim，并结合确定性校验结果，最多两次只退回不合格 Claim。

复合子任务并行执行；单项失败使用有界重试。重试耗尽后保留其他成功结果，将任务标记 `partially_completed`，报告附人工处理说明。生产使用 PostgreSQL Checkpointer，测试使用内存 Checkpointer；相同 thread/run id 可在进程重启后恢复。

## 7. 证据门禁

确定性层检查引用存在、项目和版本有效、原文哈希一致、定位可回读以及结构化数字/日期/比例/周期与原文匹配。语义层由 ReviewerAgent 判断证据是否蕴含 Claim、是否断章取义，以及冲突是否完整呈现。

确定性失败不得由模型覆盖。语义失败只回退相关 Claim；超过两次后将 Claim 标记 `unsupported`，报告显示“无法判断”及所需资料。

## 8. API、队列与实时进度

REST 管理项目、文档、版本、任务和报告。上传或创建分析任务后返回资源 ID/`run_id`，耗时工作进入 Redis 队列。SSE 端点按持久化事件序号续传，支持 `Last-Event-ID`，因此刷新页面或 API 重启后仍能恢复进度；WebSocket 不在范围内。

## 9. 可观测性与安全

每次运行生成 `trace_id`。每个节点、检索、模型、工具、重试、校验和状态变化写入父子 `TraceSpan`，记录状态、时间、耗时、模型、Token、估算成本、脱敏参数和错误。禁止保存隐藏思维链、API Key、Authorization header 或原始敏感配置。Prometheus 只暴露聚合指标；LangSmith 是关闭默认的适配器。

## 10. 前端原则

界面围绕单一项目工作台：资料库、分析任务、报告和评测四个视图。进入实施前先用 ImageGen 生成完整视觉概念并取得认可，再提取设计系统、实现桌面/移动页面，并用真实浏览器截图做视觉验收。视觉方向采用编辑部式信息密度、清晰网格和克制强调色，避免紫色渐变与无意义圆角卡片。

## 11. 评测与验收

本地标注集包含可公开分发的合成供应商资料、问题、相关 chunk、结构化字段、冲突和期望报告要素。四层评测为计划拆解、检索、Claim/工具和端到端报告；固定随机种子、模型配置、数据版本和运行元数据。指标仅从脚本实际输出进入 README 或简历。

项目验收：真实 LangGraph checkpoint；工具事务、幂等、审计；真实 BM25+Vector+RRF 对比；数据库 Trace；可恢复 SSE；分层评测；所有量化陈述可由测试产物复现。

## 12. 明确默认值

- Python 3.12 作为服务运行时，Node.js 22 LTS 作为前端运行时。
- SQLAlchemy 2 + Alembic；Pydantic v2；RQ 作为首版 Redis 队列。
- 本地 Embedding 默认 `BAAI/bge-small-zh-v1.5`，测试使用确定性哈希 Embedding，避免下载模型。
- 文档与运行数据默认保存在项目级 Docker volume；日志采用 JSON。
- 首个纵向切片允许通过同步管理命令触发解析和评测，随后接入 Worker/API，不用模拟实现替代真实逻辑。
