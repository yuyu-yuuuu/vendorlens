import json

from vendorlens.model_smoke import build_smoke_input, format_result, run
from vendorlens.schemas import (
    DomainAssessment,
    DomainAssessmentStatus,
    ReviewDomain,
    VendorAssessment,
)


class CapturingExecutor:
    def __init__(self, result):
        self.result = result
        self.input_data = None

    def execute(self, input_data):
        self.input_data = input_data
        return self.result


def result():
    return VendorAssessment(
        vendor_id="smoke-vendor",
        domains={
            domain: DomainAssessment(
                domain=domain,
                vendor_id="smoke-vendor",
                status=DomainAssessmentStatus.FAILED,
                missing_information=["smoke result"],
            )
            for domain in ReviewDomain
        },
    )


def test_smoke_input_contains_isolated_evidence_for_all_domains():
    input_data = build_smoke_input()

    assert input_data.vendor_id == "smoke-vendor"
    assert set(input_data.evidence_by_domain) == set(ReviewDomain)
    assert all(input_data.evidence_by_domain[domain] for domain in ReviewDomain)
    assert {item.vendor_id for item in input_data.evidence} == {"smoke-vendor"}


def test_smoke_run_uses_real_executor_contract():
    expected = result()
    executor = CapturingExecutor(expected)

    actual = run(executor)

    assert actual is expected
    assert executor.input_data.task_id == "model-smoke-task"


def test_smoke_output_is_json_and_contains_no_configuration():
    payload = json.loads(format_result(result()))

    assert payload["vendor_id"] == "smoke-vendor"
    assert set(payload["domains"]) == {domain.value for domain in ReviewDomain}
    assert "api_key" not in format_result(result()).lower()
