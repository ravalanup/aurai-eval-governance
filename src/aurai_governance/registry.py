"""Load the use-case registry and validate documents against the JSON Schema."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator


def load_schema() -> dict[str, Any]:
    text = resources.files("aurai_governance").joinpath("schemas/use-case.schema.json").read_text(encoding="utf-8")
    return json.loads(text)


@dataclass
class Entry:
    path: Path
    document: dict[str, Any]
    schema_errors: list[str]


def schema_errors(document: Any) -> list[str]:
    validator = Draft202012Validator(load_schema())
    return [
        f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}"
        for e in sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path))
    ]


def load_entry(path: Path) -> Entry:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return Entry(path, {}, [f"(root): invalid YAML: {exc}"])
    return Entry(path, document if isinstance(document, dict) else {}, schema_errors(document))


def load_registry(location: str | Path) -> list[Entry]:
    """Load one file, or every *.yaml / *.yml file in a directory."""
    path = Path(location)
    files = [path] if path.is_file() else sorted([*path.glob("*.yaml"), *path.glob("*.yml")])
    return [load_entry(f) for f in files]


def find_use_case(location: str | Path, use_case_id: str) -> dict[str, Any] | None:
    for entry in load_registry(location):
        if not entry.schema_errors and entry.document.get("id") == use_case_id:
            return entry.document
    return None


def duplicate_ids(entries: list[Entry]) -> list[str]:
    seen: dict[str, Path] = {}
    errors = []
    for entry in entries:
        use_case_id = entry.document.get("id")
        if not use_case_id:
            continue
        if use_case_id in seen:
            errors.append(f"duplicate use case id {use_case_id!r} in {entry.path} and {seen[use_case_id]}")
        seen[use_case_id] = entry.path
    return errors
