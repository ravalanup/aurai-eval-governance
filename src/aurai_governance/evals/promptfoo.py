"""Export a suite to a Promptfoo config and import Promptfoo's output as evidence.

Live runs go through the gateway like any other client: the provider is an OpenAI-compatible endpoint at
``$AURAI_GATEWAY_URL/v1`` using a key for the evaluation team, so evaluation traffic is traced, masked and
budgeted like production traffic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .checks import FAIL, NOT_EVALUATED, PASS
from .results import LIVE, AssertionResult, CaseResult, RunResult, parse_time
from .suite import RUBRIC, Suite

GATEWAY_URL_ENV = "AURAI_GATEWAY_URL"
GATEWAY_KEY_ENV = "AURAI_EVAL_KEY"
REPLAY_PROVIDER_FILE = "replay_provider.py"


class ImportError_(ValueError):  # noqa: N818  (avoids shadowing the builtin ImportError)
    pass


def _gateway_provider(model: str) -> dict[str, Any]:
    return {
        "id": f"openai:chat:{model}",
        "config": {"apiBaseUrl": "{{env." + GATEWAY_URL_ENV + "}}/v1", "apiKeyEnvar": GATEWAY_KEY_ENV},
    }


def chat_prompt(suite: Suite) -> str:
    messages = []
    if suite.system:
        messages.append({"role": "system", "content": suite.system})
    messages.append({"role": "user", "content": suite.user})
    return json.dumps(messages, ensure_ascii=False)


def export_config(suite: Suite, *, replay_file: str | None = None, variant: str = "pass") -> dict[str, Any]:
    """Build the Promptfoo config. With ``replay_file`` the provider replays recordings, not the gateway."""
    if suite.kind == "retrieval":
        raise ValueError("retrieval suites are run by the knowledge service, not Promptfoo")
    if replay_file:
        provider: dict[str, Any] = {
            "id": f"file://{REPLAY_PROVIDER_FILE}",
            "config": {"replay": replay_file, "variant": variant},
        }
    else:
        provider = _gateway_provider(suite.model)

    config: dict[str, Any] = {
        "description": f"{suite.id} ({suite.kind}) for {suite.use_case}",
        "prompts": [chat_prompt(suite)],
        "providers": [provider],
        "evaluateOptions": {"maxConcurrency": 2},
        "metadata": {"aurai_suite": suite.id, "aurai_suite_hash": suite.hash},
        "tests": [
            {
                "description": case.id,
                "vars": {**case.vars, "aurai_case": case.id},
                "assert": [_assertion(a, provider if replay_file else None) for a in case.assertions],
                "metadata": {"aurai_suite": suite.id, "aurai_suite_hash": suite.hash, "aurai_case": case.id},
            }
            for case in suite.cases
        ],
    }
    if suite.has_rubric and not replay_file:
        # The judge also goes through the gateway. Its model alias is part of the suite, so it is part of the hash.
        config["defaultTest"] = {"options": {"provider": _gateway_provider(suite.judge_model)}}
    return config


def _assertion(assertion: Any, grader: dict[str, Any] | None) -> dict[str, Any]:
    out = assertion.to_promptfoo()
    if grader is not None and assertion.type == RUBRIC:
        # Offline, the replay provider answers the grader call with "not evaluated".
        out["provider"] = {"id": grader["id"], "config": {**grader["config"], "grader": True}}
    return out


# Promptfoo writes the resolved URL, not the {{env.X}} template, into its output. Calls that went straight to a
# model provider instead of the gateway are not live evidence for the platform.
DIRECT_HOSTS = ("api.openai.com", "api.anthropic.com", "generativelanguage.googleapis.com")


def _mode(data: dict[str, Any], provider_id: str, gateway_url: str | None) -> str:
    providers = data.get("config", {}).get("providers") or []
    configured = providers[0] if providers and isinstance(providers[0], dict) else {}
    base = str((configured.get("config") or {}).get("apiBaseUrl") or "")
    if REPLAY_PROVIDER_FILE in provider_id:
        return "replay"
    if not provider_id.startswith("openai:chat:") or not base:
        return "other"
    if gateway_url:
        return LIVE if base.rstrip("/").startswith(gateway_url.rstrip("/")) else "other"
    return "other" if any(host in base for host in DIRECT_HOSTS) else LIVE


def import_results(suite: Suite, data: dict[str, Any], gateway_url: str | None = None) -> RunResult:
    """Convert Promptfoo's ``--output`` JSON into a RunResult, refusing results that do not belong to this suite.

    ``gateway_url`` is the gateway the run should have called; without it, any endpoint that is not a direct model
    provider counts as the gateway.
    """
    try:
        rows = data["results"]["results"]
    except (KeyError, TypeError) as exc:
        raise ImportError_("not a Promptfoo results file (no results.results)") from exc

    provider_ids = {row["provider"]["id"] for row in rows}
    if len(provider_ids) != 1:
        raise ImportError_(f"expected one provider, found {sorted(provider_ids)}")
    provider_id = provider_ids.pop()

    by_case: dict[str, dict[str, Any]] = {}
    for row in rows:
        meta = (row.get("testCase") or {}).get("metadata") or {}
        if meta.get("aurai_suite") != suite.id:
            raise ImportError_(f"results are for suite {meta.get('aurai_suite')!r}, not {suite.id!r}")
        if meta.get("aurai_suite_hash") != suite.hash:
            raise ImportError_(
                f"the suite changed since this run (run {meta.get('aurai_suite_hash')}, "
                f"current {suite.hash}); run it again"
            )
        case_id = meta.get("aurai_case")
        if case_id in by_case:
            raise ImportError_(f"case {case_id} appears more than once")
        by_case[case_id] = row

    expected = [c.id for c in suite.cases]
    if sorted(by_case) != sorted(expected):
        missing = sorted(set(expected) - set(by_case))
        extra = sorted(set(by_case) - set(expected))
        raise ImportError_(f"cases do not match the suite (missing {missing}, unexpected {extra})")

    cases: list[CaseResult] = []
    for case in suite.cases:
        row = by_case[case.id]
        outcome = CaseResult(case.id)
        parts = ((row.get("gradingResult") or {}).get("componentResults")) or []
        if not parts:
            reason = str(row.get("error") or "no grading result")
            outcome.assertions = [
                AssertionResult(a.type, FAIL, a.critical, f"run error: {reason}") for a in case.assertions
            ]
        else:
            if len(parts) != len(case.assertions):
                raise ImportError_(
                    f"case {case.id}: {len(parts)} grading results for {len(case.assertions)} assertions"
                )
            for assertion, part in zip(case.assertions, parts, strict=True):
                got = (part.get("assertion") or {}).get("type")
                if got != assertion.type:
                    raise ImportError_(f"case {case.id}: expected a {assertion.type} result, found {got}")
                if (part.get("metadata") or {}).get("graderError"):
                    status = NOT_EVALUATED  # the judge could not grade it; the gate refuses evidence with any of these
                else:
                    status = PASS if part.get("pass") else FAIL
                reason = "" if status == PASS else str(part.get("reason", ""))
                outcome.assertions.append(AssertionResult(assertion.type, status, assertion.critical, reason))
        cases.append(outcome)

    meta = data.get("metadata") or {}
    stamp = meta.get("evaluationCreatedAt") or (data["results"].get("timestamp"))
    if not stamp:
        raise ImportError_("results carry no timestamp")
    parse_time(stamp)  # fails loudly on a malformed value
    model = provider_id.removeprefix("openai:chat:")
    return RunResult(
        suite=suite,
        mode=_mode(data, provider_id, gateway_url),
        cases=cases,
        executed_at=stamp.replace(".000Z", "Z") if stamp.endswith(".000Z") else stamp,
        model=model,
        judge_model=suite.judge_model if suite.has_rubric else "",
        tool=f"promptfoo {meta.get('promptfooVersion', 'unknown')}",
    )


def write_export(
    suite: Suite, directory: str | Path, *, replay: dict[str, Any] | None = None, variant: str = "pass"
) -> Path:
    """Write promptfooconfig.yaml (and, for replay, the provider and recordings) into a directory."""
    import yaml

    class NoAliases(yaml.SafeDumper):
        def ignore_aliases(self, data: Any) -> bool:  # repeated schemas are written out, not as &anchors
            return True

    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    replay_name = None
    if replay is not None:
        replay_name = "replay.yaml"
        (out / replay_name).write_text(
            yaml.dump(replay, Dumper=NoAliases, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        provider_src = Path(__file__).with_name("replay_provider.py")
        (out / REPLAY_PROVIDER_FILE).write_text(provider_src.read_text(encoding="utf-8"), encoding="utf-8")
    config = export_config(suite, replay_file=replay_name, variant=variant)
    target = out / "promptfooconfig.yaml"
    target.write_text(
        yaml.dump(config, Dumper=NoAliases, sort_keys=False, allow_unicode=True, width=120), encoding="utf-8"
    )
    return target
