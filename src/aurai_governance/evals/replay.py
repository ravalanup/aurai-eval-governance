"""Offline replay: apply a suite's checks to recorded outputs.

Replay proves the checks and the gate logic work. It does not prove anything about a model, so its results are
labelled ``replay`` and the promotion gate never accepts them as evidence.

A replay file maps each case to two recorded outputs: ``pass`` (an output a correct system would give) and
``fail`` (an output that falls for the case). Running both shows that every case can tell good from bad.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .results import CaseResult, RunResult, check_case, now_iso
from .suite import Suite

VARIANTS = ("pass", "fail")


class ReplayError(ValueError):
    pass


def load_replay(path: str | Path) -> dict[str, Any]:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or not isinstance(doc.get("outputs"), dict):
        raise ReplayError(f"{path}: expected a mapping with an 'outputs' mapping")
    return doc


def replay_path(replay_dir: str | Path, suite: Suite) -> Path:
    return Path(replay_dir) / f"{suite.id}.yaml"


def problems(suite: Suite, replay: dict[str, Any]) -> list[str]:
    """Coverage problems: cases without recordings, recordings without cases."""
    out: list[str] = []
    if replay.get("suite") != suite.id:
        out.append(f"replay file is for {replay.get('suite')!r}, not {suite.id!r}")
    case_ids = {c.id for c in suite.cases}
    recorded = replay["outputs"]
    for case_id in sorted(case_ids - set(recorded)):
        out.append(f"case {case_id}: no recorded output")
    for case_id in sorted(set(recorded) - case_ids):
        out.append(f"recorded output for unknown case {case_id}")
    for case_id, variants in recorded.items():
        for variant in VARIANTS:
            if not isinstance(variants, dict) or not isinstance(variants.get(variant), str):
                out.append(f"case {case_id}: missing a recorded '{variant}' output")
    return out


def replay_suite(suite: Suite, replay: dict[str, Any], variant: str = "pass") -> RunResult:
    if variant not in VARIANTS:
        raise ReplayError(f"variant must be one of {VARIANTS}")
    found = problems(suite, replay)
    if found:
        raise ReplayError("; ".join(found))
    cases: list[CaseResult] = [check_case(suite, c.id, replay["outputs"][c.id][variant]) for c in suite.cases]
    return RunResult(suite=suite, mode="replay", cases=cases, executed_at=now_iso(), model="recorded", tool="replay")
