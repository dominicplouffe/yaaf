"""Invariant selftests: the plan view is one dataset, and the path is real.

The chairman asked for a visual plan twice — "either a waterfall type schedule
or some workflow diagram" — and twice it was deferred to last and dropped. This
stage ships it third of ten, and these tests pin the properties that make it
worth looking at rather than merely present:

  * both views read ONE response, so they cannot disagree with each other
  * the critical path is computed server-side from the same arithmetic the
    refusals quote — the screen can never argue with the gate
  * settled work conducts but does not cost: met requirements stay in the
    drawing without inflating the number beside it
"""

from __future__ import annotations

from tests.conftest import ADMIN, make_project
from tests.selftest_invariants.test_requirements import (
    accept,
    ceo_token,
    deliver_project,
    make_task,
    propose,
)
from yaaf.core.db import db_session
from yaaf.server import policy


def plan_view(client, project: dict) -> dict:
    response = client.get(f"/observe/projects/{project['id']}/plan", headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def test_an_explore_project_reports_planless_rather_than_empty(client):
    """"No plan by design" and "no plan yet" are different answers, and the
    screen needs to know which it is showing."""
    project = make_project(client, slug="explorer")
    view = plan_view(client, project)
    assert view == {"mode": "explore", "planless": True}


def test_both_views_read_one_response(client):
    """Everything either view draws — stages, edges, tasks, spend, the path,
    the plan gate's own state — arrives in one payload. A second request would
    be a second chance to disagree."""
    project = deliver_project(client)
    token = ceo_token(project)
    first = propose(client, token, statement="The data exists and is sourced", stage=1)
    accept(client, first["requirement_id"], project)
    second = propose(client, token, statement="The report is built from the data",
                     stage=2, depends_on=[first["requirement_id"]])
    accept(client, second["requirement_id"], project)
    created = make_task(client, token, project, first["requirement_id"])
    assert created.status_code == 200, created.text

    view = plan_view(client, project)
    assert view["planless"] is False
    assert view["plan"]["holds"] is True
    assert view["open_stage"] == 1

    by_key = {r["key"]: r for r in view["requirements"]}
    assert set(by_key) == {"R-01", "R-02"}
    assert "R-00" not in by_key, "the gate is the plan block, not a row of scope"
    assert by_key["R-02"]["depends_on"] == ["R-01"]
    assert len(by_key["R-01"]["tasks"]) == 1
    assert by_key["R-01"]["tasks"][0]["status"] == "ready"
    assert by_key["R-01"]["status"] == "in_progress"


def test_the_critical_path_is_the_longest_unsettled_chain(client):
    """A diamond: R-01 feeds R-02 (heavy) and R-03 (light), both feed R-04.
    The path must run through the heavy branch."""
    project = deliver_project(client)
    token = ceo_token(project)
    a = propose(client, token, statement="A the foundation is laid", est_runs=5)
    accept(client, a["requirement_id"], project)
    heavy = propose(client, token, statement="B the heavy branch is built",
                    est_runs=20, depends_on=[a["requirement_id"]])
    accept(client, heavy["requirement_id"], project)
    light = propose(client, token, statement="C the light branch is built",
                    est_runs=2, depends_on=[a["requirement_id"]])
    accept(client, light["requirement_id"], project)
    top = propose(client, token, statement="D the two halves are joined",
                  est_runs=3, depends_on=[heavy["requirement_id"],
                                          light["requirement_id"]])
    accept(client, top["requirement_id"], project)

    view = plan_view(client, project)
    path = view["critical_path"]
    assert path["keys"] == ["R-01", "R-02", "R-04"], path
    assert path["adjusted_runs"] == 28  # 5 + 20 + 3, no bias history yet


def test_settled_work_conducts_but_does_not_cost(client):
    """Cutting the heavy branch reroutes the path through the light one — a
    met or cut requirement stays in the drawing but weighs nothing, so the
    number beside the path is always the work still ahead."""
    project = deliver_project(client)
    token = ceo_token(project)
    a = propose(client, token, statement="A the foundation is laid", est_runs=5)
    accept(client, a["requirement_id"], project)
    heavy = propose(client, token, statement="B the heavy branch is built",
                    est_runs=20, depends_on=[a["requirement_id"]])
    accept(client, heavy["requirement_id"], project)
    light = propose(client, token, statement="C the light branch is built",
                    est_runs=2, depends_on=[a["requirement_id"]])
    accept(client, light["requirement_id"], project)
    top = propose(client, token, statement="D the two halves are joined",
                  est_runs=3, depends_on=[heavy["requirement_id"],
                                          light["requirement_id"]])
    accept(client, top["requirement_id"], project)

    cut = client.post(f"/admin/requirements/{heavy['requirement_id']}/cut",
                      json={"reason": "the client already owns this half"}, headers=ADMIN)
    assert cut.status_code == 200

    path = plan_view(client, project)["critical_path"]
    assert path["keys"] == ["R-01", "R-03", "R-04"], path
    assert path["adjusted_runs"] == 10  # 5 + 2 + 3


def test_the_path_quotes_the_same_arithmetic_as_the_gate(client):
    """The multiplier that adjusts the feasibility refusal adjusts the path.
    Two numbers for the same question is how screens stop being trusted."""
    project = deliver_project(client)
    with db_session() as session:
        multiplier, _ = policy.estimate_bias(session, project["id"])
    view_multiplier = None
    token = ceo_token(project)
    accept(client, propose(client, token, est_runs=8)["requirement_id"], project)
    view = plan_view(client, project)
    view_multiplier = view["critical_path"].get("multiplier")
    assert view_multiplier == round(multiplier, 2)
    assert view["critical_path"]["adjusted_runs"] == round(8 * multiplier)
    assert view["plan"]["arithmetic"]["adjusted_runs"] == round(8 * multiplier)
