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

目标采用 monorepo；当前后端纵向切片集中在 `src/vendorlens`，待前端实施时再拆出独立应用包，避免过早制造空目录：

- `src/vendorlens`：当前领域模型、解析、索引、Hybrid RAG、检索协调、证据门禁、LangGraph 工作流和 API。
- `services/api`：FastAPI 容器入口；当前提供健康检查，任务 REST/SSE 尚待接线。
- `services/worker`：RQ Worker 容器入口；具体解析和分析 Job 尚待接线。
- `apps/web`：计划中的 Next.js、TypeScript、Tailwind CSS、shadcn/ui 工作台。
- `evals`：本地标注集、检索/Claim/端到端评测和消融脚本。
- `migrations` 与 `docker-compose.yml`：数据库迁移和本地依赖服务。

PostgreSQL 保存业务对象、运行状态、LangGraph checkpoint 和 Trace；Redis 只承担队列及轻量通知；ChromaDB 保存向量；BM25 索引本地持久化；原文件经 `DocumentStorage` 接口写入本地目录，未来可替换 S3/MinIO。

## 4. 领域模型

- Project：一次采购或项目评审空间。
- Document：逻辑资料；DocumentVersion：不可变版本和文件哈希。
- ParsedChunk/DocumentChunk：保留父子关系、标题层级、页码/段落或工作表/单元格坐标、原文及哈希。
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

叙事文档采用父子 Chunk。父块保留完整段落或章节语义，不直接参与召回；父块再切成最多 320 字的子块并记录 `parent_chunk_id`，由子块参与 BM25 和向量检索。命中后按 `parent_id` 回查同一 `DocumentVersion` 的父块，去重后将完整父块交给分析流程，同时保留获胜子块作为精确引用。XLSX 行本身已具备稳定单元格定位，因此作为独立父块直接索引。

索引包含：

- `rank_bm25` 词法检索；中文使用字符二元组与词元混合分词。
- ChromaDB 持久化向量检索；当前使用确定性哈希 Embedding 建立离线可复现基线，并保留 `EmbeddingProvider` 接口替换真实中文模型。
- Reciprocal Rank Fusion：`score(d)=Σ 1/(k+rank_i(d))`，默认 `k=60`。
- 可选 reranker，默认关闭，确保基线可复现。

检索条件由 LangGraph 显式节点集中执行。`DatabaseRetrievalCoordinator` 先从 PostgreSQL 选出项目内每份文档最新且状态为 `indexed` 的版本，再对 Planner 子任务的多个查询执行 BM25/Vector/RRF，避免把旧版本事实混入当前分析。返回的 `EvidenceItem` 固定到父 Chunk、DocumentVersion、位置和原文哈希，quote 来自命中的子 Chunk。评测分别运行 BM25、Vector 和 Hybrid，输出 Recall@K、Precision@K、MRR、NDCG。

## 6. 领域 Agent 与状态图

LangGraph 状态以 `ResearchPlan`、`EvidenceItem[]`、`Claim[]`、`ReviewResult` 和 `FinalReport` 为核心：

当前代码中的过渡图：

`plan → retrieve → analyze（并行子任务）→ review → prepare_revision | finalize`

- PlannerAgent：生成维度、权重、可并行子任务和检索查询。
- AnalystAgent：按子任务产出 Claim。
- ReviewerAgent：结合确定性门禁检查 Claim。

这三个名称对应流程阶段，不作为最终多 Agent 业务边界。已确认的目标架构将其替换为需求符合性 Agent、成本建模 Agent 和承诺核验 Agent。三者分别交付需求—响应矩阵、统一成本模型和承诺核验表；Planner、Reviewer、评分器和报告合并器降为普通工作流组件。

目标图：

`prepare_review → retrieve_domain_evidence → analyze_domains（并行）→ validate_domains → retry_failed_domains → run_domain_fallbacks → create_human_review_requests → aggregate_scores → compose_report`

只返工失败领域，最多两次；仍失败时进入同领域确定性降级处理器，关键冲突再生成人工复核请求，不跨 Agent 兜底。推荐由公开的 50/30/20 权重和硬性淘汰规则确定，不允许任何 Agent 直接选择获胜供应商。完整契约见[领域 Agent 重构设计](../superpowers/specs/2026-10-01-domain-agent-redesign.md)。

## 7. 证据门禁

确定性层检查引用存在、项目和版本有效、原文哈希一致、定位可回读以及结构化数字/日期/比例/周期与原文匹配。语义支持判断作为校验服务判断证据是否蕴含 Claim、是否断章取义，以及冲突是否完整呈现；它不作为业务 Agent。

确定性失败不得由模型覆盖。语义失败只回退相关 Claim；超过两次后将 Claim 标记 `unsupported`，报告显示“无法判断”及所需资料。

## 8. API、队列与实时进度

目标 REST API 管理项目、文档、版本、任务和报告。当前已实现数据库健康检查、RQ Worker 入口、按任务单调递增的持久化 `RunEvent`，以及标准 SSE 文本格式和按序号续读；任务创建、入队和 SSE HTTP 端点尚待接线。完成后上传或创建分析任务会立即返回资源 ID/`run_id`，前端用 `Last-Event-ID` 在刷新后续传；WebSocket 不在范围内。

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
- 当前运行和测试使用确定性哈希 Embedding，避免下载模型并保证结果可复现；接入 `BAAI/bge-small-zh-v1.5` 后才切换为默认语义模型。
- 文档与运行数据默认保存在项目级 Docker volume；日志采用 JSON。
- 首个纵向切片允许通过同步管理命令触发解析和评测，随后接入 Worker/API，不用模拟实现替代真实逻辑。

## 13. 当前实现状态（2026-10-01）

- 已完成：版本化摄取、父子 Chunk、BM25 落盘、ChromaDB 持久化、RRF、最新版本隔离、父块回查和 EvidenceItem 转换。
- 已完成：LangGraph 主流程、并行 Analyst、确定性证据门禁、两次局部返工、内存/PostgreSQL Checkpointer。
- 已完成：AgentRun/RunEvent 数据模型、事件持久化与 SSE 格式化。
- 已确认设计：以需求符合性、成本建模和承诺核验三个领域 Agent 替换按流程阶段划分的 Planner/Analyst/Reviewer 角色。
- 已完成：Vendor、Requirement、ReviewConfig、DomainAssessment 数据模型，供应商文件隔离和确定性推荐规则。
- 进行中：实现三个领域 Agent 契约、专属工具和同领域降级处理器，再重构工作流。
- 未开始：真实中文 Embedding 与 reranker、完整任务 REST/SSE、Trace 写入、前端工作台和端到端演示。
