# 证据门禁与 Trace：沿一份报价理解

学习目标：解释结论如何被约束、失败如何返工，以及执行过程怎样留下可检查的记录。数据库管理接口只需要了解其输入/输出与事务边界。

## 一、证据门禁为什么必要

假设供应商原文为“软件及实施一次性费用80万元，年度维保10万元”。评审周期为36个月，已知费用的 TCO 为80+10×3=110万元。模型可能写成一次性费用90万元、把年费误认为月费，或者漏掉维保后推荐供应商。JSON 合法、HTTP 200、检索命中都不能排除这些错误。

证据门禁是本地执行的规则：模型提交结构化结论后，代码决定结论能否参与评分。模型说“置信度0.99”不能覆盖失败规则。

## 二、当前实现的三道检查

1. 输入来源和引用范围：coordinator 冻结本项目采购方与当前供应商的最新已索引版本；VendorReviewInput/Executor 校验供应商、版本与 evidence_id 范围。不能用另一供应商资料为当前报价背书。EvidenceItem 校验父块全文哈希和引用包含关系；这不是单独的数据库真实性证明。
2. 数字事实：evidence.validate_numeric_output 逐条检查需求说明、承诺陈述、付款条款和说明中的金额、百分比、完整日期、时长和数量，只查看该条实际引用的证据。80万元可规范化为800000 CNY；80万元不能支持90万元，90天不能支持90个月。
3. 成本授权与派生：cost_grounding 从来源提取明确金额、币种、周期、类别，保存 cost_sources 的原文片段与字符范围。ReviewToolRegistry 验证 calculate_tco 输入与提取结果一致，代码按冻结周期复算；ModelAnalyzer 将权威工具结果覆盖模型成本数值。TCO 是计算值，无需原文逐字写出110万元，但不能用这个派生值证明“供应商首付款110万元”。

默认必要业务成本类别为 software/implementation/maintenance。只有软件40万元的报价可以有已知成本，但 implementation 和 maintenance 进入 missing_costs；不能把缺少报价解释为免费，也不能获得无条件推荐。一次性/经常性/可选是算术类别，软件/实施/维保是业务完整性类别，两者不要混淆。

## 三、失败不会自动推翻所有领域

需求、成本、承诺属于同一 VendorReviewAgent 的三个结果领域。成本数字失败时丢弃成本评分载荷，保留需求与承诺结果；错误码进入 revision_feedback。首轮之后最多两次局部返工，仍失败则进入确定性 fallback。降级也使用同样的费用来源提取、成本完整性和输出门禁，不能成为绕过规则的通道。

可用部分来源时明确缺项并降级；没有可验证费用时成本领域失败，不能构造0元 TCO。aggregate_scores 根据失败、缺项、硬性需求和可信度限制推荐等级，compose_report 保存结果。

阅读顺序：schemas.EvidenceItem → review_agent.VendorReviewExecutor → numeric_facts.unsupported_numeric_facts → evidence.validate_numeric_output → cost_grounding.extract_grounded_costs/validate_cost_numbers → review_tools.ReviewToolRegistry → workflow 的 validate/revisions/fallback。

注意 DeterministicEvidenceGate.review 是保留的 Claim 校验接口，能回查数据库来源；正式三领域执行链以 validate_numeric_output 为中心。不要把两者混称为每次都执行的同一方法。

## 四、Trace 怎样记录一轮执行

Trace 是一次业务任务的执行树，Span 是其中一项操作。trace_id 将任务尝试关联起来，id 标识当前 Span，parent_span_id 表示父操作。

```text
vendor_review（workflow 根）
├─ retrieve_vendor_evidence（node）
│  ├─ retrieve_vendor（供应商A）
│  └─ retrieve_vendor（供应商B）
├─ analyze_vendors（node）
│  ├─ VendorReviewAgent（供应商A，revision_count=0）
│  │  ├─ chat_completion（model，第1轮）
│  │  ├─ calculate_tco（tool）
│  │  └─ chat_completion（model，第2轮）
│  └─ VendorReviewAgent（供应商B）
├─ validate_domains（node）
├─ prepare_domain_revisions → analyze_vendors（局部返工）
├─ run_domain_fallbacks → domain_fallback（耗尽返工时）
├─ aggregate_scores
└─ compose_report
```

树只是可能路径，成功首轮不必返工或降级。工具由本地代码在模型 HTTP 返回后执行，所以模型请求与工具 Span 在 Agent 下为兄弟节点。

TraceRecorder.span 是 contextmanager。进入 with 时写 running、开始时间、父 ID 并提交；执行期间不持有数据库事务；退出时写 ended_at、duration_ms 和状态。异常只记录异常类名再抛出。monotonic 计算耗时，UTC 时间用于显示。

父 ID 放在 ContextVar 中；每个供应商线程通过独立 copy_context 继承上下文。共享全局 parent_id 会在并行执行时相互覆盖，导致A的模型出现在B下面。

## 五、怎样读 Trace 才有用

- 检索跨度慢：检查索引与查询。当前没有逐条召回排名详情，不能单凭 Trace 判断 Recall。
- model 已 completed，但 agent 为 partially_completed：HTTP/协议成功，部分结果领域仍失败；结合 failed_domain_count、报告里的 missing_information 与门禁错误码定位。
- revision_count 为0、1、2后出现 fallback：原始尝试和两次返工都没有得到可接受结果。最终有报告仍应保留这些失败历史。
- 根 partially_completed：业务只完成部分领域或发生降级；不等于所有子操作异常。
- interrupted：旧 Worker 执行中断。一次 trace_id 可有多个尝试根跨度，恢复沿用 task_id/run_id/trace_id 并从 checkpoint 继续，未完成节点可能重做。

父跨度已包含子操作时间，供应商还可能并行；所有 duration_ms 相加不是整次任务耗时。中断跨度没有可靠结束耗时，duration_ms 可空。

## 六、四种记录不要混淆

| 记录 | 回答的问题 |
| --- | --- |
| 报告/EvidenceItem/cost_sources | 结论是什么，依据哪份原文？ |
| TraceSpan | 哪次调用成功/失败/慢，报告了多少 Token？ |
| RunEvent + SSE | 用户刷新后从哪个进度事件继续读取？ |
| LangGraph checkpoint | Worker 从哪个已保存状态继续执行？ |

Trace 不保存提示词、资料全文、工具原始参数/结果、Key 或隐藏思维链。safe_attributes 只允许选定标量字段。GET /tasks/{id}/trace 返回树和汇总；GET /tasks/{id}/events 只回放进度，不重新执行。

usage_complete=false 表示 Token 汇总不完整，未知不等于0。单价和完整 usage 同时存在才估算费用，按币种汇总，cost_complete 单独表示费用完整性；估算不等于实际账单。离线演示没有 model Span，因为 Agent 为确定性替身；模型请求树由 mock HTTP/model 测试验证，不冒充付费实跑。

## 七、验收与边界

tests/test_numeric_facts.py、test_cost_grounding.py、test_review_tools.py、test_review_agent.py、test_workflow.py 覆盖来源、数值、口径、领域失败、返工和降级。test_tracing.py/test_tasks.py 覆盖并行父子链、失败历史、usage 与脱敏；test_recovery.py 覆盖有效租约拒绝接管、过期恢复、旧执行禁止写结果/checkpoint，以及已完成 checkpoint 不重复 Agent。

数字一致不能证明语义蕴含。“最高支持500用户”和“保证支持500用户”包含同一个数值，但承诺含义不同。复杂报价、税费、数量乘法、打包关系、检索遗漏和完整上下文语义仍是边界。恢复也不承诺外部模型调用 exactly-once。Trace 是排障证据，需要结合标注评测才说明模型质量。

面试表达：我把模型输出看作待验证结论，在本地对证据范围、数字事实、费用口径与必要报价类别做确定性检查，失败按领域返工及降级；同时用父子 Trace 记录节点、Agent、模型和工具操作，在并行供应商与恢复场景下保留调用历史、耗时与不完整 usage 标记。
