# 后台评审运行纵向切片

沿用已确定的单 VendorReviewAgent，不新增业务角色。采用既有 RQ 与 PostgreSQL 事件存储，不使用 API 进程内 BackgroundTasks 或同步 HTTP 执行，避免连接必须等待模型完成。

## 实现边界

- POST 项目任务：校验供应商归属和需求目录，冻结需求/配置/查询、记忆上下文和 Skills 内容，提交任务与 queued 事件，然后入队并返回 202。
- Worker：原子 queued → running；以 run_id 为 checkpoint thread_id 执行工作流；每个完成节点写事件；领域评审、报告、终态事件、历史摘要与会话答复一次事务提交。
- GET 状态/报告；报告未生成返回 409。
- SSE：Last-Event-ID 优先于 after，序号必须非负；按序回放并轮询新事件，终态后关闭；不保存长事务。
- 运行级 Trace 只存时间、耗时、状态和异常类型，避免原始异常携带密钥。

## 故障与限制

数据库提交后入队可能失败，返回包含 task_id 的 503 和 enqueue_failed；若 Worker 已领取，不覆盖其状态。固定 RQ job_id 和数据库原子领取防止重复执行。不是事务 outbox，也不保证跨数据库/Redis 的 exactly-once。已实现执行租约、心跳、过期恢复、手动重试、模型/工具级 Trace 和可选共享 Bearer Key；身份与租户权限仍待实现。文档版本在检索节点冻结。项目与资料通过管理和上传 API 准备。

## 验证

离线真实 LangGraph 运行配合替代检索/执行器，检查快照、归属校验、重复领取、报告/领域保存、失败脱敏和 SSE 续读；验证 PostgreSQL 迁移往返，不调用付费模型。

## 后续已实现扩展：详细 Trace

运行级 Trace 已扩展为节点、Vendor 检索、Agent 尝试、模型、工具和领域降级的父子跨度；GET /tasks/{task_id}/trace 返回结构与 Token 汇总。ContextVar 加每项线程任务独立 copy_context 保证并行父子关系。属性采用标量白名单，未知工具名称不落库，异常仅保留类型，未报告完整 usage 不计为零消耗。新增离线验证覆盖嵌套、并行、工具异常、Token 缺失、两次返工及降级。Worker 已支持持久化 checkpoint 恢复；中断节点内的外部模型请求可能重复执行。

## 会话、规则与模型路由

任务创建时按显式请求、评审配置、用户偏好、默认值的顺序确定口径，并冻结会话上下文和 Skills 内容。Worker 依据实际证据包的供应商和版本范围过滤历史摘要。模型端点按健康窗口选择，每次工具循环固定端点，领域返工可重新选择；路由记录与 Trace 可查询。结果提交同时回写记忆。详细约束见[会话、Skills 与路由实现记录](../../learning/2026-10-02-conversation-skills-routing.md)。
