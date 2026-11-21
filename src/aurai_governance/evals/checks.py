"""Deterministic assertion checks. These mirror Promptfoo's assertion types of the same name."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from jsonschema import Draft7Validator

from .suite import RUBRIC, Assertion

PASS = "pass"
FAIL = "fail"
NOT_EVALUATED = "not_evaluated"


@dataclass(frozen=True)
class CheckResult:
    status: str
    reason: str = ""


def _json_schema_errors(output: str, schema: dict | None) -> str | None:
    try:
        parsed = json.loads(output)
    except (TypeError, ValueError) as exc:
        return f"output is not valid JSON ({exc})"
    if schema is None:
        return None
    errors = sorted(Draft7Validator(schema).iter_errors(parsed), key=lambda e: list(e.absolute_path))
    if errors:
        first = errors[0]
        where = "/".join(str(p) for p in first.absolute_path) or "(root)"
        return f"JSON does not match the schema at {where}: {first.message}"
    return None


def _positive(base: str, value, output: str) -> tuple[bool, str]:
    """Return whether the un-negated condition holds, with a reason that reads right for the failure."""
    if base == "contains":
        return value in output, f"output does not contain {value!r}"
    if base == "icontains":
        return value.casefold() in output.casefold(), f"output does not contain {value!r} (ignoring case)"
    if base == "contains-any":
        return any(v in output for v in value), f"output contains none of {value!r}"
    if base == "icontains-any":
        lowered = output.casefold()
        return any(v.casefold() in lowered for v in value), f"output contains none of {value!r} (ignoring case)"
    if base == "regex":
        return re.search(value, output) is not None, f"output does not match /{value}/"
    raise ValueError(f"unsupported assertion {base!r}")


def run_check(assertion: Assertion, output: str) -> CheckResult:
    """Apply one assertion to one model output. llm-rubric is not evaluated here; a judge model grades it live."""
    if assertion.type == RUBRIC:
        return CheckResult(NOT_EVALUATED, "needs a judge model (live run)")
    if assertion.base == "is-json":
        problem = _json_schema_errors(output, assertion.value)
        return CheckResult(PASS) if problem is None else CheckResult(FAIL, problem)

    holds, reason = _positive(assertion.base, assertion.value, output)
    if assertion.negated:
        return CheckResult(FAIL, _negated_reason(assertion, output)) if holds else CheckResult(PASS)
    return CheckResult(PASS) if holds else CheckResult(FAIL, reason)


def _negated_reason(assertion: Assertion, output: str) -> str:
    base, value = assertion.base, assertion.value
    if base in ("contains", "icontains"):
        return f"output contains {value!r}"
    if base == "regex":
        match = re.search(value, output)
        return f"output matches /{value}/ at {match.group(0)!r}" if match else f"output matches /{value}/"
    return f"output contains one of {value!r}"
