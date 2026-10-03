# VendorLens 设计说明

## 1. 产品边界

VendorLens 是“项目方案与供应商资料评审 Agent 平台”。用户上传项目需求书、供应商方案、报价单、项目排期和会议纪要，系统围绕一次明确的评审目标，完成结构化提取、横向比较、矛盾发现、证据核验，并生成可追溯报告。

首版只支持可复制文字的 PDF、DOCX、XLSX、Markdown 和 TXT。不提供扫描件 OCR、泛文档聊天、合同法律审查、互联网搜索或自由 Agent 讨论。

## 2. 核心不变量

1. 每条事实性领域结论至少关联一个 EvidenceItem，否则只能输出“无法判断”和缺失资料。
2. 金额、日期、比例和周期必须精确定位到页码、段落、行区间或工作表/单元格，并通过确定性数字匹配。
3. 引用固定到文档版本、原文哈希和位置；新版本不会静默改变旧报告证据。
4. 冲突并列展示，不自动选择“正确”版本。
5. 推荐意见必须公开评审维度、权重和证据。
6. Agent 只负责需要模型判断的工作；解析、检索、引用定位、数字核验、持久化和渲染均为确定性组件。

## 3. 系统架构

项目采用 monorepo，后端集中在 `src/vendorlens`，前端位于 `apps/web`：

- `src/vendorlens`：当前领域模型、解析、索引、Hybrid RAG、检索协调、证据门禁、LangGraph 工作流和 API。
- `services/api`：FastAPI 容器入口；提供管理/上传、任务 REST/SSE、Trace 和聚合指标。
- `services/worker`：RQ Worker 容器入口；已接入后台评审、租约心跳和 checkpoint 恢复。
- `apps/web`：Next.js、TypeScript、Tailwind CSS、shadcn/ui 工作台。
- `evals`：本地标注集、检索/Claim/端到端评测和消融脚本。
- `migrations` 与 `docker-compose.yml`：数据库迁移和本地依赖服务。

PostgreSQL 保存业务对象、运行状态、LangGraph checkpoint 和 Trace；Redis 承担队列、轻量通知及可选短期会话缓存；ChromaDB 保存向量；BM25 索引本地持久化；原文件经 `DocumentStorage` 接口写入本地目录，未来可替换 S3/MinIO。

## 4. 领域模型

- Project：一次采购或项目评审空间。
- Document：逻辑资料；DocumentVersion：不可变版本和文件哈希。
- ParsedChunk/DocumentChunk：保留父子关系、标题层级、页码/段落或工作表/单元格坐标、原文及哈希。
- ResearchTask：一次评审运行，关联目标、状态、`run_id` 和 `trace_id`。
- ReviewConfig：冻结的评审权重、成本口径、必要费用和承诺清单。
- EvidenceItem：版本固定的原文、定位、哈希和检索分数。
- DomainAssessment：需求、成本和承诺的结构化结果、领域状态及证据引用。
- VendorAssessment：供应商的三个领域结果；RecommendationResult：确定性评分及推荐限制。
- FinalReport：正文、供应商比较、风险、冲突、缺失信息及版本固定的引用。
- ConversationSession/ConversationMessage、EpisodicMemory、UserPreference：会话消息、历史评审摘要和显式偏好。
- RoutingHealth/RoutingAttempt：端点冷却、探测租约和每次模型路由决策。
- AgentRun/TraceSpan：节点、检索、模型、工具、重试、核验和状态变化的审计记录。

所有写操作使用事务。上传以项目、文件 SHA-256 和版本号实现幂等；后台任务以稳定 job key 防止重复执行；运行结果、事件和会话回写在受所有权校验的事务中提交。

## 5. 文档摄取与 Hybrid RAG

解析器输出统一 `ParsedChunk`，同时保留格式特有定位：PDF 页码，DOCX 标题/段落，XLSX 工作表/单元格范围，Markdown/TXT 行区间。空文本 PDF 明确标记为 `ocr_required`，不进入索引。

叙事文档采用父子 Chunk。父块保留完整段落或章节语义，不直接参与召回；父块再切成最多 320 字的子块并记录 `parent_chunk_id`，由子块参与 BM25 和向量检索。命中后按 `parent_id` 回查同一 `DocumentVersion` 的父块，去重后将完整父块交给分析流程，同时保留获胜子块作为精确引用。XLSX 行本身已具备稳定单元格定位，因此作为独立父块直接索引。

EvidenceItem 通过 `context_text` 保存父块全文、通过 `quote` 保存命中子块原文；`chunk_id` 和 `source_text_hash` 对应父块，`matched_chunk_id` 对应命中的召回块。构造时校验父块全文 SHA-256 一致且引用是父块中的连续原文；切片保留句间空白。`location` 仍是父块来源定位，并非新增加的子块字符坐标。模型输入显式携带全文并要求考虑限制、依赖和例外。旧快照允许上下文为空，只能依据原有 quote，不推断丢失的上下文。这一校验保证内容内部一致性，不替代数据库来源核验，也不保证模型一定正确理解限制条件。成本抽取和正式数字门禁优先读取经哈希校验的 context_text；旧快照仅读取 quote。需求/承诺工具读取完整父块，需求使用相关子句、否定作用域与限制条件检查；规则覆盖有限，仍不保证通用语义蕴含。

索引包含：

- `rank_bm25` 词法检索；中文使用字符二元组与词元混合分词。
- ChromaDB 持久化向量检索；当前使用确定性哈希 Embedding 建立离线可复现基线，已提供本地 SentenceTransformer 接入口，真实权重质量尚未评测。
- Reciprocal Rank Fusion：`score(d)=Σ 1/(k+rank_i(d))`，默认 `k=60`。
- 可选 reranker，默认关闭，确保基线可复现。

检索条件由 LangGraph 显式节点集中执行。`DatabaseRetrievalCoordinator` 为每个 Vendor 从 PostgreSQL 冻结采购方公共文档和当前 Vendor 每份文档最新且状态为 `indexed` 的版本，再按需求、成本和承诺查询执行 BM25/Vector/RRF，避免旧版本和竞争对手事实混入当前分析。返回的 `EvidenceItem` 固定到 Vendor、父 Chunk、DocumentVersion、位置和原文哈希，quote 来自命中的子 Chunk。评测分别运行 BM25、Vector 和 Hybrid，输出 Recall@K、Precision@K、MRR、NDCG。

## 6. 供应商评审 Agent 与状态图

LangGraph 状态以 `VendorReviewInput`、`VendorAssessment`、领域返工次数、`RecommendationResult` 和 `FinalReport` 为核心。当前图：

`prepare_review → retrieve_vendor_evidence → analyze_vendors（Vendor 间并行）→ validate_domains → prepare_domain_revisions → run_domain_fallbacks → aggregate_scores → compose_report`

VendorReviewAgent 每个 Vendor 首轮调用一次，Vendor 之间并行；输出按需求、成本和承诺三个领域分别校验。后续只重新调用包含失败领域的 Vendor，并通过 `revision_domains` 指定返工范围，最多两次；仍失败时进入对应的确定性降级处理器。单个 Vendor 检索或执行失败不会删除其他 Vendor 结果。降级结果中的冲突和缺失信息直接进入最终报告；若降级处理器也无法形成领域结果，则标记为 `failed`。推荐由公开的 50/30/20 权重和硬性淘汰规则确定，不允许 Agent 直接选择获胜供应商。完整契约见[供应商评审 Agent 设计](../superpowers/specs/2026-10-01-domain-agent-redesign.md)。

当前已实现 VendorReviewInput、VendorReviewAgent、ReviewToolbox、VendorReviewExecutor 和上述 ReviewWorkflow。三个业务领域由同一次 Agent 调用完成：一次调用读取当前 Vendor 的冻结证据包并返回三个 DomainAssessment；单领域输出缺失时只将该领域标记为 `failed`。执行器校验 Vendor、版本范围、成本口径和证据 ID，并逐领域核验数字事实与成本原文绑定。

DatabaseRetrievalCoordinator 已新增 Vendor 级检索：从数据库冻结采购方公共文档和当前 Vendor 每份文档的最新已索引版本，再按三个领域分别执行检索并合并为 VendorReviewInput。版本集合在检索前排除其他 Vendor，EvidenceItem 同时携带 `vendor_id` 供输入门禁二次校验。

## 6.1 会话记忆

短期层以数据库消息为准，可选 Redis 缓存；达到阈值时将较早消息生成抽取式摘要并保留最近消息。情节层保存每家供应商的历史评审摘要、证据 ID 和版本依赖，只召回当前项目/用户、当前供应商且依赖版本仍有效的记录；Worker 按实际冻结版本再次过滤。可选 Chroma 使用哈希向量，失败时退回本地检索。长期层只保存用户显式设置的偏好，不从模型回答自动推断。历史记忆提供上下文，事实引用仍须来自当前证据包。

## 6.2 Skills 加载

Skills 支持 JSON 或包含 JSON 元数据的 SKILL.md，按行业、领域和关键词选择。加载器校验全部文件后原子替换；任务创建时检查文件变化，失败保留最后有效版本，并支持显式重载接口。任务冻结内容和 SHA-256，Worker 和提示构造验证哈希；文件更新影响后续任务。Skills 提供评审规则文本，工具权限和证据门禁由代码控制。

## 6.3 模型端点路由

每次供应商分析或领域返工根据最近窗口的错误、限流、领域质量失败、请求延迟及可比估算成本选择模型端点，同一次工具循环固定端点。达到失败阈值后冷却，期满通过数据库租约允许单次探测；成功恢复，失败再次冷却。路由结果保存候选评分、选择和安全运行指标，并写入 Trace。全部端点不可用时沿用有限返工与确定性降级；配置单端点时无法切换到备用端点。

## 7. 证据门禁

输入门禁检查项目、Vendor、冻结版本和领域证据范围；输出门禁检查 Requirement ID、成本口径、证据 ID 和三个领域身份。确定性失败不得由模型覆盖。失败领域最多返工两次，之后进入只保留明确事实的降级处理器；报告显示降级、冲突和缺失信息。

正式执行使用 `evidence.validate_numeric_output`，不依赖旧 Claim 流程被调用。需求 explanation 和承诺 statement 各自只读取该条 evidence_ids；付款条款和成本数字只读取 cost.evidence_ids；领域摘要读取所属领域结论引用的并集，不能从其他领域借数字。金额保留币种与周期维度，百分比不同于百分点，工作日不同于自然日，年/月只在月维度换算，天/小时在固定秒维度换算，不把月换算成三十天。完整日期按日历规范化并拒绝非法日期。常见中文整数单位表达也进入数字匹配；未支持的格式不构成完整语义校验。

`cost_grounding.extract_grounded_costs` 从实际所引父块（或旧 quote）的独立费用片段构造 `CostSourceItem`：证据 ID、连续原文、字符区间、金额、币种、周期、费用类别和年化金额。同版本同父块同区间仅计一次；不同区间的同金额费用仍分别保留。`calculate_tco` 要求三个输入类别合计与提取结果一致，然后用 Decimal 计算 `一次性 + 年化持续费 × comparison_months / 12`；可选费用单列且不计入基础 TCO。模型提供的来源区间在出口被重新构造，不能自行认证。CostAssessment 新字段默认空列表，旧已保存结果保持可读取。

TCO 是派生值，按已验证费用复算，不要求原文逐字存在；成本说明只有明确标注 TCO/总拥有成本的片段才可引用该派生金额及冻结比较周期，付款事实不能借用派生结果。年费、月费、币种、费用类别不允许只凭相同数字互换。归一化工具只做算术，不能给凭空金额授权。来源错误的工具调用向模型返回有限、可纠正的错误；最终仍没有成功的权威 TCO 时只将成本领域设为 failed。

数字门禁失败会移除该领域的评分载荷，保留其他领域。返工输入通过 revision_feedback 携带字段级失败码；首轮加最多两次返工后，成本降级复用同一费用提取与计算，工作流验证节点和降级出口再次运行门禁。没有明确费用时降级仍失败；只能识别部分费用时标记 degraded 并保留 missing_costs，限制推荐等级。

本次只接受明确币种的独立费用与一次性可选费用。未标周期的独立报价按一次性解释；季度/日计价、单价乘数量、多年打包、估算、税费条件、汇总明细混合、周期性可选费用等不猜算，保留缺失信息。未提取的类别零值仅是已知费用小计，不等于免费。元/人民币按 CNY，USD/EUR 明示代码或文字按对应币种；不读取跨块表头推断币种，也不自动换汇。不会完整判断事实指向的对象、合同起算条件、否定或范围限定，也不证明检索完整性或必要费用类别齐全。详细学习记录见[数字事实与成本证据门禁](../learning/2026-10-02-numeric-evidence-gate.md)。

## 8. API、队列与实时进度

目标 REST API 管理项目、文档、版本、任务和报告。当前已实现健康检查、`POST /projects/{project_id}/tasks`、任务/报告查询和 SSE HTTP 端点。任务提交先将需求、配置、查询、记忆上下文和 Skills 内容快照与 queued 事件提交到数据库，再以 run_id 作为 RQ job_id 入队，正常返回 202；入队失败返回 503 和 task_id 并记录 enqueue_failed。Worker 原子领取 queued 任务，运行真实检索与模型工作流，按节点完成写入事件，领域结果、报告、终态事件、分供应商历史摘要和会话答复在同一事务提交。前端用 `Last-Event-ID` 或 after 续读，终态连接关闭；每次轮询只持有短数据库事务。项目/供应商/需求/配置、上传、版本、来源块、下载和重建索引管理端点已实现；上传同步解析并索引，失败保留可重试版本。WebSocket 不在范围内。

文档版本在 Worker 检索节点冻结，不是任务提交时冻结。重复投递不会重复执行已领取任务；任务以租约/心跳保护执行，过期扫描或手动 retry 保留快照并恢复持久化 checkpoint；结果、事件和 checkpoint 写入校验所有权。有效租约或已有报告拒绝重放。共享 Bearer Key 可选，未配置时限可信本地使用；不提供租户权限。

会话创建、消息记录、上下文查询、显式偏好读写、Skills 列表/重载、路由健康和任务路由记录接口已接入。当前没有独立的开放聊天生成接口；关联会话的评审任务产生报告和会话答复。`user_id` 是数据范围标签，不能替代身份认证。

接口文档由 FastAPI/Pydantic 自动生成：`/docs` 为 Swagger UI，`/redoc` 为阅读版，`/openapi.json` 为规范。业务端点按中文标签分组，声明请求示例、成功响应模型、已知错误状态和 SSE 的 text/event-stream 类型；执行调试调用真实业务，不是模拟接口。文档页无需数据库连接，业务调用需要依赖与前置数据；CDN 前端资源未离线打包。操作说明见[接口文档](../api.md)。

## 9. 可观测性与安全

每次运行生成 `trace_id`。每个节点、检索、模型、工具、重试、校验和状态变化写入父子 `TraceSpan`，记录状态、时间、耗时、模型、Token、估算成本和异常类型。禁止保存隐藏思维链、API Key、Authorization header 或原始敏感配置。Prometheus 只暴露聚合指标；当前未接入 LangSmith。

当前已实现整次运行、节点、供应商检索、Agent 尝试、模型请求、工具和领域降级的父子 TraceSpan，并提供 GET /tasks/{task_id}/trace 查询。TraceRecorder 通过 ContextVar 管理父节点，线程池每项任务使用独立 copy_context，避免并行供应商串线。跨度单独提交起始状态，再运行操作，结束时提交耗时/终态；不持有跨模型请求的数据库事务。属性只保留白名单标量，不存原始参数、模型全文或异常文本；错误只存异常类型。模型完整 usage 的 Token 计入汇总，未报告或不完整 usage 不当作零消耗。Trace 根状态与业务终态一致，子操作状态反映本次调用是否成功；降级不等同于模型成功。金额按配置单价与完整 Token 估算，按币种汇总且标记完整性；Prometheus 提供低基数聚合，恢复时将未结束跨度置为 interrupted。运行记录页已展示 Trace 父子链和汇总；估算不是实际账单。

## 10. 前端原则

界面围绕单一项目工作台：项目资料、评审任务、评审报告和运行记录四个视图。进入实施前先用 ImageGen 生成完整视觉概念并取得认可，再提取设计系统、实现桌面/移动页面，并用真实浏览器截图做视觉验收。视觉方向已选定为文档审阅风：米白底、深绿强调色、开放表格与原文核验区。完整页面与交互设计见[前端工作台设计](2026-10-02-frontend-workbench.md)。

## 11. 评测与验收

本地标注集包含可公开分发的合成供应商资料、问题、相关 chunk、结构化字段、冲突和期望报告要素。评测覆盖检索、领域结果/工具和端到端报告；固定随机种子、模型配置、数据版本和运行元数据。指标仅从脚本实际输出进入 README 或简历。

项目验收：真实 LangGraph checkpoint；任务事务、幂等、运行审计；真实 BM25+Vector+RRF 对比；数据库 Trace；可恢复 SSE；分层评测；所有量化陈述可由测试产物复现。

## 12. 明确默认值

- Python 3.12 作为服务运行时，Node.js 22 LTS 作为前端运行时。
- SQLAlchemy 2 + Alembic；Pydantic v2；RQ 作为首版 Redis 队列。
- 当前运行和测试使用确定性哈希 Embedding，避免下载模型并保证结果可复现；接入 `BAAI/bge-small-zh-v1.5` 后才切换为默认语义模型。
- 文档与运行数据默认保存在项目级 Docker volume；日志采用 JSON。
- 上传同步解析与索引；评审通过 RQ Worker 执行。
- 短期消息默认保留最近 6 条、TTL 24 小时；累计 16 条未压缩消息触发摘要，Redis/Chroma 记忆加速默认关闭。
- Skills 默认从 `skills` 目录读取，任务创建时检测变化并冻结内容。
- 模型路由默认窗口 900 秒、最低 3 个样本、冷却 60 秒、探测租约 120 秒。

## 13. 当前实现状态（2026-10-03）

- 已完成：版本化摄取、父子 Chunk、BM25 落盘、ChromaDB 持久化、RRF、最新版本隔离、父块回查和 EvidenceItem 转换。
- 已完成：ReviewWorkflow、Vendor 间并行、领域级校验与两次局部返工、确定性降级、评分、报告和内存/SQLite/PostgreSQL Checkpointer。
- 已完成：AgentRun/RunEvent 数据模型、事件持久化与 SSE 格式化。
- 已完成：单个 VendorReviewAgent 输出需求、成本、承诺三个领域结果，不保留 Planner/Analyst/Reviewer 业务角色。
- 已完成：Vendor、Requirement、ReviewConfig、DomainAssessment 数据模型，供应商文件隔离和确定性推荐规则。
- 已完成：VendorReviewAgent 输入/输出契约、11 个正式领域工具、OpenAI 兼容 Chat Completions 模型适配器、证据范围校验和三个领域降级处理器。
- 已完成：正式数字门禁、成本输入原文绑定、费用来源片段、月费年化与 TCO 派生校验、字段失败反馈及降级复检。
- 已完成：正式模型工作流接入 RQ、任务创建/状态/报告 API、SSE HTTP 续读、任务输入快照、结果事务落库与运行/节点/检索/Agent/模型/工具/降级父子 Trace。
- 已完成：租约/心跳与所有权写入门禁、硬中断 checkpoint 恢复、管理/上传 REST、配置单价费用估算、Prometheus、本地 Embedding/reranker 接入口、必要成本类别及承诺清单。
- 已验证：真实本地 BM25/Chroma/SQLite checkpoint 离线三供应商演示；确定性 Agent 替身，无付费模型调用。PostgreSQL 独立库迁移升级/回退/重升级及 schema check。
- 已完成：三层会话记忆、Skills 热加载与任务快照、模型端点健康路由、冷却与探测、运行管理接口。
- 已验证：237 项离线测试及 Ruff；新增迁移在隔离 SQLite 的前置 schema 上升级、回退、重升级及 schema check。当前运行服务的数据库迁移与进程更新尚待执行。
- 待实施/验证：真实模型与权重质量、复杂报价语义、OCR、租户身份权限和生产负载。恢复不承诺外部模型调用 exactly-once。

前端首版已实现资料上传、评审讨论与任务提交、SSE 进度、领域报告与冻结引用、运行追踪和偏好设置。服务端代理管理 API 地址与共享凭据。评测继续使用 CLI，导出支持 JSON 与浏览器打印。

已新增事务 outbox、受限工具参数纠错反馈与评测脚本。固定挑战集及真实模型结果见[修订测试报告](../results/2026-10-03-challenge-metrics/测试报告.md)。当前父块规则不能可靠处理同义改写、缺答和数值约束，复杂报价计算与工具循环也有失败；此前模板化满分仅作为功能回归记录。
