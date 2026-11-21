import copy

import pytest
import yaml

from aurai_governance.evals.suite import SuiteError, load_suite, suite_hash, validate_document

from .evalhelpers import SUITES_DIR, suites

MINIMAL = {
    "id": "example-golden",
    "kind": "golden",
    "use_case": "example",
    "model": "aurai-fast",
    "prompt": {"system": "Be brief.", "user": "{{question}}"},
    "cases": [{"id": "one", "vars": {"question": "Hi"}, "assert": [{"type": "icontains", "value": "hello"}]}],
}


def doc(**changes):
    out = copy.deepcopy(MINIMAL)
    out.update(changes)
    return out


def test_every_repository_suite_loads():
    loaded = suites()
    assert {"ops-insights-golden", "ops-insights-redteam", "ops-insights-grounding"} <= set(loaded)
    assert {"knowledge-grounded-answers", "knowledge-search-retrieval", "gateway-smoke-pii"} <= set(loaded)


def test_hash_ignores_prose_and_changes_with_content():
    base = suite_hash(MINIMAL)
    reworded = doc(description="A new description", notes="Some notes")
    reworded["cases"][0]["description"] = "Says hello"
    assert suite_hash(reworded) == base
    changed = doc()
    changed["cases"][0]["assert"][0]["value"] = "goodbye"
    assert suite_hash(changed) != base
    assert suite_hash(doc(model="aurai-balanced")) != base


def test_hash_does_not_mutate_the_document():
    original = doc()
    original["cases"][0]["description"] = "kept"
    suite_hash(original)
    assert original["cases"][0]["description"] == "kept"


def test_retrieval_suite_hash_is_the_pinned_golden_set():
    suite = suites()["knowledge-search-retrieval"]
    assert suite.hash == "sha256:" + suite.raw["retrieval"]["golden_set_sha256"]


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"kind": "unknown"}, "kind"),
        ({"use_case": "_platform"}, "platform"),
        ({"model": None}, "model"),
        ({"prompt": {"user": "{{question}} {% if x %}"}}, "template syntax"),
        ({"prompt": {"user": "{{question}} {{ other }}"}}, "missing variable"),
        ({"prompt": {"user": "{{ question + 1 }}"}}, "plain {{variable}}"),
        ({"cases": []}, "at least one case"),
    ],
)
def test_invalid_suites_are_rejected(change, expected):
    document = doc(**change)
    if document.get("model") is None:
        document.pop("model")
    assert any(expected in e for e in validate_document(document)), validate_document(document)


@pytest.mark.parametrize(
    ("assertion", "expected"),
    [
        ({"type": "equals", "value": "x"}, "unsupported assertion type"),
        ({"type": "contains"}, "non-empty string"),
        ({"type": "contains-any", "value": "not a list"}, "list of strings"),
        ({"type": "regex", "value": "(?<=a)b"}, "JavaScript and Python"),
        ({"type": "regex", "value": "(unclosed"}, "invalid pattern"),
        ({"type": "is-json", "value": {"type": 12}}, "invalid JSON Schema"),
        ({"type": "llm-rubric", "value": "short"}, "at least a sentence"),
    ],
)
def test_invalid_assertions_are_rejected(assertion, expected):
    document = doc()
    document["cases"][0]["assert"] = [assertion]
    assert any(expected in e for e in validate_document(document)), validate_document(document)


def test_rubric_needs_a_judge_model():
    document = doc()
    document["cases"][0]["assert"] = [{"type": "llm-rubric", "value": "The answer greets the user politely."}]
    assert any("judge_model" in e for e in validate_document(document))
    document["judge_model"] = "aurai-reasoning"
    assert validate_document(document) == []


def test_red_team_suite_needs_a_critical_assertion():
    assert any("critical" in e for e in validate_document(doc(kind="red_team")))
    document = doc(kind="red_team")
    document["cases"][0]["assert"][0]["critical"] = True
    assert validate_document(document) == []


def test_duplicate_case_ids_are_rejected():
    document = doc()
    document["cases"].append(copy.deepcopy(document["cases"][0]))
    assert any("duplicate case id" in e for e in validate_document(document))


def test_platform_suites_use_the_platform_scope():
    document = doc(kind="platform", use_case="_platform")
    assert validate_document(document) == []


def test_retrieval_suite_rules():
    pin = {"source_repo": "r", "golden_set": "g.yaml", "golden_set_sha256": "a" * 64}
    good = {"id": "x-retrieval", "kind": "retrieval", "use_case": "x", "retrieval": pin, "cases": []}
    assert validate_document(good) == []
    with_cases = {**good, "cases": [{"id": "c", "assert": [{"type": "contains", "value": "x"}]}]}
    assert any("no cases here" in e for e in validate_document(with_cases))
    pinned_golden = doc(retrieval=pin)
    assert any("only retrieval suites pin" in e for e in validate_document(pinned_golden))
    no_pin = {k: v for k, v in good.items() if k != "retrieval"}
    assert any("pin its golden set" in e for e in validate_document(no_pin))


def test_file_name_must_match_the_id(tmp_path):
    path = tmp_path / "other-name.yaml"
    path.write_text(yaml.safe_dump(MINIMAL), encoding="utf-8")
    with pytest.raises(SuiteError, match="must match the file name"):
        load_suite(path)


def test_prompts_have_no_stray_template_syntax():
    for suite in suites().values():
        assert "{%" not in suite.system + suite.user


def test_suite_files_live_where_the_loader_expects():
    assert SUITES_DIR.is_dir()
    assert all(path.stem in suites() for path in SUITES_DIR.glob("*.yaml"))
