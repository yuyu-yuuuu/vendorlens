# VendorLens 领域 Agent 重构设计

日期：2026-10-01  
状态：已完成产品讨论，待用户审阅后实施

## 1. 背景与决策

现有工作流把 Planner、Analyst、Reviewer 称为三个 Agent。这三者主要对应同一条处理流水线中的计划、执行和检查阶段，职责按步骤而不是业务能力划分。它们之间高度依赖，不能证明多 Agent 相比单 Agent 工作流的必要性。

本次重构将“Agent”限定为具有独立业务目标、输入范围、工具权限和结构化交付物的领域执行者。计划、检索、校验、评分和报告合并仍然存在，但作为确定性工作流组件，不计入业务 Agent。

选定三个领域 Agent：

1. 需求符合性 Agent：生成需求—响应矩阵。
2. 成本建模 Agent：生成统一口径的成本模型。
3. 承诺核验 Agent：生成可执行承诺核验表。

## 2. 设计目标

- 三个 Agent 的核心判断互不替代，允许真正并行执行。
- 每个 Agent 只读取和输出本领域允许的数据，避免角色污染。
- 所有事实性结论必须固定到文档版本、原文哈希、位置和引用。
- Agent 不直接选择最终供应商；推荐由公开、可复现的规则计算。
- 一个领域失败时保留其他领域结果，并支持 checkpoint 后局部恢复。
- 现有文档摄取、父子 Chunk、Hybrid RAG 和证据门禁继续复用。

## 3. 非目标

- 不引入自由讨论式 Agent 群聊。
- 不让多个 Agent 对同一业务指标重复打分后投票。
- 不让模型自行修改权重、绕过硬性淘汰或覆盖确定性证据错误。
- 不在本次重构中实现真实中文 Embedding、前端或完整异步 API。
- 不提供法律意见；合同内容只作为承诺、费用和限制条件进行事实提取。

## 4. Agent 契约

每个领域 Agent 是一种业务能力，不是一个长期保存状态的单例。运行时按“领域 × 供应商”创建独立任务：三家供应商会形成九个可并行、可单独重试的分析任务。跨供应商比较只读取通过校验的结构化结果，不让一家供应商的材料进入另一家的 Agent 输入包。

### 4.1 需求符合性 Agent

目标：逐条判断供应商材料是否支持项目需求。

输入：

- 已结构化的 Requirement 列表；
- 与当前需求相关的最新版本 EvidenceItem；
- 供应商身份和项目上下文；
- 需要返工的符合性条目。

允许工具：

- `search_requirement_evidence`：按需求和供应商检索证据；
- `compare_requirement_response`：比较需求文本与响应文本；
- `mark_missing_response`：记录材料未说明，不生成推测。

输出：RequirementAssessment 列表，每项包含供应商、需求 ID、必选标记、状态、解释、证据 ID 和置信度。状态只允许 `met`、`partially_met`、`not_met`、`not_stated`。

禁止：处理价格竞争力、计算总成本、评价人员和时间承诺。

### 4.2 成本建模 Agent

目标：把不同供应商报价转换成统一、可比较的成本结构。

输入：

- 报价、付款、维保和可选费用相关 EvidenceItem；
- 项目的比较周期和币种；
- 供应商身份；
- 需要返工的成本条目。

允许工具：

- `extract_cost_items`：提取金额、币种、税费和周期；
- `normalize_cost`：执行确定性币种/周期/含税口径转换；
- `calculate_tco`：计算指定周期的总拥有成本；
- `compare_cost_items`：识别重复、缺失或口径冲突。

输出：CostAssessment，包含一次性成本、持续成本、可选成本、TCO、付款节点、缺失费用、冲突和证据 ID。模型负责识别费用语义，所有算术由确定性工具完成。

禁止：判断功能是否满足、评价技术方案或预测交付能力。

### 4.3 承诺核验 Agent

目标：核验供应商对交付和服务作出的承诺是否明确、完整、一致且可验证。

输入：

- 里程碑、人员、依赖、验收、SLA、质保和限制条件相关 EvidenceItem；
- 项目要求的关键日期和验收条件；
- 供应商身份；
- 需要返工的承诺条目。

允许工具：

- `extract_commitments`：提取主体、动作、时间和条件；
- `compare_milestones`：检查日期、周期和先后关系；
- `check_dependencies`：识别客户侧和第三方前置条件；
- `detect_commitment_conflicts`：发现同一供应商材料中的矛盾承诺。

输出：CommitmentAssessment 列表，覆盖交付日期、里程碑、人员投入、依赖、验收、SLA、质保和限制条件，并携带状态、证据、冲突和缺失项。

禁止：重复判断功能符合性、计算价格优势或直接推荐供应商。

## 5. 公共结构化输出

三个 Agent 均包装为 DomainAssessment：

```text
DomainAssessment
  domain
  vendor_id
  status
  findings[]
  missing_information[]
  conflicts[]
  evidence_ids[]
  revision_count
```

领域专属结果保存在 `findings` 的有类型结构中，不使用无约束字典作为核心业务接口。事实性 finding 必须至少关联一个 EvidenceItem；`not_stated` 和缺失信息可以没有正向证据，但要记录检索范围。

## 6. 必要领域数据

现有数据模型缺少供应商和需求归属，重构需要补充：

- Vendor：属于一个 Review Project，拥有稳定 ID 和显示名称；
- Document 增加可空 `vendor_id`：采购方需求资料为空，供应商提交资料必须关联 Vendor；
- Requirement：包含原文、来源证据、是否必选、可选权重和稳定 ID；
- ReviewConfig：包含三个领域权重、成本比较周期、币种和承诺核验清单；
- DomainAssessment：按任务、供应商和领域持久化结构化输出与状态。

RequirementCatalogBuilder 从采购方需求资料提取 Requirement，并保存每条需求的来源证据。它是受 Schema 约束的预处理服务，不是业务 Agent；第一版允许用户在运行评审前确认和修正需求目录。

## 7. 工作流

目标 LangGraph：

```text
prepare_review
  → retrieve_domain_evidence
  → analyze_domains（领域 × 供应商并行）
  → validate_domains
  → prepare_domain_revisions
  → analyze_domains（仅失败领域，最多 2 次）
  → run_domain_fallbacks（仍失败的领域）
  → create_human_review_requests（仍有高风险不确定项时）
  → aggregate_scores
  → compose_report
```

`prepare_review` 由确定性 ReviewOrchestrator 校验 Requirement 目录，为每个 Vendor 创建三个领域任务，并冻结本次运行使用的文档版本、权重和比较周期。它不调用模型。

`retrieve_domain_evidence` 复用 DatabaseRetrievalCoordinator，但查询模板、metadata 过滤和结果预算按领域隔离。一个原文块可以被多个领域引用，但每个 Agent 只能基于自己的输入包生成结果。

`analyze_domains` 并行执行“领域 × 供应商”任务。每个 Agent 只收到当前 Vendor 的领域证据、工具白名单和 Schema；不读取其他 Agent 或其他 Vendor 的草稿，避免互相锚定和供应商证据串线。

`validate_domains` 先执行现有确定性证据门禁，再执行领域 Schema 和规则校验。失败项按领域归组，后续只重跑相关 Agent。

两次返工后仍未通过的任务进入同领域确定性降级处理器，不转交其他 Agent：需求符合性进入 RequirementRuleEvaluator，成本建模进入 DeterministicCostEngine，承诺核验进入 CommitmentRuleChecker。降级处理器只保留明确事实和程序计算，禁止语义推断；结果状态为 `degraded`，最多支持条件推荐。

降级后仍存在必选需求冲突、关键金额冲突、关键日期/SLA 冲突或无法形成可靠推荐时，系统创建 HumanReviewRequest。它记录触发原因、受影响 Vendor、冲突证据和需要人工回答的问题；人工复核是工作流出口，不是第四个 Agent。

`aggregate_scores` 和 `compose_report` 都不是 Agent。前者执行硬性规则和公开公式，后者将已验证结构渲染为报告；可选模型润色只能改写措辞，不能新增事实或分数。

## 8. 推荐规则

项目默认权重：

- 需求符合性：50%；
- 成本竞争力：30%；
- 承诺可信度：20%。

权重可以由项目配置覆盖，但每次运行开始后冻结并写入审计数据。最终总分只使用已通过证据门禁的结构化结果。

领域分采用确定性公式：

- 需求符合性：`met=100`、`partially_met=50`、`not_met=0`、`not_stated=0`，按 Requirement 权重求加权平均；未配置权重时各项权重为 1；
- 成本竞争力：在成本信息完整、未被淘汰的 Vendor 中按 `最低 TCO / 当前 Vendor TCO × 100` 计算；只有一家可比较 Vendor 时该领域记 100，但报告标明缺少横向价格基准；
- 承诺可信度：核验清单中 `verified=100`、`partial=50`、`conflicting=0`、`not_stated=0`，各核验项默认等权；
- 总分：`需求符合性 × 50% + 成本竞争力 × 30% + 承诺可信度 × 20%`。

硬性规则优先于加权分数：

- 明确不满足任一必选需求的供应商标记为硬性淘汰；
- 关键费用缺失时不能输出无条件推荐；
- 关键交付日期、人员或验收条件无证据时不能输出无条件推荐；
- 冲突信息必须展示，不能静默选择其中一个版本；
- 需求符合性领域失败时禁止产生推荐结果；
- 成本或承诺领域失败时最多产生“有条件推荐”；
- 三个领域都失败时只生成失败报告。

推荐等级为 `recommended`、`conditionally_recommended`、`unable_to_recommend`。未被淘汰且总分最高者成为候选推荐；完全同分时并列推荐，不使用隐藏规则打破平局。三个领域都成功且不存在关键缺失或未解决冲突时为 `recommended`；成本或承诺不完整时为 `conditionally_recommended`；需求符合性失败或没有合格 Vendor 时为 `unable_to_recommend`。报告展示推荐供应商、推荐等级、总分、领域分、推荐理由、硬性淘汰项、风险、缺失信息、冲突和 Claim-Evidence 矩阵。

## 9. 失败与恢复

- Agent 输出无法通过 Pydantic Schema 时只重试该领域。
- 证据、版本、哈希、位置或数字校验失败时只返工对应 finding。
- 每个领域最多局部返工两次，之后进入对应的确定性降级处理器。
- 确定性降级结果标记为 `degraded`，不能产生无条件推荐。
- 降级后仍存在关键不确定项时创建 `needs_human_review` 的 HumanReviewRequest。
- Agent 或工具异常不会删除已成功的其他领域结果。
- Checkpoint 保存冻结版本、领域状态、返工次数和已验证输出；恢复时不重跑完成领域。
- 找不到材料时输出 `not_stated` 或缺失信息，而不是制造事实。
- 最终领域状态为 `completed`、`degraded`、`needs_human_review` 或 `failed`；任务状态为 `completed`、`partially_completed` 或 `failed`。

## 10. 代码迁移边界

- `PlannerAgent` 协议替换为确定性的 ReviewOrchestrator。
- 通用 `AnalystAgent` 替换为三个实现同一 DomainAgent 协议的领域 Agent。
- `ReviewerAgent` 从业务 Agent 列表移除；确定性门禁保留，语义支持判断作为校验服务存在。
- `ResearchPlan` 调整为冻结后的 ReviewPlan，包含三个领域任务、权重、供应商和版本集合。
- 新增领域专属 Pydantic Schema 和推荐结果 Schema。
- 新增 Vendor、Requirement、ReviewConfig 和 DomainAssessment 的持久化模型及迁移。
- 新增三个确定性领域降级处理器和 HumanReviewRequest Schema。
- 重构 ReviewWorkflow 状态，使结果、错误和返工次数按领域存储。
- 现有摄取、索引、DatabaseRetrievalCoordinator、EvidenceItem 和 checkpoint 接口继续复用。

迁移期间保留现有工作流测试作为行为参考，但不为旧 Agent 名称提供长期兼容层。

## 11. 测试与验收

单元测试：

- 每个 Agent 的输入、工具白名单和输出 Schema 相互隔离；
- 四种需求符合性状态和必选需求规则；
- 金额归一化、TCO 算术和缺失费用；
- 日期、周期、依赖和承诺冲突；
- 50/30/20 默认权重及项目自定义权重；
- 低价供应商不能覆盖必选需求淘汰；
- 无证据事实被门禁拒绝。

工作流测试：

- 三个领域首次执行可并行；
- 不同 Vendor 的证据不会进入彼此的 Agent 输入包；
- 只有失败领域进入局部返工；
- 单领域失败保留其他领域结果；
- 两次无效输出后只进入同领域降级处理器，不发生跨 Agent 兜底；
- 降级结果只能生成条件推荐，关键冲突会生成人工复核请求；
- PostgreSQL checkpoint 恢复后只执行未完成节点；
- 缺少需求符合性时不产生推荐；
- 缺少成本或承诺时只产生条件推荐。

端到端验收使用至少三家合成供应商：一家综合最优、一家价格最低但不满足必选需求、一家材料不完整。最终结果必须淘汰第二家、限制第三家的推荐等级，并让报告中每个事实回到版本固定的原文证据。
