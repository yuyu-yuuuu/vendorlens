# VendorLens 领域 Agent 重构实施计划

依据：[领域 Agent 重构设计](../superpowers/specs/2026-10-01-domain-agent-redesign.md)

## 阶段 1：领域基础与推荐规则

1. 在 `schemas.py` 增加供应商评审枚举、RequirementAssessment、CostAssessment、CommitmentAssessment、DomainAssessment、ReviewWeights 和 RecommendationResult。
2. 新增确定性评分模块，覆盖需求符合性、相对 TCO、承诺可信度、50/30/20 总分、硬性淘汰、条件推荐和并列推荐。
3. 先写失败测试，再实现评分规则；不接入模型。

验收：低价供应商不能覆盖必选需求失败；缺少成本或承诺只能条件推荐；公式和边界有单元测试。

## 阶段 2：供应商与需求持久化

1. 新增 Vendor、Requirement、ReviewConfig、DomainAssessmentRecord。
2. Document 增加可空 `vendor_id`，采购方材料保持为空，供应商材料必须在服务层校验关联。
3. 增加 Alembic 迁移并验证 PostgreSQL upgrade、downgrade、upgrade。
4. 扩展摄取服务，使索引 metadata 带上 `vendor_id`。

验收：同一项目供应商名称唯一；需求保留来源 Chunk；检索可按项目、供应商和版本联合过滤。

## 阶段 3：三个领域 Agent 契约与工具

进度：输入契约、确定性路由、受限工具箱和统一执行器已完成；领域工具注册与真实模型适配器仍待接入，目前使用固定分析器替身验证边界。

1. 定义统一 DomainAgent 协议和三种领域输入包。
2. 实现需求匹配、金额归一化/TCO、日期周期/承诺冲突等确定性工具。
3. 为三个 Agent 建立互斥工具白名单和结构化输出校验。
4. 为三个领域分别实现 RequirementRuleEvaluator、DeterministicCostEngine 和 CommitmentRuleChecker。
5. 降级结果保留冲突和缺失信息；无法形成领域结果时标记为 `failed`。
6. 使用固定模型替身测试，不在此阶段依赖真实模型服务。

验收：跨领域工具调用被拒绝；事实性 finding 无证据不能通过；算术只由工具执行；降级不跨 Agent，降级结果不能产生无条件推荐。

## 阶段 4：按领域和供应商隔离检索

1. ReviewOrchestrator 冻结 Vendor、DocumentVersion、Requirement、权重和成本周期。
2. 扩展检索协调器，按 `domain × vendor_id × latest_version_ids` 生成隔离证据包。
3. 采购方需求证据与供应商响应证据使用不同来源角色，禁止供应商之间串线。

验收：三家供应商生成九个任务；每个输入包只包含当前供应商和公共需求材料。

## 阶段 5：重构 LangGraph

1. 将旧 Planner/Analyst/Reviewer 工作流替换为 prepare、retrieve、analyze、validate、revise、fallback、aggregate、compose 节点。
2. 首轮并行执行全部领域和供应商任务；返工仅重跑失败任务，最多两次。
3. Checkpoint 保存冻结上下文、每个任务状态、返工次数和已验证输出。
4. 报告合并器只消费验证结果，不新增事实。

验收：单任务失败不丢失其他结果；恢复后不重跑完成任务；缺少符合性结果时不推荐。

## 阶段 6：运行入口、评测与文档

1. 将重构后的工作流接入 Worker 和任务 API。
2. 增加至少三家合成供应商端到端数据：综合最优、低价但硬性淘汰、材料不完整。
3. 输出领域准确性、证据有效率、推荐正确性和恢复行为测试结果。
4. 按实测状态更新 README、总体设计和结果文档。

验收：端到端结果淘汰低价不合格供应商，对材料不完整供应商只给条件推荐，每项事实可回到原文。

## 实施顺序与提交边界

- 提交 1：领域 Schema 与确定性推荐引擎。
- 提交 2：数据库模型、迁移和 Vendor 隔离摄取。
- 提交 3：领域工具和 Agent 契约。
- 提交 4：隔离检索与新 LangGraph。
- 提交 5：API/Worker、端到端评测和文档。

每个提交必须通过全量测试、Ruff、Alembic 检查；涉及迁移时额外执行 PostgreSQL 往返迁移。
