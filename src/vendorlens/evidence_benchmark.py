"""Finite source-scope attack suite, not a general attack success rate."""

import argparse
import hashlib
import json
from pathlib import Path

from pydantic import ValidationError

from vendorlens.model_smoke import build_smoke_input
from vendorlens.review_agent import VendorReviewInput


def run():
    cases = []
    for i in range(2):
        base = build_smoke_input().model_dump(mode="json")
        for item in base["evidence"]:
            item["context_text"] = item["quote"]
        base["evidence_by_domain"] = {
            k: [dict(e) for e in items] for k, items in base["evidence_by_domain"].items()
        }
        for items in base["evidence_by_domain"].values():
            for item in items:
                item["context_text"] = item["quote"]
        base["task_id"] = f"guard-{i}"
        for attack in ["valid", "project", "vendor", "version", "hash", "quote", "canonical"]:
            payload = json.loads(json.dumps(base))
            canonical = payload["evidence"][0]
            grouped = payload["evidence_by_domain"]["requirement_compliance"][0]
            if attack in {"project", "vendor", "version"}:
                field = {
                    "project": "project_id",
                    "vendor": "vendor_id",
                    "version": "document_version_id",
                }[attack]
                canonical[field] = grouped[field] = "foreign-scope"
            elif attack == "hash":
                canonical["source_text_hash"] = grouped["source_text_hash"] = "0" * 64
            elif attack == "quote":
                canonical["quote"] = grouped["quote"] = "伪造的事实不属于来源全文"
            elif attack == "canonical":
                grouped["context_text"] = grouped["quote"] = "独立且合法哈希的替换材料"
                grouped["source_text_hash"] = hashlib.sha256(grouped["quote"].encode()).hexdigest()
            accepted = True
            try:
                VendorReviewInput.model_validate(payload)
            except (ValueError, ValidationError):
                accepted = False
            cases.append(
                {
                    "id": f"{i}-{attack}",
                    "attack": attack,
                    "expected_accept": attack == "valid",
                    "accepted": accepted,
                    "passed": accepted == (attack == "valid"),
                }
            )
    return {
        "cases": cases,
        "attack_cases": sum(c["attack"] != "valid" for c in cases),
        "attacks_rejected": sum(c["attack"] != "valid" and not c["accepted"] for c in cases),
        "valid_cases": sum(c["attack"] == "valid" for c in cases),
        "valid_cases_accepted": sum(c["attack"] == "valid" and c["accepted"] for c in cases),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = run()
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "cases"}))


if __name__ == "__main__":
    main()
