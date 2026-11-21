"""Trace conventions for every Aurai service.

Every span that matters for governance carries the same attributes, so traces from the gateway,
the knowledge service, connectors and agents can be filtered by use case, team and risk tier in
Langfuse and Phoenix.

This module has no OpenTelemetry dependency. `start_span` works with any tracer that offers
`start_as_current_span(name, attributes=...)`, which is what the OpenTelemetry API provides.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

TIERS = ("T0", "T1", "T2", "T3")
FRAMEWORKS = ("langgraph", "n8n", "claude-agent-sdk", "crewai")
APPROVAL_OUTCOMES = ("pending", "approved", "rejected")


class Attr:
    """Attribute names. Standard GenAI names are used where they exist."""

    USE_CASE_ID = "aurai.use_case.id"
    TEAM_ID = "aurai.team.id"
    RISK_TIER = "aurai.risk_tier"
    MODEL_ALIAS = "aurai.model.alias"
    PII_MASKED_TOTAL = "aurai.pii.masked_total"
    RETRIEVAL_COLLECTION = "aurai.retrieval.collection"
    RETRIEVAL_SOURCE_COUNT = "aurai.retrieval.source_count"
    TOOL_NAME = "aurai.tool.name"
    AGENT_NAME = "aurai.agent.name"
    AGENT_FRAMEWORK = "aurai.agent.framework"
    APPROVAL_REQUIRED = "aurai.approval.required"
    APPROVAL_OUTCOME = "aurai.approval.outcome"
    # OpenTelemetry GenAI semantic conventions (not Aurai-specific)
    GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
    GEN_AI_INPUT_TOKENS = "gen_ai.usage.input_tokens"
    GEN_AI_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"


COMMON = (Attr.USE_CASE_ID, Attr.TEAM_ID, Attr.RISK_TIER)

# Span name -> attributes required in addition to COMMON.
SPAN_KINDS: dict[str, tuple[str, ...]] = {
    "aurai.gateway.request": (Attr.MODEL_ALIAS,),
    "aurai.retrieval": (Attr.RETRIEVAL_COLLECTION, Attr.RETRIEVAL_SOURCE_COUNT),
    "aurai.tool.call": (Attr.TOOL_NAME,),
    "aurai.agent.step": (Attr.AGENT_NAME, Attr.AGENT_FRAMEWORK),
    "aurai.approval": (Attr.APPROVAL_REQUIRED, Attr.APPROVAL_OUTCOME),
}


def required_attributes(span_name: str) -> tuple[str, ...]:
    if span_name not in SPAN_KINDS:
        raise KeyError(f"unknown Aurai span name: {span_name}")
    return COMMON + SPAN_KINDS[span_name]


def validate_span(span_name: str, attributes: Mapping[str, Any]) -> list[str]:
    """Return a list of problems; empty means the span follows the conventions."""
    if span_name not in SPAN_KINDS:
        return [f"unknown span name {span_name!r}; expected one of {sorted(SPAN_KINDS)}"]

    problems = [f"missing attribute {name}" for name in required_attributes(span_name) if name not in attributes]

    tier = attributes.get(Attr.RISK_TIER)
    if tier is not None and tier not in TIERS:
        problems.append(f"{Attr.RISK_TIER} must be one of {TIERS}, got {tier!r}")

    count = attributes.get(Attr.RETRIEVAL_SOURCE_COUNT)
    if count is not None and (not isinstance(count, int) or isinstance(count, bool) or count < 0):
        problems.append(f"{Attr.RETRIEVAL_SOURCE_COUNT} must be a non-negative integer")

    framework = attributes.get(Attr.AGENT_FRAMEWORK)
    if framework is not None and framework not in FRAMEWORKS:
        problems.append(f"{Attr.AGENT_FRAMEWORK} must be one of {FRAMEWORKS}, got {framework!r}")

    outcome = attributes.get(Attr.APPROVAL_OUTCOME)
    if outcome is not None and outcome not in APPROVAL_OUTCOMES:
        problems.append(f"{Attr.APPROVAL_OUTCOME} must be one of {APPROVAL_OUTCOMES}, got {outcome!r}")

    required_flag = attributes.get(Attr.APPROVAL_REQUIRED)
    if required_flag is not None and not isinstance(required_flag, bool):
        problems.append(f"{Attr.APPROVAL_REQUIRED} must be a boolean")

    return problems


def start_span(tracer: Any, span_name: str, attributes: Mapping[str, Any], *, strict: bool = True):
    """Start a span that follows the conventions. Raises ValueError if `strict` and attributes are wrong."""
    problems = validate_span(span_name, attributes)
    if problems and strict:
        raise ValueError(f"{span_name}: " + "; ".join(problems))
    return tracer.start_as_current_span(span_name, attributes=dict(attributes))


def attributes_from_gateway_metadata(metadata: Mapping[str, Any], *, use_case_id: str, team_id: str) -> dict[str, Any]:
    """Map the metadata the aurai-gateway guardrail adds to a request onto the conventions.

    The gateway sets `aurai_risk_tier` and `aurai_pii` (with `masked_total`). The use case and team come
    from the caller's key, so pass them in.
    """
    attrs: dict[str, Any] = {Attr.USE_CASE_ID: use_case_id, Attr.TEAM_ID: team_id}
    if "aurai_risk_tier" in metadata:
        attrs[Attr.RISK_TIER] = metadata["aurai_risk_tier"]
    pii = metadata.get("aurai_pii")
    if isinstance(pii, Mapping) and "masked_total" in pii:
        attrs[Attr.PII_MASKED_TOTAL] = pii["masked_total"]
    return attrs
