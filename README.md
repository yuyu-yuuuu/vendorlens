# VendorLens

面向项目方案与供应商资料的证据驱动评审 Agent 平台。它读取需求书、供应商方案、报价、排期和会议纪要，形成带版本、页码或单元格定位的对比结论，而不是做泛化文档聊天。

## 当前可运行能力

- 本地文件存储接口与基于 SHA-256 的幂等上传。
- 文档不可变版本；内容变化自动递增版本号。
- PDF、DOCX、XLSX、Markdown、TXT 统一解析接口，保留来源定位和原文哈希。
- `rank_bm25`、离线确定性向量和 RRF 融合检索，支持 metadata 过滤。
- PostgreSQL 业务模型、Alembic 事务迁移、Redis Worker 入口和 FastAPI 健康检查。
- 完全合成的本地检索标注集与 Recall@K、Precision@K、MRR、NDCG 评测。

三 Agent LangGraph、PostgreSQL Checkpointer、证据门禁、SSE、持久化 Trace 和前端工作台按[实施计划](docs/plans/2026-09-30-implementation-plan.md)继续迭代。完整边界见[设计说明](docs/design/2026-09-30-vendorlens-design.md)。

## 本地启动

要求 Python 3.12+ 与 Docker Desktop。

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
docker compose up -d postgres redis
.venv/bin/alembic upgrade head
.venv/bin/uvicorn vendorlens.api:app --reload
```

API 健康检查：`GET http://localhost:8000/health`。

## 验证

```bash
.venv/bin/pytest --cov=vendorlens
.venv/bin/ruff check .
.venv/bin/alembic check
.venv/bin/python -m vendorlens.eval_cli --top-k 3
```

当前提交的实测检索结果来自 7 个合成 chunk、4 个标注查询。样本仅用于打通可复现评测链路，不代表生产质量，也不会直接写入简历。详见[基线说明](docs/results/2026-09-30-retrieval-baseline.md)与[原始结果](docs/results/retrieval-baseline.json)。

## 独立性

VendorLens 是独立仓库，不导入、不复制 EchoMind 模块或指标。所有后续量化结果必须由本仓库的版本化数据、脚本和原始产物复现。

