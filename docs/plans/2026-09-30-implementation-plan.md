# VendorLens 实施计划

## 里程碑 1：独立骨架与可运行数据层

交付：独立 Git 仓库、monorepo 目录、配置、PostgreSQL/Redis/ChromaDB/服务容器、SQLAlchemy 核心模型、Alembic 初始迁移、健康检查。

验收：单元测试通过；Compose 配置可解析；数据库迁移可在 PostgreSQL 上完成；API 健康检查返回依赖状态。

## 里程碑 2：文档版本化与真实 Hybrid RAG 基线

交付：本地 DocumentStorage；PDF/DOCX/XLSX/Markdown/TXT 解析；不可变版本与 chunk 定位；BM25、向量、RRF；metadata 过滤；缓存与持久化；最小本地标注集和三策略对比脚本。

验收：相同文件重复上传幂等；修改文件创建新版本；所有 chunk 可按定位回读且哈希一致；运行评测生成 JSON/Markdown 实测结果。

## 里程碑 3：LangGraph 证据驱动纵向流程

交付：结构化状态；三 Agent；集中检索节点；并行 Analyst 子图；最多三轮工具循环；确定性/语义证据门禁；最多两次局部返工；PostgreSQL/InMemory Checkpointer；部分完成语义。

验收：固定模型替身测试所有分支；断点恢复测试；无证据 Claim 无法进入最终事实；数字错配被确定性拒绝；失败子任务不丢失成功结果。

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

本次直接完成里程碑 1 的可运行核心，并推进里程碑 2 的最小闭环：TXT/Markdown/XLSX 解析、版本化、本地 BM25/确定性向量/RRF、标注集与检索指标。PDF/DOCX、Chroma 持久化和 API 异步接入在下一迭代补齐，但接口在本次固定并由测试覆盖。

