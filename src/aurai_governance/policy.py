"""Policy as code: required controls by risk tier, evaluated against a registered use case.

Source of truth: aurai-docs governance/risk-tiers.md ("Required controls by tier" and "Promotion gates").
Eval thresholds and the CI promotion gate are added in part 2 of this repository.

Strictness depends on the stage:
  sandbox     control gaps are warnings; tier consistency, tracing, PII masking and an owner are errors
  pilot       every control for the tier is required; evals and red team must at least be planned
  production  as pilot, and evals and red team must be passing
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .rubric import classify, tier_rank

ERROR = "error"
WARNING = "warning"

MONITORING_LEVELS = ("none", "basic", "full", "full_with_runbook")
APPROVAL_LEVELS = ("none", "single", "dual")
EVAL_LEVELS = ("none", "planned", "passing")
RETENTION_LEVELS = ("standard", "extended")

# tier -> requirements. None means not required at that tier.
REQUIREMENTS: dict[str, dict[str, Any]] = {
    "T0": {
        "golden": None,
        "red_team": None,
        "audit": "standard",
        "monitoring": "basic",
        "approval_for_writes": None,
        "reviews": (),
    },
    "T1": {
        "golden": "required",
        "red_team": None,
        "audit": "standard",
        "monitoring": "full",
        "approval_for_writes": None,
        "reviews": ("team_lead",),
    },
    "T2": {
        "golden": "required",
        "red_team": "required",
        "audit": "extended",
        "monitoring": "full",
        "approval_for_writes": "single",
        "reviews": ("compliance",),
    },
    "T3": {
        "golden": "required",
        "red_team": "required",
        "audit": "extended",
        "monitoring": "full_with_runbook",
        "approval_for_writes": "dual",
        "reviews": ("compliance", "legal"),
    },
}


@dataclass(frozen=True)
class Finding:
    severity: str
    rule: str
    message: str

    def __str__(self) -> str:
        return f"{self.severity}: [{self.rule}] {self.message}"


def _at_least(value: str, minimum: str, order: tuple[str, ...]) -> bool:
    return order.index(value) >= order.index(minimum)


def evaluate(use_case: dict[str, Any]) -> list[Finding]:
    """Evaluate one use case against the policy. Assumes the document already passed schema validation."""
    findings: list[Finding] = []
    stage = use_case["stage"]
    tier = use_case["tier"]
    controls = use_case["controls"]
    req = REQUIREMENTS[tier]
    enforce = stage in ("pilot", "production")
    gap = ERROR if enforce else WARNING

    def add(severity: str, rule: str, message: str) -> None:
        findings.append(Finding(severity, rule, message))

    # Tier consistency: always an error.
    classification = classify(use_case)
    if tier_rank(tier) < tier_rank(classification.min_tier):
        add(
            ERROR,
            "tier-below-rubric",
            f"declared {tier} but the rubric requires at least {classification.min_tier} ({_triggers(classification)})",
        )

    # Controls required at every tier and stage.
    if not controls.get("tracing"):
        add(ERROR, "tracing-required", "tracing must be enabled at every tier")
    if not controls.get("pii_masking"):
        add(ERROR, "pii-masking-required", "PII masking must be enabled at every tier")
    if not use_case.get("owner"):
        add(ERROR, "owner-required", "a named owner is required")

    # Retrieval controls.
    if use_case.get("uses_retrieval"):
        if not controls.get("entitlement_retrieval"):
            add(gap, "entitlement-retrieval-required", "retrieval must evaluate the caller's permissions")
        if not controls.get("citations"):
            add(gap, "citations-required", "answers grounded in retrieved content must include citations")

    # Human approval for actions.
    needed = req["approval_for_writes"]
    if use_case["actions"] == "write" and needed:
        have = controls.get("human_approval", "none")
        if not _at_least(have, needed, APPROVAL_LEVELS):
            add(
                gap,
                "human-approval-required",
                f"{tier} use cases that write to other systems need {needed} human approval (found {have})",
            )

    # Evaluation and red team.
    _check_eval(add, stage, "golden_set_eval", "golden-set-evaluation", req["golden"], controls)
    _check_eval(add, stage, "red_team", "red-team", req["red_team"], controls)

    # Audit retention and monitoring.
    if not _at_least(controls["audit_retention"], req["audit"], RETENTION_LEVELS):
        add(gap, "audit-retention", f"{tier} requires {req['audit']} audit log retention")
    if not _at_least(controls["monitoring"], req["monitoring"], MONITORING_LEVELS):
        add(
            gap,
            "monitoring-required",
            f"{tier} requires {req['monitoring']} monitoring (found {controls['monitoring']})",
        )

    # Reviews and the pause procedure (promotion gates).
    approved_roles = {r["role"] for r in use_case.get("reviews", [])}
    for role in req["reviews"]:
        if role not in approved_roles:
            add(gap, "review-required", f"{tier} requires a {role} review")
    if not use_case.get("pause_procedure"):
        add(gap, "pause-procedure-required", "a documented pause procedure is required before pilot")

    return findings


def _triggers(classification) -> str:
    triggered = [f"{q.id}: {q.reason}" for q in classification.questions if q.triggered]
    return "; ".join(triggered) if triggered else f"base tier {classification.base_tier}"


def _check_eval(add, stage, key, rule, requirement, controls) -> None:
    level = controls.get(key, "none")
    if requirement != "required":
        return
    if stage == "production" and level != "passing":
        add(ERROR, rule, f"{key.replace('_', ' ')} must be passing before production (found {level})")
    elif stage == "pilot" and level == "none":
        add(ERROR, rule, f"{key.replace('_', ' ')} must be at least planned before pilot")
    elif stage == "sandbox" and level == "none":
        add(WARNING, rule, f"{key.replace('_', ' ')} is required before pilot")


def check_models(use_case: dict[str, Any], catalog_path: str | Path) -> list[Finding]:
    """Check each model alias against the gateway's approved catalog (aurai-gateway config/model-catalog.yaml)."""
    raw = yaml.safe_load(Path(catalog_path).read_text(encoding="utf-8"))
    catalog = {m["alias"]: set(m["tiers"]) for m in raw.get("models", [])}
    findings = []
    for alias in use_case["models"]:
        if alias not in catalog:
            findings.append(Finding(ERROR, "model-not-in-catalog", f"model {alias} is not in the approved catalog"))
        elif use_case["tier"] not in catalog[alias]:
            findings.append(
                Finding(ERROR, "model-not-approved-for-tier", f"model {alias} is not approved for {use_case['tier']}")
            )
    return findings
