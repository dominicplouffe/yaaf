"""Invariant selftests: access the chairman promised, from promise to proof.

"I'll give you my WordPress login and a Beehiiv key" has to become something
the project keeps asking for, by name, until it is given — and then something
that says how far it is proven. What must hold:

  * a promise made while founding becomes a named setup step, in place of the
    generic one that ticked itself off for any single secret
  * yaaf's catalog decides the fields of a service it knows; the Founder's
    suggestion is kept, labelled as a suggestion, for one it does not
  * the values are ordinary project secrets, and no screen ever shows one
  * a suggested check can only read, and only the public internet over https
  * an agent's report is recorded as its report, and a refusal turns it red
  * the CEO is told what it cannot use yet, and nothing once it can
"""

from __future__ import annotations

import base64

import pytest

from tests.conftest import ADMIN, agent_by_name, bearer, live_token, make_project, person
from yaaf.core.db import db_session
from yaaf.core.models import Secret
from yaaf.server import credential_checks

BEEHIIV = {
    "service": "Beehiiv", "purpose": "send the weekly newsletter", "roles": ["worker"],
    "fields": [{"key": "BEEHIIV_API_KEY", "label": "API key", "help": "Settings › API",
                "secret": True},
               {"key": "BEEHIIV_PUBLICATION_ID", "label": "Publication ID", "secret": False}],
    "check": {"method": "GET",
              "url": "https://api.beehiiv.com/v2/publications/{BEEHIIV_PUBLICATION_ID}",
              "headers": {"Authorization": "Bearer {BEEHIIV_API_KEY}"},
              "reads": "your publication's name"},
}
WORDPRESS = {"service": "wordpress", "purpose": "publish posts",
             "fields": [{"key": "IGNORED", "label": "the catalog wins"}]}


def project_with_promises(client, *promises) -> dict:
    return make_project(client, credentials_promised=list(promises))


def needs(client, project: dict) -> list[dict]:
    response = client.get(f"/observe/projects/{project['id']}/credential_needs", headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def setup(client, project: dict) -> dict:
    return client.get(f"/observe/projects/{project['id']}/setup", headers=ADMIN).json()


def save(client, project: dict, need_id: str, headers=ADMIN, **body):
    return client.put(f"/admin/projects/{project['id']}/credential_needs/{need_id}",
                      json=body, headers=headers)


def test_a_promise_becomes_a_named_setup_step(client):
    project = project_with_promises(client, WORDPRESS, BEEHIIV)
    rows = {n["label"]: n for n in needs(client, project)}
    assert set(rows) == {"WordPress", "Beehiiv"}

    # The catalog decides a known service's fields, whatever the draft said.
    wp = rows["WordPress"]
    assert wp["known"] is True and wp["check"] is None
    assert [f["key"] for f in wp["fields"]] == [
        "WORDPRESS_SITE", "WORDPRESS_USERNAME", "WORDPRESS_APP_PASSWORD"]
    assert {f["origin"] for f in wp["fields"]} == {"catalog"}
    # An unknown one keeps the Founder's suggestion, labelled as one.
    bh = rows["Beehiiv"]
    assert bh["known"] is False
    assert {f["origin"] for f in bh["fields"]} == {"suggested"}
    assert bh["check"]["url"].startswith("https://api.beehiiv.com/")
    assert bh["status"] == "needed" and bh["used_by"] == ["Alpha"]

    steps = setup(client, project)["steps"]
    labels = [s["label"] for s in steps]
    assert "Secrets & variables" not in labels
    assert {"WordPress", "Beehiiv"} <= set(labels)
    assert all(s["state"] == "todo" for s in steps if s["label"] in ("WordPress", "Beehiiv"))


def test_a_check_that_could_change_something_is_not_kept(client):
    writes = {**BEEHIIV, "service": "Writer", "check": {"method": "POST",
                                                        "url": "https://api.example.com/x"}}
    plain_http = {**BEEHIIV, "service": "Plain", "check": {"method": "GET",
                                                           "url": "http://api.example.com/x"}}
    moving = {**BEEHIIV, "service": "Moving", "check": {"method": "GET",
                                                        "url": "https://{HOST}/x"}}
    project = project_with_promises(client, writes, plain_http, moving)
    assert [n["check"] for n in needs(client, project)] == [None, None, None]


def test_a_suggested_check_never_calls_inside_the_network():
    for url in ("https://127.0.0.1/v1/me", "https://localhost/v1/me", "https://10.0.0.5/x"):
        result = credential_checks.run_suggested({"method": "GET", "url": url}, {})
        assert result.ok is False and "won't run" in result.summary, url


def test_values_are_ordinary_secrets_and_never_shown(client):
    project = project_with_promises(client, BEEHIIV)
    need = needs(client, project)[0]
    saved = save(client, project, need["id"],
                 values={"BEEHIIV_API_KEY": "bh_live_secret", "BEEHIIV_PUBLICATION_ID": "pub_7f3a"},
                 add_fields=[{"key": "BEEHIIV_SEGMENT", "label": "Segment ID", "secret": False}])
    assert saved.status_code == 200, saved.text
    body = saved.json()
    # Given, not proven: an unknown service waits for him to run its check.
    assert body["status"] == "saved"
    assert {f["key"]: f["given"] for f in body["fields"]} == {
        "BEEHIIV_API_KEY": True, "BEEHIIV_PUBLICATION_ID": True, "BEEHIIV_SEGMENT": False}
    assert next(f for f in body["fields"] if f["key"] == "BEEHIIV_SEGMENT")["origin"] == "user"
    assert "bh_live_secret" not in str(needs(client, project))
    # The check he is shown fills in the plain value and masks the secret.
    assert body["check"]["url"].endswith("/pub_7f3a")
    assert "bh_live_secret" not in str(body["check"])

    with db_session() as session:
        held = {s.name: s for s in session.query(Secret).filter(
            Secret.project_id == project["id"])}
    assert held["BEEHIIV_API_KEY"].role_scopes == ["worker"]
    assert held["BEEHIIV_PUBLICATION_ID"].visibility == "plain"


def test_a_known_service_is_tested_the_moment_it_is_given(client, monkeypatch):
    seen = {}

    def fake(values):
        seen.update(values)
        return credential_checks.Result(True, "Signed in as dominic (Administrator).")

    monkeypatch.setattr(credential_checks.CATALOG["wordpress"], "test", fake)
    project = project_with_promises(client, WORDPRESS)
    need = needs(client, project)[0]
    body = save(client, project, need["id"], values={
        "WORDPRESS_SITE": "https://example.com", "WORDPRESS_USERNAME": "dominic",
        "WORDPRESS_APP_PASSWORD": "abcd efgh"}).json()
    assert body["status"] == "ok"
    assert body["last_check"]["kind"] == "verified" and "dominic" in body["last_check"]["summary"]
    assert seen["WORDPRESS_APP_PASSWORD"] == "abcd efgh"

    monkeypatch.setattr(credential_checks.CATALOG["wordpress"], "test",
                        lambda values: credential_checks.Result(
                            False, "WordPress refused this username and application password.",
                            ["Make a new application password."]))
    again = client.post(f"/admin/projects/{project['id']}/credential_needs/{need['id']}/check",
                        headers=ADMIN).json()
    assert again["status"] == "failed" and again["last_check"]["fix"]
    step = next(s for s in setup(client, project)["steps"] if s["label"] == "WordPress")
    assert step["state"] == "failed"


def test_a_file_field_is_stored_as_a_file(client):
    project = project_with_promises(client, {"service": "Google Analytics",
                                             "purpose": "read page views"})
    need = needs(client, project)[0]
    key = base64.b64encode(b'{"client_email": "bot@x.iam.gserviceaccount.com"}').decode()
    save(client, project, need["id"], values={"GOOGLE_SERVICE_ACCOUNT": key},
         filenames={"GOOGLE_SERVICE_ACCOUNT": "key.json"})
    with db_session() as session:
        secret = session.query(Secret).filter(Secret.project_id == project["id"],
                                              Secret.name == "GOOGLE_SERVICE_ACCOUNT").one()
        assert secret.filename == "key.json"


def test_an_agent_report_is_recorded_as_its_word_and_a_refusal_turns_it_red(client):
    project = project_with_promises(client, BEEHIIV)
    need = needs(client, project)[0]
    _, token = live_token(project["id"], agent_by_name(project, "Lead"))

    refused = client.post("/agent/credentials/report", headers=bearer(token), json={
        "name": "BEEHIIV_API_KEY", "worked": False, "detail": "401 Unauthorized"}).json()
    assert refused["recorded"] is True
    row = needs(client, project)[0]
    assert row["status"] == "failed"
    assert row["last_use"]["by"] == "Lead" and row["last_use"]["ok"] is False
    assert next(s for s in setup(client, project)["steps"]
                if s["key"] == f"cred:{need['id']}")["state"] == "failed"

    client.post("/agent/credentials/report", headers=bearer(token),
                json={"name": "Beehiiv", "worked": True, "detail": "sent the newsletter"})
    assert needs(client, project)[0]["status"] == "ok"

    unknown = client.post("/agent/credentials/report", headers=bearer(token),
                          json={"name": "STRIPE_KEY", "worked": True}).json()
    assert unknown["recorded"] is False


def test_im_good_without_it_settles_the_step_and_can_be_undone(client):
    project = project_with_promises(client, BEEHIIV)
    key = f"cred:{needs(client, project)[0]['id']}"
    waived = client.post(f"/admin/projects/{project['id']}/setup",
                         json={"action": "skip", "step": key}, headers=ADMIN)
    assert waived.status_code == 200, waived.text
    assert next(s for s in waived.json()["steps"] if s["key"] == key)["state"] == "skipped"
    back = client.post(f"/admin/projects/{project['id']}/setup",
                       json={"action": "unskip", "step": key}, headers=ADMIN).json()
    assert next(s for s in back["steps"] if s["key"] == key)["state"] == "todo"


def test_the_ceo_plans_around_what_is_not_ready_and_hears_nothing_once_it_is(client):
    project = project_with_promises(client, BEEHIIV)
    _, token = live_token(project["id"], project["ceo_agent_id"])
    pack = client.get("/agent/context", headers=bearer(token)).json()
    assert [i["service"] for i in pack["access_not_ready"]["items"]] == ["Beehiiv"]

    need = needs(client, project)[0]
    save(client, project, need["id"],
         values={"BEEHIIV_API_KEY": "k", "BEEHIIV_PUBLICATION_ID": "pub_1"})
    pack = client.get("/agent/context", headers=bearer(token)).json()
    assert "access_not_ready" not in pack


@pytest.mark.parametrize("who", ["observer", "stranger"])
def test_only_a_chair_gives_a_credential(client, who):
    project = project_with_promises(client, BEEHIIV)
    need = needs(client, project)[0]
    someone = person(f"{who}@example.com")
    if who == "observer":
        client.post(f"/admin/projects/{project['id']}/members",
                    json={"email": f"{who}@example.com", "role": "observer"}, headers=ADMIN)
    refused = save(client, project, need["id"], headers=someone,
                   values={"BEEHIIV_API_KEY": "stolen"})
    assert refused.status_code in (403, 404)


def test_the_form_can_test_before_the_project_exists(client):
    result = client.post("/admin/credential_checks", headers=ADMIN, json={
        "service": "Some Service", "values": {"X": "y"}}).json()
    assert result["ok"] is False and result["kind"] == "none"
