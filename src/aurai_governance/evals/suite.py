"""Evaluation suites as data: load, validate and fingerprint them.

A suite is one YAML file. It is the single source for three things: the Promptfoo config used for live runs,
the offline replay that regression-tests the checks in CI, and the SHA-256 that ties evidence to the exact
suite that produced it.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

KINDS = ("golden", "red_team", "grounding", "platform", "retrieval")

# The assertion vocabulary is the part of Promptfoo's that this repository checks itself, so that a result
# means the same thing in a live run and in a replay. Any type except llm-rubric may be negated with "not-".
BASE_TYPES = ("contains", "icontains", "contains-any", "icontains-any", "regex", "is-json")
NEGATABLE = BASE_TYPES
RUBRIC = "llm-rubric"
ASSERTION_TYPES = (*BASE_TYPES, *(f"not-{t}" for t in NEGATABLE if t != "is-json"), RUBRIC)

# Regex features that JavaScript (Promptfoo) and Python read differently. Patterns must avoid them.
_REGEX_UNSAFE = re.compile(r"\(\?<|\(\?P|\(\?[a-z]+[:)]|\\A|\\Z")
# Anything in a prompt that nunjucks would treat as template syntax, other than a declared {{variable}}.
_TEMPLATE_SYNTAX = re.compile(r"\{%|\{#")
_VARIABLE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")

# Fields that describe a suite without changing what it measures. They are left out of the hash.
_UNHASHED = ("description", "notes")


class SuiteError(ValueError):
    def __init__(self, path: Path | str, messages: list[str]):
        self.path = str(path)
        self.messages = messages
        super().__init__(f"{path}: " + "; ".join(messages))


@dataclass(frozen=True)
class Assertion:
    type: str
    value: Any = None
    critical: bool = False

    @property
    def negated(self) -> bool:
        return self.type.startswith("not-")

    @property
    def base(self) -> str:
        return self.type[4:] if self.negated else self.type

    def to_promptfoo(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type}
        if self.value is not None:
            out["value"] = self.value
        if self.critical:
            out["metric"] = "critical"
        return out


@dataclass(frozen=True)
class Case:
    id: str
    assertions: tuple[Assertion, ...]
    vars: dict[str, str] = field(default_factory=dict)
    description: str = ""


@dataclass(frozen=True)
class Suite:
    id: str
    kind: str
    use_case: str
    cases: tuple[Case, ...]
    path: Path
    raw: dict[str, Any]
    model: str = ""
    judge_model: str = ""
    system: str = ""
    user: str = ""
    description: str = ""

    @property
    def hash(self) -> str:
        if self.kind == "retrieval":
            # The evidence comes from the knowledge service, so what identifies it is the golden set it ran.
            return "sha256:" + self.raw["retrieval"]["golden_set_sha256"]
        return suite_hash(self.raw)

    @property
    def has_rubric(self) -> bool:
        return any(a.type == RUBRIC for c in self.cases for a in c.assertions)

    @property
    def retrieval(self) -> dict[str, Any] | None:
        return self.raw.get("retrieval")


def suite_hash(raw: dict[str, Any]) -> str:
    material = copy.deepcopy({k: v for k, v in raw.items() if k not in _UNHASHED})
    for case in material.get("cases", []):
        case.pop("description", None)  # per-case descriptions are prose too
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _schema() -> dict[str, Any]:
    text = resources.files("aurai_governance").joinpath("schemas/eval-suite.schema.json").read_text(encoding="utf-8")
    return json.loads(text)


def _check_assertion(where: str, a: dict[str, Any], errors: list[str]) -> None:
    kind = a["type"]
    value = a.get("value")
    if kind not in ASSERTION_TYPES:
        errors.append(f"{where}: unsupported assertion type {kind!r} (use one of {', '.join(ASSERTION_TYPES)})")
        return
    base = kind[4:] if kind.startswith("not-") else kind
    if base in ("contains", "icontains"):
        if not isinstance(value, str) or not value:
            errors.append(f"{where}: {kind} needs a non-empty string value")
    elif base in ("contains-any", "icontains-any"):
        if not (isinstance(value, list) and value and all(isinstance(v, str) and v for v in value)):
            errors.append(f"{where}: {kind} needs a non-empty list of strings")
    elif base == "regex":
        if not isinstance(value, str) or not value:
            errors.append(f"{where}: {kind} needs a pattern string")
        elif _REGEX_UNSAFE.search(value):
            errors.append(f"{where}: pattern uses syntax JavaScript and Python read differently")
        else:
            try:
                re.compile(value)
            except re.error as exc:
                errors.append(f"{where}: invalid pattern: {exc}")
    elif base == "is-json":
        if value is not None:
            if not isinstance(value, dict):
                errors.append(f"{where}: is-json value must be a JSON Schema object")
            else:
                try:
                    Draft202012Validator.check_schema(value)
                except Exception as exc:  # jsonschema raises SchemaError
                    errors.append(f"{where}: invalid JSON Schema: {exc}")
    elif base == RUBRIC:
        if not isinstance(value, str) or len(value) < 20:
            errors.append(f"{where}: llm-rubric needs a rubric of at least a sentence")


def validate_document(doc: Any) -> list[str]:
    if not isinstance(doc, dict):
        return ["(root): a suite must be a mapping"]
    validator = Draft202012Validator(_schema())
    errors = [
        f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}"
        for e in sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path))
    ]
    if errors:
        return errors

    kind = doc["kind"]
    if (doc["use_case"] == "_platform") != (kind == "platform"):
        errors.append("use_case: platform suites use _platform, and only platform suites do")
    if kind == "retrieval":
        if "retrieval" not in doc:
            errors.append("retrieval: a retrieval suite must pin its golden set")
        if doc["cases"]:
            errors.append("cases: a retrieval suite has no cases here; the knowledge service runs its own golden set")
        return errors

    if "retrieval" in doc:
        errors.append("retrieval: only retrieval suites pin a golden set")
    if not doc["cases"]:
        errors.append("cases: at least one case is required")
    if "prompt" not in doc:
        errors.append("prompt: required for every suite except retrieval")
    if "model" not in doc:
        errors.append("model: the gateway alias a live run calls is required")

    prompt = doc.get("prompt", {})
    text = "\n".join(prompt.get(k, "") for k in ("system", "user"))
    if _TEMPLATE_SYNTAX.search(text):
        errors.append("prompt: contains template syntax ({% or {#) that Promptfoo would interpret")
    declared = set(_VARIABLE.findall(text))
    if "{{" in _VARIABLE.sub("", text):
        errors.append("prompt: contains '{{' that is not a plain {{variable}}")

    seen: set[str] = set()
    has_rubric = False
    for index, case in enumerate(doc["cases"]):
        where = f"cases/{index} ({case['id']})"
        if case["id"] in seen:
            errors.append(f"{where}: duplicate case id")
        seen.add(case["id"])
        missing = declared - set(case.get("vars", {}))
        if missing:
            errors.append(f"{where}: missing variable(s) {', '.join(sorted(missing))}")
        for a_index, assertion in enumerate(case["assert"]):
            _check_assertion(f"{where} assert/{a_index}", assertion, errors)
            has_rubric = has_rubric or assertion["type"] == RUBRIC
    if has_rubric and not doc.get("judge_model"):
        errors.append("judge_model: required when any assertion is an llm-rubric")
    if kind == "red_team" and not any(a.get("critical") for c in doc["cases"] for a in c["assert"]):
        errors.append("cases: a red-team suite needs at least one critical assertion")
    return errors


def load_suite(path: str | Path) -> Suite:
    path = Path(path)
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SuiteError(path, [f"invalid YAML: {exc}"]) from exc
    errors = validate_document(doc)
    if errors:
        raise SuiteError(path, errors)
    if doc["id"] != path.stem:
        raise SuiteError(path, [f"id: {doc['id']!r} must match the file name {path.stem!r}"])
    prompt = doc.get("prompt", {})
    cases = tuple(
        Case(
            id=c["id"],
            description=c.get("description", ""),
            vars=dict(c.get("vars", {})),
            assertions=tuple(Assertion(a["type"], a.get("value"), bool(a.get("critical", False))) for a in c["assert"]),
        )
        for c in doc["cases"]
    )
    return Suite(
        id=doc["id"],
        kind=doc["kind"],
        use_case=doc["use_case"],
        cases=cases,
        path=path,
        raw=doc,
        model=doc.get("model", ""),
        judge_model=doc.get("judge_model", ""),
        system=prompt.get("system", ""),
        user=prompt.get("user", ""),
        description=doc.get("description", ""),
    )


def load_suites(directory: str | Path) -> dict[str, Suite]:
    """Load every suite in a directory. Raises SuiteError on the first invalid one."""
    suites: dict[str, Suite] = {}
    for path in sorted(Path(directory).glob("*.yaml")):
        suite = load_suite(path)
        suites[suite.id] = suite
    return suites
