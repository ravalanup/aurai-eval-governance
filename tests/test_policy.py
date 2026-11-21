import pytest

from aurai_governance.policy import ERROR, WARNING, check_models, evaluate

from .conftest import compliant, make


def rules(findings, severity=None):
    return {f.rule for f in findings if severity is None or f.severity == severity}


@pytest.mark.parametrize("tier", ["T0", "T1", "T2", "T3"])
def test_compliant_use_case_passes_at_every_tier(tier):
    assert evaluate(compliant(tier)) == []


def test_declared_tier_below_rubric_is_an_error():
    doc = make(tier="T1", actions="write", data_classification="confidential")
    assert "tier-below-rubric" in rules(evaluate(doc), ERROR)


def test_higher_declared_tier_is_allowed():
    doc = compliant("T3")
    doc["actions"] = "none"
    doc["audience"] = "team"
    doc["data_classification"] = "internal"
    assert "tier-below-rubric" not in rules(evaluate(doc))


def test_tracing_and_pii_masking_always_required_even_in_sandbox():
    doc = make(stage="sandbox", controls={"tracing": False, "pii_masking": False})
    assert {"tracing-required", "pii-masking-required"} <= rules(evaluate(doc), ERROR)


def test_t2_write_needs_human_approval():
    doc = compliant("T2")
    doc["controls"]["human_approval"] = "none"
    assert "human-approval-required" in rules(evaluate(doc), ERROR)


def test_t3_write_needs_dual_approval():
    doc = compliant("T3")
    doc["controls"]["human_approval"] = "single"
    assert "human-approval-required" in rules(evaluate(doc), ERROR)


def test_retrieval_requires_entitlements_and_citations():
    doc = compliant("T1")
    doc["controls"]["entitlement_retrieval"] = False
    doc["controls"]["citations"] = False
    assert {"entitlement-retrieval-required", "citations-required"} <= rules(evaluate(doc), ERROR)


def test_t3_requires_legal_and_compliance_reviews():
    doc = compliant("T3")
    doc["reviews"] = [r for r in doc["reviews"] if r["role"] != "legal"]
    findings = evaluate(doc)
    assert "review-required" in rules(findings, ERROR)
    assert any("legal" in f.message for f in findings)


def test_t3_requires_incident_runbook_monitoring():
    doc = compliant("T3")
    doc["controls"]["monitoring"] = "full"
    assert "monitoring-required" in rules(evaluate(doc), ERROR)


def test_t2_requires_extended_audit_retention():
    doc = compliant("T2")
    doc["controls"]["audit_retention"] = "standard"
    assert "audit-retention" in rules(evaluate(doc), ERROR)


def test_pause_procedure_required_from_pilot():
    doc = compliant("T1", stage="pilot")
    doc["pause_procedure"] = ""
    assert "pause-procedure-required" in rules(evaluate(doc), ERROR)


def test_sandbox_gaps_are_warnings():
    doc = compliant("T2", stage="sandbox")
    doc["controls"]["human_approval"] = "none"
    doc["controls"]["golden_set_eval"] = "none"
    doc["reviews"] = []
    findings = evaluate(doc)
    assert not rules(findings, ERROR)
    assert {"human-approval-required", "golden-set-evaluation", "review-required"} <= rules(findings, WARNING)


def test_evals_planned_is_enough_for_pilot_but_not_production():
    pilot = compliant("T2", stage="pilot")
    pilot["controls"].update(golden_set_eval="planned", red_team="planned")
    assert evaluate(pilot) == []

    production = compliant("T2", stage="production")
    production["controls"].update(golden_set_eval="planned", red_team="planned")
    assert {"golden-set-evaluation", "red-team"} <= rules(evaluate(production), ERROR)


def test_evals_none_blocks_pilot():
    doc = compliant("T2", stage="pilot")
    doc["controls"]["golden_set_eval"] = "none"
    assert "golden-set-evaluation" in rules(evaluate(doc), ERROR)


def test_t0_does_not_require_evals_or_reviews():
    doc = make(stage="production")
    assert evaluate(doc) == []


def test_check_models_against_catalog(tmp_path):
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(
        "models:\n  - {alias: aurai-fast, tiers: [T0, T1, T2, T3]}\n  - {alias: aurai-openai-mini, tiers: [T0, T1]}\n",
        encoding="utf-8",
    )
    ok = make(tier="T2", models=["aurai-fast"])
    assert check_models(ok, catalog) == []
    bad = make(tier="T2", models=["aurai-openai-mini", "aurai-unknown"])
    assert rules(check_models(bad, catalog)) == {"model-not-approved-for-tier", "model-not-in-catalog"}
