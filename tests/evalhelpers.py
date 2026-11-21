"""Helpers for evaluation tests: the real suites, plus synthetic evidence shaped like what an import writes."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aurai_governance.evals.suite import load_suites
from aurai_governance.evals.thresholds import load_thresholds

ROOT = Path(__file__).resolve().parents[1]
SUITES_DIR = ROOT / "evals" / "suites"
REPLAY_DIR = ROOT / "evals" / "replay"
THRESHOLDS = ROOT / "evals" / "thresholds.yaml"
TODAY = datetime(2026, 11, 21, tzinfo=UTC).date()


def suites():
    return load_suites(SUITES_DIR)


def thresholds():
    return load_thresholds(THRESHOLDS)


def stamp(days_ago: int = 1) -> str:
    moment = datetime(2026, 11, 21, 12, 0, tzinfo=UTC) - timedelta(days=days_ago)
    return moment.isoformat().replace("+00:00", "Z")


def evidence(suite, *, tier="T2", model="aurai-balanced", days_ago=1, **overrides):
    """Evidence that meets every threshold, with named fields overridden for one scenario."""
    doc = {
        "schema": 1,
        "suite": suite.id,
        "kind": suite.kind,
        "use_case": suite.use_case,
        "suite_hash": suite.hash,
        "tier_at_run": tier,
        "mode": "live",
        "executed_at": stamp(days_ago),
        "model": model,
        "judge_model": "",
        "tool": "promptfoo 0.126.0",
        "totals": {
            "cases": len(suite.cases),
            "passed": len(suite.cases),
            "pass_rate": 1.0,
            "critical_failures": 0,
            "not_evaluated": 0,
        },
        "failures": [],
    }
    if suite.kind == "retrieval":
        doc["totals"] = None
        doc["metrics"] = {"cases": 28, "answerable": 21, "hit_rate": 0.95, "mrr": 0.8, "leaks": 0, "top_k": 5}
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(doc.get(key), dict):
            doc[key] = {**doc[key], **value}
        else:
            doc[key] = value
    return copy.deepcopy(doc)


def save(results_dir: Path, doc: dict) -> Path:
    path = results_dir / doc["use_case"] / f"{doc['suite']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path
