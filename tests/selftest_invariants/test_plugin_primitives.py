"""Core's primitives hold for every plugin, attacked over the agent API (build step 4).

A plugin declares a gated artifact, a review, platform pages and receipts; core
holds them (docs/plans/factory-plugins.md §4.4). These tests publish as seats
of a project of the example grant factory, with a real plugin host answering,
and try what the Amazon PPC review found possible plus what §6.2 lists:

* a refused artifact stores nothing; an accepted one is stored as the plugin
  normalised it, with its numbers kept by core;
* no seat publishes a platform page or a data action's output;
* a review must name the latest subject's exact hash, come from a seat that did
  not publish those bytes (even after another seat republished them), and
  re-derive every load-bearing number against what the SUBJECT reported, not
  what the review claims was reported;
* only a passing review of the latest subject lets the project complete, and a
  later subject or a later return takes that back;
* a plugin that cannot be asked refuses; it never accepts;
* a read whose fields cannot be told backs no field citation.

A real host starts per test (about 0.2 s). Projects are made of the plugin kind
by setting the columns directly: creating one over HTTP is build step 5.
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import select

from tests.conftest import ADMIN, bearer, live_token, make_project
from yaaf.core.config import settings
from yaaf.core.db import db_session
from yaaf.core.models import Artifact, Evidence, PluginArtifact, Project
from yaaf.plugins import host as H
from yaaf.plugins import primitives, registry

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "packages/yaaf_plugin_api/examples/grant-factory"
GOOD = {"title": "Community garden", "budget": [{"rule": "3.1", "amount": 1200}]}


@pytest.fixture
def grant(client, tmp_path, monkeypatch):
    """A project of the example plugin's kind, and two of its seats' run tokens."""
    monkeypatch.setattr(settings(), "plugins_root", str(tmp_path / "installed"))
    monkeypatch.setattr(settings(), "allow_local_plugins", True)
    src = tmp_path / "checkout"
    shutil.copytree(EXAMPLE, src, ignore=shutil.ignore_patterns("__pycache__"))
    version = client.post("/admin/plugins/local", json={"path": str(src)},
                          headers=ADMIN).json()
    project = make_project(client)
    with db_session() as s:
        row = s.get(Project, project["id"])
        row.kind, row.kind_mode, row.plugin_version_id = (
            "plugin:grant_application", "draft", version["id"])
    seats = [a["id"] for team in project["teams"] for a in team["agents"]][:2]
    tokens = [bearer(live_token(project["id"], seat)[1]) for seat in seats]
    yield {"project": project["id"], "seats": seats, "tokens": tokens, "version": version}
    registry.close_all()


def publish(client, token, name, body) -> object:
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    return client.post("/agent/artifacts", headers=token, json={
        "filename": name, "content_base64": base64.b64encode(data).decode()})


def rows(project_id: str, name: str) -> list[Artifact]:
    with db_session() as s:
        found = s.execute(select(Artifact).where(Artifact.project_id == project_id,
                                                 Artifact.name == name)
                          .order_by(Artifact.version)).scalars().all()
        s.expunge_all()
        return found


def body_of(row: Artifact) -> bytes:
    from yaaf.core.artifacts import store

    return store().get_bytes(row.storage_key)


def review(subject: Artifact, verdict="pass", **extra) -> dict:
    return {"subject_sha256": subject.sha256, "verdict": verdict, "summary": "checked it",
            "rederived": [{"claim": "budget_total", "value": 1200}], **extra}


def outstanding(project_id: str) -> list[str]:
    from yaaf.server import policy

    with db_session() as s:
        return policy.delivery_outstanding(s, s.get(Project, project_id))


# ── gated artifacts, reserved names, pages ─────────────────────────────────────


def test_a_refused_artifact_stores_nothing_and_says_why(client, grant):
    r = publish(client, grant["tokens"][0], "application.json", {"title": "x", "budget": []})
    assert r.status_code == 422 and "budget needs at least one line" in r.text
    assert rows(grant["project"], "application.json") == []
    assert rows(grant["project"], "application.md") == []


def test_an_accepted_artifact_is_stored_as_the_plugin_normalised_it(client, grant):
    assert publish(client, grant["tokens"][0], "application.json", GOOD).status_code == 200
    [row] = rows(grant["project"], "application.json")
    assert body_of(row) == json.dumps(GOOD, sort_keys=True, indent=2).encode()
    with db_session() as s:
        assert s.get(PluginArtifact, row.id).load_bearing == {"budget_total": 1200.0}
        assert s.get(Project, grant["project"]).kind_state == {"asked": 1200.0}
    [page] = rows(grant["project"], "application.md")
    assert body_of(page).startswith(b"> DRAFT") and page.published_by_agent_id is None


@pytest.mark.parametrize("name", ["application.md", "calls.json"])
def test_no_seat_publishes_what_the_platform_writes(client, grant, name):
    r = publish(client, grant["tokens"][0], name, b"# my own version of the page")
    assert r.status_code == 422 and "written by the platform" in r.text
    assert rows(grant["project"], name) == []


def test_a_plugin_that_cannot_be_asked_refuses_and_stores_nothing(client, grant, monkeypatch):
    dead = H.HostPool(H.HostSpec.from_checkout(EXAMPLE),
                      H.BrokerLauncher(("127.0.0.1", 1), b"k" * 32))
    monkeypatch.setattr(registry, "pool_for", lambda version: dead)
    r = publish(client, grant["tokens"][0], "application.json", GOOD)
    assert r.status_code == 422 and "could not be consulted" in r.text
    assert rows(grant["project"], "application.json") == []


# ── the review ─────────────────────────────────────────────────────────────────


def test_there_is_nothing_to_review_before_a_subject(client, grant):
    r = publish(client, grant["tokens"][1], "application-review.json",
                {"subject_sha256": "0" * 64, "verdict": "pass", "rederived": []})
    assert r.status_code == 409


def test_the_author_cannot_review_their_own_subject(client, grant):
    publish(client, grant["tokens"][0], "application.json", GOOD)
    [subject] = rows(grant["project"], "application.json")
    r = publish(client, grant["tokens"][0], "application-review.json", review(subject))
    assert r.status_code == 403 and "a different seat reviews it" in r.text


def test_republishing_the_same_bytes_does_not_launder_a_self_review(client, grant):
    """The PPC branch's hole: seat B republishes A's exact report, and A, no
    longer the latest publisher, reviews it. Independence is by content here."""
    a, b = grant["tokens"]
    publish(client, a, "application.json", GOOD)
    publish(client, b, "application.json", GOOD)
    subjects = rows(grant["project"], "application.json")
    assert len(subjects) == 2 and subjects[0].sha256 == subjects[1].sha256
    r = publish(client, a, "application-review.json", review(subjects[1]))
    assert r.status_code == 403 and "with this exact content" in r.text


def test_a_review_of_another_version_is_refused(client, grant):
    publish(client, grant["tokens"][0], "application.json", GOOD)
    [first] = rows(grant["project"], "application.json")
    changed = {**GOOD, "title": "Community garden, phase two"}
    publish(client, grant["tokens"][0], "application.json", changed)
    r = publish(client, grant["tokens"][1], "application-review.json", review(first))
    assert r.status_code == 409 and "latest application.json" in r.text


@pytest.mark.parametrize(("rederived", "why"), [
    ([], "was not re-derived"),
    ([{"claim": "budget_total", "value": 900}], "more than 5% apart"),
    ([{"claim": "budget_total", "value": "1200"}], "value is the number"),
    # A review that states its own "reported" cannot move the yardstick: the
    # subject said 1,200, so 500 is a return however the review labels it.
    ([{"claim": "budget_total", "value": 500, "reported": 500}], "more than 5% apart"),
], ids=["missing", "outside-tolerance", "not-a-number", "own-yardstick"])
def test_a_pass_must_re_derive_every_number_against_the_subject(client, grant, rederived, why):
    publish(client, grant["tokens"][0], "application.json", GOOD)
    [subject] = rows(grant["project"], "application.json")
    r = publish(client, grant["tokens"][1], "application-review.json",
                review(subject, rederived=rederived))
    assert r.status_code == 422 and why in r.text
    assert rows(grant["project"], "application-review.json") == []


def test_the_plugins_own_rubric_still_applies_after_core_is_satisfied(client, grant):
    publish(client, grant["tokens"][0], "application.json", GOOD)
    [subject] = rows(grant["project"], "application.json")
    doc = review(subject)
    del doc["summary"]  # the example plugin's rubric requires one
    r = publish(client, grant["tokens"][1], "application-review.json", doc)
    assert r.status_code == 422 and "summary" in r.text


def test_only_a_passing_review_of_the_latest_subject_lets_the_project_complete(client, grant):
    owed = "no passing independent review of the latest application.json"
    publish(client, grant["tokens"][0], "application.json", GOOD)
    assert any(owed in o for o in outstanding(grant["project"]))
    [subject] = rows(grant["project"], "application.json")
    r = publish(client, grant["tokens"][1], "application-review.json", review(subject))
    assert r.status_code == 200, r.text
    assert not any(owed in o for o in outstanding(grant["project"]))
    final = rows(grant["project"], "application.md")[-1]
    assert not body_of(final).startswith(b"> DRAFT")
    with db_session() as s:
        assert s.get(PluginArtifact, final.id).reviewed is True
        assert s.execute(select(Evidence).where(
            Evidence.project_id == grant["project"])).scalars().first() is not None
    # A new subject version is unreviewed again ...
    publish(client, grant["tokens"][0], "application.json", {**GOOD, "title": "Garden v2"})
    assert any(owed in o for o in outstanding(grant["project"]))
    newest = rows(grant["project"], "application.json")[-1]
    publish(client, grant["tokens"][1], "application-review.json", review(newest))
    assert not any(owed in o for o in outstanding(grant["project"]))
    # ... and a later return takes the pass back.
    assert publish(client, grant["tokens"][1], "application-review.json",
                   review(newest, verdict="return", rederived=[])).status_code == 200
    assert any(owed in o for o in outstanding(grant["project"]))


# ── receipts ───────────────────────────────────────────────────────────────────


def test_a_read_is_recorded_and_one_whose_fields_cannot_be_told_backs_no_citation(
        client, grant):
    other_id = make_project(client, slug="other")["id"]
    with db_session() as s:
        project = s.get(Project, grant["project"])
        known = primitives.record_read(s, project, None, "grants-db", "get_call", {},
                                       {"structuredContent": {"funder": "F", "max": 1}}, None)
        unknown = primitives.record_read(s, project, None, "grants-db", "get_call", {},
                                         {"content": [{"type": "text",
                                                       "text": "Account 7: {\"funder\": 1}"}]},
                                         None)
        errored = primitives.record_read(s, project, None, "grants-db", "get_call", {},
                                         {"isError": True, "content": []}, None)
        find = primitives.receipt_lookup(s, project)
        assert find(known).fields == frozenset({"funder", "max"})
        assert find(unknown).fields is None  # proves a read happened; backs no field
        assert errored is None
        other = s.get(Project, other_id)
        assert primitives.record_read(s, other, None, "grants-db", "get_call", {},
                                      {"structuredContent": {"a": 1}}, None) is None
        assert primitives.receipt_lookup(s, other)(known) is None
        assert find("audt_forged") is None
    digest = hashlib.sha256(json.dumps({"structuredContent": {"funder": "F", "max": 1}},
                                       sort_keys=True).encode()).hexdigest()
    with db_session() as s:
        assert primitives.receipt_lookup(s, s.get(Project, grant["project"]))(
            known).result_sha256 == digest


def test_a_project_that_is_not_a_plugins_is_untouched(client, grant):
    other = make_project(client, slug="plain")
    seat = other["teams"][0]["agents"][0]["id"]
    token = bearer(live_token(other["id"], seat)[1])
    assert publish(client, token, "application.md", b"# fine here").status_code == 200
    assert publish(client, token, "application.json", {"anything": 1}).status_code == 200
    with db_session() as s:
        assert primitives.outstanding(s, s.get(Project, other["id"])) == []
