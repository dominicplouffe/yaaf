"""Quick founding: a brief in, a running project out, nobody answering.

For a caller with a job and no one to answer questions — another agent
framework handing off "find the best home printer". The attacks are what that
shortcut invites:

  * the request returns at once; drafting, creating and starting happen behind
    it, and the thread says where they are;
  * the interview still happens — every recommendation is accepted and kept,
    and the CEO's briefing carries what was decided on the chairman's behalf;
  * nothing a person would have had to supply gets in: a draft wanting email,
    domains or a second credential is refused inside the turn, fixed on the
    retry, or ends the founding with nothing created;
  * who pays is never a guess: the caller's choice, the chairman's last, or
    the only one — otherwise it is refused with the menu;
  * over MCP it is confirm-gated like every write that spends money, and a
    client reads only its own chairman's quick foundings.
"""

from __future__ import annotations

import json
import time

import pytest

from tests.conftest import ADMIN, seed_role
from tests.selftest_invariants.test_founder import API_KEY, PROJECT
from tests.selftest_invariants.test_mcp_founding import call, confirmed
from tests.selftest_invariants.test_mcp_founding import founder as mcp_client
from yaaf.founder import progress

DELIVER = {
    **PROJECT,
    "slug": "home-printer-pick",
    "name": "Best home printer",
    "goal": ("A ranked list of three to five home printers, each with its price, its "
             "cost per page and the sources behind it, for a household that prints "
             "mostly documents."),
    "mode": "deliver",
    "constitution": {"stakes": "normal"},
    "teams": [{"role_slug": "worker", "name": "Research", "employees": ["Ada"]},
              {"role_slug": "adversary", "name": "Challenge", "employees": ["Sam"]}],
}

ROUND = {"questions": [
    {"id": "reader", "title": "Who reads it", "question": "For you, as a household buyer?",
     "options": [{"label": "Me, at home"}, {"label": "Someone else"}], "recommended": 0},
    {"id": "shape", "title": "Shape", "question": "Deliver one list, then stop?",
     "options": [{"label": "Deliver"}, {"label": "Operate"}], "recommended": 0}],
    "settled": [{"topic": "Deadline", "decision": "none", "source": "assumed"}],
    "done": False}


@pytest.fixture()
def quick(client, monkeypatch):
    """A configured Founder, one LLM credential to pay with, and a hook to
    script each Founder turn of a quick founding in order."""
    seed_role(client, "ceo", is_ceo=True)
    seed_role(client, "worker")
    seed_role(client, "adversary", category="challenge")
    assert client.put("/admin/founder/credential", json={"value": API_KEY, "mode": "api_key"},
                      headers=ADMIN).status_code == 200
    paying = client.post("/admin/settings/llm-credentials", headers=ADMIN, json={
        "name": "Household key", "mode": "api_key", "value": "sk-ant-api03-test-not-real",
        "provider": "anthropic"})
    assert paying.status_code == 201, paying.text
    progress.clear()

    built: dict = {}
    real_build = __import__("yaaf.founder.tools", fromlist=["x"]).build_server

    def spy_build(*args, **kwargs):
        built["tools"] = real_build(*args, **kwargs)
        return built["tools"]

    monkeypatch.setattr("yaaf.founder.tools.build_server", spy_build)
    seen: dict = {"prompts": [], "results": [], "graded": []}

    def script(*turns, blocks_on=None):
        remaining = list(turns)

        async def fake_run(prompt, opts, stderr_sink=None, thread_id=""):
            if not opts.mcp_servers:
                seen["graded"].append(prompt)
                return json.dumps({"elements": [], "summary": "graded"})
            seen["prompts"].append(prompt)
            if blocks_on is not None:
                import anyio

                await anyio.to_thread.run_sync(blocks_on.wait, abandon_on_cancel=True)
            for name, args in remaining.pop(0) if remaining else []:
                result = await built["tools"].handlers[name]({**args})
                seen["results"].append((name, json.loads(result["content"][0]["text"])))
            return "Built a one-off research project."

        monkeypatch.setattr("yaaf.founder.session.run", fake_run)
        return seen

    script.credential = paying.json()
    return script


def project_status(project_id: str) -> str:
    from yaaf.core.db import db_session
    from yaaf.core.models import Project

    with db_session() as session:
        return str(session.get(Project, project_id).status)


def settle(client, thread_id: str) -> dict:
    for _ in range(200):
        state = client.get(f"/admin/founder/threads/{thread_id}", headers=ADMIN).json()
        if not state["running"]:
            return state
        time.sleep(0.05)
    raise AssertionError("the quick founding never settled")


def test_a_brief_becomes_a_running_project(client, quick):
    seen = quick([("ask", ROUND), ("propose_bundle", {"project": DELIVER, "tempo": "live"})])

    response = client.post("/admin/founder/quick", headers=ADMIN,
                           json={"brief": "Research the best printer for home use."})
    assert response.status_code == 202, response.text
    assert response.json()["quick"]["phase"] == "drafting"
    thread = settle(client, response.json()["id"])

    status = thread["quick"]
    assert thread["last_error"] is None and status["error"] is None, thread
    assert status["phase"] == "started"
    assert status["billing"] == "Household key"
    assert project_status(status["project_id"]) == "running"
    assert thread["created_project_id"] == status["project_id"]
    # What was decided on the chairman's behalf, recommendation by recommendation.
    assert {"question": "Shape", "answer": "Deliver"} in status["decided"]
    assert {"question": "Deadline", "answer": "none"} in status["decided"]
    # The Founder was told nobody answers, and who pays.
    assert "<quick-founding>" in seen["prompts"][0]
    assert "Household key" in seen["prompts"][0]


def test_the_ceo_is_briefed_with_what_was_accepted(client, quick):
    seen = quick([("ask", ROUND), ("propose_bundle", {"project": DELIVER, "tempo": "live"})])
    started = client.post("/admin/founder/quick", headers=ADMIN,
                          json={"brief": "Research the best printer for home use."}).json()
    thread = settle(client, started["id"])

    turns = thread["turns"]
    assert turns[-1]["role"] == "chairman", (seen["results"], thread["last_error"])
    assert "every recommendation accepted" in turns[-1]["text"]
    assert "Q2 Shape — yes — Deliver" in turns[-1]["text"]
    from sqlalchemy import select

    from yaaf.core.db import db_session
    from yaaf.core.models import Message

    with db_session() as session:
        bodies = " ".join(session.scalars(select(Message.body).where(
            Message.project_id == thread["quick"]["project_id"])))
    assert "designed in a conversation with the chairman" in bodies
    assert "Research the best printer for home use." in bodies


def test_a_draft_wanting_what_nobody_supplied_is_refused_then_fixed(client, quick):
    seen = quick(
        [("ask", ROUND), ("propose_bundle", {"project": {**DELIVER, "needs_mail": True},
                                             "tempo": "live"})],
        [("propose_bundle", {"project": DELIVER, "tempo": "live"})],
    )
    started = client.post("/admin/founder/quick", headers=ADMIN,
                          json={"brief": "Research printers and email me the list."}).json()
    thread = settle(client, started["id"])

    refused = seen["results"][1][1]
    assert refused["ok"] is False
    assert any(p.startswith("needs_mail") for p in refused["problems"])
    # The second turn was handed the refusal, and fixed it.
    assert "needs_mail" in seen["prompts"][1]
    assert thread["quick"]["phase"] == "started"


def test_a_draft_never_fixed_creates_nothing(client, quick):
    wants_domains = {**DELIVER, "controlled_domains": ["printers.example"]}
    quick([("propose_bundle", {"project": wants_domains, "tempo": "live"})],
          [("propose_bundle", {"project": wants_domains, "tempo": "live"})])
    before = client.get("/admin/projects", headers=ADMIN).json()
    started = client.post("/admin/founder/quick", headers=ADMIN,
                          json={"brief": "Watch printer prices on my site."}).json()
    thread = settle(client, started["id"])

    assert thread["quick"]["phase"] == "failed"
    assert "controlled_domains" in thread["last_error"]
    assert "Nothing was created" in thread["last_error"]
    assert client.get("/admin/projects", headers=ADMIN).json() == before


def test_a_quick_founding_is_not_graded(client, quick):
    """Nobody is left to ask about what a grade finds."""
    seen = quick([("ask", ROUND), ("propose_bundle", {"project": DELIVER, "tempo": "live"})])
    started = client.post("/admin/founder/quick", headers=ADMIN,
                          json={"brief": "Research the best printer for home use."}).json()
    settle(client, started["id"])
    time.sleep(0.2)
    assert seen["graded"] == []


def test_asked_not_to_start_it_is_created_and_left(client, quick):
    quick([("ask", ROUND), ("propose_bundle", {"project": DELIVER, "tempo": "live"})])
    started = client.post("/admin/founder/quick", headers=ADMIN,
                          json={"brief": "Research printers.", "start": False}).json()
    thread = settle(client, started["id"])
    assert thread["quick"]["phase"] == "created", thread["last_error"]
    assert project_status(thread["quick"]["project_id"]) != "running"


def test_who_pays_is_named_remembered_or_the_only_one(client, quick):
    """Several credentials and no earlier project: refused, with the menu."""
    client.post("/admin/settings/llm-credentials", headers=ADMIN, json={
        "name": "Work key", "mode": "api_key", "value": "sk-ant-api03-other-not-real",
        "provider": "anthropic"})
    quick([("ask", ROUND), ("propose_bundle", {"project": DELIVER, "tempo": "live"})])

    refused = client.post("/admin/founder/quick", headers=ADMIN,
                          json={"brief": "Research printers."})
    assert refused.status_code == 422
    assert "Household key" in refused.json()["detail"]
    assert "Work key" in refused.json()["detail"]

    named = client.post("/admin/founder/quick", headers=ADMIN,
                        json={"brief": "Research printers.", "billing": "work KEY"})
    assert named.status_code == 202, named.text
    assert settle(client, named.json()["id"])["quick"]["billing"] == "Work key"

    # The chairman's last project paid with Work key, so that is the default now.
    quick([("ask", ROUND), ("propose_bundle", {
        "project": {**DELIVER, "slug": "printer-two"}, "tempo": "live"})])
    remembered = client.post("/admin/founder/quick", headers=ADMIN,
                             json={"brief": "Research printers again."})
    assert remembered.status_code == 202, remembered.text
    assert remembered.json()["quick"]["billing"] == "Work key"
    settle(client, remembered.json()["id"])


def test_without_a_founder_credential_it_says_so_at_once(client):
    seed_role(client, "ceo", is_ceo=True)
    response = client.post("/admin/founder/quick", headers=ADMIN,
                           json={"brief": "Research printers."})
    assert response.status_code == 422
    assert "Settings → Founder" in response.json()["detail"]


def test_over_mcp_it_is_confirmed_then_polled(client, quick):
    quick([("ask", ROUND), ("propose_bundle", {"project": DELIVER, "tempo": "live"})])
    token = mcp_client(client)["token"]

    failed, preview = call(client, token, "quick_project",
                           {"brief": "Research the best printer for home use."})
    assert failed is False and preview["confirm_required"] is True
    assert preview["about_to"]["billing"]["name"] == "Household key"
    assert "secrets" in preview["about_to"]["cannot_have"]

    failed, began = confirmed(client, token, "quick_project",
                              {"brief": "Research the best printer for home use."})
    assert failed is False, began
    assert settle(client, began["thread"])["last_error"] is None

    failed, status = call(client, token, "quick_project_status", {"thread": began["thread"]})
    assert failed is False, status
    assert status["phase"] == "started" and status["slug"] == "home-printer-pick"
    assert "project" in status["next"]


def test_a_client_reads_only_its_own_chairmans_quick_foundings(client, quick):
    quick([("ask", ROUND), ("propose_bundle", {"project": DELIVER, "tempo": "live"})])
    conversation = client.post("/admin/founder/threads", headers=ADMIN, json={}).json()
    token = mcp_client(client)["token"]

    failed, message = call(client, token, "quick_project_status",
                           {"thread": conversation["id"]})
    assert failed is True and "no quick founding" in message
    failed, message = call(client, token, "quick_project_status", {"thread": "fnd_nope"})
    assert failed is True and "no quick founding" in message


def test_the_terminal_starts_it_and_says_what_was_decided(client, quick, monkeypatch):
    import contextlib

    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from yaaf.cli import main as cli

    quick([("ask", ROUND), ("propose_bundle", {"project": DELIVER, "tempo": "live"})])
    as_admin = TestClient(client.app, headers=ADMIN)
    monkeypatch.setattr(cli, "_client", lambda: contextlib.nullcontext(as_admin))
    monkeypatch.setattr(cli, "QUICK_POLL_SECONDS", 0.02)

    shown = CliRunner().invoke(cli.app, ["create-project", "--quick",
                                         "Research the best printer for home use."])
    assert shown.exit_code == 0, shown.output
    assert "paid by Household key" in shown.output
    assert "Started Best home printer (home-printer-pick) — Deliver" in shown.output
    assert "  - Shape: Deliver" in shown.output
    assert "yaaf briefing home-printer-pick" in shown.output


def test_the_terminal_says_why_nothing_was_created(client, quick, monkeypatch):
    import contextlib

    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from yaaf.cli import main as cli

    wants_mail = {**DELIVER, "needs_mail": True}
    quick([("propose_bundle", {"project": wants_mail, "tempo": "live"})],
          [("propose_bundle", {"project": wants_mail, "tempo": "live"})])
    as_admin = TestClient(client.app, headers=ADMIN)
    monkeypatch.setattr(cli, "_client", lambda: contextlib.nullcontext(as_admin))
    monkeypatch.setattr(cli, "QUICK_POLL_SECONDS", 0.02)

    shown = CliRunner().invoke(cli.app, ["create-project", "--quick", "Email me printers."])
    assert shown.exit_code == 1
    assert "It was not created" in shown.output and "needs_mail" in shown.output
    flags_alone = CliRunner().invoke(cli.app, ["create-project", "--wait", "x"])
    assert flags_alone.exit_code == 2


def test_stopping_a_quick_founding_hands_the_brief_back_as_a_blank_draft(client, quick):
    """Stop is the way out of a brief he got wrong, here as in the conversation;
    what is left is an ordinary draft, so what he sends next is a conversation."""
    import threading

    release = threading.Event()
    quick([("ask", ROUND)], blocks_on=release)
    try:
        began = client.post("/admin/founder/quick", headers=ADMIN,
                            json={"brief": "Research the best pinter."}).json()
        stopped = client.post(f"/admin/founder/threads/{began['id']}/stop",
                              headers=ADMIN).json()
        assert stopped["withdrawn"]["message"] == "Research the best pinter."
        assert stopped["quick"] is None
        assert stopped["turns"] == []
    finally:
        release.set()
