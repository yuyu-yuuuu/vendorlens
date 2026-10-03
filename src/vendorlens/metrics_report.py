"""Render challenge diagnostics and a resume without promoting regression pass rates."""

# ruff: noqa: E501

import argparse
import json
from pathlib import Path

from vendorlens.strength_eval import classification, digest

LABELS = ["met", "partially_met", "not_met", "not_stated"]


def percent(value):
    return f"{value * 100:.2f}%"


def generate(root, report_path, resume_path):
    result = json.loads((root / "metrics.json").read_text())
    if result.get("dataset_version") != "vendorlens-challenge-v1":
        raise ValueError("template regression data cannot generate resume effect metrics")
    dataset_path = Path("evals/challenge_dataset.json")
    if digest(dataset_path) != result["dataset_sha256"]:
        raise ValueError("dataset differs from recorded benchmark")
    dataset = json.loads(dataset_path.read_text())
    o = result["offline"]
    req = o["requirements"]
    cost = result["cost_diagnostics"]
    real = result.get("real_model")
    if not real or real["status"] != "executed":
        raise ValueError("real-model results required; no fabricated substitute")
    real_class = classification(
        [
            {"gold": c["gold_requirement"], "prediction": c.get("requirement_prediction")}
            for c in real["cases"]
        ],
        LABELS,
    )
    usable = sum(c["all_domains_passed"] for c in real["cases"])
    joint = sum(
        c["all_domains_passed"] and c["requirement_passed"] and c["cost_passed"]
        for c in real["cases"]
    )
    real_known = [c for c in real["cases"] if c["gold_tco"] is not None]
    real_unknown = [c for c in real["cases"] if c["gold_tco"] is None]
    summary = {
        "requirement_rule_accuracy": req["accuracy"],
        "requirement_rule_macro_f1": req["macro_f1"],
        "real_model_classification": real_class,
        "real_three_domain_returns": usable,
        "real_joint_checked_success": joint,
        "real_samples": real["samples"],
        "real_known_tco_correct": sum(c["cost_passed"] for c in real_known),
        "real_known_tco_total": len(real_known),
        "real_unknown_successful_abstentions": sum(c["cost_passed"] for c in real_unknown),
        "real_unknown_total": len(real_unknown),
        "offline_cost": cost,
    }
    (root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    lines = [
        "# VendorLens 修订测试报告",
        "",
        "测试日期：2026-10-03。上一版模板集的满分结果已撤出简历效果指标，原始产物仍作为功能回归记录保留。本轮用异质挑战集测量已知边界，不修改分数、不移除失败，也没有在运行后修补业务实现或标签。",
        "",
        "## 数据与执行范围",
        "",
        f"数据由编码代理编写，未经过独立人工标注，包含 {len(dataset['requirements'])} 条需求、{len(dataset['costs'])} 条费用、{len(dataset['chunks'])} 个片段和 {len(dataset['queries'])} 条检索查询。它是针对已知问题设计的合成诊断集，不能称作盲测、真实客户数据或生产准确率。标签和理由逐条保存在数据文件中。",
        "需求标签约定：明确满足为 met；明确不满足为 not_met；条件支持、部分满足、尚未交付的能力或材料矛盾需澄清为 partially_met；没有回答为 not_stated。复合需求、未来承诺和条件支持存在业务口径差异，独立评审者尚未复核这些标签。",
        "费用标签约定：对明确给出的比较范围计算完整 TCO；未知价格、缺失周期或换汇条件不能输出完整 TCO，小计不能替代完整 TCO。可选费用不计入基础 TCO。这里只验证指定片段的费用范围，不表示覆盖真实合同所有成本。",
        "",
        f"数据版本：`{result['dataset_version']}`；SHA-256：`{result['dataset_sha256']}`。",
        f"真实模型输入在调用前导出到 model-inputs.json，SHA-256：`{result['model_inputs_sha256']}`。12 组配对编号也在固定数据集内，覆盖改写、否定、复合要求、版本限制、未来承诺、缺答与复杂计价，不能临时删掉失败输入。",
        f"开始：{result['started_at']}；结束：{result['finished_at']}。运行结束时数据及 src 文件哈希校验：数据未变={result['dataset_unchanged']}，代码未变={result['code_unchanged']}；这只说明本轮测量期间未变，报告生成脚本随后作了口径修订。",
        "",
        "离线判断调用正式 RequirementRuleEvaluator；费用调用 extract_grounded_costs 与确定性 TCO 工具。检索为内存 HybridRetriever，使用默认哈希向量，未加载语义 embedding 或 reranker，也没有访问生产 Chroma 索引。",
        f"真实调用模型 `{real['model']}`，执行正式评审模型适配器、工具循环及领域校验。当前测试直接调用 executor，未覆盖上传、RQ/Worker、API、SSE、前端或最终推荐工作流，不是全流程端到端评测。每个样本只测一次，没有择优重跑。",
        "",
        "## 需求判断",
        "",
        "Accuracy=正确数/全部输入数。每类 P=TP/(TP+FP)，R=TP/(TP+FN)，F1=2PR/(P+R)；Macro-F1 为四类 F1 均值，零分母按零处理。模型异常或无判断作为错误进入分母。",
        "",
        "| 测试 | 正确数 / 输入数 | Accuracy | Macro-F1 |",
        "| --- | ---: | ---: | ---: |",
        f"| 离线规则判断 | {req['correct']}/{req['samples']} | {percent(req['accuracy'])} | {req['macro_f1']:.4f} |",
        f"| 真实模型集成（异常算错） | {real_class['correct']}/{real_class['samples']} | {percent(real_class['accuracy'])} | {real_class['macro_f1']:.4f} |",
        "",
        "两行输入数量和组成不同，不能当成模型对规则的同集提升对比。真实模型异常可能发生在工具循环或成本核验阶段；当前 executor 抛出异常后没有返回已生成的中间领域，因此这里统计的是可交付的需求判断，不是模型独立语义能力。",
        "",
        "| 方法 | 类别 | 样本数 | Precision | Recall | F1 |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for method, metric in [("规则", req), ("模型集成", real_class)]:
        for label, v in metric["per_class"].items():
            lines.append(
                f"| {method} | {label} | {v['support']} | {v['precision']:.4f} | {v['recall']:.4f} | {v['f1']:.4f} |"
            )
    lines += [
        "",
        "规则判断逐条结果：",
        "",
        "| ID | 类型 | Gold | 实测 | 判定依据 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for c in req["cases"]:
        lines.append(
            f"| {c['id']} | {c['category']} | {c['gold']} | {c['prediction']} | {c['rationale']} |"
        )
    lines += [
        "",
        "失败集中于同义表述与缺答识别；规则不能可靠识别“不可用”、版本约束、性能上限及复合条件。父块可提供限制信息，但本集仍存在读取父块后错误判断，不能据此宣称已解决语义理解。",
        "",
        "## 检索",
        "",
        "每条查询只按项目、供应商和版本过滤，保留同供应商各主题作为混淆候选；另加入 6 个范围外片段。Top-3 与预先固定的相关片段 ID 比较，部分问题有两个相关片段。Recall@3=命中相关数/全部相关数，Precision@3=命中相关数/3，MRR@3 为首个相关位置倒数，NDCG@3 使用二元相关性。这里的相关列表是小范围人工编写语料的预设答案，尚无独立相关性评审。",
        "",
        "| 方法 | Recall@3 | Precision@3 | MRR@3 | NDCG@3 | 范围泄漏数 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, v in o["retrieval"]["strategies"].items():
        m = v["aggregate"]
        lines.append(
            f"| {name} | {percent(m['recall_at_k'])} | {m['precision_at_k']:.4f} | {m['mrr']:.4f} | {m['ndcg_at_k']:.4f} | {v['scope_leakage_count']} |"
        )
    lines += [
        "",
        "本轮 Hybrid 低于 BM25，不能写“混合检索提升召回率”。范围泄漏计数只说明本组 metadata 过滤检查结果。20 条查询的顺序内存调用没有生产并发、HTTP、磁盘或网络条件，延迟不作为吞吐或 SLA 指标。",
        "",
        "| 查询 | 问题 | 预设相关片段 | Hybrid Top-3 | Recall@3 |",
        "| --- | --- | --- | --- | ---: |",
    ]
    query_by_id = {q["id"]: q for q in dataset["queries"]}
    for c in o["retrieval"]["strategies"]["hybrid"]["cases"]:
        q = query_by_id[c["id"]]
        lines.append(
            f"| {c['id']} | {q['text']} | {', '.join(c['gold'])} | {', '.join(c['ranked_ids'])} | {c['metrics']['recall_at_k']:.2f} |"
        )
    lines += [
        "",
        "## 成本：覆盖与正确必须分开",
        "",
        f"有唯一数值答案的 {cost['known_tco_cases']} 条中，工具输出数值 {cost['numeric_answers']} 条，数值输出覆盖率 {percent(cost['numeric_coverage'])}；输出数值的样例中正确 {cost['correct_numeric_answers']}/{cost['numeric_answers']}，条件准确率 {percent(cost['answer_exact_accuracy'])}；按全部可计算输入计，完整 TCO 精确匹配 {cost['correct_numeric_answers']}/{cost['known_tco_cases']}，即 {percent(cost['known_tco_exact_accuracy'])}。覆盖率不等于已支持这些计价类型，错误数值仍属于未解决的能力。",
        f"另外 {cost['unknown_tco_cases']} 条未知 TCO 输入中，正确不输出数值 {cost['unknown_correct_abstentions']} 条；错误输出完整 TCO 的样例：{', '.join(cost['unknown_numeric_outputs']) or '无'}。这些拒答单独统计，不能加到可计算样例里凑准确率。",
        "",
        "| ID | 计价类型 | 预设 TCO（元） | 实测 TCO（元） | 正确 | 计算依据 |",
        "| --- | --- | ---: | ---: | --- | --- |",
    ]
    for c in o["costs"]["cases"]:
        lines.append(
            f"| {c['id']} | {c['category']} | {c['gold_tco']} | {c['prediction_tco']} | {c['passed']} | {c['rationale']} |"
        )
    lines += [
        "",
        "单价×数量和含税换算未计算；季度费、折扣存在错误数值；缺少维保金额时出现将实施小计当完整 TCO 的情况。不能用此前“简单四则计算与预设值一致”推断复杂报价理解准确率。",
        "",
        "## 真实模型调用与失败",
        "",
        f"三个领域均返回 completed 的输入为 {usable}/{real['samples']}；需求与 TCO 均符合预设值、且三个领域返回的输入为 {joint}/{real['samples']}。这里只核对需求和 TCO，未人工评审承诺分析或最终推荐，因此不是整体报告质量合格率。",
        f"可确定 TCO 的 {len(real_known)} 条中，输出与预设值相符 {summary['real_known_tco_correct']} 条；未知 TCO 的 {len(real_unknown)} 条中，成功返回且正确拒答 {summary['real_unknown_successful_abstentions']} 条。异常不能算成成功拒答。",
        f"HTTP 调用 {len(real['http'])} 次；任务级耗时均值 {real['latency']['mean_ms'] / 1000:.2f}s，P95 {real['latency']['p95_ms'] / 1000:.2f}s。n=12 的 nearest-rank P95 等于最大值；不同输入工具轮次不同，不代表线上 SLA，也没有与原报告小样本作速度提升对比。原始 HTTP 记录仅含状态、时长、usage 与工具数量。",
        "",
        "| 输入 | Gold → 判断 | Gold → TCO | 领域返回数 | 异常 |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for c in real["cases"]:
        lines.append(
            f"| {c['id']} | {c['gold_requirement']} → {c.get('requirement_prediction')} | {c['gold_tco']} → {c.get('tco')} | {c['completed_domains']} | {c.get('error_type', '无')} |"
        )
    lines += [
        "",
        "ModelToolLoopError 表示正式适配器工具循环未成功交付结果；本轮记录了异常类型，未保存详细工具错误轨迹，不能断言各条异常的单一根因。复杂报价工具能力不足与输出门禁可能参与其中，需后续单独定位，不能直接归因为模型语义错误。",
        "",
        "## 复现与产物",
        "",
        "在项目根目录执行；模型测试需本机已配置可用端点、模型名称及密钥，命令不输出密钥。新输出目录用于保留本轮原始记录，重新调用模型可能产生不同结果。",
        "",
        "```sh",
        ".venv/bin/python -m vendorlens.challenge_eval --dataset evals/challenge_dataset.json --output docs/results/challenge-rerun --real-model",
        ".venv/bin/python -m vendorlens.metrics_report --results docs/results/challenge-rerun --report docs/results/challenge-rerun/测试报告.md --resume docs/results/challenge-rerun/简历.txt",
        "```",
        "",
        "不加 --real-model 可仅执行离线部分，报告生成器要求真实模型结果，不会以离线满分替代。",
        "数据：[challenge_dataset.json](../../../evals/challenge_dataset.json)。本轮 [metrics.json](metrics.json) 包含逐条输入、预测、失败及源码哈希；[model-inputs.json](model-inputs.json) 保存调用前固定输入；[real-model-progress.json](real-model-progress.json) 保存实际输出和调用计数；[summary.json](summary.json) 为聚合摘要。",
        "本轮评测计算与旧模板集保护的 4 项针对性测试通过，记录见 [evaluation-tests.xml](evaluation-tests.xml)。旧版功能回归、记忆/Skills/路由故障注入和 237 项后端测试保存在 [旧结果目录](../2026-10-03-strength-metrics/测试报告.md)。它们用于验证既定功能，不能再作为效果准确率或生产可用性；本轮没有重跑这些业务回归测试。",
        "",
        "## 简历口径",
        "",
        "当前证据不足以支撑高准确率、生产规模、性能提升或稳定性承诺。简历保留实现机制与测试范围；检索结果明确注明合成集和哈希向量，规则及模型失败指标放在验证口径中。待独立标注的实际文档测试与错误修复完成后，才适合写泛化效果指标。",
    ]
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n")
    hybrid = o["retrieval"]["strategies"]["hybrid"]["aggregate"]
    resume = f"""定位
AI 应用工程 / 后端开发方向

项目经历
VendorLens 证据驱动供应商评审平台
技术栈：Python / FastAPI / LangGraph / SQLAlchemy / PostgreSQL / Redis / RQ / ChromaDB / Docker / Next.js / TypeScript
项目背景：面向企业采购方案评审，解析需求书、供应商方案、报价与排期，生成带固定版本原文引用的供应商对比报告。

项目表述（机制、测试范围与实测口径）
- 设计单供应商评审 Agent 与供应商间并行工作流，覆盖需求、成本、承诺 3 个领域，通过 11 个受控工具、领域校验、最多 2 次局部返工和确定性降级控制执行；建立 32 条异质需求样例与 12 条真实模型调用样例，逐条记录误判及工具循环失败。
- 构建父子 Chunk、BM25/ChromaDB/RRF 检索与项目、供应商、版本过滤，报告保存固定原文引用快照；在 30 个合成片段、20 条固定查询的内存哈希向量诊断中，Hybrid Recall@3 为 {percent(hybrid["recall_at_k"])}、MRR@3 为 {hybrid["mrr"]:.3f}，保留 BM25 基线和逐查询结果供优化对照。
- 将费用金额、币种与周期绑定原文，以确定性工具计算 TCO 并报告必要费用缺项；建立 14 条包含单价数量、税率、季度、打包、折扣及未知价格的挑战样例，将可计算覆盖率、数值正确率和未知报价拒答分开统计，定位复杂费用口径与小计误用问题。
- 实现最近消息、评审摘要与显式偏好的三层记忆，支持任务上下文冻结和动态 Skills 热加载；使用 20 个会话、320 条消息及 50 次有效/50 次非法配置变更检查压缩、快照切换与回退。
- 实现模型端点健康路由、租约/心跳/Checkpoint 恢复、事务 outbox、SSE 续读与父子 Trace；通过 20 组受控故障场景检查冷却、备用端点与探测恢复，维护 237 项后端回归测试及可复现诊断脚本。

精简投递版
- 实现证据驱动供应商评审 Agent，覆盖 3 个评审领域、11 个受控工具、局部返工与确定性降级，结论可回查固定版本原文。
- 实现父子 Chunk 与 BM25/ChromaDB/RRF 检索；在 30 个合成片段、20 条查询的哈希向量诊断集上测得 Hybrid Recall@3 {percent(hybrid["recall_at_k"])}，同步保留基线与失败样例。
- 实现三层记忆、Skills 热加载、健康路由、事务 outbox、异步任务恢复、SSE 与 Trace，维护后端回归及需求、费用、检索挑战集。

测试口径
离线需求规则：{req["correct"]}/{req["samples"]}，Accuracy {percent(req["accuracy"])}，Macro-F1 {req["macro_f1"]:.4f}。
确定 TCO 的费用输入：正确 {cost["correct_numeric_answers"]}/{cost["known_tco_cases"]}，输出数值覆盖率 {percent(cost["numeric_coverage"])}；未知 TCO 单独统计。
真实模型集成：需求正确 {real_class["correct"]}/{real_class["samples"]}，三领域返回 {usable}/{real["samples"]}；异常计入分母。
上述数据来自编码代理编写、未经独立标注的合成诊断集，不是生产准确率。Hybrid 本轮低于 BM25，不宣称效果提升；真实模型成本结果由工具与门禁共同决定，不称为模型报价理解准确率。237 项测试为前阶段回归结果，本轮未重跑完整业务测试。
"""
    resume_path.parent.mkdir(parents=True, exist_ok=True)
    resume_path.write_text(resume)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--resume", type=Path, required=True)
    args = parser.parse_args()
    generate(args.results, args.report, args.resume)


if __name__ == "__main__":
    main()
