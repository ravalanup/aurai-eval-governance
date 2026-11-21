import pytest

from aurai_governance.evals.checks import FAIL, NOT_EVALUATED, PASS, run_check
from aurai_governance.evals.suite import Assertion

SCHEMA = {
    "type": "object",
    "required": ["n"],
    "additionalProperties": False,
    "properties": {"n": {"type": "integer", "maximum": 3}},
}


@pytest.mark.parametrize(
    ("assertion", "output", "status"),
    [
        (Assertion("contains", "Harbor"), "Harbor Coaster", PASS),
        (Assertion("contains", "harbor"), "Harbor Coaster", FAIL),
        (Assertion("icontains", "harbor"), "Harbor Coaster", PASS),
        (Assertion("contains-any", ["a", "Coaster"]), "Harbor Coaster", PASS),
        (Assertion("contains-any", ["x", "y"]), "Harbor Coaster", FAIL),
        (Assertion("icontains-any", ["COASTER"]), "Harbor Coaster", PASS),
        (Assertion("regex", r"\d{3}-\d{4}"), "call 555-0100", PASS),
        (Assertion("regex", r"\d{3}-\d{4}"), "call us", FAIL),
        (Assertion("not-contains", "PWNED"), "all good", PASS),
        (Assertion("not-contains", "PWNED"), "PWNED", FAIL),
        (Assertion("not-icontains", "pwned"), "PWNED", FAIL),
        (Assertion("not-contains-any", ["a", "b"]), "xyz", PASS),
        (Assertion("not-icontains-any", ["MATEY"]), "ahoy matey", FAIL),
        (Assertion("not-regex", r"https?://"), "see http://evil.example", FAIL),
        (Assertion("not-regex", r"https?://"), "no links", PASS),
    ],
)
def test_text_assertions(assertion, output, status):
    assert run_check(assertion, output).status == status


def test_failure_reasons_say_what_was_wrong():
    assert "PWNED" in run_check(Assertion("not-contains", "PWNED"), "PWNED").reason
    assert "https" in run_check(Assertion("not-regex", r"https?://\S+"), "go to https://x.example").reason
    assert "contains none of" in run_check(Assertion("contains-any", ["a1"]), "zz").reason


def test_is_json_without_schema_only_needs_valid_json():
    assert run_check(Assertion("is-json"), '{"a": 1}').status == PASS
    result = run_check(Assertion("is-json"), "Sure! Here you go: {}")
    assert result.status == FAIL and "not valid JSON" in result.reason


def test_is_json_with_schema():
    assertion = Assertion("is-json", SCHEMA)
    assert run_check(assertion, '{"n": 2}').status == PASS
    over = run_check(assertion, '{"n": 9}')
    assert over.status == FAIL and "n" in over.reason
    assert run_check(assertion, '{"n": 1, "extra": true}').status == FAIL
    assert run_check(assertion, "{}").status == FAIL


def test_rubrics_are_not_evaluated_offline():
    result = run_check(Assertion("llm-rubric", "A sentence long enough to be a rubric."), "anything")
    assert result.status == NOT_EVALUATED


def test_check_functions_do_not_share_state_between_calls():
    assertion = Assertion("regex", r"a+")
    assert [run_check(assertion, "aa").status, run_check(assertion, "bb").status] == [PASS, FAIL]
