"""Invariant selftests: what the Secrets & variables list may show, and to whom.

The list is polled every few seconds, so a secret's value never rides it. Two
things now do, and each is attacked here from the side that must not get it:

- a *plain* value (a timezone, an account id) rides it whole, because the
  chairman said it is not a secret — but only to someone who could press the
  eye anyway, never to an observer, never to a model;
- a secret's last four characters ride it for the same people, so two keys can
  be told apart without two audited reveals — and never for a value short
  enough that four characters are most of it.

And the regression that started the page's rebuild: rotating a value used to
drop every grant, because an omitted `role_scopes` meant "nobody".
"""

from __future__ import annotations

import base64
import json

import pytest

from tests.conftest import ADMIN, headers_of, make_project
from tests.selftest_invariants.test_auth_roles import seat
from tests.selftest_invariants.test_mcp_confirm import call
from tests.selftest_invariants.test_mcp_secrets import configurer
from yaaf.server.api import admin as admin_module

SECRET = "sk-test-not-a-real-key-7731"


@pytest.fixture(autouse=True)
def offline_check(monkeypatch):
    monkeypatch.setattr(admin_module, "_check_data_source",
                        lambda url, bearer: {"ok": True, "state": "ok", "detail": "fine"})


def _store(client, project_id, name, value, **extra):
    response = client.post("/admin/secrets", json={
        "project_id": project_id, "name": name, "value": value, **extra}, headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def _listed(client, project_id, headers=ADMIN) -> dict[str, dict]:
    rows = client.get(f"/admin/secrets?project_id={project_id}", headers=headers)
    assert rows.status_code == 200, rows.text
    return {r["name"]: r for r in rows.json()}


def test_rotating_a_value_keeps_who_has_it(client):
    """The Settings eye-and-save sent only the new value. The door read the
    missing grants as "nobody", and the key quietly left every sandbox."""
    project = make_project(client, start=False)
    _store(client, project["id"], "stripe-key", SECRET, role_scopes=["worker"])

    _store(client, project["id"], "stripe-key", SECRET + "-rotated")
    assert _listed(client, project["id"])["stripe-key"]["role_scopes"] == ["worker"]

    # Saying "nobody" out loud still means nobody.
    _store(client, project["id"], "stripe-key", SECRET, role_scopes=[])
    assert _listed(client, project["id"])["stripe-key"]["role_scopes"] == []


def test_a_plain_value_rides_the_list_and_a_secret_shows_four_characters(client):
    project = make_project(client, start=False)
    _store(client, project["id"], "report-timezone", "America/Toronto",
           role_scopes=["worker"], visibility="plain")
    _store(client, project["id"], "stripe-key", SECRET, role_scopes=["worker"])
    _store(client, project["id"], "short-pin", "hunter2")

    rows = _listed(client, project["id"])
    assert rows["report-timezone"]["visibility"] == "plain"
    assert rows["report-timezone"]["value"] == "America/Toronto"
    assert rows["stripe-key"]["visibility"] == "secret"
    assert "value" not in rows["stripe-key"]
    assert rows["stripe-key"]["hint"] == SECRET[-4:]
    # Four of seven is most of a password: no hint at all.
    assert "hint" not in rows["short-pin"] and "value" not in rows["short-pin"]
    assert SECRET not in json.dumps(list(rows.values()))


def test_an_observer_sees_names_and_nothing_of_any_value(client):
    """An observer cannot press the eye, so nothing the eye would show reaches
    them by the back door of the polled list — not a plain value, not a hint."""
    project = make_project(client, start=False)
    _store(client, project["id"], "report-timezone", "America/Toronto", visibility="plain")
    _store(client, project["id"], "stripe-key", SECRET)
    observer = seat(client, project, "obs@example.com", "observer")

    rows = _listed(client, project["id"], headers=headers_of(observer))
    assert set(rows) == {"report-timezone", "stripe-key"}
    for row in rows.values():
        assert "value" not in row and "hint" not in row
    assert "America/Toronto" not in json.dumps(list(rows.values()))


def test_a_model_never_gets_a_plain_value_or_a_hint(client):
    """A plain value is not a secret to the chairman; it is still not the
    model client's to read. The credential tool strips both."""
    project = make_project(client, slug="acme")
    _store(client, project["id"], "report-timezone", "America/Toronto", visibility="plain")
    _store(client, project["id"], "stripe-key", SECRET)
    token = configurer(client)["token"]

    failed, listing = call(client, token, "credential", {"project": "acme", "verb": "list"})
    assert failed is False
    for row in listing["credentials"]:
        assert "value" not in row and "hint" not in row
    assert "America/Toronto" not in json.dumps(listing)
    assert SECRET[-4:] not in json.dumps(listing)


def test_a_model_rotating_without_roles_keeps_them_too(client):
    project = make_project(client, slug="acme")
    _store(client, project["id"], "stripe-key", SECRET, role_scopes=["worker"])
    token = configurer(client)["token"]

    _, preview = call(client, token, "credential", {
        "project": "acme", "verb": "save", "name": "stripe-key", "value": SECRET + "x"})
    assert preview["about_to"]["roles"] == "unchanged"
    failed, _ = call(client, token, "credential", {
        "project": "acme", "verb": "save", "name": "stripe-key", "value": SECRET + "x",
        "confirm": preview["confirm"]})
    assert failed is False
    assert _listed(client, project["id"])["stripe-key"]["role_scopes"] == ["worker"]


def test_plain_is_refused_where_whole_would_be_wrong(client):
    project = make_project(client, start=False)

    as_file = client.post("/admin/secrets", headers=ADMIN, json={
        "project_id": project["id"], "name": "gcp-key", "visibility": "plain",
        "value": base64.b64encode(b'{"type": "service_account"}').decode(),
        "filename": "gcp.json"})
    assert as_file.status_code == 422 and "file" in as_file.json()["detail"]

    client.post(f"/admin/projects/{project['id']}/mcp_servers", headers=ADMIN, json={
        "name": "ppc", "url": "http://data.example.com:3845/mcp", "roles": ["worker"],
        "bearer": "tok-not-real-000000"})
    for owned in ("mcp-ppc-url", "mcp-ppc-token"):
        refused = client.post("/admin/secrets", headers=ADMIN, json={
            "project_id": project["id"], "name": owned, "visibility": "plain"})
        assert refused.status_code == 422, owned
        assert "data source 'ppc'" in refused.json()["detail"]

    platform = client.post("/admin/secrets", headers=ADMIN, json={
        "project_id": None, "name": "shared-tz", "value": "UTC", "visibility": "plain"})
    assert platform.status_code == 422


def test_visibility_can_change_and_is_on_the_record(client):
    project = make_project(client, start=False)
    row = _store(client, project["id"], "ads-account", "3829104455102")
    before = _listed(client, project["id"])["ads-account"]["updated_at"]

    flipped = client.post("/admin/secrets", headers=ADMIN, json={
        "project_id": project["id"], "name": "ads-account", "visibility": "plain"})
    assert flipped.status_code == 200, flipped.text
    after = _listed(client, project["id"])["ads-account"]
    assert after["value"] == "3829104455102"
    assert after["updated_at"] >= before

    history = client.get(f"/admin/secrets/{row['id']}/history", headers=ADMIN).json()
    assert history[0]["what"] == "Made it a plain value"
    assert history[-1]["what"].startswith("Added it")


def test_history_reads_back_grants_and_reveals_in_sentences(client):
    project = make_project(client, start=False)
    row = _store(client, project["id"], "stripe-key", SECRET, role_scopes=[])
    client.post("/admin/secrets", headers=ADMIN, json={
        "project_id": project["id"], "name": "stripe-key", "role_scopes": ["worker"]})
    client.get(f"/admin/secrets/{row['id']}/value", headers=ADMIN)

    history = client.get(f"/admin/secrets/{row['id']}/history", headers=ADMIN)
    assert history.status_code == 200, history.text
    lines = [h["what"] for h in history.json()]
    # Grants are role slugs underneath; history names the team that runs one.
    assert lines == ["Revealed the value", "Gave it to Alpha", "Added it for nobody"]
    assert all(h["by"] != "yaaf" for h in history.json()), "every row names a person"
    assert SECRET not in history.text


def test_history_is_for_the_people_who_may_reveal(client):
    """It lists every reveal by name — the chairman's audit of his own people."""
    project = make_project(client, start=False)
    row = _store(client, project["id"], "stripe-key", SECRET)
    observer = seat(client, project, "obs@example.com", "observer")

    refused = client.get(f"/admin/secrets/{row['id']}/history", headers=headers_of(observer))
    assert refused.status_code == 403


def test_a_full_backup_and_a_clone_keep_a_plain_value_plain(client):
    project = make_project(client, start=False)
    _store(client, project["id"], "report-timezone", "America/Toronto",
           role_scopes=["worker"], visibility="plain")

    backup = client.get(f"/admin/projects/{project['id']}/export?include_values=true",
                        headers=ADMIN).json()
    exported = next(s for s in backup["secrets"] if s["name"] == "report-timezone")
    assert exported["visibility"] == "plain"

    twin = client.post(f"/admin/projects/{project['id']}/clone",
                       json={"slug": "acme-twin"}, headers=ADMIN)
    assert twin.status_code == 200, twin.text
    copied = _listed(client, twin.json()["id"])["report-timezone"]
    assert copied["visibility"] == "plain" and copied["value"] == "America/Toronto"
