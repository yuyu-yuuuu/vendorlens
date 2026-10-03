"""Reproducible resume metrics. Synthetic regression results are not production accuracy."""

import argparse
import hashlib
import json
import math
import platform
import statistics
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from vendorlens.cost_grounding import extract_grounded_costs
from vendorlens.evaluation import retrieval_metrics
from vendorlens.fallbacks import RequirementRuleEvaluator
from vendorlens.retrieval import HybridRetriever, IndexedChunk
from vendorlens.schemas import EvidenceItem, RequirementItem, RequirementStatus


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def timings(values):
    ordered = sorted(values)
    return {
        "samples": len(values),
        "mean_ms": statistics.mean(values),
        "p50_ms": statistics.median(values),
        "p95_ms": ordered[max(0, math.ceil(len(values) * 0.95) - 1)],
    }


def classification(rows, labels):
    per_class = {}
    for label in labels:
        tp = sum(r["gold"] == label and r["prediction"] == label for r in rows)
        fp = sum(r["gold"] != label and r["prediction"] == label for r in rows)
        fn = sum(r["gold"] == label and r["prediction"] != label for r in rows)
        p, r = tp / (tp + fp) if tp + fp else 0, tp / (tp + fn) if tp + fn else 0
        per_class[label] = {
            "precision": p,
            "recall": r,
            "f1": 2 * p * r / (p + r) if p + r else 0,
            "support": tp + fn,
        }
    correct = sum(r["gold"] == r["prediction"] for r in rows)
    return {
        "samples": len(rows),
        "correct": correct,
        "accuracy": correct / len(rows),
        "macro_f1": statistics.mean(v["f1"] for v in per_class.values()),
        "per_class": per_class,
        "cases": rows,
    }


def evidence(text, *, quote=None, item_id="ev"):
    return EvidenceItem(
        id=item_id,
        project_id="p",
        vendor_id="v",
        document_version_id="ver",
        chunk_id=item_id,
        quote=quote or text,
        context_text=text,
        source_text_hash=hashlib.sha256(text.encode()).hexdigest(),
        location={"line": 1},
    )


def offline(dataset):
    rows, durations, ablation = [], [], []
    evaluator = RequirementRuleEvaluator()
    for case in dataset["requirements"]:
        item = evidence(case["text"], quote=case.get("quote"))
        req = RequirementItem(id=case["id"], text=case["requirement"])
        start = perf_counter()
        result = evaluator.evaluate("v", [req], {req.id: [item]})
        durations.append((perf_counter() - start) * 1000)
        rows.append({**case, "prediction": result.requirement_findings[0].status.value})
        short = item.model_copy(update={"context_text": None})
        pred = (
            evaluator.evaluate("v", [req], {req.id: [short]}).requirement_findings[0].status.value
        )
        ablation.append({"id": case["id"], "gold": case["gold"], "prediction": pred})
    costs = []
    for case in dataset["costs"]:
        grounded = extract_grounded_costs([evidence(case["text"])], case["currency"])
        value = (
            grounded.result(
                vendor_id="v", currency=case["currency"], comparison_months=case["months"]
            )["total_cost_of_ownership"]
            if grounded.items
            else None
        )
        match = value == case["gold_tco"]
        costs.append(
            {**case, "prediction_tco": value, "passed": match, "missing": grounded.missing}
        )
    chunks = [IndexedChunk(c["id"], c["text"], c["metadata"]) for c in dataset["chunks"]]
    retriever = HybridRetriever(chunks)
    retrieval = {}
    for strategy in ("bm25", "vector", "hybrid"):
        cases, elapsed = [], []
        for q in dataset["queries"]:
            start = perf_counter()
            hits = retriever.search(q["text"], strategy=strategy, top_k=3, filters=q["filters"])
            elapsed.append((perf_counter() - start) * 1000)
            ranked = [hit.chunk_id for hit in hits]
            m = asdict(retrieval_metrics(ranked, set(q["relevant"]), 3))
            leakage = any(
                any(c.metadata.get(k) != v for k, v in q["filters"].items())
                for hit in hits
                for c in chunks
                if c.id == hit.chunk_id
            )
            cases.append(
                {
                    "id": q["id"],
                    "ranked_ids": ranked,
                    "gold": q["relevant"],
                    "metrics": m,
                    "scope_leakage": leakage,
                }
            )
        retrieval[strategy] = {
            "samples": len(cases),
            "latency": timings(elapsed),
            "aggregate": {
                k: statistics.mean(c["metrics"][k] for c in cases) for k in cases[0]["metrics"]
            },
            "hit_rate_at_3": sum(bool(set(c["ranked_ids"]) & set(c["gold"])) for c in cases)
            / len(cases),
            "scope_leakage_count": sum(c["scope_leakage"] for c in cases),
            "cases": cases,
        }
    labels = [s.value for s in RequirementStatus]
    return {
        "requirements": classification(rows, labels),
        "requirement_latency": timings(durations),
        "parent_context_ablation": classification(ablation, labels),
        "costs": {
            "samples": len(costs),
            "passed": sum(c["passed"] for c in costs),
            "exact_match_rate": sum(c["passed"] for c in costs) / len(costs),
            "cases": costs,
        },
        "retrieval": {
            "chunks": len(chunks),
            "queries": len(dataset["queries"]),
            "strategies": retrieval,
        },
    }


def model_cases():
    from vendorlens.model_smoke import build_smoke_input
    from vendorlens.review_agent import VendorReviewInput
    from vendorlens.schemas import ReviewDomain

    variations = [
        ("met", "支持私有化部署。", 80, 10, 36),
        ("not_met", "不支持私有化部署。", 60, 8, 24),
        ("partially_met", "支持私有化部署，仅限企业版，需另行购买。", 100, 12, 12),
        ("not_stated", "仅介绍报价与交付安排，部署模式待提供。", 50, 5, 48),
        ("met", "不支持离线运行，但支持私有化部署。", 20, 2, 18),
    ]
    for i, (label, response, one, annual, months) in enumerate(variations):
        payload = build_smoke_input().model_dump(mode="json")
        payload.update(task_id=f"metric-{i + 1}", comparison_months=months)
        req, cost, commitment = payload["evidence"]
        req.update(
            quote=response,
            context_text=response,
            source_text_hash=hashlib.sha256(response.encode()).hexdigest(),
        )
        text = f"软件许可及实施一次性费用{one}万元；年度维保服务费{annual}万元。"
        cost.update(
            quote=text,
            context_text=text,
            source_text_hash=hashlib.sha256(text.encode()).hexdigest(),
        )
        payload["evidence_by_domain"] = {
            ReviewDomain.REQUIREMENT_COMPLIANCE.value: [req],
            ReviewDomain.COST_MODELING.value: [cost],
            ReviewDomain.COMMITMENT_VERIFICATION.value: [commitment],
        }
        yield (
            VendorReviewInput.model_validate(payload),
            label,
            one * 10000 + annual * 10000 * months / 12,
        )


def real_model(output, cases=None):
    import httpx

    from vendorlens.config import get_settings
    from vendorlens.model_analyzer import build_vendor_review_executor
    from vendorlens.schemas import DomainAssessmentStatus, ReviewDomain

    settings = get_settings().model_copy(update={"model_timeout_seconds": 60})
    if not (settings.model_api_key and settings.model_base_url and settings.model_name):
        return {"status": "unavailable", "reason": "no complete model configuration"}
    calls = []

    def request_hook(request):
        request.extensions["metric_start"] = perf_counter()

    def response_hook(response):
        response.read()
        record = {
            "latency_ms": (perf_counter() - response.request.extensions["metric_start"]) * 1000,
            "status_code": response.status_code,
        }
        try:
            payload = response.json()
            record["usage"] = payload.get("usage")
            message = payload.get("choices", [{}])[0].get("message", {})
            record["tool_call_count"] = len(message.get("tool_calls", []))
        except (ValueError, IndexError, TypeError):
            record["usage"] = None
        calls.append(record)

    rows = []
    with httpx.Client(
        timeout=60, event_hooks={"request": [request_hook], "response": [response_hook]}
    ) as client:
        executor = build_vendor_review_executor(settings, client=client)
        for data, label, gold_tco in model_cases() if cases is None else cases:
            start = perf_counter()
            before = len(calls)
            row = {"id": data.task_id, "gold_requirement": label, "gold_tco": gold_tco}
            try:
                result = executor.execute(data)
                req = result.domains[ReviewDomain.REQUIREMENT_COMPLIANCE]
                cost = result.domains[ReviewDomain.COST_MODELING].cost_assessment
                row.update(
                    result=result.model_dump(mode="json"),
                    requirement_prediction=req.requirement_findings[0].status.value
                    if req.requirement_findings
                    else None,
                    tco=cost.total_cost_of_ownership if cost else None,
                    completed_domains=sum(
                        a.status is DomainAssessmentStatus.COMPLETED
                        for a in result.domains.values()
                    ),
                )
                row["requirement_passed"] = row["requirement_prediction"] == label
                row["cost_passed"] = row["tco"] == gold_tco
                row["all_domains_passed"] = row["completed_domains"] == 3
            except Exception as error:
                row.update(
                    error_type=type(error).__name__,
                    requirement_passed=False,
                    cost_passed=False,
                    all_domains_passed=False,
                    completed_domains=0,
                )
            row.update(duration_ms=(perf_counter() - start) * 1000, http_calls=len(calls) - before)
            rows.append(row)
            (output / "real-model-progress.json").write_text(
                json.dumps({"cases": rows, "http": calls}, ensure_ascii=False, indent=2)
            )
    return {
        "status": "executed",
        "model": settings.model_name,
        "samples": len(rows),
        "requirement_correct": sum(r["requirement_passed"] for r in rows),
        "cost_correct": sum(r["cost_passed"] for r in rows),
        "all_domain_success": sum(r["all_domains_passed"] for r in rows),
        "domain_success_count": sum(r["completed_domains"] for r in rows),
        "latency": timings([r["duration_ms"] for r in rows]),
        "http": calls,
        "cases": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("evals/strength_dataset.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--real-model", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    dataset = json.loads(args.dataset.read_text())
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset_version": dataset["dataset_version"],
        "dataset_sha256": digest(args.dataset),
        "data_origin": dataset["origin"],
        "environment": {
            "python": platform.python_version(),
            "system": platform.system(),
            "machine": platform.machine(),
        },
        "code_hashes": {str(p): digest(p) for p in sorted(Path("src/vendorlens").glob("*.py"))},
        "offline": offline(dataset),
    }
    (args.output / "metrics.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    if args.real_model:
        report["real_model"] = real_model(args.output)
    (args.output / "metrics.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "requirement_accuracy": report["offline"]["requirements"]["accuracy"],
                "cost_exact_match": report["offline"]["costs"]["exact_match_rate"],
                "real_model_status": report.get("real_model", {}).get("status"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
