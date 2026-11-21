"""Run results and the evidence files the promotion gate reads."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .checks import FAIL, NOT_EVALUATED, PASS, run_check
from .suite import Suite

EVIDENCE_SCHEMA = 1
LIVE = "live"  # the only mode the gate accepts
MODES = (LIVE, "replay", "offline", "other")


@dataclass
class AssertionResult:
    type: str
    status: str
    critical: bool = False
    reason: str = ""


@dataclass
class CaseResult:
    id: str
    assertions: list[AssertionResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(a.status != FAIL for a in self.assertions)

    @property
    def critical_failed(self) -> bool:
        return any(a.status == FAIL and a.critical for a in self.assertions)


@dataclass
class RunResult:
    suite: Suite
    mode: str
    cases: list[CaseResult]
    executed_at: str
    model: str = ""
    judge_model: str = ""
    tool: str = ""

    @property
    def passed(self) -> int:
        return sum(1 for c in self.cases if c.passed)

    @property
    def pass_rate(self) -> float:
        return self.passed / len(self.cases) if self.cases else 0.0

    @property
    def critical_failures(self) -> int:
        return sum(1 for c in self.cases if c.critical_failed)

    @property
    def not_evaluated(self) -> int:
        return sum(1 for c in self.cases for a in c.assertions if a.status == NOT_EVALUATED)

    def failures(self) -> list[dict[str, Any]]:
        return [
            {"case": c.id, "assertion": a.type, "critical": a.critical, "reason": a.reason}
            for c in self.cases
            for a in c.assertions
            if a.status == FAIL
        ]

    def render(self) -> str:
        head = (
            f"{self.suite.id} [{self.suite.kind}] mode={self.mode}: {self.passed}/{len(self.cases)} cases pass "
            f"({self.pass_rate:.0%}), {self.critical_failures} critical failure(s)"
        )
        if self.not_evaluated:
            head += f", {self.not_evaluated} rubric assertion(s) not evaluated"
        lines = [head]
        for f in self.failures():
            flag = " CRITICAL" if f["critical"] else ""
            lines.append(f"  FAIL{flag} {f['case']}: {f['assertion']}: {f['reason']}")
        return "\n".join(lines)


def check_case(suite: Suite, case_id: str, output: str) -> CaseResult:
    case = next(c for c in suite.cases if c.id == case_id)
    result = CaseResult(case.id)
    for assertion in case.assertions:
        outcome = run_check(assertion, output)
        result.assertions.append(AssertionResult(assertion.type, outcome.status, assertion.critical, outcome.reason))
    return result


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def to_evidence(result: RunResult, tier: str | None) -> dict[str, Any]:
    suite = result.suite
    return {
        "schema": EVIDENCE_SCHEMA,
        "suite": suite.id,
        "kind": suite.kind,
        "use_case": suite.use_case,
        "suite_hash": suite.hash,
        "tier_at_run": tier,
        "mode": result.mode,
        "executed_at": result.executed_at,
        "model": result.model,
        "judge_model": result.judge_model,
        "tool": result.tool,
        "totals": {
            "cases": len(result.cases),
            "passed": result.passed,
            "pass_rate": round(result.pass_rate, 4),
            "critical_failures": result.critical_failures,
            "not_evaluated": result.not_evaluated,
        },
        "failures": result.failures(),
    }


def evidence_path(results_dir: str | Path, use_case: str, suite_id: str) -> Path:
    return Path(results_dir) / use_case / f"{suite_id}.json"


def write_evidence(results_dir: str | Path, evidence: dict[str, Any]) -> Path:
    path = evidence_path(results_dir, evidence["use_case"], evidence["suite"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_evidence(results_dir: str | Path, use_case: str, suite_id: str) -> dict[str, Any] | None:
    path = evidence_path(results_dir, use_case, suite_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"_invalid": "not valid JSON"}
    return data if isinstance(data, dict) else {"_invalid": "not a JSON object"}


__all__ = ["PASS", "FAIL", "NOT_EVALUATED"]
