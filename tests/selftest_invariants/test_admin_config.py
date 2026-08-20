"""Invariant selftests: the wiring/rules split.

`projects.integrations` is outward wiring — which runtime, which MCP servers,
which Slack workspace. Limits are not wiring, and before the rebuild four of
them lived here anyway: `ceo_heartbeat_hours`, `ceo_heartbeat_always`,
`ceo_daily_at` and `max_concurrent_runs` were project config, which meant they
generated no settings UI, recorded no amendment, and were never disclosed to
the CEO they governed. A limit written to this endpoint is now refused, and
the refusal names the route that does the job properly.
"""

from __future__ import annotations

from tests.conftest import ADMIN, amend, make_project
from yaaf.core.constitution import ConstitutionDoc, effective
from yaaf.core.db import db_session


def test_patch_project_integrations_and_billing(client):
    project = make_project(client, start=False)

    secret = client.post(
        "/admin/secrets",
        json={"project_id": project["id"], "name": "anthropic-api-key",
              "value": "sk-ant-test-not-real", "role_scopes": [], "kind": "billing"},
        headers=ADMIN,
    ).json()

    patched = client.patch(
        f"/admin/projects/{project['id']}",
        json={"billing_secret_id": secret["id"],
              "integrations": {"runtime": "agent_sdk"}},
        headers=ADMIN,
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["billing_secret_id"] == secret["id"]
    assert body["integrations"]["runtime"] == "agent_sdk"

    # Merge, not replace: a later patch keeps earlier keys.
    again = client.patch(
        f"/admin/projects/{project['id']}",
        json={"integrations": {"chat": {"enabled": False}}},
        headers=ADMIN,
    ).json()
    assert again["integrations"]["runtime"] == "agent_sdk"
    assert again["integrations"]["chat"] == {"enabled": False}

    # A foreign or unknown secret is refused.
    bad = client.patch(
        f"/admin/projects/{project['id']}",
        json={"billing_secret_id": "secr_doesnotexist"},
        headers=ADMIN,
    )
    assert bad.status_code == 422


def test_a_limit_cannot_be_smuggled_in_as_wiring(client):
    """The exact failure this split exists to prevent: a limit accepted here
    would be stored, never read, and never disclosed — so the chairman would
    believe he had changed a rule that never moved."""
    project = make_project(client, start=False)

    for rule, value in [("ceo_heartbeat_hours", 2),
                        ("max_concurrent_runs", 9),
                        ("runs_per_project_per_day", 40),
                        ("ceo_daily_at", "09:00")]:
        refused = client.patch(
            f"/admin/projects/{project['id']}",
            json={"integrations": {rule: value}},
            headers=ADMIN,
        )
        assert refused.status_code == 422, f"{rule} was accepted as wiring: {refused.text}"
        detail = refused.json()["detail"]
        assert rule in detail
        assert "amendments" in detail, "the refusal must name the route that works"

    # And nothing was written on the way to being refused.
    dash = client.get(f"/observe/projects/{project['id']}", headers=ADMIN).json()
    for rule in ("ceo_heartbeat_hours", "max_concurrent_runs", "runs_per_project_per_day"):
        assert rule not in dash["integrations"]


def test_a_rule_changes_only_through_an_amendment(client):
    """The same value, set the right way: validated, versioned, and carrying
    the chairman's reason."""
    project = make_project(client, start=False)

    with db_session() as session:
        before = effective(session, project["id"])
    assert before.max_concurrent_runs == ConstitutionDoc().max_concurrent_runs

    amend(client, project["id"], reason="this box is sharing a rate limit",
          max_concurrent_runs=1)

    with db_session() as session:
        after = effective(session, project["id"])
    assert after.max_concurrent_runs == 1

    log = client.get(f"/admin/projects/{project['id']}/amendments", headers=ADMIN)
    assert log.status_code == 200, log.text
    assert any(a["reason"] == "this box is sharing a rate limit" for a in log.json())


def test_an_impossible_rule_is_refused_whole(client):
    """A bad overlay never half-applies: the whole amendment is validated
    against the resulting document before anything is written."""
    project = make_project(client, start=False)

    refused = client.post(
        f"/admin/projects/{project['id']}/amendments",
        json={"reason": "nine in the morning, allegedly",
              "changes": {"ceo_daily_at": "half past nine", "queue_ceiling": 3}},
        headers=ADMIN,
    )
    assert refused.status_code == 422
    assert "HH:MM" in refused.json()["detail"]

    with db_session() as session:
        assert effective(session, project["id"]).queue_ceiling != 3, \
            "the valid half of a refused amendment must not survive"
