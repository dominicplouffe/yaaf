"""Attack the managed credential boundary over HTTP, including legacy doors."""
import importlib

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations

from tests.conftest import ADMIN, bearer, live_token, make_project
from yaaf.core.crypto import decrypt, encrypt
from yaaf.core.db import db_session
from yaaf.core.models import Project, Secret
from yaaf.runner.daemon import _billing_env

URL = "/admin/settings/llm-credentials"


def create(client, name="Production", value="test-credential-value", mode="api_key"):
    response = client.post(URL, headers=ADMIN, json={"name": name, "value": value, "mode": mode})
    assert response.status_code == 201, response.text
    return response.json()


def test_shared_assignment_rotation_and_deletion_guards(client):
    key = create(client)
    projects = [make_project(client, slug=slug, start=False, billing_secret_id=key["id"])
                for slug in ("first", "second")]
    rows = client.get(URL, headers=ADMIN).json()
    assert len(rows[0]["projects"]) == 2
    assert "value" not in rows[0] and "ciphertext" not in rows[0]
    for endpoint in (f"{URL}/{key['id']}", f"/admin/secrets/{key['id']}"):
        assert client.delete(endpoint, headers=ADMIN).status_code == 409
    updated = client.patch(f"{URL}/{key['id']}", headers=ADMIN,
                           json={"name": "Renamed", "value": "replacement-test-value"})
    assert updated.status_code == 200
    with db_session() as session:
        for p in projects:
            project = session.get(Project, p["id"])
            assert _billing_env(session, project) == {"ANTHROPIC_API_KEY": "replacement-test-value"}
        assert session.get(Secret, key["id"]).ciphertext != "replacement-test-value"
    spare = create(client, "Replacement")
    for p in projects:
        assert client.patch(f"/admin/projects/{p['id']}", headers=ADMIN,
                            json={"billing_secret_id": spare["id"]}).status_code == 200
    assert client.delete(f"{URL}/{key['id']}", headers=ADMIN).status_code == 200


def test_subscription_mode_is_derived_and_clone_keeps_assignment(client):
    key = create(client, value="sk-ant-oat-test-placeholder", mode="subscription")
    project = make_project(client, start=False, billing_secret_id=key["id"])
    with db_session() as session:
        row = session.get(Project, project["id"])
        assert row.billing_mode == "subscription"
        assert _billing_env(session, row) == {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-test-placeholder"}
    cloned = client.post(f"/admin/projects/{project['id']}/clone", headers=ADMIN,
                         json={"slug": "cloned"})
    assert cloned.status_code == 200, cloned.text
    with db_session() as session:
        assert session.get(Project, cloned.json()["id"]).billing_secret_id == key["id"]
    assert len(client.get(URL, headers=ADMIN).json()[0]["projects"]) == 2
    assert client.patch(f"{URL}/{key['id']}", headers=ADMIN,
                        json={"name": "Changed", "value": "not-a-subscription"}).status_code == 422


def test_validation_and_legacy_write_refusal(client):
    key = create(client)
    assert client.post(URL, headers=ADMIN, json={"name": " production ", "mode": "api_key",
                                               "value": "test-credential-value"}).status_code == 409
    for name, value in (("   ", "test-credential-value"), ("Blank", "        ")):
        assert client.post(URL, headers=ADMIN, json={"name": name, "mode": "api_key",
                                                   "value": value}).status_code == 422
    project = make_project(client, start=False)
    assert client.post("/admin/secrets", headers=ADMIN, json={"project_id": project["id"],
        "name": "Manual", "kind": "billing", "value": "test-credential-value"}).status_code == 422
    assert client.post("/admin/secrets", headers=ADMIN, json={"name": key["name"],
        "value": "replacement-test-value"}).status_code == 422
    assert client.patch(f"/admin/projects/{project['id']}", headers=ADMIN,
                        json={"billing_secret_id": "missing"}).status_code == 422
    assert client.patch(f"{URL}/{key['id']}", headers=ADMIN,
                        json={"name": "Rename only"}).status_code == 200
    with db_session() as session:
        assert decrypt(session.get(Secret, key["id"]).ciphertext) == "test-credential-value"


def test_only_admin_can_manage_credentials(client):
    key = create(client)
    project = make_project(client)
    token, _ = live_token(project["id"], project["ceo_agent_id"])
    for headers in ({}, bearer(token)):
        assert client.get(URL, headers=headers).status_code == 401
        assert client.post(URL, headers=headers, json={"name": "Forged", "mode": "api_key",
                           "value": "test-credential-value"}).status_code == 401
        assert client.patch(f"{URL}/{key['id']}", headers=headers,
                            json={"name": "Forged"}).status_code == 401
        assert client.delete(f"{URL}/{key['id']}", headers=headers).status_code == 401


@pytest.mark.parametrize("mode,value", [("api_key", "test-credential-value"),
                                       ("subscription", "sk-ant-oat-test-placeholder")])
def test_migration_preserves_ciphertext_and_assignments(client, mode, value):
    project = make_project(client, start=False, billing_mode=mode)
    with db_session() as session:
        ciphertext = encrypt(value)
        session.add(Secret(id="legacy-billing", project_id=project["id"], name="Original",
                           ciphertext=ciphertext, kind="billing", role_scopes=[]))
        session.flush()
        session.get(Project, project["id"]).billing_secret_id = "legacy-billing"
    with db_session() as session:
        migration = importlib.import_module("migrations.versions.0034_managed_llm_credentials")
        with Operations.context(MigrationContext.configure(session.connection())):
            migration.upgrade()
        session.expire_all()
        row = session.get(Secret, "legacy-billing")
        assert row.ciphertext == ciphertext
        assert row.project_id is None and row.llm_mode == mode
        assert session.get(Project, project["id"]).billing_secret_id == row.id
    renamed = client.patch(f"{URL}/legacy-billing", headers=ADMIN, json={"name": "Migrated key"})
    assert renamed.status_code == 200
    assert client.delete(f"{URL}/legacy-billing", headers=ADMIN).status_code == 409


def test_legacy_platform_assignment_is_preserved_separately(client):
    from yaaf.core import platform

    project = make_project(client, start=False)
    with db_session() as session:
        platform.set_credential(session, "founder-test-value")
        session.flush()
        original = session.query(Secret).filter_by(name=platform.PLATFORM_BILLING_SECRET).one()
        original_id, ciphertext = original.id, original.ciphertext
        session.get(Project, project["id"]).billing_secret_id = original_id
    with db_session() as session:
        migration = importlib.import_module("migrations.versions.0034_managed_llm_credentials")
        with Operations.context(MigrationContext.configure(session.connection())):
            migration.upgrade()
        session.expire_all()
        attached = session.get(Project, project["id"]).billing_secret_id
        assert attached != original_id
        row = session.get(Secret, attached)
        assert row.ciphertext == ciphertext and row.llm_mode == "api_key"
        assert platform.credential(session) == "founder-test-value"
    assert client.delete(f"{URL}/{attached}", headers=ADMIN).status_code == 409


def test_credential_type_and_role_grants_cannot_change(client):
    key = create(client)
    for patch in ({"mode": "subscription"}, {"role_scopes": ["worker"]}):
        assert client.patch(f"{URL}/{key['id']}", headers=ADMIN,
                            json={"name": "Production", **patch}).status_code == 422
    for name in ("platform-billing", "github"):
        assert client.post(URL, headers=ADMIN, json={"name": name, "mode": "api_key",
                           "value": "test-credential-value"}).status_code == 422


# ── hiding a credential ─────────────────────────────────────────────────────────
#
# The verb exists because lifting per-project keys above projects made every
# key an install had ever used a permanent, undeletable row on one page. These
# attack the promise it makes: hidden means *unofferable everywhere*, and it
# can never be used to make a live project's money unexplainable.


def hide(client, *ids, undo=False):
    return client.post(f"{URL}/{'unhide' if undo else 'hide'}", headers=ADMIN,
                       json={"ids": list(ids)})


def test_hidden_credential_leaves_every_door_and_comes_back(client):
    from yaaf.core import llm_credentials
    from yaaf.founder import tools as founder_tools

    keep = create(client, "Keep me")
    key = create(client, "Retired key")
    assert hide(client, key["id"]).json()["hidden"] == [{"id": key["id"], "name": "Retired key"}]

    # The list, the picker and the import matcher all read this one route.
    listed = client.get(URL, headers=ADMIN).json()
    assert [r["id"] for r in listed] == [keep["id"]]
    drawer = client.get(f"{URL}?include_hidden=true", headers=ADMIN).json()
    assert {r["id"] for r in drawer} == {keep["id"], key["id"]}
    assert next(r for r in drawer if r["id"] == key["id"])["hidden_at"] is not None

    with db_session() as session:
        # The Founder's menu, the MCP spec's name lookup, and the sentence a
        # refusal quotes — all of them go through the same two functions.
        assert [r["id"] for r in llm_credentials.choices(session)] == [keep["id"]]
        assert llm_credentials.resolve(session, credential_id=key["id"]) is None
        assert llm_credentials.resolve(session, name="Retired key") is None
        assert "Retired key" not in llm_credentials.menu(session)
        # One visible credential means the Founder is told to name it rather
        # than to ask — which is what an inventory of dead keys was costing.
        assert "Exactly one" in founder_tools.list_credentials(session)["note"]

    project = make_project(client, start=False)
    for attempt in (client.patch(f"/admin/projects/{project['id']}", headers=ADMIN,
                                 json={"billing_secret_id": key["id"]}),
                    client.post("/admin/projects", headers=ADMIN,
                                json={"slug": "second", "name": "Second", "goal": "Ship the thing end to end.",
                                      "chairman_email": "chair@example.com",
                                      "ceo_role_slug": "ceo", "ceo_name": "CEO",
                                      "billing_secret_id": key["id"]})):
        assert attempt.status_code == 422, attempt.text
        assert "hidden" in attempt.json()["detail"]

    assert hide(client, key["id"], undo=True).json()["restored"][0]["id"] == key["id"]
    assert client.patch(f"/admin/projects/{project['id']}", headers=ADMIN,
                        json={"billing_secret_id": key["id"]}).status_code == 200


def test_hiding_is_refused_while_a_live_project_can_still_spend(client):
    key = create(client)
    project = make_project(client, start=False, billing_secret_id=key["id"])
    refused = hide(client, key["id"]).json()
    assert refused["hidden"] == []
    assert "Acme" in refused["refused"][0]["reason"]
    with db_session() as session:
        assert session.get(Secret, key["id"]).hidden_at is None

    # A draft is not running and is still live: it can start tomorrow. Only an
    # ended project makes the credential history.
    assert client.post(f"/admin/projects/{project['id']}/stop", headers=ADMIN,
                       json={"reason": "done"}).status_code == 200
    assert hide(client, key["id"]).json()["refused"] == []
    # The receipt survives: the stopped project still names what paid for it,
    # and the row is still undeletable because of it.
    drawer = client.get(f"{URL}?include_hidden=true", headers=ADMIN).json()[0]
    assert drawer["in_use"] is False
    assert drawer["projects"] == [{"id": project["id"], "name": "Acme", "status": "stopped",
                                  "live": False}]
    assert client.delete(f"{URL}/{key['id']}", headers=ADMIN).status_code == 409


def test_in_use_counts_only_projects_that_can_still_spend(client):
    from yaaf.core import llm_credentials

    key = create(client)
    live = make_project(client, slug="live", start=False, billing_secret_id=key["id"])
    done = make_project(client, slug="done", start=False, billing_secret_id=key["id"])
    assert client.post(f"/admin/projects/{done['id']}/stop", headers=ADMIN,
                       json={"reason": "done"}).status_code == 200
    row = client.get(URL, headers=ADMIN).json()[0]
    assert row["in_use"] is True
    assert {p["id"]: p["live"] for p in row["projects"]} == {live["id"]: True, done["id"]: False}
    with db_session() as session:
        assert llm_credentials.choices(session)[0]["projects_using_it"] == 1


def test_hiding_releases_the_name_and_restoring_says_who_took_it(client):
    old = create(client, "Production")
    assert hide(client, old["id"]).json()["refused"] == []
    fresh = create(client, "production")  # the name is free the moment nothing shows it
    refused = hide(client, old["id"], undo=True).json()["refused"][0]
    assert "rename this one" in refused["reason"]
    assert client.patch(f"{URL}/{old['id']}", headers=ADMIN,
                        json={"name": "Production (retired)"}).status_code == 200
    assert hide(client, old["id"], undo=True).json()["refused"] == []
    assert {r["name"] for r in client.get(URL, headers=ADMIN).json()} == \
        {"production", "Production (retired)"}
    assert fresh["id"] != old["id"]


def test_cloning_a_project_whose_credential_was_hidden_starts_unassigned(client):
    """The twin of a finished project is exactly what gets cloned after a
    cleanup, and refusing the whole clone over the least interesting field it
    copies would be the wrong answer."""
    key = create(client)
    source = make_project(client, start=False, billing_secret_id=key["id"])
    assert client.post(f"/admin/projects/{source['id']}/stop", headers=ADMIN,
                       json={"reason": "done"}).status_code == 200
    assert hide(client, key["id"]).json()["refused"] == []

    twin = client.post(f"/admin/projects/{source['id']}/clone", headers=ADMIN,
                       json={"slug": "twin"})
    assert twin.status_code == 200, twin.text
    assert twin.json()["copied"]["billing_credential_hidden"] == "Production"
    with db_session() as session:
        assert session.get(Project, twin.json()["id"]).billing_secret_id is None
    # And it says so where the chairman is already looking for reasons a
    # project cannot run.
    health = client.get(f"/observe/projects/{twin.json()['id']}/health", headers=ADMIN)
    billing = next(c for c in health.json()["checks"] if c["key"] == "billing")
    assert billing["status"] == "fail"


def test_bulk_hide_judges_each_row_and_only_admin_may(client):
    from tests.conftest import bearer, live_token

    spare = create(client, "Spare")
    busy = create(client, "Busy")
    project = make_project(client, start=False, billing_secret_id=busy["id"])
    result = hide(client, spare["id"], busy["id"], "sec_nonexistent").json()
    # One refusal must not cost the other thirty-nine their cleanup.
    assert [r["name"] for r in result["hidden"]] == ["Spare"]
    assert [r["id"] for r in result["refused"]] == [busy["id"], "sec_nonexistent"]

    token, _ = live_token(project["id"], project["ceo_agent_id"])
    for headers in ({}, bearer(token)):
        for verb in ("hide", "unhide"):
            assert client.post(f"{URL}/{verb}", headers=headers,
                               json={"ids": [spare["id"]]}).status_code == 401


def test_credentials_holding_one_key_say_so_without_showing_it(client):
    """The name cannot tell two credentials apart; a digest of the value can.

    Promoting per-project keys named each credential after the project
    variable it came from, so an install ended up with twelve rows called
    "Migrated anthropic-api-key" that were one `sk-ant-oat` subscription —
    correctly typed, identically named, and impossible to choose between.
    """
    token = "sk-ant-oat01-one-key-behind-many-names"
    same = [create(client, name, value=token, mode="subscription")
            for name in ("Migrated anthropic-api-key (a)", "Migrated anthropic-api-key (b)",
                         "Something else entirely")]
    alone = create(client, "Its own key", value="sk-ant-oat01-a-key-of-its-own",
                   mode="subscription")

    rows = {r["id"]: r for r in client.get(URL, headers=ADMIN).json()}
    digests = {rows[c["id"]]["token"] for c in same}
    assert len(digests) == 1, "one key, one digest, whatever the rows are called"
    assert rows[alone["id"]]["token"] not in digests
    for c in same:
        assert rows[c["id"]]["shares_token_with"] == 2
    assert rows[alone["id"]]["shares_token_with"] == 0

    # The digest identifies; it never reveals. Nothing on the wire is the key,
    # and the digest is not a prefix of it either.
    body = client.get(URL, headers=ADMIN).text
    assert token not in body and "sk-ant" not in body
    digest = digests.pop()
    assert len(digest) == 8 and digest not in token

    # Hiding one does not change what the others are: the grouping is about
    # the value, not about what the screen is currently willing to draw.
    assert hide(client, same[0]["id"]).json()["refused"] == []
    listed = {r["id"]: r for r in client.get(f"{URL}?include_hidden=true", headers=ADMIN).json()}
    assert listed[same[1]["id"]]["shares_token_with"] == 2
    assert listed[same[0]["id"]]["token"] == digest


# ── the plan meter ──────────────────────────────────────────────────────────────


class _Response:
    def __init__(self, status_code: int, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


METER = {
    "five_hour": {"utilization": 0.21, "resets_at": "2026-09-08T15:22:00Z"},
    "seven_day": {"utilization": 0.51, "resets_at": 1757395920},
    "seven_day_overage_included": {"utilization": 0.9},
    "by_model": [{"scope": {"model": {"display_name": "Fable"}}, "percent": 0.24,
                  "resets_at": "2026-09-09T02:24:00Z"}],
    "some_future_field": {"unrelated": True},
}


def sandbox_plan_windows():
    """`_plan_windows` out of the sandbox entry script.

    That file is copied into the sandbox image on its own, with no yaaf package
    beside it, so it reads its run environment at import time and cannot simply
    be imported here — the same reason `test_data_fetch` reloads it under a
    fake environment.
    """
    import importlib
    import os

    os.environ.setdefault("YAAF_API_BASE_URL", "http://api:8080")
    os.environ.setdefault("YAAF_API_TOKEN", "test-token")
    os.environ.setdefault("YAAF_RUN_ID", "run_test")
    os.environ.setdefault("YAAF_TOOL_PERMISSIONS", "{}")
    import yaaf.runner.sandbox_entry as mod

    return importlib.reload(mod)._plan_windows


#: A RateLimitEvent exactly as the SDK delivered it on this install. The typed
#: `utilization` is None — it describes only the window the event fired about —
#: while every window's real number sits in `raw["unifiedWindows"]`. Reading
#: the typed field and shipping it would have produced a meter of zeroes.
LIVE_EVENT_RAW = {
    "status": "allowed",
    "resetsAt": 1788901800,
    "rateLimitType": "five_hour",
    "overageStatus": "rejected",
    "isUsingOverage": False,
    "unifiedWindows": {
        "five_hour": {"utilization": 0.31, "resetsAt": 1788901800},
        "seven_day": {"utilization": 0.14, "resetsAt": 1788922800},
    },
}


def live_event_today() -> dict:
    """The live event with its clock moved, and nothing else touched.

    A captured payload has one perishable field. These reset times were a few
    hours ahead of the capture and are now days behind, and a reading whose
    windows have all reset is — correctly — no longer drawn as the plan, so the
    calendar would otherwise decide what these tests assert. The whole event is
    shifted by one offset, which keeps the spacing the live host reported;
    `LIVE_EVENT_RAW` stays verbatim for the parser, which is what it was
    captured for.
    """
    import copy
    from datetime import UTC, datetime, timedelta

    raw = copy.deepcopy(LIVE_EVENT_RAW)
    shift = (datetime.now(UTC) + timedelta(hours=2)).timestamp() - raw["resetsAt"]
    raw["resetsAt"] += shift
    for window in raw["unifiedWindows"].values():
        window["resetsAt"] += shift
    return raw


class _Info:
    """Duck-typed stand-in for claude_agent_sdk.RateLimitInfo."""

    def __init__(self, raw, status="allowed", utilization=None):
        self.raw = raw
        self.status = status
        self.utilization = utilization


def test_the_meter_is_harvested_from_runs_and_never_asks_anthropic(client, monkeypatch):
    """The rule that replaced the poller: this endpoint makes no network call.

    It used to read an undocumented endpoint once a minute per credential,
    which earned a 429 with a Retry-After in tens of minutes and left the meter
    dark while the credential itself was healthy. The SDK volunteers the same
    windows on every run, so there is nothing left to ask.
    """
    import httpx

    from yaaf.core import plan_usage
    from yaaf.core.db import db_session
    _plan_windows = sandbox_plan_windows()

    def never(*a, **k):
        raise AssertionError("the plan meter must never call out")

    monkeypatch.setattr(httpx, "get", never)
    monkeypatch.setattr(httpx, "post", never)

    token = "sk-ant-oat01-harvested-not-fetched"
    key = create(client, value=token, mode="subscription")

    # Before any run there is no reading, and that is a sentence, not a 500.
    empty = client.get(f"{URL}/{key['id']}/usage?refresh=true", headers=ADMIN)
    assert empty.status_code == 200, empty.text
    assert empty.json()["available"] is False
    assert "next agent run" in empty.json()["reason"]

    # What the sandbox pulls out of the SDK event, filed as a run would file it.
    harvested = _plan_windows(_Info(live_event_today()))
    assert harvested["status"] == "allowed"
    assert {w["key"] for w in harvested["windows"]} == {"five_hour", "seven_day"}
    with db_session() as session:
        plan_usage.record(session, token, harvested)

    body = client.get(f"{URL}/{key['id']}/usage", headers=ADMIN).json()
    assert body["available"] is True
    windows = {w["key"]: w for w in body["windows"]}
    assert windows["five_hour"]["utilization"] == 0.31
    assert windows["five_hour"]["label"] == "Current session"
    # Epoch in, ISO out: one clock for the screen to read, naming the instant
    # the event named rather than merely a string shaped like a date.
    from datetime import UTC, datetime
    said = {w["key"]: w["resets_at"] for w in harvested["windows"]}["five_hour"]
    assert (datetime.fromisoformat(windows["five_hour"]["resets_at"])
            == datetime.fromtimestamp(said, tz=UTC))
    # A window still ahead of the clock is live, and is drawn as a bar.
    assert windows["five_hour"]["expired"] is False
    assert windows["seven_day"]["utilization"] == 0.14
    # And the value never rides along with its own meter.
    assert token not in client.get(f"{URL}/{key['id']}/usage", headers=ADMIN).text


def test_finishing_a_run_files_the_plan_reading_against_its_credential(client):
    """The last link: the daemon puts what the run was told where screens read.

    Driven through the real `finalize`, because the value of this whole path is
    that a screen shows a number nobody fetched — and every earlier attempt at
    this meter failed on a link that was reasoned about rather than run.
    """
    from tests.conftest import live_token
    from yaaf.runner import daemon
    from yaaf.runner.types import RunJob, RunResult

    token = "sk-ant-oat01-filed-by-the-daemon"
    key = create(client, value=token, mode="subscription")
    project = make_project(client, billing_secret_id=key["id"])
    assert client.get(f"{URL}/{key['id']}/usage",
                      headers=ADMIN).json()["available"] is False

    run_id, _ = live_token(project["id"], project["ceo_agent_id"])
    job = RunJob(run_id=run_id, project_id=project["id"],
                 agent_id=project["ceo_agent_id"], kind="agent_run",
                 trigger={}, task_id=None, attempt=1)
    # Reset times ahead of the clock, because a reading whose windows have all
    # reset is deliberately not offered as the plan and this test is about the
    # filing, not the expiry.
    ahead = live_event_today()["unifiedWindows"]
    daemon.finalize(job, RunResult(ok=True, summary="did a thing", tokens_in=5, tokens_out=7,
                                   plan={"status": "allowed", "windows": [
                                       {"key": "five_hour", "utilization": 0.31,
                                        "resets_at": ahead["five_hour"]["resetsAt"]},
                                       {"key": "seven_day", "utilization": 0.14,
                                        "resets_at": ahead["seven_day"]["resetsAt"]}]}))

    body = client.get(f"{URL}/{key['id']}/usage", headers=ADMIN).json()
    assert body["available"] is True and body["status"] == "allowed"
    assert {w["key"]: w["utilization"] for w in body["windows"]} == {
        "five_hour": 0.31, "seven_day": 0.14}
    # And the Founder's own menu sees it without a call of its own.
    from yaaf.core import llm_credentials
    from yaaf.core.db import db_session

    with db_session() as session:
        choice = llm_credentials.choices(session, with_plan=True)[0]
        assert choice["plan"][0]["used_percent"] == 31


def test_a_run_with_no_reading_finishes_exactly_as_before(client):
    """A meter is never worth a run. A result carrying no plan — the mock
    runtime, an older SDK, a killed container — must book identically."""
    from tests.conftest import live_token
    from yaaf.runner import daemon
    from yaaf.runner.types import RunJob, RunResult

    key = create(client, value="sk-ant-oat01-no-reading-at-all", mode="subscription")
    project = make_project(client, billing_secret_id=key["id"])
    run_id, _ = live_token(project["id"], project["ceo_agent_id"])
    job = RunJob(run_id=run_id, project_id=project["id"],
                 agent_id=project["ceo_agent_id"], kind="agent_run",
                 trigger={}, task_id=None, attempt=1)
    daemon.finalize(job, RunResult(ok=True, summary="no meter here", tokens_in=3, tokens_out=4))
    assert client.get(f"{URL}/{key['id']}/usage", headers=ADMIN).json()["available"] is False


def test_a_window_past_its_own_reset_stops_claiming_the_credential_is_full(client):
    """The meter that could never clear itself.

    A window at 100% is exactly the window that stops the runs, and a stopped
    run reports nothing, so the reading froze at full and the screen went on
    saying "runs on this credential fail until it resets" long after it had. The
    reading had not aged; it had outlived the window it measured, and only the
    clock could tell — which is why the check is on every read rather than on a
    sweep that would leave the screen wrong until its next tick.
    """
    from datetime import UTC, datetime, timedelta

    from yaaf.core import llm_credentials, plan_usage
    from yaaf.core.db import db_session

    token = "sk-ant-oat01-the-session-window-rolled-over"
    key = create(client, value=token, mode="subscription")
    now = datetime.now(UTC)
    with db_session() as session:
        plan_usage.record(session, token, {"status": "rejected", "windows": [
            {"key": "five_hour", "utilization": 1.0,
             "resets_at": (now - timedelta(hours=2)).isoformat()},
            {"key": "seven_day", "utilization": 0.4,
             "resets_at": (now + timedelta(days=3)).isoformat()}]})

    body = client.get(f"{URL}/{key['id']}/usage", headers=ADMIN).json()
    # There is still a plan to show: one of its windows is live.
    assert body["available"] is True
    windows = {w["key"]: w for w in body["windows"]}
    # The spent window keeps its name and its number — a reader wants to know
    # which window turned over and what it had been — and is marked as
    # describing a window that no longer exists. Nothing is invented in its
    # place: "0% used" is a measurement nobody took.
    assert windows["five_hour"]["expired"] is True
    assert windows["five_hour"]["utilization"] == 1.0
    assert windows["seven_day"]["expired"] is False

    # And the menu the Founder picks a credential from offers only the window
    # that is still open, rather than arguing against a credential that is in
    # fact wide open.
    with db_session() as session:
        choice = next(c for c in llm_credentials.choices(session, with_plan=True)
                      if c["id"] == key["id"])
        assert [entry["window"] for entry in choice["plan"]] == ["This week, all models"]


def test_a_reading_whose_windows_have_all_reset_is_not_drawn_as_the_plan(client):
    """When every window has turned over there is no plan left to draw, and the
    honest answer is the sentence the screen already has for "nothing to show
    yet" — not a set of bars describing five hours that ended this morning."""
    from datetime import UTC, datetime, timedelta

    from yaaf.core import llm_credentials, plan_usage
    from yaaf.core.db import db_session

    token = "sk-ant-oat01-every-window-has-rolled-over"
    key = create(client, value=token, mode="subscription")
    gone = (datetime.now(UTC) - timedelta(hours=9)).isoformat()
    with db_session() as session:
        plan_usage.record(session, token, {"status": "rejected", "windows": [
            {"key": "five_hour", "utilization": 1.0, "resets_at": gone},
            {"key": "seven_day", "utilization": 0.97, "resets_at": gone}]})

    body = client.get(f"{URL}/{key['id']}/usage", headers=ADMIN).json()
    assert body["available"] is False
    assert "reset" in body["reason"] and "next agent run" in body["reason"]
    # The reading is not deleted — it is the last thing Anthropic said, and the
    # screen names the time it was said — it simply stops standing in for now.
    assert body["fetched_at"] is not None
    with db_session() as session:
        choice = next(c for c in llm_credentials.choices(session, with_plan=True)
                      if c["id"] == key["id"])
        assert "plan" not in choice


def test_a_window_with_no_reset_time_is_never_retired_on_a_guess(client):
    """The SDK's shapes are Anthropic's to grow. A window that reports no reset
    time, or one this cannot parse, is shown as reported: the cost of retiring a
    live window by mistake is hiding the bar that is about to stop every run."""
    from yaaf.core import plan_usage
    from yaaf.core.db import db_session

    token = "sk-ant-oat01-a-window-without-a-clock"
    key = create(client, value=token, mode="subscription")
    with db_session() as session:
        plan_usage.record(session, token, {"status": "allowed", "windows": [
            {"key": "five_hour", "utilization": 0.6, "resets_at": None},
            {"key": "seven_day", "utilization": 0.2, "resets_at": "whenever"}]})

    body = client.get(f"{URL}/{key['id']}/usage", headers=ADMIN).json()
    assert body["available"] is True
    assert all(w["expired"] is False for w in body["windows"])


def test_a_run_carries_the_plan_reading_home(tmp_path, monkeypatch):
    """The capture half, executed rather than reasoned about.

    Drives the real `sandbox_entry.main()` with a fake SDK stream and asserts
    the reading reaches `result.json`, which is the only channel out of the
    sandbox. Everything downstream is ordinary code; this is the part that
    lives in a container nobody can step through, and the part that was
    assumed to work in three earlier attempts at this meter.

    Deliberately heavier than its neighbours: it imports the entry script and
    runs an event loop. It is worth it once.
    """
    import asyncio
    import importlib
    import json
    import types

    for key, value in (("YAAF_API_BASE_URL", "http://api:8080"), ("YAAF_API_TOKEN", "t"),
                       ("YAAF_RUN_ID", "run_test"), ("YAAF_TOOL_PERMISSIONS", "{}")):
        monkeypatch.setenv(key, value)
    import yaaf.runner.sandbox_entry as mod

    entry = importlib.reload(mod)
    staging = tmp_path / "workspace" / ".yaaf"
    staging.mkdir(parents=True)
    monkeypatch.setattr(entry, "WORKSPACE", tmp_path / "workspace")
    monkeypatch.setattr(entry, "STAGING", staging)
    (staging / "system_prompt-run_test.txt").write_text("be useful")
    (staging / "context-run_test.json").write_text(json.dumps({"trigger": "test"}))

    class _Event:
        """A RateLimitEvent as the SDK delivers it — the numbers are in `raw`."""

        rate_limit_info = _Info(LIVE_EVENT_RAW)
        session_id = "s1"

    async def fake_query(prompt=None, options=None):
        yield _Event()
        yield entry.ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="s1", total_cost_usd=0.01,
            usage={"input_tokens": 5, "output_tokens": 7}, result="done")

    monkeypatch.setattr(entry, "query", fake_query)
    monkeypatch.setattr(entry, "create_sdk_mcp_server", lambda **kw: types.SimpleNamespace())
    for name in ("run_tools", "skill_names"):
        monkeypatch.setattr(entry, name, list)
    monkeypatch.setattr(entry, "build_hooks", dict)
    monkeypatch.setattr(entry, "_external_mcp_servers", dict)

    assert asyncio.run(entry.main()) == 0
    result = json.loads((staging / "result-run_test.json").read_text())
    # The meter rode home beside the token counts, from the same run.
    assert result["tokens_in"] == 5 and result["tokens_out"] == 7
    assert result["plan"]["status"] == "allowed"
    assert {w["key"]: w["utilization"] for w in result["plan"]["windows"]} == {
        "five_hour": 0.31, "seven_day": 0.14}

    # And the host reads exactly that field off exactly that file.
    from yaaf.runner.types import RunResult

    carried = RunResult(ok=True, plan=result["plan"])
    assert carried.plan["windows"][0]["key"] == "five_hour"


def test_one_subscription_behind_many_credentials_shares_one_reading(client):
    """The reading is filed under the token, so every row on that subscription
    has it the moment any one of their projects runs — and a different
    subscription is unaffected."""
    from yaaf.core import plan_usage
    from yaaf.core.db import db_session
    _plan_windows = sandbox_plan_windows()

    token = "sk-ant-oat01-one-key-many-rows"
    rows = [create(client, f"Copy {n}", value=token, mode="subscription") for n in range(3)]
    other = create(client, "Different subscription", mode="subscription",
                   value="sk-ant-oat01-a-key-of-its-own")

    with db_session() as session:
        plan_usage.record(session, token, _plan_windows(_Info(live_event_today())))

    for row in rows:
        body = client.get(f"{URL}/{row['id']}/usage", headers=ADMIN).json()
        assert body["available"] is True
        assert {w["key"]: w["utilization"] for w in body["windows"]}["five_hour"] == 0.31
    assert client.get(f"{URL}/{other['id']}/usage", headers=ADMIN).json()["available"] is False


def test_a_reading_the_sdk_cannot_make_numbers_of_never_breaks_a_run(client):
    """`record` is on the completion path of a finished run. It skips what it
    cannot read rather than raising, because a meter is not worth a run."""
    from yaaf.core import plan_usage
    from yaaf.core.db import db_session
    _plan_windows = sandbox_plan_windows()

    # Shapes the SDK could grow into, none of which may raise.
    assert _plan_windows(_Info({})) is None
    assert _plan_windows(_Info({"unifiedWindows": []})) is None
    assert _plan_windows(_Info({"unifiedWindows": {"five_hour": "not a dict"}})) is None
    assert _plan_windows(_Info({"unifiedWindows": {"x": {"utilization": None}}})) is None
    # An unknown window still renders, under its own name.
    grown = _plan_windows(_Info({"unifiedWindows": {"thirty_day": {"utilization": 0.5}}}))
    assert grown["windows"][0]["key"] == "thirty_day"

    key = create(client, value="sk-ant-oat01-partial-shapes", mode="subscription")
    with db_session() as session:
        plan_usage.record(session, "sk-ant-oat01-partial-shapes",
                          {"windows": [{"key": "five_hour", "utilization": "nonsense"},
                                       {"key": None, "utilization": 0.2},
                                       {"key": "seven_day", "utilization": 42}]})
    body = client.get(f"{URL}/{key['id']}/usage", headers=ADMIN).json()
    # Only the readable window survives, and a percentage is folded to a share.
    assert [w["key"] for w in body["windows"]] == ["seven_day"]
    assert body["windows"][0]["utilization"] == 0.42
    assert body["windows"][0]["label"] == "This week, all models"


def test_plan_meter_has_nothing_to_say_about_an_api_key(client, monkeypatch):
    import httpx

    def never(*a, **k):
        raise AssertionError("an API key must never be sent to the plan meter")

    monkeypatch.setattr(httpx, "get", never)
    key = create(client)
    body = client.get(f"{URL}/{key['id']}/usage", headers=ADMIN).json()
    assert body["available"] is False and body["mode"] == "api_key"
    assert "per call" in body["reason"] and body["windows"] == []


def test_plan_meter_is_the_administrators_to_read(client):
    key = create(client, value="sk-ant-oat01-plan-meter-test-token", mode="subscription")
    project = make_project(client)
    token, _ = live_token(project["id"], project["ceo_agent_id"])
    for headers in ({}, bearer(token)):
        assert client.get(f"{URL}/{key['id']}/usage", headers=headers).status_code == 401
    assert client.get(f"{URL}/nope/usage", headers=ADMIN).status_code == 404
    # A service secret is not an LLM credential, and has no meter to read.
    service = client.post("/admin/secrets", headers=ADMIN, json={
        "project_id": project["id"], "name": "wordpress", "value": "wp-secret-value",
        "kind": "service"}).json()
    assert client.get(f"{URL}/{service['id']}/usage", headers=ADMIN).status_code == 404


def test_the_drill_ignores_who_pays(client):
    """A draft names the credential it will pay with; the fire drill runs in a
    throwaway database that holds none. Deliberately runs its own drill (~3s):
    the assertion is on the drill's own 'created' step, not a report."""
    key = create(client, value="sk-ant-oat-test-placeholder", mode="subscription")
    make_project(client, start=False)  # seeds the chairman and the roles
    spec = {"slug": "drill", "name": "Drill", "chairman_email": "chair@example.com",
            "goal": "Prove the drill survives a draft that names who pays for it.",
            "ceo_role_slug": "ceo", "ceo_name": "CEO", "billing_secret_id": key["id"],
            "teams": [{"role_slug": "worker", "name": "A", "employees": ["L"], "lead": "L"}]}
    report = client.post("/admin/rehearse", headers=ADMIN, json={"spec": spec, "days": 2})
    assert report.status_code == 200, report.text
    created = next(s for s in report.json()["walk"] if s["key"] == "created")
    assert created["ok"], created


def test_create_refuses_a_mode_that_contradicts_the_credential(client):
    """The same pair PATCH refuses must not be swapped silently on create —
    the CLI passes both flags, and a chairman who asked for token metering
    would otherwise get dollars without a word."""
    from tests.conftest import AUTONOMY_PRESETS, seed_role

    sub = create(client, value="sk-ant-oat-test-placeholder", mode="subscription")
    client.post("/admin/users", json={"email": "chair@example.com", "display_name": "Chair"},
                headers=ADMIN)
    seed_role(client, "ceo", is_ceo=True)
    body = {"slug": "mismatch", "name": "Mismatch", "chairman_email": "chair@example.com",
            "goal": "Prove a contradictory billing mode is refused at creation.",
            "ceo_role_slug": "ceo", "ceo_name": "CEO", "teams": [],
            "constitution": AUTONOMY_PRESETS["supervised"],
            "billing_secret_id": sub["id"], "billing_mode": "api_key"}
    refused = client.post("/admin/projects", json=body, headers=ADMIN)
    assert refused.status_code == 422 and "must match" in refused.json()["detail"]
    # Omitted, the mode is the credential's.
    del body["billing_mode"]
    created = client.post("/admin/projects", json=body, headers=ADMIN)
    assert created.status_code == 200, created.text
    with db_session() as session:
        assert session.get(Project, created.json()["id"]).billing_mode == "subscription"
    # And with no credential at all, the old default still holds.
    bare = client.post("/admin/projects", json={**body, "slug": "bare", "billing_secret_id": None},
                       headers=ADMIN)
    assert bare.status_code == 200, bare.text
    with db_session() as session:
        assert session.get(Project, bare.json()["id"]).billing_mode == "api_key"


def test_llm_credential_values_are_never_shown_back(client):
    """A project's own secrets are the chairman's to read back with the eye.
    The credential that pays for every project on it is rotated, never read."""
    key = create(client, value="sk-ant-api03-never-shown")
    revealed = client.get(f"/admin/secrets/{key['id']}/value", headers=ADMIN)
    assert revealed.status_code == 422
    assert "never-shown" not in revealed.text and "value" not in revealed.json()
    project = make_project(client, start=False)
    stored = client.post("/admin/secrets", headers=ADMIN, json={
        "project_id": project["id"], "name": "wordpress", "value": "wp-secret-value",
        "kind": "service", "role_scopes": []}).json()
    assert client.get(f"/admin/secrets/{stored['id']}/value",
                      headers=ADMIN).json()["value"] == "wp-secret-value"


def test_a_project_can_detach_its_credential_and_then_it_can_go(client):
    """The only way a leaked key on a one-credential install can ever be
    deleted: detach every project, then delete."""
    key = create(client)
    project = make_project(client, start=False, billing_secret_id=key["id"])
    assert client.delete(f"{URL}/{key['id']}", headers=ADMIN).status_code == 409
    detached = client.patch(f"/admin/projects/{project['id']}", headers=ADMIN,
                            json={"billing_secret_id": ""})
    assert detached.status_code == 200, detached.text
    with db_session() as session:
        row = session.get(Project, project["id"])
        assert row.billing_secret_id is None and row.billing_mode == "api_key"
    assert client.delete(f"{URL}/{key['id']}", headers=ADMIN).status_code == 200


def test_a_dangling_credential_id_is_a_sentence_not_a_500(client):
    """Postgres enforces the foreign key, so a project cannot point at a
    credential that is gone. SQLite installs have no such key, and a reference
    left behind by a hand edit must still answer with a 422 on the next mode
    change rather than a traceback. The scenario cannot be built on Postgres
    at all — which is the guarantee this test is the SQLite half of."""
    import pytest

    key = create(client)
    project = make_project(client, start=False, billing_secret_id=key["id"])
    with db_session() as session:
        if session.get_bind().dialect.name != "sqlite":
            pytest.skip("the foreign key makes a dangling credential id impossible here")
        session.get(Project, project["id"]).billing_secret_id = "secr_gone"
    dangling = client.patch(f"/admin/projects/{project['id']}", headers=ADMIN,
                            json={"billing_mode": "subscription"})
    assert dangling.status_code == 422 and "no longer exists" in dangling.json()["detail"]


def test_a_shared_founder_key_splits_by_billing_mode_and_survives_an_unreadable_row(client):
    from yaaf.core import platform

    tokens = make_project(client, slug="tokens", start=False, billing_mode="subscription")
    dollars = make_project(client, slug="dollars", start=False, billing_mode="api_key")
    with db_session() as session:
        platform.set_credential(session, "founder-test-value")
        session.flush()
        original = session.query(Secret).filter_by(name=platform.PLATFORM_BILLING_SECRET).one()
        for p in (tokens, dollars):
            session.get(Project, p["id"]).billing_secret_id = original.id
        # An orphaned legacy key stored under a master key this install no longer has.
        session.add(Secret(id="legacy-unreadable", project_id=dollars["id"], name="old",
                           ciphertext="not-a-fernet-token", kind="billing", role_scopes=[]))
    with db_session() as session:
        migration = importlib.import_module("migrations.versions.0034_managed_llm_credentials")
        with Operations.context(MigrationContext.configure(session.connection())):
            migration.upgrade()
        session.expire_all()
        by_project = {p["id"]: session.get(Secret, session.get(Project, p["id"]).billing_secret_id)
                      for p in (tokens, dollars)}
        assert by_project[tokens["id"]].llm_mode == "subscription"
        assert by_project[dollars["id"]].llm_mode == "api_key"
        assert by_project[tokens["id"]].id != by_project[dollars["id"]].id
        assert all(row.ciphertext == original.ciphertext for row in by_project.values())
        unreadable = session.get(Secret, "legacy-unreadable")
        assert unreadable.llm_mode == "api_key" and unreadable.project_id is None


def test_unknown_credential_ids_are_404_on_the_settings_screen(client):
    assert client.patch(f"{URL}/secr_nope", headers=ADMIN, json={"name": "x"}).status_code == 404
    assert client.delete(f"{URL}/secr_nope", headers=ADMIN).status_code == 404
    assert client.get(f"{URL}/secr_nope/usage", headers=ADMIN).status_code == 404
