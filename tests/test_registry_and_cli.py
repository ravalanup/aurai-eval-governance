from pathlib import Path

import httpx
import pytest
import yaml

from aurai_governance import cli, pause
from aurai_governance.registry import duplicate_ids, find_use_case, load_registry, schema_errors

from .conftest import compliant, make

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_CATALOG = ROOT.parent / "aurai-gateway" / "config" / "model-catalog.yaml"


def write(tmp_path, name, doc):
    path = tmp_path / name
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return path


def test_repository_use_cases_are_valid():
    entries = load_registry(ROOT / "use-cases")
    assert entries
    for entry in entries:
        assert entry.schema_errors == [], entry.path
    assert cli.main(["validate", str(ROOT / "use-cases")]) == 0


def test_schema_rejects_bad_documents():
    assert schema_errors(make(tier="T7"))
    assert schema_errors({**make(), "surprise": 1})
    broken = make()
    del broken["controls"]["tracing"]
    assert any("tracing" in e for e in schema_errors(broken))
    assert schema_errors(make()) == []


def test_validate_fails_on_policy_errors(tmp_path, capsys):
    bad = compliant("T2")
    bad["controls"]["human_approval"] = "none"
    write(tmp_path, "bad.yaml", bad)
    assert cli.main(["validate", str(tmp_path)]) == 1
    assert "human-approval-required" in capsys.readouterr().out


def test_validate_strict_turns_warnings_into_failures(tmp_path):
    sandbox = compliant("T2", stage="sandbox")
    sandbox["controls"]["golden_set_eval"] = "none"
    write(tmp_path, "sandbox.yaml", sandbox)
    assert cli.main(["validate", str(tmp_path)]) == 0
    assert cli.main(["validate", str(tmp_path), "--strict"]) == 1


def test_validate_reports_schema_errors_and_empty_registry(tmp_path, capsys):
    write(tmp_path, "broken.yaml", {"id": "x"})
    assert cli.main(["validate", str(tmp_path)]) == 1
    assert "[schema]" in capsys.readouterr().out
    empty = tmp_path / "empty"
    empty.mkdir()
    assert cli.main(["validate", str(empty)]) == 1


def test_duplicate_ids_detected(tmp_path):
    write(tmp_path, "a.yaml", make(id="same"))
    write(tmp_path, "b.yaml", make(id="same"))
    assert duplicate_ids(load_registry(tmp_path))


def test_example_models_are_approved_in_gateway_catalog():
    if not GATEWAY_CATALOG.exists():
        pytest.skip("aurai-gateway is not checked out beside this repository")
    assert cli.main(["validate", str(ROOT / "use-cases"), "--catalog", str(GATEWAY_CATALOG)]) == 0


def test_classify_and_requirements_commands(capsys):
    assert cli.main(["classify", str(ROOT / "use-cases" / "ops-insights.yaml")]) == 0
    out = capsys.readouterr().out
    assert "Minimum tier: T2" in out and "Declared tier: T2" in out
    assert cli.main(["requirements", "T3"]) == 0
    assert "full_with_runbook" in capsys.readouterr().out


def _mock_gateway(monkeypatch, status=200):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, request.content))
        return httpx.Response(status, json={})

    real = httpx.Client
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-test")
    monkeypatch.setattr(cli.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return calls


def test_pause_and_resume_call_gateway(monkeypatch, capsys):
    calls = _mock_gateway(monkeypatch)
    args = ["--registry", str(ROOT / "use-cases")]
    assert cli.main(["pause", "ops-insights", *args]) == 0
    assert cli.main(["resume", "ops-insights", *args]) == 0
    assert [c[0] for c in calls] == ["/team/block", "/team/unblock"]
    assert b"ops-insights" in calls[0][1]
    assert "paused ops-insights" in capsys.readouterr().out


def test_pause_reports_gateway_errors_and_unknown_ids(monkeypatch, capsys):
    _mock_gateway(monkeypatch, status=500)
    assert cli.main(["pause", "ops-insights", "--registry", str(ROOT / "use-cases")]) == 1
    assert cli.main(["pause", "nope", "--registry", str(ROOT / "use-cases")]) == 1
    assert find_use_case(ROOT / "use-cases", "nope") is None


def test_pause_requires_admin_key(monkeypatch):
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
    try:
        cli.main(["pause", "ops-insights", "--registry", str(ROOT / "use-cases")])
    except SystemExit as exc:
        assert "LITELLM_MASTER_KEY" in str(exc)
    else:
        raise AssertionError("expected SystemExit")


def test_pause_module_raises_on_failure():
    client = httpx.Client(base_url="http://x", transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    try:
        pause.pause_team(client, "t")
    except pause.PauseError:
        pass
    else:
        raise AssertionError("expected PauseError")
