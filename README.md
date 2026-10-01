# VendorLens

面向项目方案与供应商资料的证据驱动评审 Agent 平台。它读取需求书、供应商方案、报价、排期和会议纪要，形成带版本、页码或单元格定位的对比结论，而不是做泛化文档聊天。

## 当前可运行能力

- 本地文件存储接口与基于 SHA-256 的幂等上传。
- 文档不可变版本；内容变化自动递增版本号。
- PDF、DOCX、XLSX、Markdown、TXT 统一解析，保留来源定位和原文哈希。
- 叙事文档采用父子 Chunk：最多 320 字的子块负责召回，父块负责提供完整上下文；XLSX 保持一行一个可定位块。
- BM25 索引落盘、ChromaDB 向量持久化、RRF 融合和 metadata 过滤；检索只使用每份文档最新的已索引版本。
- Review Project 可登记多家 Vendor；采购方资料与各供应商同名文件相互隔离，检索 metadata 固定供应商归属。
- 确定性推荐引擎支持需求符合性、相对 TCO、承诺可信度、50/30/20 权重、必选需求硬性淘汰和条件推荐。
- 可恢复 LangGraph 工作流骨架：集中检索、并行分析、确定性证据门禁、最多两次局部返工和部分完成语义。
- 内存/PostgreSQL Checkpointer、持久化运行事件与可恢复 SSE 事件格式。
- PostgreSQL 业务模型、Alembic 事务迁移、Redis Worker 入口和 FastAPI 健康检查。
- 完全合成的本地检索标注集与 Recall@K、Precision@K、MRR、NDCG 评测。

下一步将按[领域 Agent 重构设计](docs/superpowers/specs/2026-10-01-domain-agent-redesign.md)实现需求符合性、成本建模和承诺核验三个业务 Agent 及其同领域降级处理器。当前仍使用确定性哈希向量作为可复现基线；真实中文 Embedding、任务 API/RQ 接线、SSE HTTP 端点、完整 Trace 和前端工作台按[实施计划](docs/plans/2026-09-30-implementation-plan.md)继续迭代。完整边界见[设计说明](docs/design/2026-09-30-vendorlens-design.md)。

## 本地启动

要求 Python 3.12+ 与 Docker Desktop。

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
docker compose up -d postgres redis chroma
.venv/bin/alembic upgrade head
.venv/bin/uvicorn vendorlens.api:app --reload
```

API 健康检查：`GET http://localhost:8000/health`。本机 ChromaDB 使用 `http://localhost:8100`；容器内服务通过 `http://chroma:8000` 访问。

## 当前数据流

```text
上传文件
  → SHA-256 幂等判断与不可变 Version
  → 解析并保存父子 Chunk
  → 子 Chunk 写入 BM25 + ChromaDB
  → 仅过滤各文档最新已索引 Version
  → BM25 / Vector 召回并用 RRF 融合
  → 根据 parent_id 回查父 Chunk
  → 生成带版本、位置和原文哈希的 EvidenceItem
  → LangGraph 分析与证据门禁
```

## 验证

```bash
.venv/bin/pytest --cov=vendorlens
.venv/bin/ruff check .
.venv/bin/alembic check
.venv/bin/python -m vendorlens.eval_cli --top-k 3
```

当前提交的实测检索结果来自 7 个合成 chunk、4 个标注查询。样本仅用于打通可复现评测链路，不代表生产质量，也不会直接写入简历。详见[基线说明](docs/results/2026-09-30-retrieval-baseline.md)与[原始结果](docs/results/retrieval-baseline.json)。

## 可复现性

所有后续量化结果必须由本仓库的版本化数据、脚本和原始产物复现。
