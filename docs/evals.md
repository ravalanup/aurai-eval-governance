# Evaluation suites

An evaluation suite is a YAML file in `evals/suites/`. It holds the prompt, the test cases and the assertions
for one thing a use case has to get right. The same file drives three things:

1. **Live runs.** `aurai-governance evals export` turns it into a Promptfoo config that calls the gateway, so
   evaluation traffic is traced, masked and budgeted like any other client.
2. **Offline replay.** `aurai-governance evals check` applies the suite's assertions to recorded outputs in CI.
   It proves the checks work. It says nothing about a model.
3. **Evidence.** `aurai-governance evals import` turns a live Promptfoo run into a file in `evals/results/`
   that [the promotion gate](promotion-gates.md) reads. The evidence records the SHA-256 of the suite that produced it.

## The suites

| Suite | Kind | Use case | Cases | What it checks |
|---|---|---|---|---|
| `ops-insights-golden` | golden | ops-insights | 12 | Output contract (JSON shape, at most 3 proposals, areas given), faithfulness, ticket discipline |
| `ops-insights-redteam` | red_team | ops-insights | 11 | Injection through ticket and incident text, prompt exfiltration, invented areas, ticket floods, links, approval bypass, credentials, PII propagation, over-refusal |
| `ops-insights-grounding` | grounding | ops-insights | 6 | Figures, counts, areas and ticket numbers come from the facts; missing data is not invented |
| `knowledge-grounded-answers` | grounding | internal-help | 8 | Answers over fixed passages are cited, refuse when the passages do not cover the question, resist instructions inside a passage, prefer newer sources |
| `knowledge-search-retrieval` | retrieval | knowledge-search | n/a | Hit rate, MRR and entitlement leaks, measured by the knowledge service's own golden set and pinned by hash |
| `gateway-smoke-pii` | platform | `_platform` | 5 | A completion works through the gateway and PII is masked before the model sees it |

The ops-insights prompt is a copy of the agent's `SYSTEM_PROMPT`. `aurai-governance evals drift <suite> --source
../aurai-agent-runtime/src/aurai_agents/agents/ops_insights.py` fails when the two differ, and the test suite runs it
when both repositories are checked out side by side. A real deployment would import the prompt rather than copy it.

## Assertions

The vocabulary is the part of Promptfoo's that this repository evaluates itself, so a case means the same thing in a
live run and in a replay:

| Type | Passes when |
|---|---|
| `contains`, `icontains` | the output contains the string (`icontains` ignores case) |
| `contains-any`, `icontains-any` | the output contains at least one string from the list |
| `regex` | the pattern is found (keep to syntax JavaScript and Python share; the loader rejects named groups and inline flags) |
| `is-json` | the output is JSON; with a `value`, it also matches that JSON Schema |
| `not-` + any of the above except `is-json` | the opposite |
| `llm-rubric` | a judge model, called through the gateway, says the output meets the rubric |

Mark an assertion `critical: true` when failing it means the system did something it must never do (obeyed an
instruction in data, leaked a value). One critical failure fails the red-team gate whatever the pass rate.

## Running a suite live

Evaluation goes through the gateway with a key for a team whose tier masks contact details (T1 or above):

```powershell
$env:AURAI_GATEWAY_URL = "http://localhost:4000"     # your gateway
$env:AURAI_EVAL_KEY    = "<a virtual key for the evaluation team>"
.\scripts\run-evals.ps1 ops-insights-golden ops-insights-redteam
```

`run-evals.ps1` (and `run-evals.sh`) export each suite, run `npx promptfoo eval`, and import the result. Then:

```powershell
aurai-governance gate use-cases/                      # does the evidence support the claims?
git add evals/results; git commit -m "Record live evaluation results"
```

Retrieval evidence comes from the knowledge service instead:

```powershell
aurai-knowledge eval --url http://localhost:8000 --json report.json     # in aurai-knowledge-rag
aurai-governance evals import-retrieval report.json --suite knowledge-search-retrieval
```

An evidence file is accepted by the gate only if all of these hold: it came from a run through the gateway
(`mode: live`), its `suite_hash` equals the suite file in the repository, its use case matches, it was recorded at
the use case's tier or higher, it used one of the use case's registered models, it is newer than the tier's age limit
and it meets the tier's thresholds. Replay and offline results are written nowhere.

## Adding a case or a suite

1. Add the case to the suite file, or copy a suite and change `id` (the file name must match).
2. Add recorded `pass` and `fail` outputs to `evals/replay/<id>.yaml`. The `fail` output must be one a bad system
   would plausibly give. `aurai-governance evals check` fails if a case cannot tell the two apart.
3. List the suite under `evals:` in the use case (`golden`, `red_team` or `grounding`).
4. Run it live and commit the evidence. Editing a suite changes its hash, so earlier evidence stops counting until it is run again.

## What replay is and is not

The recordings in `evals/replay/` are written by hand. They are not model output. They exist so that the assertions,
the Promptfoo export and import, and the gate can be tested without a model. `evals check` runs every case on a good and a bad
recording, and the test suite runs the real Promptfoo CLI over the same recordings (set `AURAI_RUN_PROMPTFOO=1`) and
requires it to agree with the Python checks. A second test points Promptfoo's gateway provider at a local fake server to
exercise the live path.

## Limits

- **Judge calibration is not verified.** `llm-rubric` assertions are graded by `judge_model`. Nobody has compared its
  verdicts with human ones, so treat rubric results as a screen. The deterministic assertions carry the gate where they can.
- **Small suites.** Eleven red-team cases is a starting point, not coverage. The thresholds set a minimum size per tier.
- **One model, one run.** A pass rate from one run of a non-deterministic model is a sample. The gate asks for recent evidence; it does not ask for repeated runs.
- **Evidence is files in git.** The importer can tell a gateway run from a direct provider call and a replay, but it cannot prove
  a person did not edit the JSON. Review, CODEOWNERS and a CI job that writes the evidence are the controls (see below).
