"""Fixed heterogeneous diagnostic benchmark; never substitute pass rates for quality."""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from vendorlens.review_agent import VendorReviewInput
from vendorlens.strength_eval import digest, offline, real_model


def cost_metrics(rows):
    known = [r for r in rows if r["gold_tco"] is not None]
    answered = [r for r in known if r["prediction_tco"] is not None]
    unknown = [r for r in rows if r["gold_tco"] is None]
    correct = sum(r["passed"] for r in known)
    return {
        "known_tco_cases": len(known),
        "numeric_answers": len(answered),
        "numeric_coverage": len(answered) / len(known),
        "correct_numeric_answers": correct,
        "answer_exact_accuracy": correct / len(answered) if answered else None,
        "known_tco_exact_accuracy": correct / len(known),
        "unknown_tco_cases": len(unknown),
        "unknown_correct_abstentions": sum(r["prediction_tco"] is None for r in unknown),
        "unknown_numeric_outputs": [r["id"] for r in unknown if r["prediction_tco"] is not None],
        "wrong_numeric_outputs": [r["id"] for r in answered if not r["passed"]],
        "unanswered_known_cases": [r["id"] for r in known if r["prediction_tco"] is None],
    }


def frozen_model_cases(dataset):
    base = json.loads(Path("evals/real_model_cases.json").read_text())[0]["input"]
    rows = []
    for req_number, cost_number in dataset["model_case_pairs"]:
        req = dataset["requirements"][req_number - 1]
        cost = dataset["costs"][cost_number - 1]
        payload = json.loads(json.dumps(base))
        payload["task_id"] = f"challenge-{req['id']}-{cost['id']}"
        payload["comparison_months"] = cost["months"]
        payload["requirements"][0]["text"] = req["requirement"]
        for index, case in ((0, req), (1, cost)):
            ev = payload["evidence"][index]
            ev.update(
                quote=case.get("quote", case["text"]),
                context_text=case["text"],
                source_text_hash=hashlib.sha256(case["text"].encode()).hexdigest(),
            )
        for key, index in (
            ("requirement_compliance", 0),
            ("cost_modeling", 1),
            ("commitment_verification", 2),
        ):
            payload["evidence_by_domain"][key] = [payload["evidence"][index]]
        rows.append(
            {
                "input": payload,
                "gold_requirement": req["gold"],
                "gold_tco": cost["gold_tco"],
                "requirement_case": req["id"],
                "cost_case": cost["id"],
            }
        )
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path("evals/challenge_dataset.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--real-model", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    dataset = json.loads(args.dataset.read_text())
    fixtures = frozen_model_cases(dataset)
    fixture_path = args.output / "model-inputs.json"
    fixture_path.write_text(json.dumps(fixtures, ensure_ascii=False, indent=2) + "\n")
    result = {
        "started_at": datetime.now(UTC).isoformat(),
        "dataset_sha256": digest(args.dataset),
        "model_inputs_sha256": digest(fixture_path),
        "dataset_version": dataset["dataset_version"],
        "origin": dataset["origin"],
        "label_policy": dataset["label_policy"],
        "code_hashes": {str(p): digest(p) for p in sorted(Path("src/vendorlens").glob("*.py"))},
        "offline": offline(dataset),
    }
    result["cost_diagnostics"] = cost_metrics(result["offline"]["costs"]["cases"])
    output = args.output / "metrics.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "offline_accuracy": result["offline"]["requirements"]["accuracy"],
                "cost_diagnostics": result["cost_diagnostics"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if args.real_model:
        result["real_model"] = real_model(
            args.output,
            [
                (VendorReviewInput.model_validate(r["input"]), r["gold_requirement"], r["gold_tco"])
                for r in fixtures
            ],
        )
    result["finished_at"] = datetime.now(UTC).isoformat()
    result["dataset_unchanged"] = digest(args.dataset) == result["dataset_sha256"]
    result["code_unchanged"] = all(digest(p) == sha for p, sha in result["code_hashes"].items())
    if not result["dataset_unchanged"] or not result["code_unchanged"]:
        raise RuntimeError("benchmark source changed during run")
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {"output": str(output), "real_model": result.get("real_model", {}).get("samples")},
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
