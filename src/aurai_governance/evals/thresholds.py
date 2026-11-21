"""Per-tier thresholds that evidence has to meet. They live in evals/thresholds.yaml, so changes are reviewed."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

TIER_ORDER = ("T0", "T1", "T2", "T3")
KINDS = ("golden", "red_team", "grounding", "retrieval")


class ThresholdError(ValueError):
    pass


def load_thresholds(path: str | Path) -> dict[str, Any]:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ThresholdError(f"{path}: expected a mapping")
    for kind in KINDS:
        table = doc.get(kind)
        if not isinstance(table, dict) or not table:
            raise ThresholdError(f"{path}: no thresholds for {kind}")
        for tier, values in table.items():
            if tier not in TIER_ORDER or not isinstance(values, dict) or "max_age_days" not in values:
                raise ThresholdError(f"{path}: {kind}.{tier} must be a tier with max_age_days")
    platform = doc.get("platform")
    if not isinstance(platform, dict) or not platform.get("suites"):
        raise ThresholdError(f"{path}: platform.suites is required")
    return doc


def for_tier(doc: dict[str, Any], kind: str, tier: str) -> tuple[str, dict[str, Any]]:
    """Thresholds for a kind at a tier. A tier without its own row uses the nearest stricter one."""
    table = doc[kind]
    for candidate in TIER_ORDER[TIER_ORDER.index(tier) :]:
        if candidate in table:
            return candidate, table[candidate]
    raise ThresholdError(f"no {kind} thresholds at or above {tier}")
