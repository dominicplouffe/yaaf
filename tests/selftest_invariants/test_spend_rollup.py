"""The spend rollup groups runs by model/team/agent and never loses a run."""

from __future__ import annotations

from tests.conftest import ADMIN, make_project
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import Run, RunStatus, utcnow


def _add_run(project_id: str, agent_id: str, model: str | None = None,
             cost: float | None = None, tokens_out: int = 0, cache_read: int = 0) -> None:
    with db_session() as session:
        session.add(Run(id=ids.new_id(ids.RUN), project_id=project_id, agent_id=agent_id,
                        trigger={"type": "test"}, status=RunStatus.DONE, model=model,
                        cost_usd=cost, tokens_out=tokens_out, cache_read_tokens=cache_read,
                        started_at=utcnow(), finished_at=utcnow()))


def test_spend_rollup_by_model_and_team(client):
    project = make_project(client)
    ceo = project["ceo_agent_id"]
    _add_run(project["id"], ceo, model="claude-opus-5", cost=2.0, tokens_out=10)
    _add_run(project["id"], ceo, model="claude-opus-5", cost=1.0, cache_read=5_000)
    _add_run(project["id"], ceo, model=None, cost=0.5)  # pre-migration history

    by_model = client.get(f"/observe/projects/{project['id']}/spend?group_by=model",
                          headers=ADMIN).json()
    rows = {r["key"]: r for r in by_model["rows"]}
    assert rows["claude-opus-5"]["runs"] == 2
    assert rows["claude-opus-5"]["usd"] == 3.0
    assert rows["claude-opus-5"]["cache_read"] == 5_000
    assert rows["unknown"]["runs"] == 1
    assert by_model["rows"][0]["key"] == "claude-opus-5", "sorted by usd desc"

    by_team = client.get(f"/observe/projects/{project['id']}/spend?group_by=team",
                         headers=ADMIN).json()
    # The CEO has no team; it rolls up under its own name, not "unknown".
    assert "CEO" in [r["label"] for r in by_team["rows"]]

    bad = client.get(f"/observe/projects/{project['id']}/spend?group_by=nope", headers=ADMIN)
    assert bad.status_code == 400
