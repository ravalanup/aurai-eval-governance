"""The ``aurai-governance evals`` and ``gate`` commands."""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from ..gate import check as gate_check
from ..policy import ERROR, WARNING, evaluate
from ..registry import find_use_case, load_registry
from .promptfoo import ImportError_, import_results, write_export
from .replay import VARIANTS, ReplayError, load_replay, problems, replay_path, replay_suite
from .results import LIVE, now_iso, to_evidence, write_evidence
from .suite import Suite, SuiteError, load_suites
from .thresholds import ThresholdError, load_thresholds

DEFAULT_SUITES = Path("evals/suites")
DEFAULT_REPLAY = Path("evals/replay")
DEFAULT_RESULTS = Path("evals/results")
DEFAULT_THRESHOLDS = Path("evals/thresholds.yaml")


def _suites(args: argparse.Namespace) -> dict[str, Suite] | None:
    try:
        return load_suites(args.suites)
    except SuiteError as exc:
        for message in exc.messages:
            print(f"{exc.path}: error: {message}", file=sys.stderr)
        return None


def _suite(args: argparse.Namespace) -> Suite | None:
    suites = _suites(args)
    if suites is None:
        return None
    if args.suite not in suites:
        print(f"error: no suite {args.suite!r} in {args.suites}", file=sys.stderr)
        return None
    return suites[args.suite]


def _tier_of(use_case_id: str, registry: Path) -> str | None:
    if use_case_id == "_platform":
        return None
    document = find_use_case(registry, use_case_id)
    if document is None:
        raise SystemExit(f"error: use case {use_case_id!r} not found or invalid in {registry}")
    return document["tier"]


def cmd_list(args: argparse.Namespace) -> int:
    suites = _suites(args)
    if suites is None:
        return 1
    for suite in suites.values():
        cases = len(suite.cases) if suite.kind != "retrieval" else "-"
        print(f"{suite.id:<34} {suite.kind:<10} {suite.use_case:<18} cases={cases} {suite.hash[:19]}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """CI check: suites are valid, every case has recordings, and the checks tell good output from bad."""
    suites = _suites(args)
    if suites is None:
        return 1
    failures = 0
    for suite in suites.values():
        if suite.kind == "retrieval":
            print(f"ok    {suite.id}: retrieval suite pinned to {suite.hash[:19]}")
            continue
        path = replay_path(args.replay, suite)
        if not path.is_file():
            print(f"error {suite.id}: no replay file at {path}")
            failures += 1
            continue
        replay = load_replay(path)
        gaps = problems(suite, replay)
        if gaps:
            failures += 1
            print(f"error {suite.id}: " + "; ".join(gaps))
            continue
        good = replay_suite(suite, replay, "pass")
        bad = replay_suite(suite, replay, "fail")
        undetected = [c.id for c in bad.cases if c.passed]
        if good.failures():
            failures += 1
            print(f"error {suite.id}: recorded passing outputs fail their own checks")
            print(good.render())
        elif undetected:
            failures += 1
            print(f"error {suite.id}: these cases cannot tell a bad output from a good one: {', '.join(undetected)}")
        else:
            print(f"ok    {suite.id}: {len(suite.cases)} cases, good outputs pass, bad outputs fail")
    print(f"{len(suites)} suite(s), {failures} problem(s)")
    return 1 if failures else 0


def cmd_replay(args: argparse.Namespace) -> int:
    suite = _suite(args)
    if suite is None:
        return 1
    try:
        result = replay_suite(suite, load_replay(replay_path(args.replay, suite)), args.variant)
    except (ReplayError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(result.render())
    print("replay results are never written as evidence")
    return 0 if not result.failures() else 1


def cmd_export(args: argparse.Namespace) -> int:
    suite = _suite(args)
    if suite is None:
        return 1
    replay = None
    if args.replay_mode:
        try:
            replay = load_replay(replay_path(args.replay, suite))
        except OSError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    target = write_export(suite, args.out, replay=replay, variant=args.variant)
    print(f"wrote {target}")
    if replay is None:
        print("run:  npx promptfoo@latest eval -c " + str(target) + " -o results.json")
        print("      (needs AURAI_GATEWAY_URL and AURAI_EVAL_KEY; see docs/evals.md)")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    suite = _suite(args)
    if suite is None:
        return 1
    try:
        data = json.loads(Path(args.file).read_text(encoding="utf-8"))
        result = import_results(suite, data, os.environ.get("AURAI_GATEWAY_URL"))
    except (ImportError_, json.JSONDecodeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    evidence = to_evidence(result, _tier_of(suite.use_case, args.registry))
    print(result.render())
    if result.mode != LIVE:
        print(f"note: this was a {result.mode!r} run; the gate only accepts live runs", file=sys.stderr)
    if args.dry_run:
        print(json.dumps(evidence, indent=2))
        return 0
    print(f"wrote {write_evidence(args.results, evidence)}")
    return 0


def cmd_import_retrieval(args: argparse.Namespace) -> int:
    suite = _suite(args)
    if suite is None:
        return 1
    if suite.kind != "retrieval":
        print(f"error: {suite.id} is a {suite.kind} suite, not a retrieval suite", file=sys.stderr)
        return 1
    try:
        report = json.loads(Path(args.file).read_text(encoding="utf-8"))
        digest = "sha256:" + report["golden_set_sha256"]
        stamp = report["executed_at"]
        metrics = {k: report[k] for k in ("cases", "answerable", "hit_rate", "mrr", "leaks", "top_k")}
    except (KeyError, json.JSONDecodeError, OSError) as exc:
        print(
            f"error: not a knowledge-service report with the expected fields ({exc!r}); re-run with --json",
            file=sys.stderr,
        )
        return 1
    if digest != suite.hash:
        print(f"error: the report ran golden set {digest}, but the suite pins {suite.hash}", file=sys.stderr)
        return 1
    evidence = {
        "schema": 1,
        "suite": suite.id,
        "kind": "retrieval",
        "use_case": suite.use_case,
        "suite_hash": suite.hash,
        "tier_at_run": _tier_of(suite.use_case, args.registry),
        "mode": LIVE if report.get("mode") == "live" else "offline",
        "executed_at": stamp,
        "model": "",
        "judge_model": "",
        "tool": "aurai-knowledge eval",
        "metrics": metrics,
        "failures": [
            {"case": "-", "assertion": "retrieval", "critical": False, "reason": r} for r in report.get("failures", [])
        ],
    }
    summary = f"hit_rate={metrics['hit_rate']} mrr={metrics['mrr']} leaks={metrics['leaks']}"
    print(f"{suite.id}: {summary} mode={evidence['mode']}")
    if evidence["mode"] != LIVE:
        print("note: an offline report proves the plumbing only; the gate only accepts live runs", file=sys.stderr)
    print(f"wrote {write_evidence(args.results, evidence)}")
    return 0


def _module_prompt(path: Path, name: str) -> str:
    """Read a module-level string (including simple f-strings over module constants) without importing it."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    constants = {
        t.id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
        for t in node.targets
        if isinstance(t, ast.Name)
    }

    def render(node: ast.AST) -> str:
        if isinstance(node, ast.Constant):
            return str(node.value)
        if isinstance(node, ast.JoinedStr):
            return "".join(render(v) for v in node.values)
        if isinstance(node, ast.FormattedValue):
            return str(constants[node.value.id])  # type: ignore[attr-defined]
        raise ValueError(f"cannot evaluate {type(node).__name__} without importing the module")

    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return render(node.value)
    raise ValueError(f"{name} not found in {path}")


def cmd_drift(args: argparse.Namespace) -> int:
    suite = _suite(args)
    if suite is None:
        return 1
    source = suite.raw.get("prompt_source") or {}
    symbol = source.get("symbol", "SYSTEM_PROMPT")
    try:
        live = _module_prompt(Path(args.source), symbol)
    except (OSError, ValueError, SyntaxError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if live.strip() == suite.system.strip():
        print(f"{suite.id}: the suite prompt matches {symbol} in {args.source}")
        return 0
    print(f"{suite.id}: the suite prompt differs from {symbol} in {args.source}; update the suite and re-run it live")
    return 1


def cmd_gate(args: argparse.Namespace) -> int:
    suites = _suites(args)
    if suites is None:
        return 1
    try:
        thresholds = load_thresholds(args.thresholds)
    except (ThresholdError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    entries = load_registry(args.location)
    if not entries:
        print(f"error: no use case files found in {args.location}", file=sys.stderr)
        return 1
    today = date.fromisoformat(args.today) if args.today else datetime.now(UTC).date()

    errors = warnings = checked = 0
    for entry in entries:
        if entry.schema_errors:
            for message in entry.schema_errors:
                print(f"{entry.path}: error: [schema] {message}")
                errors += 1
            continue
        document = entry.document
        if args.use_case and document["id"] != args.use_case:
            continue
        checked += 1
        target = args.target_stage
        findings = gate_check(document, suites, args.results, thresholds, target_stage=target, today=today)
        if target:
            findings = evaluate({**document, "stage": target}) + findings
        label = f"{entry.path}" + (f" -> {target}" if target else "")
        if not findings:
            print(f"{label}: ok")
        for finding in findings:
            print(f"{label}: {finding}")
            errors += finding.severity == ERROR
            warnings += finding.severity == WARNING
    if checked == 0:
        print(f"error: use case {args.use_case!r} not found", file=sys.stderr)
        return 1
    print(f"{checked} use case(s): {errors} error(s), {warnings} warning(s)")
    return 1 if errors or (args.strict and warnings) else 0


def _paths(p: argparse.ArgumentParser, *, replay: bool = False, results: bool = False) -> None:
    p.add_argument("--suites", type=Path, default=DEFAULT_SUITES)
    if replay:
        p.add_argument("--replay", type=Path, default=DEFAULT_REPLAY)
    if results:
        p.add_argument("--results", type=Path, default=DEFAULT_RESULTS)


def register(sub: Any) -> None:
    gate = sub.add_parser("gate", help="check evaluation claims against committed evidence (EV-3)")
    gate.add_argument("location", type=Path, nargs="?", default=Path("use-cases"))
    gate.add_argument("--use-case", help="check one use case")
    gate.add_argument(
        "--target-stage", choices=["sandbox", "pilot", "production"], help="check promotion to this stage"
    )
    gate.add_argument("--thresholds", type=Path, default=DEFAULT_THRESHOLDS)
    gate.add_argument("--strict", action="store_true", help="treat warnings as errors")
    gate.add_argument("--today", help="override today's date (YYYY-MM-DD), for tests")
    _paths(gate, results=True)
    gate.set_defaults(func=cmd_gate)

    evals = sub.add_parser("evals", help="evaluation suites: check, replay, export to Promptfoo, import evidence")
    inner = evals.add_subparsers(dest="evals_command", required=True)

    p = inner.add_parser("list", help="list suites")
    _paths(p)
    p.set_defaults(func=cmd_list)

    p = inner.add_parser("check", help="validate suites and prove their checks with recorded outputs (CI)")
    _paths(p, replay=True)
    p.set_defaults(func=cmd_check)

    p = inner.add_parser("replay", help="run a suite against recorded outputs; never writes evidence")
    p.add_argument("suite")
    p.add_argument("--variant", choices=VARIANTS, default="pass")
    _paths(p, replay=True)
    p.set_defaults(func=cmd_replay)

    p = inner.add_parser("export", help="write a Promptfoo config for a suite")
    p.add_argument("suite")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--replay-mode", action="store_true", help="use recorded outputs instead of the gateway")
    p.add_argument("--variant", choices=VARIANTS, default="pass")
    _paths(p, replay=True)
    p.set_defaults(func=cmd_export)

    p = inner.add_parser("import", help="turn Promptfoo output JSON into an evidence file")
    p.add_argument("file", type=Path)
    p.add_argument("--suite", required=True)
    p.add_argument("--registry", type=Path, default=Path("use-cases"))
    p.add_argument("--dry-run", action="store_true")
    _paths(p, results=True)
    p.set_defaults(func=cmd_import)

    p = inner.add_parser("import-retrieval", help="turn an `aurai-knowledge eval --json` report into evidence")
    p.add_argument("file", type=Path)
    p.add_argument("--suite", required=True)
    p.add_argument("--registry", type=Path, default=Path("use-cases"))
    _paths(p, results=True)
    p.set_defaults(func=cmd_import_retrieval)

    p = inner.add_parser("drift", help="compare a suite's prompt with the prompt in the agent source")
    p.add_argument("suite")
    p.add_argument("--source", type=Path, required=True)
    _paths(p)
    p.set_defaults(func=cmd_drift)


__all__ = ["register", "now_iso"]
