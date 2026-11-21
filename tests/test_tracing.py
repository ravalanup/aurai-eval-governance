import pytest

from aurai_governance.tracing import (
    SPAN_KINDS,
    Attr,
    attributes_from_gateway_metadata,
    required_attributes,
    start_span,
    validate_span,
)

COMMON = {Attr.USE_CASE_ID: "ops-insights", Attr.TEAM_ID: "ops-insights", Attr.RISK_TIER: "T2"}


def test_valid_gateway_span():
    assert validate_span("aurai.gateway.request", {**COMMON, Attr.MODEL_ALIAS: "aurai-fast"}) == []


def test_missing_attributes_reported():
    problems = validate_span("aurai.retrieval", COMMON)
    assert any(Attr.RETRIEVAL_COLLECTION in p for p in problems)
    assert any(Attr.RETRIEVAL_SOURCE_COUNT in p for p in problems)


def test_unknown_span_name():
    assert "unknown span name" in validate_span("aurai.mystery", COMMON)[0]


@pytest.mark.parametrize(
    "extra, fragment",
    [
        ({Attr.RISK_TIER: "T9"}, "risk_tier"),
        ({Attr.RETRIEVAL_SOURCE_COUNT: -1}, "non-negative"),
        ({Attr.RETRIEVAL_SOURCE_COUNT: True}, "non-negative"),
    ],
)
def test_value_checks(extra, fragment):
    attrs = {**COMMON, Attr.RETRIEVAL_COLLECTION: "policies", Attr.RETRIEVAL_SOURCE_COUNT: 3, **extra}
    assert any(fragment in p for p in validate_span("aurai.retrieval", attrs))


def test_agent_framework_and_approval_values():
    agent = {**COMMON, Attr.AGENT_NAME: "triage", Attr.AGENT_FRAMEWORK: "autogen"}
    assert any("agent.framework" in p for p in validate_span("aurai.agent.step", agent))
    approval = {**COMMON, Attr.APPROVAL_REQUIRED: "yes", Attr.APPROVAL_OUTCOME: "maybe"}
    problems = validate_span("aurai.approval", approval)
    assert any("approval.outcome" in p for p in problems) and any("boolean" in p for p in problems)


def test_every_span_kind_requires_common_attributes():
    for name in SPAN_KINDS:
        assert set(required_attributes(name)) >= set(COMMON)


class FakeTracer:
    def __init__(self):
        self.calls = []

    def start_as_current_span(self, name, attributes=None):
        self.calls.append((name, attributes))
        return "span"


def test_start_span_passes_attributes_to_tracer():
    tracer = FakeTracer()
    attrs = {**COMMON, Attr.TOOL_NAME: "create_ticket"}
    assert start_span(tracer, "aurai.tool.call", attrs) == "span"
    assert tracer.calls == [("aurai.tool.call", attrs)]


def test_start_span_strict_rejects_bad_attributes():
    with pytest.raises(ValueError):
        start_span(FakeTracer(), "aurai.tool.call", COMMON)
    assert start_span(FakeTracer(), "aurai.tool.call", COMMON, strict=False) == "span"


def test_gateway_metadata_mapping():
    meta = {"aurai_risk_tier": "T3", "aurai_pii": {"tier": "T3", "masked_total": 4}}
    attrs = attributes_from_gateway_metadata(meta, use_case_id="guest-assistant", team_id="guest-assistant")
    assert attrs[Attr.RISK_TIER] == "T3"
    assert attrs[Attr.PII_MASKED_TOTAL] == 4
    assert attrs[Attr.USE_CASE_ID] == "guest-assistant"
