# VendorLens 模型适配器与领域工具设计

日期：2026-10-02
状态：待用户审阅

## 1. 背景与决策

VendorLens 已完成单 `VendorReviewAgent` 契约、Vendor 级证据隔离、领域级返工与确定性降级，但当前分析器仍是测试替身，`ReviewToolbox` 也只有工具名称白名单。下一步把真实模型接入现有 Agent，同时保留现有结构化输出门禁，不引入第二个 Agent、Judge Agent 或低分转交机制。

模型接入采用 OpenAI 兼容的 Chat Completions 接口。运行时只依赖统一的 `base_url`、`api_key` 和 `model` 配置，因此既可连接 OpenAI，也可连接实现相同协议的服务。适配器通过 `httpx` 调用 `/chat/completions`，业务代码不直接依赖具体供应商 SDK；测试使用内存假模型或假 HTTP 传输，不发送网络请求。

本阶段只实现模型适配器、提示输入、领域工具注册和相应测试。RQ Worker、任务 REST API、SSE 与 Trace 接线属于后续阶段。

## 2. 目标与非目标

目标：

- 让 `VendorReviewAgent` 能通过统一模型接口生成 `VendorReviewDraft`；
- 让模型只能使用明确注册的当前 Vendor 领域工具；
- 继续由确定性代码完成金额计算、证据范围检查和最终推荐；
- 将模型超时、协议错误、结构化输出错误映射到现有失败、返工和降级流程；
- 确保 API Key、Authorization header 和原始敏感配置不进入日志或异常文本。

非目标：

- 不增加领域 Agent、Judge Agent、Agent 评分或 Agent 间转交；
- 不允许模型搜索当前冻结证据包之外的数据；
- 不让模型自行计算最终 TCO、供应商总分或推荐结论；
- 不在本阶段实现流式对话、网页搜索、Worker/API/SSE/Trace；
- 不在测试中调用真实模型或消耗模型额度。

## 3. 组件边界

### 3.1 `ChatModel` 协议

新增供应商无关的 `ChatModel` 协议。它接收已经脱敏的系统指令、业务输入和可用工具定义，返回结构化响应及工具调用请求。协议不暴露 HTTP、SDK 或供应商专有对象给 Agent 层。

模型响应统一包含：

- 最终结构化 JSON；或
- 一组工具调用，包含调用 ID、工具名和 JSON 参数；
- 可选的公开运行元数据，例如模型名、输入/输出 token 数；
- 不保存隐藏思维链。

### 3.2 `OpenAICompatibleChatModel`

该适配器负责：

- 使用 `base_url`、`api_key`、`model`、超时和最大工具循环次数发起请求；
- 使用 OpenAI 兼容的 Chat Completions 消息与 `tools`/`tool_calls` 字段，不依赖供应商专有扩展；
- 将 VendorLens 工具定义转换为兼容接口的 function tool schema；
- 解析工具调用和最终 JSON；
- 将网络超时、鉴权失败、限流、无效 JSON 和循环超限转换为类型明确的模型异常；
- 对异常信息脱敏，不回显密钥和 Authorization header。

适配器不得包含需求、成本或承诺领域逻辑。

### 3.3 `VendorReviewModelAnalyzer`

新增分析器连接 `ChatModel` 与现有 `VendorReviewAgent`：

```text
VendorReviewInput
  -> 构造受控模型上下文
  -> ChatModel 请求
  -> 执行允许的工具调用
  -> 继续模型循环
  -> VendorReviewDraft.model_validate
  -> VendorReviewAgent 生成三个 DomainAssessment
  -> VendorReviewExecutor 做最终输出门禁
```

模型上下文只包含当前输入包的项目背景、Requirement 目录、领域配置和证据摘要。返工时明确给出 `revision_domains`、返工次数及上一次失败原因，要求模型只重做指定领域。

工具循环设置有限上限；超过上限视为本次 Agent 执行失败，并由现有工作流决定是否返工或降级。

## 4. 正式领域工具

工具以显式注册表注入 `VendorReviewExecutor`。每个工具接收已冻结的 `VendorReviewInput` 上下文，不接收任意项目 ID、Vendor ID、SQL 或文件路径。模型传入的证据 ID 必须先在当前领域证据集合中解析，未知 ID 立即拒绝。

### 4.1 需求符合性

- `search_requirement_evidence`：在当前需求领域证据中按 Requirement 和查询词筛选候选证据；
- `compare_requirement_response`：比较 Requirement 与指定证据，返回可供模型判断的确定性匹配特征；
- `mark_missing_response`：在没有可引用答复时生成明确的缺失结果。

### 4.2 成本建模

- `extract_cost_items`：从指定证据中提取金额候选、币种、周期和费用类别；
- `normalize_cost`：按冻结币种与比较周期归一化已确认费用项；
- `calculate_tco`：使用确定性算术计算一次性、持续、可选费用及 TCO；
- `compare_cost_items`：检测重复、遗漏和相互冲突的费用项。

模型负责把文本费用映射到业务类别，但不得覆盖 `calculate_tco` 返回的算术结果。

### 4.3 承诺核验

- `extract_commitments`：从指定证据提取交付、人员、依赖、验收、SLA、质保和限制候选；
- `compare_milestones`：比较日期和里程碑是否一致；
- `check_dependencies`：检查承诺依赖和前提是否缺失；
- `detect_commitment_conflicts`：保留并报告相互冲突的承诺。

## 5. 配置与安全

新增以下环境配置：

- `VENDORLENS_MODEL_BASE_URL`；
- `VENDORLENS_MODEL_API_KEY`；
- `VENDORLENS_MODEL_NAME`；
- `VENDORLENS_MODEL_TIMEOUT_SECONDS`，使用保守默认值；
- `VENDORLENS_MODEL_MAX_TOOL_ROUNDS`，默认值必须有限。

应用启动时可以在未配置模型的情况下运行健康检查和确定性测试；只有创建真实模型适配器时才要求模型配置完整。配置对象和异常的字符串表示不得暴露 API Key。工具参数不得包含数据库连接、系统路径或未脱敏的运行配置。

## 6. 错误处理

错误分为：

- `ModelConfigurationError`：配置缺失或非法，在创建适配器时失败；
- `ModelTransportError`：超时、连接或服务端错误；
- `ModelAuthenticationError`：鉴权失败；
- `ModelRateLimitError`：限流；
- `ModelProtocolError`：响应形状、工具调用或 JSON 非法；
- `ModelToolLoopError`：超过工具循环上限；
- 现有 `ReviewToolAccessError`：调用未授权工具；
- 现有 `ReviewOutputError`：最终结构化结果违反证据或业务契约。

除配置错误外，运行时错误统一转换为当前 Vendor/领域的失败结果，进入现有“最多返工两次，再确定性降级”的流程。不得静默吞掉异常，也不得把失败伪装成 `completed`。

## 7. 测试与验收

测试全部使用假传输或 `FakeChatModel`，覆盖：

- 无工具调用时解析合法 `VendorReviewDraft`；
- 多轮工具调用后生成最终结构化结果；
- 未注册工具和跨 Vendor/跨领域证据被拒绝；
- TCO 使用确定性工具结果，模型不能篡改；
- 返工输入只要求失败领域；
- 超时、鉴权、限流、非法 JSON 和工具循环超限映射到正确异常；
- 错误文本、日志和可公开元数据不包含 API Key；
- 现有 `VendorReviewExecutor` 输出门禁、工作流返工和降级测试继续通过。

完成标准：真实适配器可由配置创建，单 Agent 可通过正式工具生成符合现有 Schema 的三个领域结果，测试不需要网络和密钥，且未改变确定性推荐规则。

## 8. 后续接线

本阶段完成后，下一阶段将构造好的 `VendorReviewExecutor` 注入 RQ Job，由任务 API 创建 `ResearchTask` 并立即返回 `run_id`，运行过程写入持久化事件和 Trace，SSE 使用 `Last-Event-ID` 恢复。该接线不改变本文定义的模型和工具边界。
