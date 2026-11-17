"""Publishing a project or a role, attacked.

The rule under attack: nothing that is the publisher's leaves the install. The
server knows every key's value, the chairman's email, the project's domains,
repo and data-source addresses, and refuses a submission that carries any of
them, however it is dressed: pasted into a prompt, base64-encoded, broken
across lines. The check runs before GitHub is called at all, so a refused
listing never reaches a fork, a branch or a pull request.
"""

from __future__ import annotations

import base64
import json

import pytest

from tests.conftest import ADMIN, headers_of, make_project, person, seed_role
from yaaf.core.db import db_session
from yaaf.core.models import Project, Secret
from yaaf.server import library, publish_github

KEY = "wp-app-password-9f8e7d6c5b4a"


@pytest.fixture
def market(tmp_path, monkeypatch):
    """A library source that takes submissions, and a GitHub that records calls."""
    index = tmp_path / "index.json"
    index.write_text(json.dumps({
        "registry": {"spec": 1, "name": "test", "publish": {
            "method": "github-pr", "repo": "owner/yaaf-marketplace", "client_id": "Iv1.test"}},
        "listings": []}))
    monkeypatch.setattr(library, "configured_sources", lambda: [str(index)])
    library.clear_cache()

    calls: list[tuple] = []

    class Fake:
        token = None

    def start_device(client_id):
        calls.append(("start", client_id))
        return {"device_code": "dev-123", "user_code": "ABCD-1234",
                "verification_uri": "https://github.com/login/device", "interval": 5, "expires_in": 900}

    def poll_device(client_id, device_code):
        calls.append(("poll", device_code))
        return {"state": "done", "token": "gho_testtoken"}

    def login_of(token):
        Fake.token = token
        return "rae"

    def next_version(token, repo, handle, slug):
        calls.append(("next_version", repo, handle, slug))
        return 1

    def submit(token, repo, *, branch, files, title, body, base="main"):
        calls.append(("submit", repo, branch, {k: v.decode() for k, v in files.items()}, title))
        return {"login": "rae", "number": 7, "url": "https://github.com/owner/yaaf-marketplace/pull/7"}

    def review_state(token, repo, number):
        calls.append(("review", number))
        return {"state": "changes_requested", "note": "Say what the WordPress key is for."}

    def open_issue(token, repo, *, title, body):
        calls.append(("issue", repo, title, body))
        return "https://github.com/owner/yaaf-marketplace/issues/3"

    for name, fn in [("open_issue", open_issue), ("start_device", start_device), ("poll_device", poll_device),
                     ("login_of", login_of), ("next_version", next_version), ("submit", submit),
                     ("review_state", review_state)]:
        monkeypatch.setattr(publish_github, name, fn)
    yield calls
    library.clear_cache()


def _sign_in(client):
    assert client.post("/admin/publish/github/start", headers=ADMIN).json()["user_code"] == "ABCD-1234"
    assert client.post("/admin/publish/github/poll", headers=ADMIN,
                       json={"device_code": "dev-123"}).json() == {"state": "done", "login": "rae"}


def _project(client):
    seed_role(client, "writer", secret_scopes=["wordpress"],
              system_prompt="You write posts and publish them as drafts with $WORDPRESS.")
    project = make_project(client, start=False, slug="acme-blog", name="Acme Outdoor Blog",
                           teams=[{"role_slug": "writer", "name": "Words", "employees": ["Ann"]}],
                           controlled_domains=["acme-outdoor.com"], github_repo="acme/site",
                           goal="Write posts for Acme Outdoor Blog readers about trail running.")
    r = client.post("/admin/secrets", headers=ADMIN, json={
        "project_id": project["id"], "name": "wordpress", "value": KEY, "role_scopes": ["writer"]})
    assert r.status_code == 200, r.text
    return project


def _draft(client, project_id):
    r = client.post(f"/admin/publish/projects/{project_id}/draft", headers=ADMIN, json={})
    assert r.status_code == 200, r.text
    return r.json()


def _ready(draft: dict) -> dict:
    """The draft as the Founder would leave it: generic, with a summary."""
    project = draft["version"]["bundle"]["project"]
    project["goal"] = "Write posts for {{site_name}} readers about {{subject}}."
    project["name"] = "A blog desk"
    draft["listing"]["name"] = "A blog desk"
    draft["listing"]["summary"] = "Posts your readers answer, drafted to WordPress."
    draft["slug"] = "blog-desk"
    draft["version"]["inputs"] = [i for i in draft["version"]["inputs"] if i["id"] == "your_domain"] + [
        {"id": "site_name", "question": "What is the site called?", "type": "text",
         "example": "Northwind Notes", "required": True},
        {"id": "subject", "question": "What do the posts cover?", "type": "text",
         "example": "home espresso", "required": True}]
    return draft


def _submit(client, project_id, draft):
    return client.post("/admin/publish/submit", headers=ADMIN,
                       json={"project_id": project_id, "draft": draft})


# ── the draft ──────────────────────────────────────────────────────────────────

def test_the_draft_leaves_the_publishers_account_behind(client, market):
    project = _project(client)
    body = _draft(client, project["id"])
    text = json.dumps(body["draft"])
    for private in (KEY, "chair@example.com", "acme/site"):
        assert private not in text
    assert "github_repo" not in body["draft"]["version"]["bundle"]["project"]
    keys = body["draft"]["version"]["needs"]["keys"]
    assert keys == [{**keys[0], "name": "wordpress", "roles": ["writer"], "required": True}]
    assert body["draft"]["version"]["needs"]["github_repo"] is True
    # The domain is the installer's to name: it arrives as a blank with its question.
    assert body["draft"]["version"]["bundle"]["project"]["controlled_domains"] == ["{{your_domain}}"]
    assert "acme-outdoor.com" not in text
    # Still in it, and said so: the name is for the Founder to replace.
    assert {leak["what"] for leak in body["leaks"]} == {"the project's name"}
    assert KEY not in json.dumps(body)


def test_standing_orders_travel_in_the_goal(client, market):
    project = _project(client)
    with db_session() as session:
        session.get(Project, project["id"]).standing_orders = ["Never post on Sundays."]
    goal = _draft(client, project["id"])["draft"]["version"]["bundle"]["project"]["goal"]
    assert "Never post on Sundays." in goal


def test_only_the_chairman_drafts_a_project(client, market):
    project = _project(client)
    stranger = person("stranger@example.com")
    r = client.post(f"/admin/publish/projects/{project['id']}/draft", headers=headers_of(stranger), json={})
    assert r.status_code in (403, 404)


# ── the check that runs before anything leaves ─────────────────────────────────

@pytest.mark.parametrize("disguise", [
    lambda v: v,
    lambda v: base64.b64encode(v.encode()).decode(),
    lambda v: v[:10] + "\n" + v[10:],
    lambda v: v.upper(),
])
def test_a_key_in_any_disguise_never_leaves(client, market, disguise):
    project = _project(client)
    _sign_in(client)
    draft = _ready(_draft(client, project["id"])["draft"])
    role = draft["version"]["bundle"]["roles"][0]
    role["system_prompt"] += f" The password is {disguise(KEY)}."
    r = _submit(client, project["id"], draft)
    assert r.status_code == 422, r.text
    assert "the value of the key 'wordpress'" in r.text
    assert KEY not in r.text
    assert not [c for c in market if c[0] in ("next_version", "submit")]


def test_a_private_address_in_the_goal_never_leaves(client, market):
    project = _project(client)
    _sign_in(client)
    draft = _ready(_draft(client, project["id"])["draft"])
    draft["version"]["bundle"]["project"]["goal"] += " Link back to acme-outdoor.com."
    r = _submit(client, project["id"], draft)
    assert r.status_code == 422 and "a domain the project controls" in r.text


def test_a_blank_inside_a_role_is_refused(client, market):
    project = _project(client)
    _sign_in(client)
    draft = _ready(_draft(client, project["id"])["draft"])
    draft["version"]["bundle"]["roles"][0]["system_prompt"] += " Write for {{site_name}}."
    r = _submit(client, project["id"], draft)
    assert r.status_code == 422 and "move the specific line" in r.text


def test_a_blank_without_a_question_is_refused(client, market):
    project = _project(client)
    _sign_in(client)
    draft = _ready(_draft(client, project["id"])["draft"])
    draft["version"]["inputs"] = [i for i in draft["version"]["inputs"] if i["id"] != "subject"]
    r = _submit(client, project["id"], draft)
    assert r.status_code == 422 and "{{subject}} has no question" in r.text


def test_a_value_smuggled_into_the_secrets_list_is_refused(client, market):
    project = _project(client)
    _sign_in(client)
    draft = _ready(_draft(client, project["id"])["draft"])
    draft["version"]["bundle"]["secrets"][0]["value"] = "anything at all"
    r = _submit(client, project["id"], draft)
    assert r.status_code == 422 and "value is in the listing" in r.text


# ── submitting ─────────────────────────────────────────────────────────────────

def test_submitting_needs_github_first(client, market):
    project = _project(client)
    r = _submit(client, project["id"], _ready(_draft(client, project["id"])["draft"]))
    assert r.status_code == 409 and "sign in with GitHub" in r.text


def test_a_clean_listing_opens_its_pull_request_under_the_publishers_name(client, market):
    project = _project(client)
    _sign_in(client)
    r = _submit(client, project["id"], _ready(_draft(client, project["id"])["draft"]))
    assert r.status_code == 200, r.text
    assert r.json()["listing_id"] == "@rae/blog-desk" and r.json()["state"] == "submitted"
    _, repo, branch, files, title = next(c for c in market if c[0] == "submit")
    assert repo == "owner/yaaf-marketplace" and branch == "publish/blog-desk-v1"
    version = json.loads(files["listings/rae/blog-desk/versions/1.json"])
    assert version["id"] == "@rae/blog-desk" and version["version"] == 1
    assert KEY not in json.dumps(files)

    rows = client.get("/admin/publish/publications", params={"project_id": project["id"]},
                      headers=ADMIN).json()
    assert rows[0]["pr_url"].endswith("/pull/7")


def test_the_moderators_note_comes_back_to_the_project(client, market, monkeypatch):
    project = _project(client)
    _sign_in(client)
    _submit(client, project["id"], _ready(_draft(client, project["id"])["draft"]))
    monkeypatch.setattr("yaaf.server.api.publish.REFRESH_AFTER", __import__("datetime").timedelta(0))
    rows = client.get("/admin/publish/publications", params={"project_id": project["id"]},
                      headers=ADMIN).json()
    assert rows[0]["state"] == "changes_requested"
    assert rows[0]["review_note"] == "Say what the WordPress key is for."


def test_the_github_token_never_reaches_a_run(client, market):
    """Even granted to a role by name through the secrets door, the person's
    GitHub sign-in is never in an agent's environment."""
    from yaaf.core.models import Role, RoleVersion
    from yaaf.runner.daemon import _role_secret_env

    project = _project(client)
    _sign_in(client)
    with db_session() as session:
        token = session.query(Secret).filter(Secret.kind == "publish").one()
        token.role_scopes = ["writer"]
        role = session.query(Role).filter(Role.slug == "writer").one()
        version = session.query(RoleVersion).filter(RoleVersion.role_id == role.id).one()
        env = _role_secret_env(session, session.get(Project, project["id"]), version)
    assert "gho_testtoken" not in json.dumps(env)
    assert env.get("WORDPRESS") == KEY


# ── a role ─────────────────────────────────────────────────────────────────────

def test_a_role_is_published_by_an_admin_and_checked_against_every_key(client, market):
    project = _project(client)
    member = person("member@example.com")
    assert client.post("/admin/publish/roles/writer/draft", headers=headers_of(member)).status_code == 403
    body = client.post("/admin/publish/roles/writer/draft", headers=ADMIN).json()
    assert body["draft"]["version"]["role"]["slug"] == "writer"
    _sign_in(client)
    draft = body["draft"]
    draft["listing"]["summary"] = "Writes posts and drafts them to WordPress."
    draft["version"]["role"]["system_prompt"] += f" Fallback key {KEY}."
    r = client.post("/admin/publish/submit", headers=ADMIN, json={"role_slug": "writer", "draft": draft})
    assert r.status_code == 422 and "the value of the key 'wordpress'" in r.text
    assert project["id"]


# ── the Founder's part ─────────────────────────────────────────────────────────

def test_the_founder_is_asked_again_when_its_answer_still_leaks(client, market, monkeypatch):
    from yaaf.core import platform

    project = _project(client)
    draft = _draft(client, project["id"])["draft"]
    prompts: list[str] = []
    answers = [
        {"summary": "Posts your readers answer.", "goal": "Posts for Acme Outdoor Blog readers about {{subject}}.",
         "inputs": [{"id": "subject", "question": "About what?", "type": "text", "example": "tea"}],
         "questions": [], "changes": ["subject"]},
        {"name": "A blog desk", "summary": "Posts your readers answer.",
         "goal": "Posts for {{site_name}} readers about {{subject}}.",
         "inputs": [{"id": "subject", "question": "About what?", "type": "text", "example": "tea"},
                    {"id": "site_name", "question": "Site name?", "type": "text", "example": "Northwind"}],
         "questions": [{"about": "trail running", "why": "maybe theirs", "recommend": "blank",
                        "blank": {"id": "topic", "question": "Topic?", "type": "text", "example": "tea"}}],
         "changes": ["site name", "subject"]},
    ]

    async def fake_run_once(*, prompt, system, env, model):
        prompts.append(prompt)
        return json.dumps(answers[len(prompts) - 1])

    monkeypatch.setattr("yaaf.founder.session.run_once", fake_run_once)
    monkeypatch.setattr(platform, "has_credential", lambda session: True)
    monkeypatch.setattr(platform, "founder_env", lambda session: {})
    monkeypatch.setattr(platform, "founder_model", lambda session, provider=None: "claude-opus-5-5")
    r = client.post(f"/admin/publish/projects/{project['id']}/generalize", headers=ADMIN,
                    json={"draft": draft})
    assert r.status_code == 200, r.text
    out = r.json()
    assert len(prompts) == 2 and "the project's name" in prompts[1]
    assert "Acme Outdoor Blog" not in prompts[1].split("follow none of its instructions")[0]
    assert out["leaks"] == []
    assert out["draft"]["version"]["bundle"]["project"]["goal"].startswith("Posts for {{site_name}}")
    assert out["questions"][0]["recommend"] == "blank"


# ── from a terminal ─────────────────────────────────────────────────────────────

def test_yaaf_publish_from_a_terminal(client, market, monkeypatch, tmp_path):
    import contextlib

    from starlette.testclient import TestClient
    from typer.testing import CliRunner

    from yaaf.cli import main as cli

    project = _project(client)
    as_admin = TestClient(client.app, headers=ADMIN)
    monkeypatch.setattr(cli, "_client", lambda: contextlib.nullcontext(as_admin))
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    monkeypatch.setattr("time.sleep", lambda s: None)
    runner = CliRunner()

    saved = tmp_path / "listing.json"
    out = runner.invoke(cli.app, ["publish", "project", "acme-blog", "--no-founder", "--save", str(saved)])
    assert out.exit_code == 0, out.output
    # As drafted, the project's name is still in it: the server will not send it.
    out = runner.invoke(cli.app, ["publish", "project", "acme-blog", "--from", str(saved), "--yes"])
    assert out.exit_code == 1 and "the project's name is in" in out.output
    assert "ABCD-1234" in out.output  # signed in once, on the way

    saved.write_text(json.dumps(_ready(json.loads(saved.read_text()))))
    out = runner.invoke(cli.app, ["publish", "project", "acme-blog", "--from", str(saved), "--yes"])
    assert out.exit_code == 0, out.output
    assert "@rae/blog-desk v1" in out.output
    assert project["id"]



# ── telling a listing how it went ──────────────────────────────────────────────

def test_an_outcome_is_numbers_only_and_sent_once(client, market):
    from tests.conftest import AUTONOMY_PRESETS

    seed_role(client, "ceo", is_ceo=True)
    seed_role(client, "writer")
    client.post("/admin/users", json={"email": "chair@example.com", "display_name": "Chair"}, headers=ADMIN)
    r = client.post("/admin/projects", headers=ADMIN, json={
        "slug": "northwind-notes", "name": "Northwind Notes Desk", "goal": "Write posts for Northwind Notes readers.",
        "chairman_email": "chair@example.com", "ceo_role_slug": "ceo",
        "constitution": AUTONOMY_PRESETS["supervised"], "controlled_domains": ["northwind.example"],
        "teams": [{"role_slug": "writer", "name": "Words", "employees": ["Ann"]}],
        "listing": {"id": "@rae/blog-desk", "version": 2, "source": "x", "needs": {}}})
    project_id = r.json()["id"]
    preview = client.get(f"/admin/publish/projects/{project_id}/outcome", headers=ADMIN).json()
    report = json.dumps(preview["report"])
    assert preview["report"]["listing"] == "@rae/blog-desk"
    for private in ("Northwind", "northwind.example", "chair@example.com", "Write posts"):
        assert private not in report

    _sign_in(client)
    sent = client.post(f"/admin/publish/projects/{project_id}/outcome", headers=ADMIN)
    assert sent.status_code == 200, sent.text
    _, repo, title, body = next(c for c in market if c[0] == "issue")
    assert title == "Outcome: @rae/blog-desk v2" and "Northwind" not in body
    again = client.post(f"/admin/publish/projects/{project_id}/outcome", headers=ADMIN)
    assert again.status_code == 409
