"""Invariant selftests: what the fire drill is allowed to call missing.

The drill runs against a throwaway database, so it cannot see the install it
was launched from — and it used to report that absence as if it were the
chairman's. "Credentials, Slack and data sources — a drill reads no secret and
opens no socket" is true on every install in the world, including the one that
connected a Slack workspace app-wide an hour ago, and the Founder read it and
told that chairman to go and attach the Slack token he had already attached.
A preflight that invents a missing dependency is worse than one that says
nothing: he cannot tell it apart from a real finding.

So the wiring facts are read by the caller that holds a live session and handed
to the drill as data. These tests hold the `client` its sibling module
deliberately avoids, and none of them runs a drill: what is under test is the
prose and the two hops the facts take to reach it, and a drill answers none of
that.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.conftest import ADMIN, seed_role
from yaaf import rehearsal
from yaaf.core.db import db_session
from yaaf.rehearsal import install
from yaaf.rehearsal import report as rehearsal_report

CONNECTED = {"slack": {"bot_token": True, "signing_secret": True, "app_token": True,
                       "chairman_user": True, "auto_create_channels": True},
             "notifications": {"email": False, "webhook": False}}
BARE = {"slack": {"bot_token": False, "signing_secret": False, "app_token": False,
                  "chairman_user": False, "auto_create_channels": True},
        "notifications": {"email": False, "webhook": False}}


def facts() -> dict:
    with db_session() as session:
        return install.collect(session)


# ── reading the install ─────────────────────────────────────────────────────────


def test_a_connected_workspace_is_read_off_the_live_install(client):
    client.put("/admin/settings/slack", headers=ADMIN, json={
        "bot_token": "xoxb-global-workspace", "signing_secret": "global-signing",
        "app_token": "xapp-global-socket", "chairman_slack_user": "U-CHAIR"})

    slack = facts()["slack"]

    assert slack == {"bot_token": True, "signing_secret": True, "app_token": True,
                     "chairman_user": True, "auto_create_channels": True}


def test_an_install_with_no_slack_says_so(client):
    assert facts()["slack"]["bot_token"] is False


def test_it_carries_no_token_and_no_user_id(client):
    """These facts travel into a subprocess, into a report, onto a screen and
    into a language model's context. Whether a token exists is the whole
    question; the token is not part of it."""
    client.put("/admin/settings/slack", headers=ADMIN,
               json={"bot_token": "xoxb-global-workspace",
                     "chairman_slack_user": "U-CHAIR"})

    flat = repr(facts())

    assert "xoxb" not in flat
    assert "U-CHAIR" not in flat
    assert all(isinstance(value, bool)
               for section in facts().values() for value in section.values())


# ── what the report is then entitled to say ─────────────────────────────────────


def test_a_connected_workspace_is_reported_as_wiring_he_already_has():
    line = install.slack_line(CONNECTED)

    assert "already" in line
    assert "provisioned" in line  # auto-create is on: the channels appear at creation
    # The sentence that started this: never tell a chairman to attach a thing
    # he has attached.
    assert "Nothing to attach" in line
    assert "until one is attached" not in line


def test_an_install_with_no_workspace_is_told_to_attach_one():
    line = install.slack_line(BARE)

    assert "no app-wide Slack connection" in line
    assert "until one is attached" in line


def test_half_a_connection_is_reported_as_half_a_connection():
    """A signing secret with no bot token mirrors exactly as much as nothing at
    all, so the pivot is the bot token — but saying what IS there is what stops
    the chairman pasting all three again to find out which one took."""
    half = {"slack": {**BARE["slack"], "signing_secret": True}}

    line = install.slack_line(half)

    assert "a signing secret" in line and "no bot token" in line


def test_a_piece_still_missing_on_a_connected_install_is_named():
    connected = {"slack": {**CONNECTED["slack"], "app_token": False}}

    line = install.slack_line(connected)

    assert "Still unset app-wide: an app token" in line


def test_a_drill_with_no_install_behind_it_claims_nothing_either_way():
    """`yaaf rehearse` on a checkout has no deployment to read. Silence is the
    honest answer there; the old line was a claim."""
    line = install.slack_line(None)

    assert "says nothing either way" in line
    assert "attached" not in line


def test_a_spec_that_turns_slack_off_is_not_reported_as_wired():
    line = install.slack_line(CONNECTED, disabled_by_spec=True)

    assert "switches it off" in line
    assert "already" not in line


def test_notices_that_have_somewhere_to_go_are_not_reported_as_lost():
    line = install.notification_line(
        {"notifications": {"email": True, "webhook": False}})

    assert "muted the transport, not your setup" in line


def test_an_install_with_no_notice_channel_at_all_is_a_real_finding():
    line = install.notification_line(BARE)

    assert "would go nowhere as well" in line


# ── and the report carries both ─────────────────────────────────────────────────


def build(install_facts, spec=None):
    """A report with no project in it: the early-return path, which needs no
    client and no drill. What is asserted here is the prose, and the prose is
    written before anything is run."""
    return rehearsal_report.build(
        None, None, spec or {"slug": "drill", "mode": "deliver"}, [], [], 30, None,
        {"loaded": [], "unresolved": []}, 0, install=install_facts)


@pytest.mark.parametrize("install_facts", [CONNECTED, BARE, None])
def test_the_report_carries_the_facts_as_data_too(install_facts):
    """Prose is for the chairman; the block is so a screen or the Founder can
    act on it without parsing English."""
    assert build(install_facts)["install"] == install_facts


def test_the_not_exercised_list_stops_calling_a_connected_workspace_missing():
    lines = " ".join(build(CONNECTED)["not_exercised"])

    assert "already" in lines
    assert "Nothing to attach" in lines
    assert "until one is attached" not in lines
    # The honest half is still there — the drill really did open no socket.
    assert "opens no socket" in lines


def test_the_spec_gets_the_last_word_on_whether_slack_is_on():
    spec = {"slug": "drill", "mode": "deliver",
            "integrations": {"slack": {"enabled": False}}}

    lines = " ".join(build(CONNECTED, spec)["not_exercised"])

    assert "switches it off" in lines


# ── across the two process boundaries, without paying for a drill ───────────────
#
# The seam is worth testing and a drill is not what tests it: the facts are put
# on a file by the route and taken off it by the entry point, and both halves
# are observable without running a project. Running one here would buy the same
# assertion for three seconds — see CLAUDE.md on which tier answers which
# question.


def test_the_route_hands_the_drill_what_this_install_has_wired(client, monkeypatch):
    """The route the dashboard and the Founder both reach the drill through."""
    import subprocess

    seed_role(client, "ceo", is_ceo=True)
    client.put("/admin/settings/slack", headers=ADMIN, json={
        "bot_token": "xoxb-global-workspace", "signing_secret": "global-signing",
        "app_token": "xapp-global-socket", "chairman_slack_user": "U-CHAIR"})
    seen: dict = {}

    def fake_run(argv, **kwargs):
        # Read it here: the temp directory is gone by the time the route
        # returns, which is the whole reason the child is given a path at all.
        flag = argv.index("--install")
        seen["argv"] = argv
        seen["install"] = json.loads(Path(argv[flag + 1]).read_text())
        return SimpleNamespace(stdout='{"ok": true}', stderr="", returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    response = client.post("/admin/rehearse", headers=ADMIN, json={
        "spec": {"project": {"slug": "drill", "goal": "Ship it.",
                             "ceo_role_slug": "ceo"}}})

    assert response.status_code == 200, response.text
    assert seen["install"]["slack"]["bot_token"] is True
    assert seen["install"]["slack"]["chairman_user"] is True
    # And the spec still travels the way it did — the note is an addition to
    # that call, not a change to it.
    assert seen["argv"][3].endswith("spec.json")


def test_the_entry_point_takes_them_off_the_file_and_into_the_report(tmp_path, monkeypatch):
    """The far side of the same seam: `python -m yaaf.rehearsal --install`."""
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"slug": "drill", "goal": "Ship it."}))
    note = tmp_path / "install.json"
    note.write_text(json.dumps(CONNECTED))
    seen: dict = {}

    def fake_rehearse(spec_body, **kwargs):
        seen.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(rehearsal, "rehearse", fake_rehearse)
    assert rehearsal.main([str(spec), "--json", "--install", str(note)]) == 0
    assert seen["install"] == CONNECTED


def test_a_note_it_cannot_read_costs_a_footnote_and_not_the_drill(tmp_path, monkeypatch):
    """The wiring note is a courtesy; the verdict is the product. A drill that
    refused to run because this file would not parse would have traded the
    whole preflight for the sentence at the bottom of it."""
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"slug": "drill", "goal": "Ship it."}))
    note = tmp_path / "install.json"
    note.write_text("{ this is not json")
    seen: dict = {}

    monkeypatch.setattr(rehearsal, "rehearse",
                        lambda spec_body, **kwargs: seen.update(kwargs) or {"ok": True})
    assert rehearsal.main([str(spec), "--json", "--install", str(note)]) == 0
    assert seen["install"] is None
