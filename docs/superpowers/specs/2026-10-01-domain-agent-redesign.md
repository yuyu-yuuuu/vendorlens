# VendorLens 供应商评审 Agent 设计

日期：2026-10-01  
状态：已确认，分阶段实施

## 1. 背景与决策

旧工作流把 Planner、Analyst、Reviewer 称为三个 Agent，但它们只是同一流水线中的步骤。随后按需求、成本和承诺拆成三个领域 Agent，虽然业务边界更清楚，却引入了固定路由、重复上下文和额外调度，而每家供应商本来就必须完成全部三个领域。

最终采用单 Agent：每个 Vendor 运行一次 VendorReviewAgent，一次输出需求符合性、成本模型和承诺核验三个结构化领域结果。三个领域继续作为 Schema、校验、评分、返工和降级边界存在，但不再包装成三个 Agent。

## 2. 设计目标

- 一家供应商对应一次 Agent 分析，减少重复上下文和调度复杂度；
- 不同 Vendor 的运行可以并行，供应商材料互不串线；
- 三个领域分别保存状态，单领域失败不丢失其他领域结果；
- 所有事实固定到文档版本、原文哈希、位置和证据 ID；
- Agent 不选择最终供应商，推荐由确定性规则计算；
- 失败领域可局部重试，超过两次后进入对应确定性降级处理器；
- 现有摄取、父子 Chunk、Hybrid RAG 和证据门禁继续复用。

## 3. 非目标

- 不引入 Agent 群聊、投票或领域路由模型；
- 不为需求、成本和承诺各创建独立 Agent；
- 不让模型修改权重、执行最终算术或覆盖确定性证据错误；
- 不在本阶段接入真实模型、前端或完整异步 API；
- 不提供法律意见。

## 4. Agent 契约

VendorReviewInput 包含：

- task、project 和 Vendor 身份；
- 本次运行冻结的 DocumentVersion 集合；
- 当前 Vendor 的 EvidenceItem 集合与项目公共需求证据；
- Requirement 目录；
- 币种、成本比较周期和必要费用类别；
- 需要核验的交付、人员、依赖、验收、SLA 和质保条目；
- 需要返工的领域和返工次数。

VendorReviewAgent 一次返回 VendorAssessment，其中必须恰好包含三个 DomainAssessment：

```text
VendorAssessment
  vendor_id
  domains
    requirement_compliance -> DomainAssessment
    cost_modeling -> DomainAssessment
    commitment_verification -> DomainAssessment
```

每个领域状态独立为 `completed`、`degraded` 或 `failed`。正常 Agent 调用生成 `completed` 或 `failed`；`degraded` 只由确定性降级处理器生成。

## 5. 三个业务结果

### 5.1 需求符合性

逐条输出 RequirementAssessment，状态为 `met`、`partially_met`、`not_met` 或 `not_stated`。事实性判断必须引用当前输入包中的证据；必选需求失败由后续确定性规则硬性淘汰。

### 5.2 成本建模

输出 CostAssessment，包括一次性成本、持续成本、可选成本、TCO、付款节点、缺失费用、冲突和证据 ID。模型识别费用语义，币种/周期归一化和 TCO 算术由确定性工具执行。

### 5.3 承诺核验

输出 CommitmentAssessment，覆盖交付日期、里程碑、人员、依赖、验收、SLA、质保和限制条件。冲突说法必须同时保留，不能静默选取其中一个。

## 6. 工具与输出门禁

ReviewToolbox 只允许已登记的需求比较、成本提取与计算、承诺检查工具；不存在让 Agent 直接选择获胜供应商的工具。单 Agent 可以在一次分析中使用多个业务领域的工具，但所有工具调用仍受代码白名单和注册表限制。

VendorReviewExecutor 校验：

- 项目、Vendor 和冻结版本范围；
- 输出必须包含且只包含三个领域；
- Requirement ID 必须来自输入目录；
- 成本币种和比较周期不得被修改；
- 所有证据 ID 必须来自当前输入包；
- 三个领域的 Vendor 和返工次数必须一致。

## 7. 工作流

目标 LangGraph：

```text
prepare_review
  → retrieve_vendor_evidence
  → analyze_vendors（Vendor 间并行）
  → validate_domains
  → retry_failed_domains（最多 2 次）
  → run_domain_fallbacks
  → aggregate_scores
  → compose_report
```

ReviewOrchestrator 为每个 Vendor 创建一个冻结任务。三家供应商形成三个并行 Agent 运行，而不是九个领域 Agent 任务。一次 Agent 调用输出三个领域结果；输出门禁按领域记录成功和失败。

局部返工时仍使用 VendorReviewAgent，但输入的 `revision_domains` 只标记失败领域，已验证领域结果由工作流保留。两次后仍失败的领域进入对应降级处理器：

- 需求符合性：RequirementRuleEvaluator；
- 成本建模：DeterministicCostEngine；
- 承诺核验：CommitmentRuleChecker。

降级不转交其他 Agent，也不创建人工处理队列。冲突和缺失信息进入最终报告。

## 8. 推荐规则

默认权重为需求 50%、成本 30%、承诺 20%，项目可配置但运行开始后冻结。需求状态分值为 `100/50/0/0`，承诺状态分值为 `100/50/0/0`，成本按最低可比 TCO 与当前 TCO 的比例计算。

明确不满足必选需求时硬性淘汰。降级结果、关键费用缺失或关键承诺缺失只能生成有条件推荐；需求领域失败时无法推荐。Agent 不直接输出推荐供应商。

## 9. 失败与恢复

- Agent 顶层异常由工作流记录，不删除已有 checkpoint；
- 单领域缺失或无效时仅该领域标记 `failed`；
- 每个领域最多返工两次；
- 确定性降级结果标记 `degraded`；
- 找不到材料时输出 `not_stated` 或缺失信息，不制造事实；
- checkpoint 保存冻结版本、三个领域状态、返工次数和已验证输出。

## 10. 测试与验收

单元测试覆盖一次调用产生三个领域结果、未知工具拒绝、证据范围、Requirement 目录、成本口径、单领域失败保留其他结果、降级规则和推荐限制。

工作流测试覆盖不同 Vendor 证据隔离、Vendor 间并行、只返工失败领域、两次失败后进入对应降级、checkpoint 恢复和最终确定性推荐。

端到端使用至少三家合成供应商：综合最优、价格最低但不满足必选需求、材料不完整。结果必须淘汰第二家、限制第三家的推荐等级，并让每个事实回到版本固定的原文证据。
