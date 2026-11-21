import copy

import pytest

from aurai_governance import cli
from aurai_governance.evals.replay import ReplayError, load_replay, problems, replay_path, replay_suite

from .evalhelpers import REPLAY_DIR, SUITES_DIR, suites

CASE_SUITES = [s for s in suites().values() if s.kind != "retrieval"]


@pytest.mark.parametrize("suite", CASE_SUITES, ids=lambda s: s.id)
def test_recorded_good_outputs_pass_their_own_checks(suite):
    result = replay_suite(suite, load_replay(replay_path(REPLAY_DIR, suite)), "pass")
    assert result.failures() == [], result.render()
    assert result.mode == "replay"


@pytest.mark.parametrize("suite", CASE_SUITES, ids=lambda s: s.id)
def test_every_case_catches_its_recorded_bad_output(suite):
    """A case that passes a bad output measures nothing."""
    result = replay_suite(suite, load_replay(replay_path(REPLAY_DIR, suite)), "fail")
    assert [c.id for c in result.cases if c.passed] == []


@pytest.mark.parametrize("suite", [s for s in CASE_SUITES if s.kind == "red_team"], ids=lambda s: s.id)
def test_red_team_bad_outputs_trip_critical_assertions(suite):
    result = replay_suite(suite, load_replay(replay_path(REPLAY_DIR, suite)), "fail")
    benign = {"benign-lookalike-not-over-refused"}
    for case in result.cases:
        if case.id not in benign:
            assert case.critical_failed or case.id == "inject-role-switch", case.id
    assert result.critical_failures >= 9


def test_rubric_assertions_are_reported_not_evaluated_in_replay():
    suite = suites()["ops-insights-golden"]
    result = replay_suite(suite, load_replay(replay_path(REPLAY_DIR, suite)), "pass")
    assert result.not_evaluated == 4


def test_replay_files_cover_every_case_exactly():
    suite = suites()["ops-insights-golden"]
    replay = copy.deepcopy(load_replay(replay_path(REPLAY_DIR, suite)))
    del replay["outputs"]["quiet-week"]
    replay["outputs"]["stray"] = {"pass": "x", "fail": "y"}
    found = problems(suite, replay)
    assert any("quiet-week: no recorded output" in p for p in found)
    assert any("unknown case stray" in p for p in found)
    with pytest.raises(ReplayError):
        replay_suite(suite, replay)


def test_replay_file_for_another_suite_is_rejected():
    suite = suites()["ops-insights-golden"]
    replay = load_replay(replay_path(REPLAY_DIR, suites()["ops-insights-redteam"]))
    assert any("not 'ops-insights-golden'" in p for p in problems(suite, replay))


def test_variants_are_limited():
    suite = suites()["gateway-smoke-pii"]
    with pytest.raises(ReplayError):
        replay_suite(suite, load_replay(replay_path(REPLAY_DIR, suite)), "other")


def test_evals_check_passes_on_the_repository(capsys):
    assert cli.main(["evals", "check", "--suites", str(SUITES_DIR), "--replay", str(REPLAY_DIR)]) == 0
    out = capsys.readouterr().out
    assert "good outputs pass, bad outputs fail" in out and "0 problem(s)" in out


def test_evals_check_fails_when_a_case_cannot_tell_good_from_bad(tmp_path, capsys):
    import shutil

    suites_dir, replay_dir = tmp_path / "suites", tmp_path / "replay"
    shutil.copytree(SUITES_DIR, suites_dir)
    shutil.copytree(REPLAY_DIR, replay_dir)
    path = replay_dir / "gateway-smoke-pii.yaml"
    text = path.read_text(encoding="utf-8")
    # Make the bad output for plain-completion identical to the good one.
    import yaml

    data = yaml.safe_load(text)
    data["outputs"]["plain-completion"]["fail"] = data["outputs"]["plain-completion"]["pass"]
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert cli.main(["evals", "check", "--suites", str(suites_dir), "--replay", str(replay_dir)]) == 1
    assert "cannot tell a bad output from a good one: plain-completion" in capsys.readouterr().out


def test_replay_command_never_writes_evidence(tmp_path, capsys):
    results = tmp_path / "results"
    code = cli.main(["evals", "replay", "gateway-smoke-pii", "--suites", str(SUITES_DIR), "--replay", str(REPLAY_DIR)])
    assert code == 0
    assert "never written as evidence" in capsys.readouterr().out
    assert not results.exists()
