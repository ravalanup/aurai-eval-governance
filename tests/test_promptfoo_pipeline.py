"""Run the real Promptfoo CLI against exported suites.

Skipped unless AURAI_RUN_PROMPTFOO=1 (CI sets it). AURAI_PROMPTFOO_CMD overrides the command, which defaults to
``npx --yes promptfoo@0.126.0``. Nothing here calls a real model: one test replays recorded outputs through a
Python provider, the other points the gateway provider at a local fake that serves the recorded outputs and
accepts every rubric.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from aurai_governance import cli
from aurai_governance.evals.promptfoo import import_results, write_export
from aurai_governance.evals.replay import load_replay, replay_path
from aurai_governance.evals.results import LIVE, to_evidence

from .evalhelpers import REPLAY_DIR, ROOT, SUITES_DIR, suites

pytestmark = pytest.mark.skipif(os.environ.get("AURAI_RUN_PROMPTFOO") != "1", reason="set AURAI_RUN_PROMPTFOO=1")
COMMAND = shlex.split(os.environ.get("AURAI_PROMPTFOO_CMD", "npx --yes promptfoo@0.126.0"))


def run_promptfoo(config, output, env_extra=None):
    env = {
        **os.environ,
        "PROMPTFOO_DISABLE_TELEMETRY": "1",
        "PROMPTFOO_DISABLE_UPDATE": "1",
        "PROMPTFOO_PYTHON": sys.executable,
        **(env_extra or {}),
    }
    cmd = [*COMMAND, "eval", "-c", str(config), "-o", str(output), "--no-cache", "--no-write"]
    done = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=300, check=False)
    assert output.is_file(), done.stdout[-2000:] + done.stderr[-2000:]
    return json.loads(output.read_text(encoding="utf-8"))


@pytest.mark.parametrize("suite_id", ["ops-insights-golden", "ops-insights-redteam", "gateway-smoke-pii"])
def test_replayed_suites_agree_between_promptfoo_and_the_python_checks(tmp_path, suite_id):
    suite = suites()[suite_id]
    replay = load_replay(replay_path(REPLAY_DIR, suite))
    for variant in ("pass", "fail"):
        out = tmp_path / variant
        config = write_export(suite, out, replay=replay, variant=variant)
        imported = import_results(suite, run_promptfoo(config, out / "results.json"))
        from aurai_governance.evals.replay import replay_suite

        expected = replay_suite(suite, replay, variant)
        assert imported.mode == "replay"
        assert [(c.id, c.passed) for c in imported.cases] == [(c.id, c.passed) for c in expected.cases]
        assert imported.critical_failures == expected.critical_failures
        assert imported.not_evaluated == expected.not_evaluated


class FakeGateway(BaseHTTPRequestHandler):
    answers: dict[str, str] = {}
    judge = ""

    def log_message(self, *args):  # keep test output quiet
        pass

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert self.headers["Authorization"] == "Bearer test-key"
        if body["model"] == self.judge:
            content = json.dumps({"pass": True, "score": 1, "reason": "fake judge accepts"})
        else:
            content = self.answers[body["messages"][-1]["content"]]
        reply = {
            "id": "x",
            "object": "chat.completion",
            "model": body["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def test_gateway_provider_path_round_trips_through_promptfoo(tmp_path):
    """Exercises the live code path (provider, env URL, key, rubric judge) against a local fake gateway."""
    suite = suites()["ops-insights-golden"]
    replay = load_replay(replay_path(REPLAY_DIR, suite))
    FakeGateway.judge = suite.judge_model
    FakeGateway.answers = {_render(suite.user, case.vars): replay["outputs"][case.id]["pass"] for case in suite.cases}
    server = HTTPServer(("127.0.0.1", 0), FakeGateway)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        config = write_export(suite, tmp_path)
        env = {"AURAI_GATEWAY_URL": f"http://127.0.0.1:{server.server_port}", "AURAI_EVAL_KEY": "test-key"}
        data = run_promptfoo(config, tmp_path / "results.json", env)
    finally:
        server.shutdown()

    result = import_results(suite, data, env["AURAI_GATEWAY_URL"])
    assert result.mode == LIVE and result.model == "aurai-balanced"
    assert result.failures() == [], result.render()
    assert result.not_evaluated == 0  # the judge ran and accepted the four rubrics
    evidence = to_evidence(result, "T2")
    assert evidence["totals"]["pass_rate"] == 1.0 and evidence["suite_hash"] == suite.hash


def test_import_command_on_real_promptfoo_output(tmp_path, capsys):
    suite = suites()["gateway-smoke-pii"]
    out = tmp_path / "x"
    config = write_export(suite, out, replay=load_replay(replay_path(REPLAY_DIR, suite)))
    run_promptfoo(config, out / "results.json")
    args = ["evals", "import", str(out / "results.json"), "--suite", suite.id, "--suites", str(SUITES_DIR)]
    assert cli.main([*args, "--dry-run"]) == 0
    assert "mode=replay" in capsys.readouterr().out
    assert ROOT.is_dir()


def _render(template, variables):
    for key, value in variables.items():
        template = template.replace("{{" + key + "}}", value)
    return template
