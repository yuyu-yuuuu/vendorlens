# 后台任务与 Trace：重点代码学习

## 先理解这一阶段的位置

模型冒烟测试是直接调用一次执行器，结果打印在终端。正式使用需要让用户提交评审后立即拿到任务 ID，后台执行，刷新后还能查进度和报告。本阶段没有增加业务 Agent，增加的是运行基础设施。

主链：api.submit_review → tasks.create_review_task → tasks.enqueue_review → RQ Worker → tasks.execute_review_job → tasks.run_review_task → ReviewWorkflow → 保存领域结果/报告/终态事件。

## 1. API、队列、Worker 分别负责什么

API 接收请求和校验，RQ/Redis 保存等待执行的工作，Worker 消费队列并执行。Redis 排队不等于 Redis 做分析；分析仍然由 Python Worker 调用原有检索、Agent 和确定性工具完成。

submit_review 先 session.commit，再调用 enqueuer。否则 Worker 可能已经执行，数据库中任务却还没提交，查不到任务。数据库和 Redis 不在同一个事务里，所以提交后入队失败仍可能发生；当前记录 enqueue_failed 并返回 task_id，不声称 exactly-once。

HTTP 202 表示已接受任务，不表示分析已经完成。创建 task_id 用于查询业务记录，run_id 用于队列 job_id 和 checkpoint thread_id，trace_id 用于关联执行记录；三个 ID 在当前首版是一个任务各一个，恢复后的生命周期策略尚未实现。

## 2. 为什么要 request_snapshot

用户上午提交任务时比较周期是 36 个月，下午改为 12 个月。后台不应该在执行到一半时突然改口径。create_review_task 从 Requirement、ReviewConfig、项目上下文生成 JSON 快照，Worker 使用快照恢复 RequirementItem、ReviewDomain 和 ReviewWeights。

快照冻结需求、权重、周期、币种、查询和目标。文档版本另由检索节点在执行时冻结；不能说整个任务提交时所有数据已经冻结。

## 3. 原子领取为什么比先查后改安全

run_review_task 执行条件更新：UPDATE research_tasks SET status='running' WHERE id=... AND status='queued'。只有影响一行的 Worker 才能继续。第二个 Worker 看到状态不再 queued，rowcount 为零，退出。

若使用先 SELECT queued 再 UPDATE，两个 Worker 都可能先看到 queued，从而重复调用模型和写报告。当前的防重针对同一个 task_id，不是同一请求多次提交的幂等，也不是硬中断后的自动恢复。

## 4. 领域结果、报告、终态为何一起提交

完成后，同一数据库事务写入 DomainAssessmentRecord、AnalysisReport、ResearchTask.status 和 run.finished。这样不会出现任务显示 completed、报告尚未写入的中间状态。SQLAlchemy 的 flush 将改动发给数据库但不提交；commit 才让事务生效。

执行异常时写 failed 与脱敏 run.failed。正常工作流也可能生成 failed 或 partially_completed 的业务报告；这与程序抛出异常是两类失败。

## 5. SSE 如何续读

RunEventStore.append 锁定任务行，取该任务最大 sequence 加一，再写事件。不同任务各自从 1 编号。format_sse 输出 id、event 和 JSON data；浏览器可回传 Last-Event-ID，查询只返回 sequence 更大的事件。

这恢复的是事件阅读位置，不是重新执行 Agent，也不是逐字模型输出。轮询每秒一次，空闲时发 keep-alive 注释；读完终态事件后关闭。每轮查询用短会话，不让数据库连接和事务跨越等待时间。

## 6. Trace 为什么需要父子结构

SSE 回答“执行到了哪里”；Trace 回答“哪个调用慢、失败在哪、用了多少 Token”。TraceSpan 的 id 标识当前操作，parent_span_id 表示它属于哪个操作，trace_id 表示同一次任务运行。

典型树：运行 → analyze_vendors 节点 → VendorReviewAgent → 第一次模型请求、calculate_tco 工具、第二次模型请求。工具是模型请求返回后由本地代码执行的，因此这里模型跨度和工具跨度是 Agent 下的兄弟节点，不是把工具执行耗时计入 HTTP 请求跨度。

tracing.TraceRecorder.span 是 contextmanager：进入 with 时写 running 跨度并设置父 ID；离开时恢复父 ID，写 ended_at/duration_ms/status；异常记录类型并继续抛出。monotonic 用于耗时，避免系统时间校准导致负耗时；UTC 时间戳用于显示发生时间。

ContextVar 管理当前执行上下文的父 ID，不使用共享全局 parent_id。ThreadPoolExecutor 不自动继承调用者上下文，所以每个 pool.submit 都用独立 copy_context().run，把正确的父节点带进当前供应商线程，避免跨 Vendor 串线。

## 7. 工具调用、返工和降级如何观察

model_analyzer 每次 complete 外包 model 跨度，记录轮次、模型名、工具调用数量和完整 usage。每个 registry.call 外包 tool 跨度，只记录登记名称、参数数量和轮次，不记录参数内容。

workflow 每次 executor.execute 外包 agent 跨度，记录 Vendor、revision_count、领域数和失败领域数。首轮 revision_count=0，最多两次返工为 1、2。fallback 记录 domain_fallback 跨度及 degraded/failed。最终报告部分完成，不会抹去前面失败的尝试。

模型 HTTP 调用 completed 只表示请求和解析成功；最终 Draft 或证据门禁仍可能失败，Agent 跨度会反映失败。不能只看 HTTP 200 判断 Agent 正确。

## 8. Token 和隐私边界

提示词、原始材料、工具参数/结果、Authorization 和 API Key 不进入 Trace。safe_attributes 只接受选定的标量字段，错误只保存异常类名，未注册工具名称统一为 unregistered_tool。

provider 未报告完整 usage 时，不把默认零值当真实消耗。trace 接口统计 model_calls/tool_calls/usage_reported_calls；Token 只合计完整 usage 的调用，通过 usage_complete 标记是否完整；没有完整 usage 时汇总为 null。当前不计算账单金额，也不记录隐藏思维链。

## 9. 面试表达与当前边界

可以说：将证据驱动评审工作流接入 RQ 后台任务，使用数据库原子领取避免同任务重复执行，将结果和终态事件事务提交，持久化 SSE 序号支持续读，并用 ContextVar/copy_context 实现并行 Vendor 的父子 Trace。

必须补充：数据库/Redis 之间没有事务 outbox；API 没有鉴权；硬中断可能遗留 running 任务或跨度，自动恢复尚未实现；trace 不是模型效果评测；离线假模型测试不代表真实模型质量或生产吞吐量。
