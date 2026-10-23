"""Invariant selftests: the CLI says what went wrong, in one line.

The audit's CLI findings, each a first-hour mistake that used to read as a
crash or a wall of JSON:

1. A server that is not running answered with a 224-line traceback.
2. A refused request printed the raw body; a validation error was a
   175-line dump for one missing field, and only six commands gave a hint.
3. `create-project` on one of the shipped examples — the obvious first try —
   failed, because examples are setup files and it wanted a bare spec.
4. `rehearse` on a path that does not exist printed a traceback.
5. Eighteen commands had no help text, and `--help` listed sixty commands in
   the order they were written.
"""

from __future__ import annotations

import contextlib
import json
import pathlib

import httpx
from starlette.testclient import TestClient
from typer.testing import CliRunner

from tests.conftest import ADMIN, seed_role
from yaaf.cli import main as cli

EXAMPLES = pathlib.Path(__file__).resolve().parents[2] / "examples"
runner = CliRunner()


def test_a_server_that_is_not_there_is_one_sentence_not_a_traceback():
    # Port 9 is discard: nothing listens, so the connection is refused.
    result = runner.invoke(cli.app, ["projects"],
                           env={"YAAF_API": "http://127.0.0.1:9", "YAAF_TOKEN": "x"})
    assert result.exit_code == 1
    assert "Can't reach yaaf at http://127.0.0.1:9" in result.output
    assert "Traceback" not in result.output
    assert len(result.output.strip().splitlines()) <= 2


def test_a_validation_refusal_reads_as_field_and_reason():
    response = httpx.Response(422, json={"detail": [
        {"loc": ["body", "slug"], "msg": "Field required", "type": "missing"},
        {"loc": ["body", "goal"], "msg": "String should have at least 10 characters"}]})
    text = cli._detail(response)
    assert "slug: Field required" in text
    assert "goal: String should have at least 10 characters" in text
    assert "type" not in text


def test_every_command_explains_itself_and_sits_in_a_section():
    from typer.main import get_command

    root = get_command(cli.app)
    silent = [name for name, cmd in root.commands.items()
              if not (cmd.help or "").strip() and not getattr(cmd, "commands", None)]
    for group_name, group in root.commands.items():
        for name, cmd in (getattr(group, "commands", None) or {}).items():
            if not (cmd.help or "").strip():
                silent.append(f"{group_name} {name}")
    assert not silent, silent
    assert {info.rich_help_panel for info in cli.app.registered_commands} <= set(cli._ORDER)
    # The section a newcomer needs is the first one they read.
    assert cli.app.registered_commands[0].rich_help_panel == "Get started"


def test_rehearsing_a_file_that_is_not_there_says_so():
    result = runner.invoke(cli.app, ["rehearse", "no/such/spec.json"])
    assert result.exit_code == 2
    assert "can't read no/such/spec.json" in result.output
    assert "Traceback" not in result.output


def test_a_shipped_example_creates_the_project_it_describes(client, monkeypatch):
    """The setup file's roles reach the library and its project is created —
    and example 01 arrives Supervised, which is the lesson it exists for."""
    as_admin = TestClient(client.app, headers=ADMIN)
    monkeypatch.setattr(cli, "_client", lambda: contextlib.nullcontext(as_admin))
    client.post("/admin/users", json={"email": "chair@example.com", "display_name": "Chair"},
                headers=ADMIN)
    # What `seed-roles` gives every real install; the file carries only the
    # roles a library might lack.
    seed_role(client, "ceo", is_ceo=True)
    seed_role(client, "writer")

    result = runner.invoke(cli.app, ["create-project",
                                     str(EXAMPLES / "01-your-first-hour.json")])
    assert result.exit_code == 0, result.output
    # Its credential is named, not carried: said once, and the project is still made.
    assert "no LLM credential called 'anthropic-api-key'" in result.output
    created = json.loads(result.output[result.output.index("{"):])
    dash = client.get(f"/observe/projects/{created['id']}", headers=ADMIN).json()
    assert dash["constitution"]["plan_authority"] == "chairman"


def test_a_setup_file_brings_the_roles_the_library_lacks(client, monkeypatch, tmp_path):
    as_admin = TestClient(client.app, headers=ADMIN)
    monkeypatch.setattr(cli, "_client", lambda: contextlib.nullcontext(as_admin))
    client.post("/admin/users", json={"email": "chair@example.com", "display_name": "Chair"},
                headers=ADMIN)
    seed_role(client, "ceo", is_ceo=True)
    bundle = {"yaaf_bundle": 1, "kind": "project_setup", "project": {
        "slug": "carried", "name": "Carried", "goal": "Write one carried note, sourced.",
        "chairman_email": "chair@example.com", "ceo_role_slug": "ceo",
        "teams": [{"role_slug": "scribe", "name": "Desk", "employees": ["Ada"]}]},
        "roles": [{"slug": "scribe", "name": "Scribe", "is_ceo_role": False,
                   "description": "writes", "system_prompt": "You write.",
                   "model": "claude-sonnet-5-5"}]}
    spec = tmp_path / "carried.json"
    spec.write_text(json.dumps(bundle))

    result = runner.invoke(cli.app, ["create-project", str(spec)])
    assert result.exit_code == 0, result.output
    assert "added role scribe from the setup file" in result.output


def test_the_drill_report_is_the_first_thing_printed():
    """Deliberately slow: one whole drill (~3s), because the noise it guards
    against only exists while a drill runs. In a subprocess, because a drill
    re-points the process-global engine this module's other tests are using."""
    import subprocess
    import sys

    done = subprocess.run(
        [sys.executable, "-m", "yaaf.cli.main", "rehearse",
         str(EXAMPLES / "01-your-first-hour.json")],
        capture_output=True, text=True, timeout=120, check=False)
    assert done.stdout.startswith("Fire drill — "), done.stdout[:300]
    assert done.stderr.strip() == "", done.stderr[:500]


def test_seeding_while_signed_out_says_so_once_and_fails(client, monkeypatch, tmp_path):
    """It printed one `401 sign in first` per file — 38 of them — and exited 0,
    so a setup script carried on into a library with nothing in it."""
    signed_out = TestClient(client.app)
    monkeypatch.setattr(cli, "_client", lambda: contextlib.nullcontext(signed_out))
    for slug in ("one", "two", "three"):
        (tmp_path / f"{slug}.json").write_text(json.dumps({"slug": slug}))
    result = runner.invoke(cli.app, ["seed-roles", str(tmp_path)])
    assert result.exit_code == 1
    assert result.output.count("401") == 1
    assert "yaaf login" in result.output


def test_status_says_the_sentences_and_lists_what_they_counted(client, monkeypatch):
    """The count in a project's sentence and the list under it are one set:
    a sentence that says two decisions over a list of one sends the reader
    looking for the other."""
    from tests.conftest import bearer, live_token, make_project

    as_admin = TestClient(client.app, headers=ADMIN)
    monkeypatch.setattr(cli, "_client", lambda: contextlib.nullcontext(as_admin))
    project = make_project(client, slug="desk-one", integrations={"runtime": "mock"})
    _, token = live_token(project["id"], project["ceo_agent_id"])
    filed = client.post("/agent/approvals", headers=bearer(token), json={
        "kind": "spend", "title": "Buy the $49 injury-news feed",
        "body": "Team news reaches us a day late.", "if_declined": "We react a day late.",
        "amount_usd": 49})
    assert filed.status_code == 200, filed.text

    result = runner.invoke(cli.app, ["status"])
    assert result.exit_code == 0, result.output
    assert result.output.startswith("1 project. Desk")
    assert "desk-one  1 decision waiting on you." in result.output
    assert "desk-one: Buy the $49 injury-news feed" in result.output

    as_json = runner.invoke(cli.app, ["status", "desk-one", "--json"])
    assert json.loads(as_json.output)[0]["verdict"] == "needs_you"
