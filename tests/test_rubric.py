import pytest

from aurai_governance.rubric import classify, max_tier

from .conftest import make


def test_personal_productivity_is_t0():
    assert classify(make()).min_tier == "T0"


def test_internal_knowledge_is_t1():
    assert classify(make(uses_retrieval=True)).min_tier == "T1"


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"data_classification": "confidential"}, "T2"),
        ({"data_classification": "restricted"}, "T3"),
        ({"actions": "write"}, "T2"),
        ({"actions": "read"}, "T0"),
        ({"audience": "external"}, "T3"),
        ({"audience": "company"}, "T0"),
        ({"error_impact": "material"}, "T3"),
        ({"error_impact": "low"}, "T0"),
        ({"uses_protected_ip": True}, "T3"),
    ],
)
def test_each_question_sets_a_floor(overrides, expected):
    assert classify(make(**overrides)).min_tier == expected


def test_highest_floor_wins():
    result = classify(make(uses_retrieval=True, actions="write", audience="external"))
    assert result.min_tier == "T3"


def test_explain_lists_all_five_questions():
    text = classify(make(actions="write")).explain()
    for q in ("Q1", "Q2", "Q3", "Q4", "Q5"):
        assert q in text
    assert "Minimum tier: T2" in text


def test_max_tier():
    assert max_tier("T1", "T3", "T0") == "T3"
