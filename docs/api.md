# VendorLens 接口文档

## 文档入口

- Swagger UI：`http://127.0.0.1:8000/docs`，查看参数、响应模型和示例，并通过 Try it out 调用接口。
- ReDoc：`http://127.0.0.1:8000/redoc`，用于阅读结构化文档。
- OpenAPI：`http://127.0.0.1:8000/openapi.json`，可导入支持 OpenAPI 的接口工具。

文档直接从 FastAPI 路由和 Pydantic 模型生成，修改接口时同步更新其描述和模型，而不是维护一份脱离实现的 JSON。

## 启动与前提

在 VendorLens 项目目录执行：

```bash
.venv/bin/uvicorn vendorlens.api:app --host 127.0.0.1 --port 8000 --reload
```

文档页面和 OpenAPI 可在没有数据库连接时读取，但业务接口需要数据库可用。先运行 `python3 scripts/setup_local_env.py` 初始化忽略的本地凭证，脚本保留已有模型密钥。`.env` 使用本机映射地址，Compose 自动覆盖容器内依赖地址。不要用示例文件覆盖已保存的模型密钥。

执行真实任务前需要完成数据库迁移、准备项目/供应商/需求目录、摄取并索引资料，以及另行启动 `.venv/bin/python -m vendorlens.worker`。项目、供应商、需求、配置与资料准备均可通过管理 HTTP 接口完成。上传同步完成解析和索引；大文件/高并发后台摄取尚未实现。

**Try it out 调用的是真实接口，不是模拟演示。** POST 创建任务可能导致后台模型费用；文档中的 ID 是占位值，执行前必须替换。配置 VENDORLENS_API_ACCESS_KEY 后，业务接口要求 Authorization: Bearer <key>；在 Swagger 的 Authorize 中填写密钥。仅 development 环境允许未配置 Key，其他环境缺少或为空时返回 503。Compose 使用 production 模式并要求凭证。文档页公开，共享 Key 不提供租户权限。

Swagger/ReDoc 使用框架默认的 CDN 前端资源；如果页面空白但 openapi.json 正常，先检查浏览器是否能加载 CDN。当前未打包离线前端资源。

## 接口索引

| 方法与路径 | 功能 | 成功状态 |
| --- | --- | --- |
| GET /health | API 与数据库健康检查，不检查所有依赖 | 200 |
| POST /projects/{project_id}/sessions | 创建评审会话 | 201 |
| POST /projects/{project_id}/sessions/{session_id}/messages | 保存用户讨论 | 201 |
| GET /projects/{project_id}/sessions/{session_id}/context | 查看当前三层上下文 | 200 |
| GET / PUT /projects/{project_id}/preferences | 读取或显式更新长期偏好 | 200 |
| GET /skills | 查看规范版本与哈希 | 200 |
| POST /skills/reload | 原子热加载规范 | 200 |
| GET /routing/health | 模型端点健康、冷却与探测状态 | 200 |
| GET /tasks/{task_id}/routing | 任务每次路由选择与反馈 | 200 |
| POST /projects/{project_id}/tasks | 创建任务与输入快照，然后入队 | 202 |
| GET /tasks/{task_id} | 查询任务状态 | 200 |
| GET /tasks/{task_id}/report | 查询最终报告 | 200 |
| GET /tasks/{task_id}/events | SSE 进度回放与订阅 | 200 |
| GET /tasks/{task_id}/trace | 查询执行树、已报告 Token 与配置单价估算 | 200 |
| POST /tasks/{task_id}/retry | 恢复过期、执行异常或入队失败任务 | 202 |
| GET /metrics | 聚合 Prometheus 文本指标 | 200 |
| POST/GET /projects | 创建/分页读取项目 | 201/200 |
| GET/PUT /projects/{project_id} | 读取/更新项目 | 200 |
| POST/GET /projects/{project_id}/vendors | 创建/列出供应商 | 201/200 |
| PUT /projects/{project_id}/vendors/{vendor_id} | 更新供应商 | 200 |
| POST/GET /projects/{project_id}/requirements | 创建/列出需求 | 201/200 |
| PUT /projects/{project_id}/requirements/{requirement_id} | 更新需求 | 200 |
| GET/PUT /projects/{project_id}/config | 读取/更新权重、币种、周期及必要清单 | 200 |
| POST /projects/{project_id}/documents | multipart 上传 file 与可选 vendor_id | 201 |
| GET /projects/{project_id}/documents | 读取资料目录 | 200 |
| GET /projects/{project_id}/documents/{document_id}/versions | 读取不可变版本 | 200 |
| GET /projects/{project_id}/versions/{version_id}/chunks | 读取原文和定位 | 200 |
| GET /projects/{project_id}/versions/{version_id}/file | 下载原文件 | 200 |
| POST /projects/{project_id}/versions/{version_id}/index | 幂等重建/重试索引 | 200 |
| GET /projects/{project_id}/tasks | 分页读取任务 | 200 |

## 操作示例

1. 打开 /docs，展开“评审任务 → 创建并入队供应商评审任务”。
2. 点击 Try it out，填写已存在的 project_id 和 vendor_ids，输入评审目标。
3. 点击 Execute，记录返回的 task_id。HTTP 202 不表示分析完成。
4. 将 task_id 填入“查询任务状态”；完成或部分完成后查询报告。
5. 查询 Trace，查看父子跨度、调用次数和耗时。usage_complete=false 时 Token 汇总不完整；不能当作完整消耗或账单金额。

请求示例：

```json
{
  "objective": "比较供应商方案与三年总拥有成本",
  "vendor_ids": ["替换为真实供应商ID"]
}
```

queries_by_domain 可省略；提供时须覆盖 requirement_compliance、cost_modeling、commitment_verification，每个领域 1-20 条非空查询，每条最多 2000 字。需求、币种、周期与权重从项目配置读取并在提交时冻结；文档版本在 Worker 检索时冻结。

## SSE 特别说明

Swagger 对持续响应显示有限，建议用 curl 观察事件，以下操作不重新执行 Agent：

```bash
curl -N 'http://127.0.0.1:8000/tasks/替换为真实任务ID/events'
curl -N -H 'Last-Event-ID: 5' 'http://127.0.0.1:8000/tasks/替换为真实任务ID/events'
```

事件包含 id/event/data。Last-Event-ID 优先于 after，只读取更大的序号；空闲时发送 keep-alive 注释，终态后关闭。这里是节点进度，不是模型逐字输出。

## 常见错误

| 状态 | 含义 | 处理 |
| --- | --- | --- |
| 404 | 项目或任务不存在 | 检查 ID 是否真实且属于当前数据库 |
| 409 | 报告没有生成 | 查询任务状态；执行异常失败时可能永远没有报告 |
| 422 | 请求字段、供应商归属、需求目录或配置不合法 | 按字段说明修正；需求目录必须非空 |
| 503 | 任务已保存但入队失败 | 保留响应 detail.task_id 查询，不要当成未创建而盲目重提 |
| 500 | 未处理的服务/数据库异常 | 检查配置、依赖连接、迁移与服务日志 |

任务状态为 queued/running/completed/partially_completed/failed/enqueue_failed。调用 retry 保留原 task_id/run_id/trace_id 和 request_snapshot，从持久化 checkpoint 继续；有效租约、queued 或已有报告的任务拒绝重放。自动扫描 `python -m vendorlens.recovery --watch` 仅恢复过期 running；执行失败、入队失败需要手动 retry。每次领取增加 attempt，Trace 生成新根跨度，旧未结束跨度标记 interrupted。已发生的外部调用无法回滚，恢复不承诺模型调用 exactly-once。

## 从空库准备评审输入

创建项目 → 创建供应商 → 上传不带 vendor_id 的采购方需求书 → 从 chunks 接口取得采购方 source_chunk_id → 登记 requirement → 配置评审口径 → 上传带 vendor_id 的供应商资料 → 创建任务。需求来源必须属于本项目采购方，不能引用供应商材料。管理 API 中项目归属检查只做数据隔离，不是身份授权。

上传支持 PDF/DOCX/XLSX/MD/TXT，默认上限 20 MiB，空文件/解析失败 422，格式 415，过大 413。索引失败返回 503 和 version_id，已解析版本保留为 index_failed，可用 index 端点重试。读取 chunks 与文件会暴露原始资料，部署时应配置访问 Key。扫描件仍需 OCR，当前不会自动 OCR。版本/引用历史不提供删除接口。

必要费用类别默认 software/implementation/maintenance，缺失将限制推荐；零费用项必须有明确来源，不能把缺报价视为免费。承诺清单与配置提交时冻结。/trace 的 cost_complete 与 usage_complete 各自表示汇总完整性；费用按配置单价和完整 Token 估算，并按币种分组，不能当作实际账单。

任务与待发送 outbox 同事务提交；scanner 使用带租约的 dispatch 重试，覆盖提交后入队前进程死亡、发送结果不确定与队列暂时不可用。重试保留同一 dispatch job_id，Worker 仍以数据库领取阻止重复业务执行；不承诺外部模型 exactly-once。Metrics 读取数据库聚合，未实现专用监控存储与看板。

## 会话、规范与模型路由

创建会话请求为 `{"user_id":"alice"}`，返回 session_id。messages 接口接收 `{"content":"重点比较维护成本"}`，context 与 preferences 接口使用 `?user_id=alice` 指定作用域。user_id 默认 local，是客户端作用域标识；当前共享 Key 不提供用户身份认证。

关联会话的任务请求：

```json
{
  "objective": "结合前面的讨论比较供应商",
  "vendor_ids": ["已有供应商ID"],
  "session_id": "创建会话返回的ID",
  "user_id": "alice",
  "industry": "software",
  "comparison_months": 60,
  "currency": "CNY"
}
```

comparison_months 和 currency 可省略。取值优先级为本次参数、项目配置、显式偏好、默认值。preferences 的 PUT 是整份偏好替换，支持 comparison_months、currency、report_style（concise/detailed）与 focus_areas；消息不会自动改偏好。任务提交时冻结已有讨论与规范，完成时保存评审摘要及 assistant 消息。messages 接口只保存讨论，评审由任务接口生成。

Skills 支持 JSON 和带 JSON front matter 的 SKILL.md，含 id、version、enabled、industries、domains、keywords 和 content。general 行业规则通用；industry=software 会同时加载通用与软件规则。新任务检测文件变化；reload 成功整体替换规则，非法文件返回 422 并保留上一代有效缓存。规则目录中不要存放模型密钥。

VENDORLENS_MODEL_ENDPOINTS 接收端点 JSON 数组，每项包含 id、base_url、api_key、model，可选 provider、timeout_seconds、base_score 与输入/输出百万 Token 单价。密钥仅放在本地配置；路由 API 返回端点标识、健康分和运行指标。整个工具循环使用一次选定的端点，领域返工时重新选择。所有端点冷却时返回执行错误，由有限返工与确定性降级处理。

健康分使用最近窗口的异常率、限流率、失败领域比例、P95 请求耗时和同币种费用估算。冷却到期允许一个有租约的恢复探测。`/tasks/{task_id}/routing` 提供候选分、所选端点、探测标记与结果；Trace 中新增 routing 跨度连接模型调用。未配置真实模型时 `/routing/health` 返回空列表。

## 报告领域详情

`GET /tasks/{task_id}/assessments` 返回任务目标、币种、比较周期、各供应商领域结果及该次评审保存的来源证据。引用包含文件名、固定 version_id、版本号、定位、quote、context_text 和哈希；版本信息按固定 ID 查询，不替换成最新版本。历史领域记录没有来源快照时返回空引用列表。前端将该接口与 report、versions/chunks、versions/file 组合完成原文核验。
