import pytest

from aurai_governance import gate
from aurai_governance.policy import ERROR, WARNING

from .conftest import compliant
from .evalhelpers import TODAY, evidence, save, suites, thresholds

ALL = suites()
TH = thresholds()


def use_case(**changes):
    """ops-insights-shaped: T2, retrieves, writes with approval, all three suites listed."""
    doc = compliant("T2", "production")
    doc.update(id="ops-insights", uses_retrieval=True, models=["aurai-balanced", "aurai-reasoning"])
    doc["controls"].update(
        entitlement_retrieval=True,
        citations=True,
        golden_set_eval="passing",
        red_team="passing",
        human_approval="single",
    )
    doc["evals"] = {
        "golden": ["ops-insights-golden"],
        "red_team": ["ops-insights-redteam"],
        "grounding": ["ops-insights-grounding"],
    }
    controls = changes.pop("controls", {})
    doc.update(changes)
    doc["controls"].update(controls)
    return doc


def seed(tmp_path, *, platform=True, **overrides):
    """Write good evidence for the three ops-insights suites (and the platform smoke suite)."""
    for suite_id in ("ops-insights-golden", "ops-insights-redteam", "ops-insights-grounding"):
        save(tmp_path, evidence(ALL[suite_id], **overrides))
    if platform:
        save(tmp_path, evidence(ALL["gateway-smoke-pii"], tier=None, model="aurai-fast"))


def run(doc, results, **kwargs):
    return gate.check(doc, ALL, results, TH, today=TODAY, **kwargs)


def rules(findings, severity=None):
    return {f.rule for f in findings if severity is None or f.severity == severity}


def messages(findings):
    return "\n".join(str(f) for f in findings)


def test_good_evidence_for_every_claim_passes(tmp_path):
    seed(tmp_path)
    assert run(use_case(), tmp_path) == []


def test_a_passing_claim_without_evidence_is_an_error_at_any_stage(tmp_path):
    for stage in ("sandbox", "pilot", "production"):
        findings = run(use_case(stage=stage), tmp_path)
        assert "eval-evidence" in rules(findings, ERROR), stage
        assert "no evidence is committed" in messages(findings)


def test_a_passing_claim_must_list_suites(tmp_path):
    seed(tmp_path)
    findings = run(
        use_case(evals={"golden": [], "red_team": ["ops-insights-redteam"], "grounding": ["ops-insights-grounding"]}),
        tmp_path,
    )
    assert "golden_set_eval is passing but evals.golden lists no suite" in messages(findings)


def test_evidence_from_an_older_version_of_the_suite_does_not_count(tmp_path):
    seed(tmp_path)
    save(tmp_path, evidence(ALL["ops-insights-redteam"], suite_hash="sha256:" + "0" * 64))
    findings = run(use_case(), tmp_path)
    assert "the suite changed after this evidence was produced" in messages(findings)


@pytest.mark.parametrize("mode", ["replay", "offline", "other"])
def test_only_live_runs_count(tmp_path, mode):
    seed(tmp_path, mode=mode)
    assert f"came from a {mode!r} run" in messages(run(use_case(), tmp_path))


def test_evidence_expires(tmp_path):
    seed(tmp_path, days_ago=61)  # T2 allows 60
    assert "61 days old; the limit is 60" in messages(run(use_case(), tmp_path))
    seed(tmp_path, days_ago=60)
    assert run(use_case(), tmp_path) == []


def test_future_dated_evidence_is_rejected(tmp_path):
    seed(tmp_path, days_ago=-5)
    assert "in the future" in messages(run(use_case(), tmp_path))


def test_pass_rate_and_size_thresholds(tmp_path):
    seed(tmp_path)
    save(tmp_path, evidence(ALL["ops-insights-golden"], totals={"pass_rate": 0.89}))
    save(tmp_path, evidence(ALL["ops-insights-redteam"], totals={"cases": 5}))
    text = messages(run(use_case(), tmp_path))
    assert "pass rate 0.89 is below 0.9" in text
    assert "5 cases; at least 10 are required" in text


def test_a_single_critical_failure_blocks_red_team(tmp_path):
    seed(tmp_path)
    save(tmp_path, evidence(ALL["ops-insights-redteam"], totals={"critical_failures": 1}))
    assert "1 critical failure(s); the limit is 0" in messages(run(use_case(), tmp_path))


def test_unevaluated_rubrics_in_live_evidence_block(tmp_path):
    seed(tmp_path)
    save(tmp_path, evidence(ALL["ops-insights-golden"], totals={"not_evaluated": 2}))
    assert "2 assertion(s) were not evaluated" in messages(run(use_case(), tmp_path))


def test_evidence_must_be_recorded_at_or_above_the_declared_tier(tmp_path):
    seed(tmp_path, tier="T1")
    assert "recorded at tier T1, below the declared T2" in messages(run(use_case(), tmp_path))
    seed(tmp_path, tier="T3")
    assert run(use_case(), tmp_path) == []
    seed(tmp_path, tier=None)
    assert "recorded at tier None" in messages(run(use_case(), tmp_path))


def test_evidence_must_come_from_a_registered_model(tmp_path):
    seed(tmp_path, model="aurai-local")
    assert "not one of the use case's models" in messages(run(use_case(), tmp_path))


def test_evidence_for_another_use_case_does_not_count(tmp_path):
    seed(tmp_path)
    save(tmp_path, evidence(ALL["ops-insights-golden"], use_case="finance-reporting"))
    # The file sits under ops-insights/ but says it belongs elsewhere.
    wrong = evidence(ALL["ops-insights-golden"], use_case="finance-reporting")
    path = tmp_path / "ops-insights" / "ops-insights-golden.json"
    import json

    path.write_text(json.dumps(wrong), encoding="utf-8")
    assert "evidence is for finance-reporting/" in messages(run(use_case(), tmp_path))


def test_unreadable_evidence_is_an_error(tmp_path):
    seed(tmp_path)
    (tmp_path / "ops-insights" / "ops-insights-golden.json").write_text("{not json", encoding="utf-8")
    assert "unreadable" in messages(run(use_case(), tmp_path))


def test_retrieving_use_cases_at_t2_and_above_need_grounding_evidence(tmp_path):
    seed(tmp_path)
    findings = run(use_case(evals={"golden": ["ops-insights-golden"], "red_team": ["ops-insights-redteam"]}), tmp_path)
    assert "eval-grounding-required" in rules(findings, ERROR)
    save(tmp_path, evidence(ALL["ops-insights-grounding"], totals={"pass_rate": 0.5}))
    assert "ops-insights-grounding: pass rate 0.5" in messages(run(use_case(), tmp_path))


def test_a_use_case_that_does_not_retrieve_needs_no_grounding(tmp_path):
    seed(tmp_path)
    doc = use_case(uses_retrieval=False)
    doc["evals"].pop("grounding")
    doc["controls"].update(entitlement_retrieval=False, citations=False)
    assert run(doc, tmp_path) == []


def test_suite_references_are_checked(tmp_path):
    seed(tmp_path)
    doc = use_case()
    doc["evals"]["golden"] = ["no-such-suite"]
    assert "no-such-suite, which is not in the suites directory" in messages(run(doc, tmp_path))
    doc["evals"]["golden"] = ["ops-insights-redteam"]
    assert "is a red_team suite and cannot fill evals.golden" in messages(run(doc, tmp_path))
    doc["evals"]["golden"] = ["knowledge-grounded-answers"]
    assert "belongs to internal-help, not ops-insights" in messages(run(doc, tmp_path))


def test_planned_claims_need_a_suite_from_pilot_on(tmp_path):
    planned = {"golden_set_eval": "planned", "red_team": "planned"}
    sandbox = run(use_case(stage="sandbox", controls=planned, evals={}), tmp_path)
    assert sandbox == []
    pilot = run(use_case(stage="pilot", controls=planned, evals={}), tmp_path)
    assert {f.rule for f in pilot} == {"eval-suite-missing"} and all(f.severity == ERROR for f in pilot)
    assert len(pilot) == 2
    assert run(use_case(stage="pilot", controls=planned), tmp_path) == []


def test_planned_claims_with_passing_evidence_get_an_upgrade_hint(tmp_path):
    seed(tmp_path)
    findings = run(use_case(stage="pilot", controls={"golden_set_eval": "planned"}), tmp_path)
    assert rules(findings) == {"eval-evidence-supports-upgrade"}
    assert findings[0].severity == WARNING


def test_planned_with_unusable_evidence_gives_no_hint(tmp_path):
    seed(tmp_path, mode="replay")
    assert run(use_case(stage="pilot", controls={"golden_set_eval": "planned", "red_team": "planned"}), tmp_path) == []


def test_production_needs_fresh_platform_evidence(tmp_path):
    seed(tmp_path, platform=False)
    findings = run(use_case(), tmp_path)
    assert "eval-platform" in rules(findings, ERROR)
    assert "gateway-smoke-pii: no evidence is committed" in messages(findings)
    save(tmp_path, evidence(ALL["gateway-smoke-pii"], tier=None, model="aurai-fast", days_ago=31))
    assert "31 days old; the limit is 30" in messages(run(use_case(), tmp_path))
    save(
        tmp_path,
        evidence(
            ALL["gateway-smoke-pii"], tier=None, model="aurai-fast", totals={"pass_rate": 0.8, "critical_failures": 1}
        ),
    )
    text = messages(run(use_case(), tmp_path))
    assert "pass rate 0.8 is below 1.0" in text and "critical failure" in text


def test_platform_evidence_is_not_required_before_production(tmp_path):
    seed(tmp_path, platform=False)
    assert run(use_case(stage="pilot"), tmp_path) == []


def test_target_stage_overrides_the_declared_stage(tmp_path):
    seed(tmp_path, platform=False)
    assert run(use_case(stage="pilot"), tmp_path) == []
    assert "eval-platform" in rules(run(use_case(stage="pilot"), tmp_path, target_stage="production"))


def test_retrieval_evidence_meets_the_pinned_golden_set(tmp_path):
    suite = ALL["knowledge-search-retrieval"]
    doc = compliant("T3", "production")
    doc.update(id="knowledge-search", uses_retrieval=True, models=["aurai-embed"])
    doc["controls"].update(entitlement_retrieval=True, citations=True, golden_set_eval="passing", red_team="none")
    doc["evals"] = {"golden": ["knowledge-search-retrieval"]}
    save(tmp_path, evidence(suite, tier="T3"))
    findings = run(doc, tmp_path)
    assert "eval-grounding-required" not in rules(findings)  # a retrieval service is judged on retrieval
    assert not [f for f in findings if f.rule == "eval-evidence"]


@pytest.mark.parametrize(
    ("metrics", "expected"),
    [
        ({"hit_rate": 0.8}, "hit rate 0.8 is below 0.85"),
        ({"mrr": 0.5}, "MRR 0.5 is below 0.6"),
        ({"leaks": 1}, "1 leaked passage(s); the limit is 0"),
        ({"cases": 10}, "10 cases; at least 25 are required"),
    ],
)
def test_retrieval_thresholds(tmp_path, metrics, expected):
    suite = ALL["knowledge-search-retrieval"]
    doc = compliant("T3", "pilot")
    doc.update(id="knowledge-search", uses_retrieval=True, models=["aurai-embed"])
    doc["controls"].update(entitlement_retrieval=True, citations=True, golden_set_eval="passing")
    doc["evals"] = {"golden": ["knowledge-search-retrieval"]}
    save(tmp_path, evidence(suite, tier="T3", metrics=metrics))
    assert expected in messages(run(doc, tmp_path))


def test_retrieval_evidence_for_a_different_golden_set_is_stale(tmp_path):
    suite = ALL["knowledge-search-retrieval"]
    doc = compliant("T3", "pilot")
    doc.update(id="knowledge-search", uses_retrieval=True, models=["aurai-embed"])
    doc["controls"].update(entitlement_retrieval=True, citations=True, golden_set_eval="passing")
    doc["evals"] = {"golden": ["knowledge-search-retrieval"]}
    save(tmp_path, evidence(suite, tier="T3", suite_hash="sha256:" + "1" * 64))
    assert "the suite changed" in messages(run(doc, tmp_path))


def test_thresholds_fall_back_to_the_nearest_stricter_tier(tmp_path):
    seed(tmp_path)
    # T1 has no red-team row; a T1 use case that runs one is held to T2's numbers.
    doc = use_case(tier="T1", stage="pilot", reviews=[{"role": "team_lead", "approver": "x", "date": "2026-11-01"}])
    save(tmp_path, evidence(ALL["ops-insights-redteam"], tier="T1", totals={"pass_rate": 0.85}))
    assert "pass rate 0.85 is below 0.9" in messages(run(doc, tmp_path))
