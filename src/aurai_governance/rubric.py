"""Risk-tier rubric: classification answers in, minimum tier out.

Implements the five classification questions from aurai-docs (governance/risk-tiers.md). The platform
interprets "moves up a tier" as a minimum tier per question:

  Q1 data above "internal"              confidential -> T2, restricted -> T3
  Q2 actions in other systems           write -> T2
  Q3 output reaches outside the company external audience -> T3
  Q4 error could affect safety, money, legal standing or employment   material -> T3
  Q5 licensed or protected creative IP  yes -> T3

The base tier is T1 for use cases that retrieve internal knowledge, otherwise T0. The minimum tier is the
highest of the base and every triggered question. A declared tier below it is a policy error; a higher
declared tier is allowed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

TIERS = ("T0", "T1", "T2", "T3")


def tier_rank(tier: str) -> int:
    return TIERS.index(tier)


def max_tier(*tiers: str) -> str:
    return max(tiers, key=tier_rank)


@dataclass(frozen=True)
class QuestionResult:
    id: str
    question: str
    triggered: bool
    floor: str | None
    reason: str


@dataclass(frozen=True)
class Classification:
    base_tier: str
    min_tier: str
    questions: tuple[QuestionResult, ...]

    def explain(self) -> str:
        lines = [f"Base tier: {self.base_tier}"]
        for q in self.questions:
            mark = f"yes -> at least {q.floor}" if q.triggered else "no"
            lines.append(f"  {q.id} {q.question}: {mark} ({q.reason})")
        lines.append(f"Minimum tier: {self.min_tier}")
        return "\n".join(lines)


def classify(use_case: dict[str, Any]) -> Classification:
    data = use_case.get("data_classification", "internal")
    actions = use_case.get("actions", "none")
    audience = use_case.get("audience", "individual")
    impact = use_case.get("error_impact", "none")
    ip = bool(use_case.get("uses_protected_ip", False))

    q1_floor = {"confidential": "T2", "restricted": "T3"}.get(data)
    results = (
        QuestionResult(
            "Q1", "Uses data classified above internal?", q1_floor is not None, q1_floor, f"data_classification={data}"
        ),
        QuestionResult(
            "Q2",
            "Takes actions in other systems?",
            actions == "write",
            "T2" if actions == "write" else None,
            f"actions={actions}",
        ),
        QuestionResult(
            "Q3",
            "Output reaches people outside the company?",
            audience == "external",
            "T3" if audience == "external" else None,
            f"audience={audience}",
        ),
        QuestionResult(
            "Q4",
            "Could an error affect safety, money, legal standing or employment?",
            impact == "material",
            "T3" if impact == "material" else None,
            f"error_impact={impact}",
        ),
        QuestionResult(
            "Q5", "Uses licensed or protected creative IP?", ip, "T3" if ip else None, f"uses_protected_ip={ip}"
        ),
    )
    base = "T1" if use_case.get("uses_retrieval") else "T0"
    floors = [q.floor for q in results if q.triggered and q.floor]
    return Classification(base_tier=base, min_tier=max_tier(base, *floors), questions=results)
