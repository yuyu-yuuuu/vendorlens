import pytest

from vendorlens.numeric_facts import unsupported_numeric_facts


@pytest.mark.parametrize(
    ("statement", "source"),
    [
        ("费用880,000元", "费用88万元"),
        ("USD 10,000", "1万美元"),
        ("99.9%", "99.9％"),
        ("2026-10-02", "2026年10月2日"),
        ("2026/10/2", "2026-10-02"),
        ("24个月", "两年"),
        ("48小时", "2天"),
        ("90天", "九十天"),
        ("百分之三十", "30％"),
        ("费用一百万元", "费用1000000元"),
    ],
)
def test_equivalent_values_and_dimensions_match(statement, source):
    assert not unsupported_numeric_facts(statement, [source])


@pytest.mark.parametrize(
    ("statement", "source"),
    [
        ("89万元", "88万元"),
        ("USD 100", "100元"),
        ("100元", "100人"),
        ("30%", "30天"),
        ("30个百分点", "30%"),
        ("每年12000元", "每月12000元"),
        ("90个月", "90天"),
        ("90工作日", "90天"),
        ("2026-10-03", "2026年10月2日"),
        ("2026-02-30", "2026-02-30"),
        ("三年", "2年"),
        ("-100元", "100元"),
    ],
)
def test_same_digits_do_not_authorize_wrong_dimension_or_value(statement, source):
    assert unsupported_numeric_facts(statement, [source])


@pytest.mark.parametrize(("statement", "source"), [
    ("6元", "1e6元"), ("2万元", "1/2万元"), ("1e6元", "1e6元"),
])
def test_unsupported_numeric_notation_cannot_authorize_a_trailing_number(statement, source):
    assert unsupported_numeric_facts(statement, [source])
