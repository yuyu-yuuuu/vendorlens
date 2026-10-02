# VendorLens 供应商评审 Agent 实施计划

依据：[供应商评审 Agent 设计](../superpowers/specs/2026-10-01-domain-agent-redesign.md)

## 阶段 1：领域基础与推荐规则

状态：已完成。

- 三个领域 Schema、50/30/20 确定性评分、必选需求硬性淘汰、条件推荐和并列推荐。

## 阶段 2：供应商与需求持久化

状态：已完成。

- Vendor、Requirement、ReviewConfig、DomainAssessmentRecord；
- Document 的 Vendor 归属和索引 metadata 隔离；
- Alembic 迁移及 PostgreSQL 往返验证。

## 阶段 3：单 Agent 契约、工具与降级

状态：已完成。

1. VendorReviewInput 一次携带当前 Vendor 的三个领域上下文。
2. VendorReviewAgent 一次输出 VendorAssessment 的三个领域结果。
3. ReviewToolbox 拒绝未登记工具；VendorReviewExecutor 校验证据、Vendor、版本和成本口径。
4. 单领域输出缺失时标记该领域 `failed`，保留其他领域结果。
5. RequirementRuleEvaluator、DeterministicCostEngine 和 CommitmentRuleChecker 提供领域级确定性降级。
6. OpenAI 兼容 Chat Completions 适配器支持多轮工具调用，11 个正式工具绑定当前 Vendor 冻结证据。

## 阶段 4：按 Vendor 隔离检索

状态：已完成。

1. ReviewOrchestrator 冻结 Vendor、DocumentVersion、Requirement、权重和成本周期。
2. 检索协调器按 `vendor_id × latest_version_ids` 生成证据包，内部按需求、成本和承诺组织证据预算。
3. 采购方需求证据与当前供应商响应证据使用不同来源角色，禁止供应商之间串线。

验收：三家供应商生成三个 Agent 输入包；每个输入包只包含当前供应商和公共需求材料。

## 阶段 5：重构 LangGraph

状态：已完成。

1. 将旧 Planner/Analyst/Reviewer 流程替换为 prepare、retrieve、analyze_vendors、validate_domains、revise、fallback、aggregate、compose。
2. Vendor 间并行；返工只标记失败领域，最多两次。
3. Checkpoint 保存冻结上下文、每个 Vendor 的三个领域状态、返工次数和已验证输出。
4. 报告合并器只消费验证结果，不新增事实。

验收：单领域失败不丢失其他结果；恢复后不重跑完成领域；缺少符合性结果时不推荐。

## 阶段 6：运行入口、评测与文档

1. 接入 Worker 和任务 API。
2. 增加综合最优、低价但硬性淘汰、材料不完整三家合成供应商。
3. 输出领域准确性、证据有效率、推荐正确性和恢复行为测试结果。
4. 按实测状态持续更新 README、设计和结果文档。

每个提交必须通过全量测试、Ruff 和 Alembic 检查；涉及迁移时额外执行 PostgreSQL 往返迁移。
