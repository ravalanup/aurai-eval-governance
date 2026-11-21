# aurai-eval-governance

Governance as code and observability for the Aurai platform: use case registry with a risk-tier rubric,
policy checks that run in CI, evaluation suites (Promptfoo) with evidence-backed promotion gates, shared trace
conventions, and Langfuse + Phoenix behind a trace fan-out collector.

Part of **Aurai**, a fictional enterprise AI platform built for Amazing-Enterprise. All data is synthetic.
This is a prototype with production patterns: it documents what a real deployment would add and what has not been verified.

> Part 1 covers tracing and risk tiers. Part 2 adds evaluation suites (Promptfoo) and promotion gates.
> The gate is built and tested; no live evaluation has been run, so every use case still claims `planned`
> rather than `passing`. See [Honest status](#honest-status).

## What is here

| Path | Purpose |
|---|---|
| `src/aurai_governance/` | Schema, rubric, policy engine, registry loader, trace conventions, pause/resume CLI, promotion gate |
| `src/aurai_governance/evals/` | Suite loader and hash, assertion checks, offline replay, Promptfoo export and import |
| `use-cases/` | Registered use cases: ops-insights, internal-help, knowledge-search |
| `evals/suites/` | Six evaluation suites (golden, red team, grounding, retrieval pin, gateway smoke) |
| `evals/replay/` | Recorded good and bad outputs for each suite, used to test the checks |
| `evals/thresholds.yaml` | What evidence must show per tier |
| `evals/results/` | Committed live evidence the gate reads (none yet) |
| `charts/aurai-observability/` | Fan-out collector, local ClickHouse/Redis/S3, database bootstrap, network policy |
| `deploy/` | Langfuse and Phoenix Helm values; overlay that forwards platform traces |
| `scripts/` | Local deploy, port-forward, undeploy, run evaluations live (PowerShell and bash) |
| `docs/` | Architecture, tracing conventions, risk tiers, evaluation suites, promotion gates, onboarding, runbook |

## Quick start: governance CLI

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
aurai-governance classify use-cases/ops-insights.yaml
aurai-governance validate use-cases/
aurai-governance requirements T2
aurai-governance evals check                          # suites are valid and their checks tell good output from bad
aurai-governance gate use-cases/                      # evaluation claims vs committed evidence
aurai-governance gate use-cases/ --use-case ops-insights --target-stage production   # what blocks this promotion?
pytest
```

## Quick start: evaluation (live)

Needs a gateway and an evaluation key; see [docs/evals.md](docs/evals.md).

```powershell
$env:AURAI_GATEWAY_URL = "http://localhost:4000"; $env:AURAI_EVAL_KEY = "<key>"
.\scripts\run-evals.ps1 ops-insights-golden ops-insights-redteam   # export, run Promptfoo, import evidence
aurai-governance gate use-cases/
```

## Quick start: observability stack (local)

Requires the local cluster from `aurai-infra` (`local\scripts\up.ps1`) and Helm.

```powershell
.\scripts\deploy-local.ps1 -LangfuseChartVersion <version> -PhoenixChartVersion <version>
.\scripts\port-forward.ps1    # Langfuse http://localhost:3100, Phoenix http://localhost:6006
```

The script generates local-only secrets, installs the stores and database bootstrap, Langfuse, Phoenix and the
fan-out collector, then upgrades the platform release so its collector forwards traces. Pin chart versions
for repeatable installs.

## Honest status

**Verified**
- Python library and CLI: unit tests and lint, including the policy engine, the gate (32 scenarios on synthetic
  evidence) and the assertion checks.
- Every suite: its recorded good outputs pass and its recorded bad outputs fail, so each case can tell the two apart.
- The real Promptfoo CLI (0.126.0) runs the exported suites. The Python checks and Promptfoo agree on every case, on good
  and bad recordings, and the importer reads Promptfoo's actual output. The gateway provider path (URL from the environment,
  key, rubric judge) was exercised against a local fake gateway.
- YAML validity; Helm template syntax of the local chart.

**Not verified**
- Any live evaluation. No gateway, model or judge was available, so no `passing` evidence exists and no use case claims it.
  The recorded outputs are hand-written fixtures, not model output.
- The pass-rate, size and age thresholds. They are a proposal, not calibrated against baseline runs.
- Judge model agreement with human graders. `llm-rubric` results are a screen, not a measurement.
- PII masking and the gateway smoke suite against a real gateway, and the Presidio entities it relies on.
- That evidence has not been edited by hand. The gate can tell a gateway run from a replay or a direct provider call,
  not an honest file from a forged one; CODEOWNERS review and a CI job that writes the evidence are the controls.
- Part 1: `helm template`/install of any chart, Langfuse and Phoenix value names and service names, the S3 mock image
  settings, LiteLLM forwarding request metadata as OTel attributes, and the LiteLLM team block/unblock calls against a
  live gateway. Expect small fixes on first install.

**What production would add**
- Evidence written and signed by a CI job from a protected environment, with the pull request opened by the job.
- Repeated runs and confidence intervals instead of one run per suite; suites grown from real incidents and traffic.
- Live quality, cost and drift monitoring on production traffic (EV-4) and scheduled red-team runs (EV-5). The suites
  here are the pre-release gate; Langfuse scoring of live traffic is not built.
- The suite prompts imported from the agent code instead of copied (`evals drift` catches the difference today).

## Related repos

`aurai-docs` (governance and architecture), `aurai-infra` (cluster, Postgres, platform collector), `aurai-gateway` (LiteLLM, PII guardrail, teams).

## License

MIT
