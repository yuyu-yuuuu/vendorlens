# VendorLens 实施计划

## 里程碑 1：独立骨架与可运行数据层

交付：独立 Git 仓库、monorepo 目录、配置、PostgreSQL/Redis/ChromaDB/服务容器、SQLAlchemy 核心模型、Alembic 初始迁移、健康检查。

验收：单元测试通过；Compose 配置可解析；数据库迁移可在 PostgreSQL 上完成；API 健康检查返回依赖状态。

## 里程碑 2：文档版本化与真实 Hybrid RAG 基线

交付：本地 DocumentStorage；PDF/DOCX/XLSX/Markdown/TXT 解析；不可变版本与 chunk 定位；BM25、向量、RRF；metadata 过滤；缓存与持久化；最小本地标注集和三策略对比脚本。

验收：相同文件重复上传幂等；修改文件创建新版本；所有 chunk 可按定位回读且哈希一致；运行评测生成 JSON/Markdown 实测结果。

## 里程碑 3：LangGraph 证据驱动纵向流程

交付：单个 VendorReviewAgent；Vendor 隔离检索；Vendor 间并行分析；需求、成本和承诺领域级状态；最多两次局部返工；确定性降级与评分；PostgreSQL/InMemory Checkpointer；部分完成语义。

验收：固定模型替身测试所有分支；断点恢复测试；输入包之外的证据无法进入结果；成本口径错配被拒绝；失败领域和失败 Vendor 不丢失其他成功结果。

## 里程碑 4：异步运行、可恢复 SSE 与 Trace

交付：RQ Worker；任务 API；持久化事件流；SSE `Last-Event-ID`；TraceSpan/审计写入；Prometheus 指标。

验收：API 立即返回 run_id；Worker 重启后任务状态可恢复；页面刷新后从事件序号续传；Trace 展示完整父子链；敏感字段脱敏测试通过。

## 里程碑 5：前端工作台

交付：经认可的 ImageGen 概念和设计系统；项目资料库、分析工作区、报告、评测四视图；引用定位抽屉；Trace 时间线；响应式布局。

验收：Playwright 核心流程；真实桌面/移动截图与概念对比；键盘导航、对比度和错误/空状态检查。

## 里程碑 6：完整评测、演示与求职材料

交付：四层评测、消融、演示数据、运行手册、架构决策记录、结果报告和基于实测数据的简历表述。

验收：全新环境一键启动；端到端演示可重复；测试与评测命令有固定输出目录；简历中的每个数字可链接到评测产物。

## 当前迭代切片

当前已完成里程碑 1 至 3 的核心闭环及正式工具/模型适配器。里程碑 4 已实现任务快照、RQ 执行、任务与报告 API、SSE HTTP 续读、领域/报告事务落库、原子任务领取、节点/模型/工具/降级父子 Trace 与 Token 汇总；尚未完成 Worker 硬中断恢复、模型金额统计与 Prometheus。后续仍需管理/上传 API 和端到端演示。
