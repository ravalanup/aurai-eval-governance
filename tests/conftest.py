"""Helpers: build valid use cases for each tier so tests change one thing at a time."""

from __future__ import annotations

import copy

import pytest

BASE = {
    "id": "example",
    "name": "Example use case",
    "owner": "Example owner role",
    "stage": "pilot",
    "tier": "T0",
    "gateway_team_id": "example",
    "data_classification": "internal",
    "uses_retrieval": False,
    "audience": "individual",
    "actions": "none",
    "error_impact": "none",
    "uses_protected_ip": False,
    "models": ["aurai-fast"],
    "controls": {
        "tracing": True,
        "pii_masking": True,
        "audit_retention": "standard",
        "monitoring": "basic",
    },
    "reviews": [],
    "pause_procedure": "Block the gateway team.",
}


def make(**overrides):
    doc = copy.deepcopy(BASE)
    controls = overrides.pop("controls", {})
    doc.update(overrides)
    doc["controls"].update(controls)
    return doc


@pytest.fixture
def make_use_case():
    return make


def compliant(tier: str, stage: str = "production") -> dict:
    """A use case that satisfies every control for the tier."""
    reviews = {
        "T0": [],
        "T1": [{"role": "team_lead", "approver": "Lead", "date": "2026-11-01"}],
        "T2": [{"role": "compliance", "approver": "Reviewer", "date": "2026-11-01"}],
        "T3": [
            {"role": "compliance", "approver": "Reviewer", "date": "2026-11-01"},
            {"role": "legal", "approver": "Counsel", "date": "2026-11-02"},
        ],
    }[tier]
    level = {
        "T0": dict(audit_retention="standard", monitoring="basic"),
        "T1": dict(audit_retention="standard", monitoring="full"),
        "T2": dict(audit_retention="extended", monitoring="full"),
        "T3": dict(audit_retention="extended", monitoring="full_with_runbook"),
    }[tier]
    return make(
        tier=tier,
        stage=stage,
        uses_retrieval=tier != "T0",
        actions="write" if tier in ("T2", "T3") else "none",
        data_classification="confidential" if tier in ("T2", "T3") else "internal",
        audience="external" if tier == "T3" else "team",
        reviews=reviews,
        controls=dict(
            entitlement_retrieval=True,
            citations=True,
            human_approval={"T0": "none", "T1": "none", "T2": "single", "T3": "dual"}[tier],
            golden_set_eval="passing",
            red_team="passing",
            **level,
        ),
    )
