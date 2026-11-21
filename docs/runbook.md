# Runbook

## Traces are missing in Langfuse or Phoenix

1. `kubectl logs -n aurai-system deploy/otel-collector` - the platform collector should show spans (debug exporter) and no export errors for `otlp/observability`.
2. `kubectl logs -n aurai-observability deploy/otel-fanout` - look for `401` from Langfuse (wrong key pair in `langfuse-basic-auth`) or connection errors to Phoenix (wrong `fanout.phoenixEndpoint`).
3. Check the secret: the Langfuse public/secret keys in `langfuse-secrets` must match the base64 pair in `observability-secrets`.
4. If the gateway emits no spans, see the note on metadata in [tracing-conventions.md](tracing-conventions.md).

## Langfuse web or worker will not start

- `kubectl logs -n aurai-observability deploy/langfuse-web` - migration failures usually mean ClickHouse is not reachable or credentials differ from `observability-secrets`.
- Redis must use the `noeviction` policy; the local chart sets it.
- The `create-databases` Job must have completed: `kubectl get job -n aurai-observability`.

## Phoenix cannot reach its database

Phoenix uses the `phoenix` database on the shared PostgreSQL. Re-run the deploy script, which passes the URL with `--set-string`.

## The gate says evidence is stale or missing

`aurai-governance gate use-cases/` names the reason per suite: the suite changed after the run (run it again), the
run is older than the tier allows, it was a replay or a direct provider call (only gateway runs count), or no evidence is committed.
Run `scripts/run-evals.ps1 <suite>` (or the `evals-live` workflow), review the numbers, and commit `evals/results`.
A suite that fails live is a finding about the use case, not about the gate: do not edit the evidence or the thresholds to get past it.

## An evaluation run cannot reach the gateway

Check `AURAI_GATEWAY_URL` (no `/v1`) and that `AURAI_EVAL_KEY` belongs to a team at tier T1 or above. The importer reports
`other` for runs that went to a direct provider URL; fix the URL rather than the label.

## A use case must stop now

`aurai-governance pause <id> --registry use-cases/ --base-url <gateway>`; notify the owner; review traces
for the affected team in Langfuse (filter on `aurai.use_case.id`); resume after sign-off.

## Resetting local state

`scripts/undeploy-local.ps1 -Purge` removes releases, volumes and generated secrets.
