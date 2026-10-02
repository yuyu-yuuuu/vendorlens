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

文档页面和 OpenAPI 可在没有数据库连接时读取，但业务接口需要数据库可用。在本机终端运行时，依赖应使用本机映射地址；`.env.example` 中 postgres/redis/chroma 主机名适用于 Compose 容器内，不能直接用于 Mac 终端。不要用示例文件覆盖已保存的模型密钥。

执行真实任务前需要完成数据库迁移、准备项目/供应商/需求目录、摄取并索引资料，以及另行启动 `.venv/bin/python -m vendorlens.worker`。目前没有项目创建或上传 HTTP 接口，Swagger 不会替代数据准备流程。

**Try it out 调用的是真实接口，不是模拟演示。** POST 创建任务可能导致后台模型费用；文档中的 ID 是占位值，执行前必须替换。API 暂无鉴权，只在可信本地环境使用。

Swagger/ReDoc 使用框架默认的 CDN 前端资源；如果页面空白但 openapi.json 正常，先检查浏览器是否能加载 CDN。当前未打包离线前端资源。

## 接口索引

| 方法与路径 | 功能 | 成功状态 |
| --- | --- | --- |
| GET /health | API 与数据库健康检查，不检查所有依赖 | 200 |
| POST /projects/{project_id}/tasks | 创建任务与输入快照，然后入队 | 202 |
| GET /tasks/{task_id} | 查询任务状态 | 200 |
| GET /tasks/{task_id}/report | 查询最终报告 | 200 |
| GET /tasks/{task_id}/events | SSE 进度回放与订阅 | 200 |
| GET /tasks/{task_id}/trace | 查询执行树与已报告 Token | 200 |

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

任务状态为 queued/running/completed/partially_completed/failed/enqueue_failed。当前没有重新入队或硬中断自动恢复端点。
