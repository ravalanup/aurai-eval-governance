import copy
import json

import pytest

from aurai_governance.evals.promptfoo import ImportError_, export_config, import_results, write_export
from aurai_governance.evals.replay import load_replay, replay_path

from .evalhelpers import REPLAY_DIR, suites


def promptfoo_output(
    suite, *, provider="openai:chat:aurai-balanced", base="https://gateway.aurai.example/v1", fail=(), grader_error=()
):
    """A results file shaped like `promptfoo eval -o`, built from the suite (fields checked against 0.126.0)."""
    rows = []
    for index, case in enumerate(suite.cases):
        parts = []
        for a in case.assertions:
            part = {"pass": (case.id, a.type) not in fail, "score": 1, "reason": "x", "assertion": {"type": a.type}}
            if (case.id, a.type) in fail:
                part["reason"] = "output contains 'PWNED'"
            if case.id in grader_error and a.type == "llm-rubric":
                part.update(
                    {"pass": False, "metadata": {"graderError": True}, "reason": "Grader response contained neither"}
                )
            parts.append(part)
        rows.append(
            {
                "provider": {"id": provider, "label": ""},
                "testIdx": index,
                "success": all(p["pass"] for p in parts),
                "gradingResult": {"pass": all(p["pass"] for p in parts), "componentResults": parts},
                "testCase": {
                    "metadata": {"aurai_suite": suite.id, "aurai_suite_hash": suite.hash, "aurai_case": case.id}
                },
            }
        )
    return {
        "config": {"providers": [{"id": provider, "config": {"apiBaseUrl": base}}]},
        "metadata": {"promptfooVersion": "0.126.0", "evaluationCreatedAt": "2026-11-20T10:00:00.000Z"},
        "results": {"timestamp": "2026-11-20T10:00:01.000Z", "results": rows},
    }


def test_gateway_export_calls_the_gateway_with_an_env_key():
    suite = suites()["ops-insights-redteam"]
    config = export_config(suite)
    provider = config["providers"][0]
    assert provider["id"] == "openai:chat:aurai-balanced"
    assert provider["config"] == {"apiBaseUrl": "{{env.AURAI_GATEWAY_URL}}/v1", "apiKeyEnvar": "AURAI_EVAL_KEY"}
    assert "defaultTest" not in config  # no rubric, no judge
    messages = json.loads(config["prompts"][0])
    assert [m["role"] for m in messages] == ["system", "user"] and messages[1]["content"] == "{{facts}}"
    assert len(config["tests"]) == len(suite.cases)
    first = config["tests"][0]
    assert first["metadata"]["aurai_suite_hash"] == suite.hash and first["vars"]["aurai_case"] == first["description"]


def test_export_marks_critical_assertions_and_routes_the_judge_through_the_gateway():
    redteam = export_config(suites()["ops-insights-redteam"])
    assert {"type": "not-icontains", "value": "PWNED", "metric": "critical"} in redteam["tests"][0]["assert"]
    golden = export_config(suites()["ops-insights-golden"])
    judge = golden["defaultTest"]["options"]["provider"]
    assert judge["id"] == "openai:chat:aurai-reasoning" and "AURAI_GATEWAY_URL" in judge["config"]["apiBaseUrl"]


def test_export_without_a_system_prompt_sends_one_message():
    config = export_config(suites()["gateway-smoke-pii"])
    assert [m["role"] for m in json.loads(config["prompts"][0])] == ["user"]


def test_retrieval_suites_cannot_be_exported():
    with pytest.raises(ValueError, match="knowledge service"):
        export_config(suites()["knowledge-search-retrieval"])


def test_replay_export_uses_the_replay_provider_and_a_non_judging_grader():
    suite = suites()["ops-insights-golden"]
    config = export_config(suite, replay_file="replay.yaml", variant="fail")
    assert config["providers"][0] == {
        "id": "file://replay_provider.py",
        "config": {"replay": "replay.yaml", "variant": "fail"},
    }
    assert "defaultTest" not in config
    rubric = next(a for t in config["tests"] for a in t["assert"] if a["type"] == "llm-rubric")
    assert rubric["provider"]["config"]["grader"] is True


def test_write_export_is_self_contained(tmp_path):
    suite = suites()["gateway-smoke-pii"]
    replay = load_replay(replay_path(REPLAY_DIR, suite))
    target = write_export(suite, tmp_path, replay=replay)
    assert {p.name for p in tmp_path.iterdir()} == {"promptfooconfig.yaml", "replay.yaml", "replay_provider.py"}
    assert "&id" not in target.read_text(encoding="utf-8")  # no YAML anchors in what people read


def test_import_converts_a_live_run():
    suite = suites()["ops-insights-redteam"]
    result = import_results(suite, promptfoo_output(suite, fail={("inject-url", "not-regex")}))
    assert result.mode == "live" and result.model == "aurai-balanced"
    assert result.executed_at == "2026-11-20T10:00:00Z"
    assert result.tool == "promptfoo 0.126.0"
    assert result.passed == len(suite.cases) - 1 and result.critical_failures == 1
    assert result.failures()[0]["case"] == "inject-url" and result.failures()[0]["critical"] is True


def test_import_labels_other_providers_so_the_gate_ignores_them():
    suite = suites()["gateway-smoke-pii"]
    assert import_results(suite, promptfoo_output(suite, provider="echo", base="")).mode == "other"
    replayed = promptfoo_output(suite, provider="file://replay_provider.py", base="")
    assert import_results(suite, replayed).mode == "replay"
    elsewhere = promptfoo_output(suite, provider="openai:chat:aurai-fast", base="https://api.openai.com/v1")
    assert import_results(suite, elsewhere).mode == "other"


def test_a_configured_gateway_url_must_match():
    suite = suites()["gateway-smoke-pii"]
    data = promptfoo_output(suite)
    assert import_results(suite, data, "https://gateway.aurai.example").mode == "live"
    assert import_results(suite, data, "https://other.example").mode == "other"


def test_import_refuses_results_from_a_changed_suite():
    suite = suites()["ops-insights-golden"]
    data = promptfoo_output(suite)
    for row in data["results"]["results"]:
        row["testCase"]["metadata"]["aurai_suite_hash"] = "sha256:old"
    with pytest.raises(ImportError_, match="suite changed"):
        import_results(suite, data)


def test_import_refuses_results_for_another_suite():
    suite = suites()["ops-insights-golden"]
    with pytest.raises(ImportError_, match="not 'ops-insights-golden'"):
        import_results(suite, promptfoo_output(suites()["ops-insights-redteam"]))


def test_import_refuses_partial_or_duplicated_runs():
    suite = suites()["gateway-smoke-pii"]
    partial = promptfoo_output(suite)
    partial["results"]["results"].pop()
    with pytest.raises(ImportError_, match="missing"):
        import_results(suite, partial)
    doubled = promptfoo_output(suite)
    doubled["results"]["results"].append(copy.deepcopy(doubled["results"]["results"][0]))
    with pytest.raises(ImportError_, match="more than once"):
        import_results(suite, doubled)


def test_import_refuses_results_whose_assertions_do_not_line_up():
    suite = suites()["gateway-smoke-pii"]
    data = promptfoo_output(suite)
    data["results"]["results"][1]["gradingResult"]["componentResults"][0]["assertion"]["type"] = "contains"
    with pytest.raises(ImportError_, match="expected a not-icontains result"):
        import_results(suite, data)
    data = promptfoo_output(suite)
    data["results"]["results"][1]["gradingResult"]["componentResults"].pop()
    with pytest.raises(ImportError_, match="grading results"):
        import_results(suite, data)


def test_import_refuses_files_that_are_not_promptfoo_output():
    with pytest.raises(ImportError_, match="not a Promptfoo results file"):
        import_results(suites()["gateway-smoke-pii"], {"hello": "world"})


def test_a_provider_error_fails_the_case():
    suite = suites()["gateway-smoke-pii"]
    data = promptfoo_output(suite)
    row = data["results"]["results"][0]
    row["gradingResult"] = None
    row["error"] = "401 Unauthorized"
    result = import_results(suite, data)
    assert result.cases[0].passed is False and "401" in result.failures()[0]["reason"]


def test_judge_errors_are_recorded_as_not_evaluated_not_as_passes():
    suite = suites()["ops-insights-golden"]
    result = import_results(suite, promptfoo_output(suite, grader_error={"recurring-downtime"}))
    assert result.not_evaluated == 1 and result.cases[1].passed is True
