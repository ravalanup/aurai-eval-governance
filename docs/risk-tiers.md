# Applying risk tiers

The tier definitions are in `aurai-docs/governance/risk-tiers.md`. This page covers how this repo applies them.

## Classification

`aurai-governance classify use-cases/<file>.yaml` prints the minimum tier and the reason for each question:

| Question | Answer | Minimum tier |
|---|---|---|
| Data classification | confidential / restricted | T2 / T3 |
| Actions | writes to systems | T2 |
| Audience | external | T3 |
| Error impact | material | T3 |
| Protected IP | yes | T3 |

The base tier is T1 if the use case retrieves from internal sources, otherwise T0. The result is the highest of the base and every floor.

## Controls by tier

`aurai-governance requirements T2` lists them. Every tier needs tracing, PII masking and a named owner.

| Tier | Golden-set eval | Red team | Audit retention | Monitoring | Human approval for writes | Reviews |
|---|---|---|---|---|---|---|
| T0 | - | - | standard | basic | - | - |
| T1 | required | - | standard | full | - | team lead |
| T2 | required | required | extended | full | single | compliance |
| T3 | required | required | extended | full with runbook | dual | compliance, legal |

Use cases that retrieve also need entitlement-aware retrieval and citations. Every use case needs a documented pause procedure before pilot.

## Stages

| Stage | Missing controls | Evals and red team |
|---|---|---|
| sandbox | warning | not required |
| pilot | error | must be at least `planned` |
| production | error | must be `passing` |

## Promotion gates

`policy.py` checks what a use case declares. `aurai-governance gate` checks the declarations: `golden_set_eval: passing` and
`red_team: passing` need committed live evidence from the listed suites, meeting the tier's thresholds. Production also
needs fresh platform smoke evidence. See [promotion-gates.md](promotion-gates.md) and [evals.md](evals.md).

## Changing the rules

Edit `REQUIREMENTS` in `policy.py` (and, for evidence thresholds, `evals/thresholds.yaml`) and the matching table in `aurai-docs`. Tests in `tests/test_policy.py`
fail when a tier's requirements change without the tests being updated, which is intended.
