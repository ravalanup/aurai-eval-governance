# Registering a use case

1. Copy `use-cases/ops-insights.yaml` and fill in the facts. Do not choose the tier first; answer the
   questions, then run `aurai-governance classify use-cases/<file>.yaml`.
2. Set `tier` to the classified minimum or higher.
3. Declare the controls you have. Be accurate: `planned` means a suite exists (from pilot) and a plan to run it, `passing` means committed live evidence from the current suite meets the thresholds for your tier.
   List the suites under `evals:` (`golden`, `red_team`, `grounding`); see `docs/evals.md` for writing one.
4. Add the reviews your tier requires: team lead for T1, compliance for T2, compliance and legal for T3 (`aurai-governance requirements <tier>`).
5. Create the matching gateway team in `aurai-gateway/teams/teams.yaml` and set `gateway_team_id`.
6. Run `aurai-governance validate use-cases/ --catalog ../aurai-gateway/config/model-catalog.yaml`.
7. Run `aurai-governance gate use-cases/ --use-case <id> --target-stage <next stage>` to see what blocks promotion.
8. Open a pull request. CI runs `validate` and `gate`; a failing policy or an unsupported claim blocks the merge.

## Pausing a use case

`aurai-governance pause <id> --registry use-cases/ --base-url <gateway>` blocks the gateway team. The admin
key is read from `LITELLM_MASTER_KEY`. `resume` reverses it. The `/team/block` and `/team/unblock` calls
follow LiteLLM's documented team management API and have not been exercised against a live gateway.
