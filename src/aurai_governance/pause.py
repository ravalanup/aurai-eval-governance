"""Pause and resume a use case by blocking or unblocking its gateway team (requirement EV-6).

A blocked team's keys are rejected by the gateway, so the use case stops calling models immediately.
Other teams are unaffected. Resuming restores access. Both actions need the gateway admin key.
"""

from __future__ import annotations

import httpx


class PauseError(RuntimeError):
    pass


def _post(client: httpx.Client, path: str, team_id: str) -> None:
    response = client.post(path, json={"team_id": team_id})
    if response.status_code >= 300:
        raise PauseError(f"{path} failed for team {team_id}: HTTP {response.status_code} {response.text[:200]}")


def pause_team(client: httpx.Client, team_id: str) -> None:
    _post(client, "/team/block", team_id)


def resume_team(client: httpx.Client, team_id: str) -> None:
    _post(client, "/team/unblock", team_id)
