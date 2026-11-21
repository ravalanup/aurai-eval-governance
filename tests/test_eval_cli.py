import json

import yaml

from aurai_governance import cli

from .conftest import compliant
from .evalhelpers import ROOT, SUITES_DIR, THRESHOLDS, evidence, save, stamp, suites
from .test_eval_promptfoo import promptfoo_output

COMMON = ["--suites", str(SUITES_DIR), "--thresholds", str(THRESHOLDS)]


def gate(*args, results):
    return cli.main(["gate", *args, *COMMON, "--results", str(results), "--today", "2026-11-21"])


def test_repository_use_cases_pass_the_gate_at_their_declared_stage(tmp_path, capsys):
    assert gate(str(ROOT / "use-cases"), results=tmp_path) == 0
    assert "0 error(s)" in capsys.readouterr().out


def test_ops_insights_cannot_be_promoted_to_production_without_evidence(tmp_path, capsys):
    code = gate(str(ROOT / "use-cases"), "--use-case", "ops-insights", "--target-stage", "production", results=tmp_path)
    out = capsys.readouterr().out
    assert code == 1
    assert "golden set eval must be passing before production (found planned)" in out
    assert "red team must be passing before production (found planned)" in out
    assert "gateway-smoke-pii: no evidence is committed" in out


def test_pilot_promotion_names_the_missing_suites(tmp_path, capsys):
    code = gate(str(ROOT / "use-cases"), "--use-case", "internal-help", "--target-stage", "pilot", results=tmp_path)
    assert code == 1
    assert "evals.golden lists no suite" in capsys.readouterr().out
    code = gate(str(ROOT / "use-cases"), "--use-case", "knowledge-search", "--target-stage", "pilot", results=tmp_path)
    assert code == 1
    assert "evals.red_team lists no suite" in capsys.readouterr().out


def test_unknown_use_case_is_an_error(tmp_path, capsys):
    assert gate(str(ROOT / "use-cases"), "--use-case", "nope", results=tmp_path) == 1
    assert "not found" in capsys.readouterr().err


def test_a_full_promotion_passes_when_the_evidence_is_there(tmp_path, capsys):
    ops = yaml.safe_load((ROOT / "use-cases" / "ops-insights.yaml").read_text(encoding="utf-8"))
    ops["stage"] = "production"
    ops["controls"].update(golden_set_eval="passing", red_team="passing")
    ops["reviews"].append({"role": "legal", "approver": "Legal reviewer", "date": "2026-11-15"})
    registry = tmp_path / "registry"
    registry.mkdir()
    (registry / "ops-insights.yaml").write_text(yaml.safe_dump(ops), encoding="utf-8")
    results = tmp_path / "results"
    loaded = suites()
    for suite_id in ("ops-insights-golden", "ops-insights-redteam", "ops-insights-grounding"):
        save(results, evidence(loaded[suite_id], model="aurai-balanced"))
    save(results, evidence(loaded["gateway-smoke-pii"], tier=None, model="aurai-fast"))
    assert gate(str(registry), results=results) == 0, capsys.readouterr().out


def test_gate_reports_schema_errors_in_the_registry(tmp_path, capsys):
    registry = tmp_path / "registry"
    registry.mkdir()
    doc = compliant("T1", "pilot")
    doc["evals"] = {"golden": ["Not A Slug"]}
    (registry / "x.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    assert gate(str(registry), results=tmp_path) == 1
    assert "[schema]" in capsys.readouterr().out


def test_strict_mode_fails_on_warnings(tmp_path):
    results = tmp_path / "results"
    loaded = suites()
    ops = yaml.safe_load((ROOT / "use-cases" / "ops-insights.yaml").read_text(encoding="utf-8"))
    registry = tmp_path / "registry"
    registry.mkdir()
    (registry / "ops-insights.yaml").write_text(yaml.safe_dump(ops), encoding="utf-8")
    for suite_id in ("ops-insights-golden", "ops-insights-redteam"):
        save(results, evidence(loaded[suite_id]))
    assert gate(str(registry), results=results) == 0  # planned claims with passing evidence: a hint, not a failure
    assert gate(str(registry), "--strict", results=results) == 1


def test_list_prints_every_suite(capsys):
    assert cli.main(["evals", "list", "--suites", str(SUITES_DIR)]) == 0
    out = capsys.readouterr().out
    assert "ops-insights-golden" in out and "knowledge-search-retrieval" in out and "_platform" in out


def test_export_command_writes_a_gateway_config(tmp_path, capsys):
    code = cli.main(["evals", "export", "ops-insights-golden", "--out", str(tmp_path), "--suites", str(SUITES_DIR)])
    assert code == 0
    config = yaml.safe_load((tmp_path / "promptfooconfig.yaml").read_text(encoding="utf-8"))
    assert config["providers"][0]["id"] == "openai:chat:aurai-balanced"
    assert "AURAI_GATEWAY_URL" in capsys.readouterr().out


def test_import_writes_evidence_with_the_tier_from_the_registry(tmp_path, capsys):
    suite = suites()["ops-insights-redteam"]
    source = tmp_path / "out.json"
    source.write_text(json.dumps(promptfoo_output(suite)), encoding="utf-8")
    results = tmp_path / "results"
    args = ["evals", "import", str(source), "--suite", suite.id, "--suites", str(SUITES_DIR)]
    args += ["--registry", str(ROOT / "use-cases"), "--results", str(results)]
    assert cli.main(args) == 0
    written = json.loads((results / "ops-insights" / "ops-insights-redteam.json").read_text(encoding="utf-8"))
    assert written["mode"] == "live" and written["tier_at_run"] == "T2" and written["suite_hash"] == suite.hash
    assert written["totals"]["pass_rate"] == 1.0


def test_import_dry_run_writes_nothing_and_flags_non_live_runs(tmp_path, capsys):
    suite = suites()["gateway-smoke-pii"]
    source = tmp_path / "out.json"
    source.write_text(json.dumps(promptfoo_output(suite, provider="echo", base="")), encoding="utf-8")
    args = ["evals", "import", str(source), "--suite", suite.id, "--suites", str(SUITES_DIR), "--dry-run"]
    assert cli.main(args + ["--results", str(tmp_path / "r")]) == 0
    captured = capsys.readouterr()
    assert "only accepts live runs" in captured.err and not (tmp_path / "r").exists()


def test_import_refuses_a_stale_run(tmp_path, capsys):
    suite = suites()["gateway-smoke-pii"]
    data = promptfoo_output(suite)
    data["results"]["results"][0]["testCase"]["metadata"]["aurai_suite_hash"] = "sha256:old"
    source = tmp_path / "out.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    args = [
        "evals",
        "import",
        str(source),
        "--suite",
        suite.id,
        "--suites",
        str(SUITES_DIR),
        "--results",
        str(tmp_path),
    ]
    assert cli.main(args) == 1
    assert "suite changed" in capsys.readouterr().err


def report(**overrides):
    suite = suites()["knowledge-search-retrieval"]
    data = {
        "version": 1,
        "top_k": 5,
        "cases": 28,
        "answerable": 21,
        "hit_rate": 0.95,
        "mrr": 0.8,
        "leaks": 0,
        "thresholds": {"hit_rate": 0.85, "mrr": 0.6, "max_leaks": 0},
        "failures": [],
        "golden_set_sha256": suite.hash.removeprefix("sha256:"),
        "mode": "live",
        "executed_at": stamp(1),
    }
    data.update(overrides)
    return data


def import_retrieval(tmp_path, data):
    source = tmp_path / "report.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    args = ["evals", "import-retrieval", str(source), "--suite", "knowledge-search-retrieval"]
    args += ["--suites", str(SUITES_DIR), "--registry", str(ROOT / "use-cases"), "--results", str(tmp_path / "results")]
    return cli.main(args)


def test_import_retrieval_writes_evidence(tmp_path):
    assert import_retrieval(tmp_path, report()) == 0
    written = json.loads((tmp_path / "results" / "knowledge-search" / "knowledge-search-retrieval.json").read_text())
    assert written["mode"] == "live" and written["tier_at_run"] == "T3"
    assert written["metrics"]["hit_rate"] == 0.95 and written["suite_hash"].startswith("sha256:5751f263")


def test_offline_retrieval_reports_are_labelled_offline(tmp_path):
    assert import_retrieval(tmp_path, report(mode="offline")) == 0
    written = json.loads((tmp_path / "results" / "knowledge-search" / "knowledge-search-retrieval.json").read_text())
    assert written["mode"] == "offline"


def test_import_retrieval_refuses_a_different_golden_set(tmp_path, capsys):
    assert import_retrieval(tmp_path, report(golden_set_sha256="f" * 64)) == 1
    assert "pins" in capsys.readouterr().err


def test_import_retrieval_refuses_reports_without_a_timestamp(tmp_path, capsys):
    data = report()
    del data["executed_at"]
    assert import_retrieval(tmp_path, data) == 1
    assert "--json" in capsys.readouterr().err


def test_drift_check_compares_the_prompt_with_the_agent_source(tmp_path, capsys):
    source = tmp_path / "agent.py"
    suite = suites()["ops-insights-golden"]
    source.write_text(f"MAX = 3\nSYSTEM_PROMPT = {suite.system!r}\n", encoding="utf-8")
    args = ["evals", "drift", suite.id, "--suites", str(SUITES_DIR), "--source", str(source)]
    assert cli.main(args) == 0
    source.write_text("SYSTEM_PROMPT = 'something else'\n", encoding="utf-8")
    assert cli.main(args) == 1
    assert "differs" in capsys.readouterr().out


def test_drift_check_reads_f_strings_over_module_constants(tmp_path):
    source = tmp_path / "agent.py"
    suite = suites()["ops-insights-golden"]
    first, second = suite.system.split("at most 3 ")
    source.write_text(f'N = 3\nSYSTEM_PROMPT = ({first!r} f"at most {{N}} " {second!r})\n', encoding="utf-8")
    args = ["evals", "drift", suite.id, "--suites", str(SUITES_DIR), "--source", str(source)]
    assert cli.main(args) == 0


def test_the_suite_prompt_matches_the_agent_runtime_when_it_is_next_door(capsys):
    source = ROOT.parent / "aurai-agent-runtime" / "src" / "aurai_agents" / "agents" / "ops_insights.py"
    if not source.is_file():
        import pytest

        pytest.skip("aurai-agent-runtime is not checked out next to this repository")
    for suite_id in ("ops-insights-golden", "ops-insights-redteam", "ops-insights-grounding"):
        assert cli.main(["evals", "drift", suite_id, "--suites", str(SUITES_DIR), "--source", str(source)]) == 0


def test_only_live_evidence_is_committed():
    """Replay and offline results are for regression tests; they never belong in evals/results."""
    for path in (ROOT / "evals" / "results").rglob("*.json"):
        assert json.loads(path.read_text(encoding="utf-8"))["mode"] == "live", path
