# Architecture

This repo has two halves that meet at the use case registry.

```
                       Governance as code                          Observability
  use-cases/*.yaml --> schema + rubric + policy  --> CI gate
        |                      |
        | gateway_team_id      | pause / resume
        v                      v
   aurai-gateway  ------ traces (OTLP) ------>  otel-collector (aurai-system)
   (LiteLLM, teams)                                     |
                                                        v
                                           otel-fanout (aurai-observability)
                                              |                    |
                                              v                    v
                                          Langfuse              Phoenix
                                   (monitoring, cost,        (debugging, trace
                                    prompts, by team)         inspection)
```

## Governance as code

A use case is a YAML file validated by three layers:

1. **JSON Schema** (`use-case.schema.json`) - shape, enums, required fields.
2. **Rubric** (`rubric.py`) - the five classification questions from `aurai-docs/governance/risk-tiers.md`
   turn the use case's facts into a *minimum* tier. A use case may claim a higher tier, never a lower one.
3. **Policy** (`policy.py`) - the controls the claimed tier requires, checked against the controls the use
   case declares. Strictness depends on the lifecycle stage: in `sandbox` gaps are warnings, in `pilot`
   controls must exist and evaluations and red teaming must at least be planned, in `production` they must
   be passing.

`aurai-governance validate use-cases/` runs all three and exits non-zero on errors, which is what CI uses.
Model aliases a use case lists can be cross-checked against the gateway's `model-catalog.yaml`.

A fourth layer, the **promotion gate** (`gate.py`), checks that claims of `passing` are backed by committed evidence from
evaluation suites; see [promotion-gates.md](promotion-gates.md). Suites are YAML in `evals/suites/`, exported to Promptfoo
for live runs against the gateway and replayed offline in CI to test the checks (see [evals.md](evals.md)).

Why Python rules rather than OPA: the checks are a few dozen comparisons, the people reading them are
product and platform engineers, and a test suite over plain functions is easier to review than Rego for a
policy this size. The `aurai-agent-runtime` repo is where OPA enters, for runtime tool authorization,
which is a different problem.

## Tracing

Every Aurai service uses the attribute names in `tracing.py` (see [tracing-conventions.md](tracing-conventions.md)).
Services emit OTLP to the platform collector from `aurai-infra`. That collector forwards to a second
collector in this repo's chart, which holds the backend credentials and fans out to both backends. The
split keeps backend secrets out of the platform namespace and lets either backend be down without
losing traces for the other.

## Why two backends

| | Langfuse | Phoenix |
|---|---|---|
| Used for | Production monitoring, cost by team, prompt management, scores | Debugging a specific trace, comparing runs while building |
| Audience | Platform owners, use case owners | Engineers building agents and retrieval |
| Data stores | PostgreSQL, ClickHouse, Redis, object storage | PostgreSQL |
| License | MIT core | Elastic License 2.0 (internal use is fine) |

## Data stores

Locally, PostgreSQL is the shared instance from `aurai-infra`; ClickHouse, Redis and an S3-compatible
store are small StatefulSets from this repo's `aurai-observability` chart. In AWS these become RDS, a
managed or operator-run ClickHouse, ElastiCache with the `noeviction` policy, and S3 with an IRSA role
(`deploy/langfuse-values-aws.yaml` is the starting point).

## What a production deployment would add

- Tail sampling in the fan-out collector (keep all errors and all T2/T3 traces; sample T0/T1).
- Per-tier retention: extended audit retention for T2/T3 in object storage with lifecycle rules.
- SSO for Langfuse and Phoenix through Keycloak; no shared admin account.
- A ClickHouse operator or ClickHouse Cloud with backups; Redis with persistence and failover.
- Alerting on the signals in the [runbook](runbook.md), routed to the use case owner.
- Evaluation evidence written by a CI job from a protected environment, repeated runs per suite, and Langfuse scoring of live traffic for quality, cost and drift (EV-4) and scheduled red-team runs (EV-5).

## Not verified

The Helm charts and Langfuse/Phoenix values were authored without access to the chart registries from the
build environment. They render as valid templates, but chart value names, the `langfuse-web` service
name and Phoenix service naming should be confirmed on first install. See the README.
