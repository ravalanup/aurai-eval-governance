"""Promotion gate (EV-3): a control claim is only as good as the evidence behind it.

``policy.py`` checks that a use case *declares* the controls its tier needs. This module checks the claims:
a use case that says ``golden_set_eval: passing`` has to point at suites, and each suite needs committed,
live, recent evidence from the exact suite file in the repository, at or above the declared tier, that
meets the tier's thresholds. Replay results and offline reports never count.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .evals.results import LIVE, load_evidence, parse_time
from .evals.suite import Suite
from .evals.thresholds import TIER_ORDER, for_tier
from .policy import ERROR, WARNING, Finding

# control -> (slot in the use case's evals section, suite kinds that may fill it)
CLAIMS = {
    "golden_set_eval": ("golden", ("golden", "retrieval")),
    "red_team": ("red_team", ("red_team",)),
}
PLATFORM = "_platform"


def _rank(tier: str) -> int:
    return TIER_ORDER.index(tier)


def evidence_problems(
    suite: Suite,
    use_case: dict[str, Any] | None,
    evidence: dict[str, Any] | None,
    thresholds: dict[str, Any],
    today: date,
) -> list[str]:
    """Why this evidence does not back a ``passing`` claim. An empty list means it does."""
    if evidence is None:
        return ["no evidence is committed; run the suite live and import the result"]
    if "_invalid" in evidence:
        return [f"the evidence file is unreadable ({evidence['_invalid']})"]
    problems: list[str] = []
    if evidence.get("schema") != 1:
        problems.append(f"unsupported evidence schema {evidence.get('schema')!r}")
    if evidence.get("suite") != suite.id or evidence.get("use_case") != suite.use_case:
        problems.append(f"evidence is for {evidence.get('use_case')}/{evidence.get('suite')}")
    if evidence.get("suite_hash") != suite.hash:
        problems.append("the suite changed after this evidence was produced; run it again")
    if evidence.get("mode") != LIVE:
        problems.append(f"evidence came from a {evidence.get('mode')!r} run; only live runs count")

    if use_case is not None:  # platform suites are not tied to one use case
        tier = use_case["tier"]
        ran = evidence.get("tier_at_run")
        if ran not in TIER_ORDER or _rank(ran) < _rank(tier):
            problems.append(f"it was recorded at tier {ran}, below the declared {tier}")
        if suite.kind != "retrieval" and evidence.get("model") not in use_case["models"]:
            problems.append(f"it ran model {evidence.get('model')!r}, which is not one of the use case's models")
        _, limits = for_tier(thresholds, suite.kind, tier)
    else:
        limits = thresholds["platform"]

    try:
        age = (today - parse_time(str(evidence.get("executed_at"))).astimezone(UTC).date()).days
    except ValueError:
        problems.append("executed_at is missing or malformed")
    else:
        if age < -1:
            problems.append("executed_at is in the future")
        elif age > limits["max_age_days"]:
            problems.append(f"it is {age} days old; the limit is {limits['max_age_days']}")

    if suite.kind == "retrieval":
        metrics = evidence.get("metrics") or {}
        if metrics.get("cases", 0) < limits.get("min_cases", 0):
            problems.append(f"{metrics.get('cases', 0)} cases; at least {limits['min_cases']} are required")
        if metrics.get("hit_rate", 0.0) < limits["min_hit_rate"]:
            problems.append(f"hit rate {metrics.get('hit_rate')} is below {limits['min_hit_rate']}")
        if metrics.get("mrr", 0.0) < limits["min_mrr"]:
            problems.append(f"MRR {metrics.get('mrr')} is below {limits['min_mrr']}")
        if metrics.get("leaks", 1) > limits["max_leaks"]:
            problems.append(f"{metrics.get('leaks')} leaked passage(s); the limit is {limits['max_leaks']}")
        return problems

    totals = evidence.get("totals") or {}
    if totals.get("cases", 0) < limits.get("min_cases", 0):
        problems.append(f"{totals.get('cases', 0)} cases; at least {limits['min_cases']} are required")
    if totals.get("pass_rate", 0.0) < limits["min_pass_rate"]:
        problems.append(f"pass rate {totals.get('pass_rate')} is below {limits['min_pass_rate']}")
    if totals.get("critical_failures", 1) > limits.get("max_critical_failures", 0):
        problems.append(
            f"{totals.get('critical_failures')} critical failure(s); "
            f"the limit is {limits.get('max_critical_failures', 0)}"
        )
    if totals.get("not_evaluated", 0):
        problems.append(f"{totals['not_evaluated']} assertion(s) were not evaluated")
    return problems


def check(
    use_case: dict[str, Any],
    suites: dict[str, Suite],
    results_dir: str | Path,
    thresholds: dict[str, Any],
    *,
    target_stage: str | None = None,
    today: date | None = None,
) -> list[Finding]:
    """Check one use case's evaluation claims against its suites and committed evidence."""
    today = today or datetime.now(UTC).date()
    stage = target_stage or use_case["stage"]
    controls = use_case["controls"]
    slots: dict[str, list[str]] = use_case.get("evals", {})
    tier = use_case["tier"]
    gap = ERROR if stage in ("pilot", "production") else WARNING
    findings: list[Finding] = []

    def add(severity: str, rule: str, message: str) -> None:
        findings.append(Finding(severity, rule, message))

    def resolve(slot: str, kinds: tuple[str, ...], severity: str) -> list[Suite]:
        found: list[Suite] = []
        for suite_id in slots.get(slot, []):
            suite = suites.get(suite_id)
            if suite is None:
                add(
                    severity,
                    "eval-suite-missing",
                    f"evals.{slot} lists {suite_id}, which is not in the suites directory",
                )
            elif suite.use_case != use_case["id"]:
                add(ERROR, "eval-suite-scope", f"{suite_id} belongs to {suite.use_case}, not {use_case['id']}")
            elif suite.kind not in kinds:
                add(ERROR, "eval-suite-kind", f"{suite_id} is a {suite.kind} suite and cannot fill evals.{slot}")
            else:
                found.append(suite)
        return found

    def _problems(suite: Suite) -> list[str]:
        evidence = load_evidence(results_dir, suite.use_case, suite.id)
        return evidence_problems(suite, use_case, evidence, thresholds, today)

    def verify(suite: Suite, severity: str) -> bool:
        problems = _problems(suite)
        for problem in problems:
            add(severity, "eval-evidence", f"{suite.id}: {problem}")
        return not problems

    upgradable: list[str] = []
    for control, (slot, kinds) in CLAIMS.items():
        level = controls.get(control, "none")
        if level == "passing":
            found = resolve(slot, kinds, ERROR)
            if not slots.get(slot):
                add(ERROR, "eval-suite-missing", f"{control} is passing but evals.{slot} lists no suite")
            for suite in found:
                verify(suite, ERROR)
        elif level == "planned":
            if stage in ("pilot", "production") and not slots.get(slot):
                add(
                    gap,
                    "eval-suite-missing",
                    f"{control} is planned but evals.{slot} lists no suite; write the suite first",
                )
            found = resolve(slot, kinds, gap if stage in ("pilot", "production") else WARNING)
            if found and all(not _problems(suite) for suite in found):
                upgradable.append(control)

    # Retrieval-backed answers also need evidence that they stay grounded in what was retrieved.
    # A retrieval service is judged on retrieval quality and leakage; the answers built on it are someone else's.
    retrieval_only = any(suites[s].kind == "retrieval" for s in slots.get("golden", []) if s in suites)
    needs_grounding = (
        use_case.get("uses_retrieval")
        and not retrieval_only
        and _rank(tier) >= _rank("T2")
        and controls.get("golden_set_eval") == "passing"
    )
    if needs_grounding and not slots.get("grounding"):
        add(
            ERROR,
            "eval-grounding-required",
            f"{tier} use cases that retrieve need grounding evidence; list a suite in evals.grounding",
        )
    for suite in resolve("grounding", ("grounding",), gap if not needs_grounding else ERROR):
        if needs_grounding:
            verify(suite, ERROR)

    for control in upgradable:
        add(
            WARNING, "eval-evidence-supports-upgrade", f"evidence meets the thresholds; {control} can be set to passing"
        )

    if stage == "production":
        for suite_id in thresholds["platform"]["suites"]:
            suite = suites.get(suite_id)
            if suite is None:
                add(ERROR, "eval-platform", f"platform suite {suite_id} is not in the suites directory")
                continue
            for problem in evidence_problems(
                suite, None, load_evidence(results_dir, PLATFORM, suite.id), thresholds, today
            ):
                add(ERROR, "eval-platform", f"{suite_id}: {problem}")
    return findings
