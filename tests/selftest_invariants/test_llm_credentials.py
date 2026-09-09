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


def test_a_throttled_meter_waits_as_long_as_anthropic_asked(client, monkeypatch):
    """429 says how long to wait, and it is not a minute.

    The reading that found this asked for 553 seconds while the cache expired
    every 60, so the server asked nine more times inside the window it had been
    told to sit out — and the screen said "try again in a minute", which was
    both wrong and an invitation to press retry through the rate limit.
    """
    import httpx

    from yaaf.core import plan_usage

    key = create(client, value="sk-ant-oat01-throttled-token", mode="subscription")
    calls: list[int] = []

    class _Throttled:
        status_code = 429
        headers = {"retry-after": "553"}
        text = ""

        def json(self):
            raise ValueError("not json")

    def fake_get(url, headers=None, timeout=None, **_):
        calls.append(1)
        return _Throttled()

    monkeypatch.setattr(httpx, "get", fake_get)
    for attr in ("_cache", "_token_of", "_backoff", "_inflight"):
        monkeypatch.setattr(plan_usage, attr, {})

    first = client.get(f"{URL}/{key['id']}/usage", headers=ADMIN).json()
    assert first["available"] is False
    # The wait is stated, in the units a reader thinks in, and it is honest.
    assert "about 9 minutes" in first["reason"]
    assert "try again in a minute" not in first["reason"]

    # Every later poll — and every press of the retry button, which is what a
    # frustrated reader actually does — is answered from the cache. Asking
    # again is what keeps a rate limit alive.
    for _ in range(5):
        client.get(f"{URL}/{key['id']}/usage", headers=ADMIN)
        client.get(f"{URL}/{key['id']}/usage?refresh=true", headers=ADMIN)
    assert len(calls) == 1

    # When the window passes, it asks again by itself.
    plan_usage._backoff[plan_usage.fingerprint("sk-ant-oat01-throttled-token")] = 0.0
    plan_usage._cache.clear()
    client.get(f"{URL}/{key['id']}/usage", headers=ADMIN)
    assert len(calls) == 2


def test_retry_after_is_read_however_it_was_sent(monkeypatch):
    from datetime import UTC, datetime, timedelta

    from yaaf.core import plan_usage

    assert plan_usage._retry_after("553") == 553
    assert plan_usage._retry_after("12.7") == 12
    # An HTTP-date is the other legal spelling.
    soon = datetime.now(UTC) + timedelta(seconds=120)
    assert 100 <= plan_usage._retry_after(
        soon.strftime("%a, %d %b %Y %H:%M:%S GMT")) <= 130
    # Missing or unreadable is not a licence to keep asking.
    assert plan_usage._retry_after(None) == plan_usage.DEFAULT_BACKOFF_S
    assert plan_usage._retry_after("soon please") == plan_usage.DEFAULT_BACKOFF_S
    # And a hostile value cannot park the meter for a week.
    assert plan_usage._retry_after("999999") == plan_usage.MAX_BACKOFF_S


def test_rows_arriving_together_on_one_token_ask_once(monkeypatch):
    """The gate is keyed by token, so a poll that lands mid-fetch waits for
    that answer rather than starting a second one against the same account.

    Threads and a deliberately slow fetch, because the property only exists
    under concurrency — sequentially the cache alone would hide the bug.
    """
    import threading
    import time as time_module

    from yaaf.core import plan_usage

    calls: list[str] = []
    gate = threading.Barrier(4)

    def slow_fetch(token):
        calls.append(token)
        time_module.sleep(0.05)
        return {"available": True, "reason": None, "windows": [],
                "fetched_at": "2026-09-08T00:00:00Z"}

    monkeypatch.setattr(plan_usage, "fetch", slow_fetch)
    monkeypatch.setattr(plan_usage, "_cache", {})
    monkeypatch.setattr(plan_usage, "_token_of", {})
    monkeypatch.setattr(plan_usage, "_inflight", {})

    def poll(credential_id):
        gate.wait()
        plan_usage.reading(credential_id, "subscription", "sk-ant-oat01-shared")

    threads = [threading.Thread(target=poll, args=(f"secr_row_{n}",)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert calls == ["sk-ant-oat01-shared"]
    # And every one of them got the answer, not just the thread that asked.
    for n in range(4):
        assert plan_usage.cached(f"secr_row_{n}") is not None


def test_one_subscription_behind_many_credentials_is_read_once(client, monkeypatch):
    """The rate limit is on the account, so the cache must be on the token.

    Promoting per-project keys made one subscription routinely sit behind
    several credential rows. Keyed by credential, each row asked Anthropic for
    the same account's meter every minute, Anthropic answered 429, and every
    row on that token went unavailable together.
    """
    import httpx

    from yaaf.core import plan_usage

    token = "sk-ant-oat01-one-subscription-many-rows"
    rows = [create(client, f"Copy {n}", value=token, mode="subscription") for n in range(3)]
    other = create(client, "A different subscription", mode="subscription",
                   value="sk-ant-oat01-a-genuinely-different-token")
    calls: list[str] = []

    def fake_get(url, headers=None, timeout=None, **_):
        calls.append(headers["Authorization"])
        return _Response(200, METER)

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(plan_usage, "_cache", {})
    monkeypatch.setattr(plan_usage, "_token_of", {})

    for row in rows:
        answer = client.get(f"{URL}/{row['id']}/usage", headers=ADMIN)
        assert answer.status_code == 200, answer.text
        assert answer.json()["available"] is True
    # Three rows, three screens, one subscription — one question.
    assert calls == [f"Bearer {token}"]
    # A different subscription is a different meter and still gets asked.
    assert client.get(f"{URL}/{other['id']}/usage", headers=ADMIN).status_code == 200
    assert len(calls) == 2

    # A refresh a human presses is a question they meant to ask, so it still
    # asks. What must not multiply is rows *arriving together* — which is what
    # the every-minute poll does, and what the gate is for.
    before = len(calls)
    assert client.get(f"{URL}/{rows[0]['id']}/usage?refresh=true", headers=ADMIN).status_code == 200
    assert len(calls) == before + 1

    # Rotating one row changes only that row's token. The other two still hold
    # the old one, and their meter is still exactly the reading on file.
    assert client.patch(f"{URL}/{rows[0]['id']}", headers=ADMIN,
                        json={"name": "Copy 0", "value": "sk-ant-oat01-rotated-away"}
                        ).status_code == 200
    before = len(calls)
    assert client.get(f"{URL}/{rows[1]['id']}/usage", headers=ADMIN).json()["cached"] is True
    assert len(calls) == before
    assert client.get(f"{URL}/{rows[0]['id']}/usage", headers=ADMIN).json()["available"] is True
    assert calls[-1] == "Bearer sk-ant-oat01-rotated-away"


def test_plan_meter_is_read_with_the_token_and_returns_only_percentages(client, monkeypatch):
    import httpx

    from yaaf.core import plan_usage

    token = "sk-ant-oat01-plan-meter-test-token"
    key = create(client, value=token, mode="subscription")
    calls: list[dict] = []

    def fake_get(url, headers=None, timeout=None, **_):
        calls.append({"url": url, "headers": headers})
        return _Response(200, METER)

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(plan_usage, "_cache", {})

    reading = client.get(f"{URL}/{key['id']}/usage", headers=ADMIN)
    assert reading.status_code == 200, reading.text
    body = reading.json()
    # The token went to Anthropic, as a bearer with the OAuth beta, and nowhere else.
    assert calls[0]["url"] == plan_usage.USAGE_URL
    assert calls[0]["headers"]["Authorization"] == f"Bearer {token}"
    assert calls[0]["headers"]["anthropic-beta"] == plan_usage.OAUTH_BETA
    assert token not in reading.text and "sk-ant" not in reading.text
    assert body["available"] is True and body["cached"] is False
    windows = {w["key"]: w for w in body["windows"]}
    assert windows["five_hour"]["utilization"] == 0.21
    assert windows["five_hour"]["resets_at"] == "2026-09-08T15:22:00Z"
    # Epoch resets become ISO so the screen has one clock to read.
    assert windows["seven_day"]["resets_at"].startswith("2025-09-09T")
    assert windows["by_model:fable"]["label"] == "This week, Fable"
    assert windows["by_model:fable"]["utilization"] == 0.24
    # Internal and unknown keys are not drawn under a label nobody chose.
    assert "seven_day_overage_included" not in windows and "some_future_field" not in windows

    # A minute-long cache: the second read is served without a second call.
    again = client.get(f"{URL}/{key['id']}/usage", headers=ADMIN).json()
    assert again["cached"] is True and len(calls) == 1
    forced = client.get(f"{URL}/{key['id']}/usage?refresh=true", headers=ADMIN).json()
    assert forced["cached"] is False and len(calls) == 2

    # Rotating the token forgets the old token's meter.
    assert client.patch(f"{URL}/{key['id']}", headers=ADMIN,
                        json={"name": "Production", "value": "sk-ant-oat01-rotated"}).status_code == 200
    client.get(f"{URL}/{key['id']}/usage", headers=ADMIN)
    assert len(calls) == 3 and calls[2]["headers"]["Authorization"].endswith("rotated")


def test_plan_meter_degrades_to_a_sentence_and_never_to_an_error_page(client, monkeypatch):
    import httpx

    from yaaf.core import plan_usage

    key = create(client, value="sk-ant-oat01-plan-meter-test-token", mode="subscription")
    monkeypatch.setattr(plan_usage, "_cache", {})

    def read() -> dict:
        response = client.get(f"{URL}/{key['id']}/usage?refresh=true", headers=ADMIN)
        assert response.status_code == 200, response.text
        return response.json()

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Response(401))
    assert read()["available"] is False and "rejected" in read()["reason"]
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Response(500))
    assert "500" in read()["reason"]
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Response(200, {"nothing": "here"}))
    assert "format" in read()["reason"]
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Response(200, None, "<html>"))
    assert read()["available"] is False

    def boom(*a, **k):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(httpx, "get", boom)
    assert "ConnectError" in read()["reason"]


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


def test_one_fetch_serves_every_screen_polling_the_same_credential(client, monkeypatch):
    """Screens poll on the same minute. When the cache expires they all arrive
    together; Anthropic is asked once and the rest read the answer."""
    import threading

    import httpx

    from yaaf.core import plan_usage

    key = create(client, value="sk-ant-oat-test-placeholder", mode="subscription")
    calls: list[str] = []
    gate = threading.Barrier(6, timeout=10)

    class Slow:
        status_code = 200

        def json(self):
            return {"five_hour": {"utilization": 0.5, "resets_at": 1}}

    def fake_get(url, *a, **k):
        calls.append(url)
        return Slow()

    monkeypatch.setattr(httpx, "get", fake_get)
    plan_usage.forget(key["id"])
    results: list[dict] = []

    def poll():
        gate.wait()
        results.append(client.get(f"{URL}/{key['id']}/usage", headers=ADMIN).json())

    threads = [threading.Thread(target=poll) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert len(results) == 6 and all(r["available"] for r in results)
    assert len(calls) == 1, calls


def test_unknown_credential_ids_are_404_on_the_settings_screen(client):
    assert client.patch(f"{URL}/secr_nope", headers=ADMIN, json={"name": "x"}).status_code == 404
    assert client.delete(f"{URL}/secr_nope", headers=ADMIN).status_code == 404
    assert client.get(f"{URL}/secr_nope/usage", headers=ADMIN).status_code == 404
