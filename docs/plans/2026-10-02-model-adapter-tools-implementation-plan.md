# 模型适配器与领域工具实施计划

日期：2026-10-02

状态：已完成。

## 目标

在不改变单 Agent、领域级返工、确定性降级和推荐规则的前提下，为 `VendorReviewAgent` 接入 OpenAI 兼容 Chat Completions 模型与受限领域工具。所有测试离线运行。

## 任务 1：模型配置与协议

- 在 `config.py` 和 `.env.example` 增加模型 URL、密钥、模型名、超时和最大工具轮数；
- 新建 `model_adapter.py`，定义请求、响应、工具调用、公开 usage 和类型化异常；
- 使用可注入的 `httpx` transport/client 实现 Chat Completions；
- 测试成功响应、工具调用、超时、401、429、服务端错误、非法响应与密钥脱敏。

## 任务 2：受限领域工具

- 新建 `review_tools.py`，定义带 JSON Schema 的工具规格和绑定当前 `VendorReviewInput` 的注册表；
- 证据参数只能引用当前领域已冻结的 EvidenceItem；
- 实现需求检索/比较/缺失、成本提取/归一化/TCO/冲突比较、承诺提取/里程碑/依赖/冲突工具；
- 对未知工具、非法参数、跨领域或跨 Vendor 证据进行测试。

## 任务 3：模型分析器

- 新建 `model_analyzer.py`，把受控输入转换为消息；
- 循环处理 `tool_calls`，把每个工具结果作为 `tool` 消息回传；
- 达到最大轮数、拒答、无内容或非法 JSON 时抛出类型化错误；
- 使用 `VendorReviewDraft.model_validate` 解析最终结果；
- 测试零工具、多轮工具、返工领域提示和最终 Draft 校验。

## 任务 4：装配、文档与回归

- 提供创建正式 `VendorReviewExecutor` 的工厂，不在 import 时读取或验证密钥；
- 将 `httpx` 放入运行依赖，更新 README 和设计状态；
- 运行 Ruff、全量 pytest/coverage、Alembic check 和 diff check；
- 提交并推送，下一阶段再接 Worker/API/SSE/Trace。

## 验收

- 未配置模型时健康检查与现有测试正常；
- 配置完整时能构造真实模型执行器；
- 模型无法调用白名单之外的工具或引用冻结范围之外的证据；
- 模型不能覆盖确定性 TCO；
- 运行错误能被现有工作流重试和降级；
- 测试不访问网络、不需要 API Key，并保持项目总覆盖率不低于 90%。
