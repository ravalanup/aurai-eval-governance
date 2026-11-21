# Tracing conventions

All Aurai services attach the same attributes so one filter works across the gateway, retrieval, tools
and agents. Names live in `src/aurai_governance/tracing.py`; use `start_span` to have the attributes
checked at the call site.

## Common attributes (every span)

| Attribute | Meaning |
|---|---|
| `aurai.use_case.id` | Registered use case id |
| `aurai.team.id` | Gateway team id |
| `aurai.risk_tier` | `T0`-`T3`, from the key or team, never from request content |

## Span kinds

| Span name | Extra required attributes |
|---|---|
| `aurai.gateway.request` | `aurai.model.alias`; optional `gen_ai.request.model`, token usage, `aurai.pii.masked_total` |
| `aurai.retrieval` | `aurai.retrieval.collection`, `aurai.retrieval.source_count` |
| `aurai.tool.call` | `aurai.tool.name` |
| `aurai.agent.step` | `aurai.agent.name`, `aurai.agent.framework` (`langgraph`, `n8n`, `claude-agent-sdk`, `crewai`) |
| `aurai.approval` | `aurai.approval.required` (bool), `aurai.approval.outcome` (`pending`, `approved`, `rejected`) |

## What never goes in a span

Prompt text, retrieved passages, tool arguments that may hold personal data, and PII values. The gateway
records counts of masked entities only. Services that must record content for debugging do so only in
tiers and environments where the policy allows it, and mask first.

## Gateway metadata mapping

The gateway guardrail adds `aurai_risk_tier` and `aurai_pii.masked_total` to request metadata.
`attributes_from_gateway_metadata` maps them onto `aurai.risk_tier` and `aurai.pii.masked_total` and adds
the use case and team (known from the caller's key). Whether the installed LiteLLM version forwards request
metadata as span attributes on its OTel callback depends on version and configuration and has not been
verified here; if it does not, emit the `aurai.gateway.request` span from a small wrapper using the helper.

## Example

```python
from aurai_governance.tracing import Attr, start_span

with start_span(
    tracer,
    "aurai.retrieval",
    {
        Attr.USE_CASE_ID: "ops-insights",
        Attr.TEAM_ID: "ops-insights",
        Attr.RISK_TIER: "T2",
        Attr.RETRIEVAL_COLLECTION: "ops-reports",
        Attr.RETRIEVAL_SOURCE_COUNT: 4,
    },
):
    ...
```
