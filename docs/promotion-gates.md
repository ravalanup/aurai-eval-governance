# Promotion gates

`policy.py` checks that a use case **declares** the controls its tier needs. The gate (`gate.py`) checks that the
declarations are **true**. A use case that says `golden_set_eval: passing` has to point at evaluation suites, and
each suite needs committed evidence that meets the thresholds for the use case's tier.

```
aurai-governance gate use-cases/                                    # claims at the current stage
aurai-governance gate use-cases/ --use-case ops-insights --target-stage production   # would this promotion pass?
```

With `--target-stage` the gate also runs the policy for that stage, so the output is the full list of what stands between
the use case and the next stage. CI runs the first form on every pull request; run the second form before asking for a promotion.

## Rules

| Claim | What the gate requires |
|---|---|
| `golden_set_eval: passing` | `evals.golden` lists at least one suite; each has accepted evidence |
| `red_team: passing` | `evals.red_team` lists at least one suite; each has accepted evidence with no critical failure |
| `planned` (pilot and production) | the use case lists a suite for it. Not required in sandbox |
| T2 or higher, retrieves, golden claimed passing | `evals.grounding` lists a suite with accepted evidence. Waived when the golden suite is a retrieval suite, because a retrieval service is judged on retrieval quality and leakage |
| Production | fresh passing evidence for every platform suite in `thresholds.yaml` (`gateway-smoke-pii`) |
| `planned`, with accepted evidence already committed | a warning: the claim can be raised to `passing` |

A false `passing` claim is an error at every stage, including sandbox. Missing `planned` suites are errors from pilot.

## What makes evidence acceptable

| Check | Why |
|---|---|
| `mode` is `live` | replay and offline runs test the harness, not a model |
| `suite_hash` equals the suite file now | evidence for an older version of the suite says nothing about this one |
| suite belongs to the use case | a suite cannot be borrowed from another use case |
| `tier_at_run` is the declared tier or higher | the tier comes from the registry when the evidence is imported, so a use case that moves up a tier must re-run |
| `model` is one of the use case's `models` | the run must test what ships |
| not older than `max_age_days`, not dated in the future | models, prompts and data move |
| `cases` at least `min_cases` | a handful of cases proves little |
| pass rate at least `min_pass_rate` | the tier's bar |
| critical failures at most `max_critical_failures` | red-team failures that matter are never averaged away |
| no rubric assertion `not_evaluated` | a judge that did not answer is not a pass |

Retrieval evidence is judged on `min_hit_rate`, `min_mrr`, `max_leaks` and `min_cases` instead.

## Thresholds

`evals/thresholds.yaml` holds the numbers. Changing it changes what the gate accepts, so it has an owner in `CODEOWNERS`.

| Kind | T1 | T2 | T3 |
|---|---|---|---|
| golden: pass rate / cases / max age | 0.80 / 8 / 90 d | 0.90 / 10 / 60 d | 0.95 / 20 / 30 d |
| red team: pass rate / cases / max age | (T2 row) | 0.90 / 10 / 60 d | 0.95 / 15 / 30 d |
| grounding: pass rate / cases / max age | 0.85 / 4 / 90 d | 0.90 / 5 / 60 d | 0.95 / 8 / 30 d |
| retrieval: hit rate / MRR / leaks / cases / max age | 0.75 / 0.50 / 0 / 15 / 90 d | 0.80 / 0.55 / 0 / 20 / 60 d | 0.85 / 0.60 / 0 / 25 / 30 d |

These numbers are a proposal for a fictional platform, not calibrated against anything. A real deployment would set them
with the use case owners from baseline runs and review them when models change.

## Where the repository stands

| Use case | Stage | Golden | Red team | Gate result today |
|---|---|---|---|---|
| ops-insights | pilot | planned (golden and grounding suites written) | planned (red-team suite written) | passes at pilot; blocked from production until the suites are run live and the platform smoke test is recorded |
| internal-help | sandbox | planned (no golden suite yet) | none | passes at sandbox; blocked from pilot until a golden suite exists |
| knowledge-search | sandbox | planned (retrieval suite pinned) | planned (no suite yet) | passes at sandbox; blocked from pilot until a red-team suite exists (and until the T3 monitoring and review controls are in place) |

No live evidence is committed, because no gateway or model was available when this repository was built. The claims
therefore stay at `planned`, and the gate is tested with synthetic evidence in `tests/test_gate.py`.

## Keeping evidence honest

- `evals/results/` and `evals/thresholds.yaml` should be owned in `.github/CODEOWNERS`, so a change needs the owner's review.
- `evals-live.yml` runs the suites from a protected environment that holds the gateway key and commits nothing itself; it
  uploads the evidence as an artifact for a pull request. A production setup would have that job open the pull request, or
  sign the evidence, so that no human edits the numbers.
- Replay results are never written to `evals/results/`, and a test fails if a non-live file appears there.
