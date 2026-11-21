"""aurai-governance command line.

  aurai-governance validate use-cases/                 schema + policy checks (CI)
  aurai-governance validate use-cases/ --catalog ../aurai-gateway/config/model-catalog.yaml
  aurai-governance classify use-cases/ops-insights.yaml  explain the rubric result
  aurai-governance requirements T2                     list required controls for a tier
  aurai-governance gate use-cases/                     evaluation claims vs committed evidence (EV-3)
  aurai-governance gate use-cases/ --target-stage production   would this promotion pass?
  aurai-governance evals check                         validate suites and prove their checks (CI)
  aurai-governance evals export ops-insights-golden --out out/    Promptfoo config for a live run
  aurai-governance evals import results.json --suite ops-insights-golden    evidence from a Promptfoo run
  aurai-governance pause ops-insights --registry use-cases/ --base-url http://localhost:4000
  aurai-governance resume ops-insights --registry use-cases/ --base-url http://localhost:4000

The gateway admin key is read from the environment variable named by --admin-key-env
(default LITELLM_MASTER_KEY), never from the command line.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

from .evals import commands as eval_commands
from .pause import PauseError, pause_team, resume_team
from .policy import ERROR, REQUIREMENTS, WARNING, check_models, evaluate
from .registry import duplicate_ids, find_use_case, load_registry
from .rubric import classify


def cmd_validate(args: argparse.Namespace) -> int:
    entries = load_registry(args.location)
    if not entries:
        print(f"error: no use case files found in {args.location}", file=sys.stderr)
        return 1

    errors = warnings = 0
    for entry in entries:
        label = str(entry.path)
        if entry.schema_errors:
            for message in entry.schema_errors:
                print(f"{label}: error: [schema] {message}")
                errors += 1
            continue
        findings = evaluate(entry.document)
        if args.catalog:
            findings += check_models(entry.document, args.catalog)
        for finding in findings:
            print(f"{label}: {finding}")
            if finding.severity == ERROR:
                errors += 1
            elif finding.severity == WARNING:
                warnings += 1

    for message in duplicate_ids(entries):
        print(f"error: {message}")
        errors += 1

    failed = errors > 0 or (args.strict and warnings > 0)
    print(f"{len(entries)} use case(s): {errors} error(s), {warnings} warning(s)")
    return 1 if failed else 0


def cmd_classify(args: argparse.Namespace) -> int:
    entries = load_registry(args.file)
    if len(entries) != 1 or entries[0].schema_errors:
        print("error: provide one valid use case file", file=sys.stderr)
        return 1
    document = entries[0].document
    result = classify(document)
    print(result.explain())
    print(f"Declared tier: {document['tier']}")
    return 0


def cmd_requirements(args: argparse.Namespace) -> int:
    req = REQUIREMENTS[args.tier]
    print(f"{args.tier} requirements")
    print("  always: tracing, PII masking, gateway access, named owner")
    print(f"  golden-set evaluation: {req['golden'] or 'optional'}")
    print(f"  red-team testing: {req['red_team'] or 'optional'}")
    print(f"  audit log retention: {req['audit']}")
    print(f"  monitoring: {req['monitoring']}")
    print(f"  human approval for writes: {req['approval_for_writes'] or 'not required'}")
    print(f"  reviews: {', '.join(req['reviews']) or 'none'}")
    return 0


def _client(args: argparse.Namespace) -> httpx.Client:
    key = os.environ.get(args.admin_key_env)
    if not key:
        raise SystemExit(f"error: set {args.admin_key_env} to the gateway admin key")
    return httpx.Client(base_url=args.base_url.rstrip("/"), headers={"Authorization": f"Bearer {key}"}, timeout=30.0)


def _pause_or_resume(args: argparse.Namespace, action) -> int:
    document = find_use_case(args.registry, args.use_case_id)
    if document is None:
        print(f"error: use case {args.use_case_id!r} not found or invalid in {args.registry}", file=sys.stderr)
        return 1
    try:
        with _client(args) as client:
            action(client, document["gateway_team_id"])
    except PauseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{action.__name__.split('_')[0]}d {args.use_case_id} (gateway team {document['gateway_team_id']})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aurai-governance", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate", help="validate use cases against the schema and policy")
    p.add_argument("location", type=Path)
    p.add_argument("--catalog", type=Path, help="gateway model catalog to check model approvals against")
    p.add_argument("--strict", action="store_true", help="treat warnings as errors")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("classify", help="explain the rubric result for a use case")
    p.add_argument("file", type=Path)
    p.set_defaults(func=cmd_classify)

    p = sub.add_parser("requirements", help="show required controls for a tier")
    p.add_argument("tier", choices=sorted(REQUIREMENTS))
    p.set_defaults(func=cmd_requirements)

    for name, action in (("pause", pause_team), ("resume", resume_team)):
        p = sub.add_parser(name, help=f"{name} a use case by changing its gateway team's status")
        p.add_argument("use_case_id")
        p.add_argument("--registry", type=Path, default=Path("use-cases"))
        p.add_argument("--base-url", default="http://localhost:4000")
        p.add_argument("--admin-key-env", default="LITELLM_MASTER_KEY")
        p.set_defaults(func=lambda a, action=action: _pause_or_resume(a, action))

    eval_commands.register(sub)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
