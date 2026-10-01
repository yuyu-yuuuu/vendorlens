# 检索基线结果（2026-09-30）

## 实验设置

- 数据：7 个完全合成的供应商评审 chunk，4 个查询。
- Top K：3。
- BM25：`rank_bm25.BM25Okapi`，中英文混合词元。
- Vector：256 维确定性哈希向量，仅用于离线可复现的首轮管线验证。
- Hybrid：BM25 与 Vector 排名使用 RRF 融合，`k=60`。
- 过滤：所有查询使用 `project=erp` metadata 过滤。

## 实测结果

| 策略 | Recall@3 | Precision@3 | MRR | NDCG@3 |
| --- | ---: | ---: | ---: | ---: |
| BM25 | 0.708 | 0.500 | 1.000 | 0.771 |
| Vector | 0.708 | 0.500 | 0.750 | 0.669 |
| Hybrid RRF | 0.708 | 0.500 | 0.875 | 0.712 |

## 结论

当前小样本中 BM25 最好，Hybrid 没有超过 BM25。原因是确定性哈希向量只能验证向量和 RRF 执行链路，语义能力有限。下一轮将接入 ChromaDB 与本地 `BAAI/bge-small-zh-v1.5`，扩充多文档、多供应商标注集后重新测量；在新结果产生前，不宣称 Hybrid 优于 BM25。

原始逐查询排名与指标保存在 `docs/results/retrieval-baseline.json`，输入数据保存在 `evals/retrieval_dataset.json`。

