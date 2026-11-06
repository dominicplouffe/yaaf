"""Invariant selftests: `yaaf setup` asks for three things and keeps none of
them anywhere it should not.

The wizard is a person at a terminal and a server it starts, which the suite
does not run whole; what is attacked here is what it decides. That the unit
file it writes holds no secret, however many the shell it ran in held. That a
key is checked with the provider before it is kept, and a refused one is not
kept at all. That a password shorter than the server would accept is asked
for again rather than sent. And that it finds harness CLIs the jail would
otherwise not see.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from yaaf.cli import setup


def _answers(monkeypatch, *replies: str) -> list[str]:
    """Feed the wizard's prompts, in order; record what it asked."""
    queue, asked = list(replies), []

    def prompt(text, *a, **k):
        asked.append(text)
        if not queue:
            raise AssertionError(f"the wizard asked more than expected: {text!r}")
        return queue.pop(0)

    monkeypatch.setattr(setup.typer, "prompt", prompt)
    return asked


def test_the_service_file_never_carries_a_key(monkeypatch, tmp_path):
    """A unit file is world-readable on most desktops and is not a secret
    store. yaaf up reads its master key from its own folder, so nothing the
    shell held needs to be written down — and nothing is."""
    monkeypatch.setenv("YAAF_MASTER_KEY", "the-master-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-the-chairmans-own")
    monkeypatch.setenv("YAAF_DATABASE_URL", "postgresql+psycopg://u:password@db/yaaf")
    monkeypatch.setattr(setup, "_npm_global_bind", lambda: None)
    unit = setup.unit_file(8080, tmp_path)

    for secret in ("the-master-key", "sk-ant-the-chairmans-own", "password@db"):
        assert secret not in unit, f"the unit file wrote down {secret!r}"
    assert " up --host " in unit, "the service runs agents in the jail, not bare"
    assert str(tmp_path) in unit


def test_harness_clis_in_the_home_folder_are_made_visible_to_the_jail(monkeypatch, tmp_path):
    """`npm install -g` under a version manager lands in the home folder, which
    the jail hides; a Codex it cannot see is a seat that dies on its first run.
    Under /usr it needs nothing, and nothing is added."""
    prefix = tmp_path / "npm-global"
    prefix.mkdir()
    monkeypatch.setattr(setup.shutil, "which", lambda name: "/usr/bin/npm")

    def npm(args, **k):
        return type("Done", (), {"stdout": f"{prefix}\n"})()

    monkeypatch.setattr(setup.subprocess, "run", npm)
    monkeypatch.delenv("YAAF_HOST_JAIL_BINDS", raising=False)
    assert setup.service_environment()["YAAF_HOST_JAIL_BINDS"] == str(prefix)

    monkeypatch.setattr(setup.subprocess, "run",
                        lambda args, **k: type("Done", (), {"stdout": "/usr\n"})())
    assert "YAAF_HOST_JAIL_BINDS" not in setup.service_environment()


def test_a_password_the_server_would_refuse_is_asked_for_again(monkeypatch):
    from yaaf.server import identity

    good = "x" * identity.MIN_PASSWORD
    _answers(monkeypatch, "short", good, "a-different-one-entirely", good, good)
    assert setup._ask_password() == good


class _Provider:
    """The settings API as the wizard meets it, with a provider that accepts
    exactly one key."""

    def __init__(self, good: str, existing: list | None = None):
        self.good, self.rows, self.deleted = good, list(existing or []), []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/admin/settings/llm-credentials":
            return httpx.Response(200, json=self.rows)
        if request.method == "POST" and path == "/admin/settings/llm-credentials":
            body = json.loads(request.content)
            row = {"id": f"secr_{len(self.rows) + len(self.deleted)}", "name": body["name"],
                   "value": body["value"]}
            self.rows.append(row)
            return httpx.Response(201, json={k: row[k] for k in ("id", "name")})
        if request.method == "GET" and path.endswith("/models"):
            row = next(r for r in self.rows if r["id"] == path.split("/")[-2])
            if row["value"] == self.good:
                return httpx.Response(200, json={"models": [{"id": "claude"}]})
            return httpx.Response(502, json={"detail": "HTTP 401: invalid x-api-key"})
        if request.method == "DELETE":
            gone = path.split("/")[-1]
            self.deleted.append(gone)
            self.rows = [r for r in self.rows if r["id"] != gone]
            return httpx.Response(200, json={"deleted": gone})
        return httpx.Response(404, json={"detail": "Not Found"})


@pytest.fixture()
def provider(monkeypatch):
    def install(fake: _Provider) -> _Provider:
        monkeypatch.setattr(setup, "_client", lambda base: httpx.Client(
            base_url=base, transport=httpx.MockTransport(fake)))
        return fake
    return install


def test_a_key_the_provider_refuses_is_not_kept(monkeypatch, provider):
    """Checked with the provider's own free model list before it is kept. A
    refused key is deleted, not left on the box looking configured."""
    fake = provider(_Provider(good="sk-ant-good"))
    _answers(monkeypatch, "1", "sk-ant-typo", "sk-ant-good")
    kept = setup._key("http://yaaf.test")

    assert kept is not None and [r["value"] for r in fake.rows] == ["sk-ant-good"]
    assert fake.deleted, "the refused key stayed on the box"


def test_three_refused_keys_leave_nothing_behind(monkeypatch, provider):
    fake = provider(_Provider(good="never-typed"))
    _answers(monkeypatch, "1", "a", "b", "c")
    assert setup._key("http://yaaf.test") is None
    assert fake.rows == [] and len(fake.deleted) == 3


def test_a_box_that_has_a_key_is_not_asked_for_another(monkeypatch, provider):
    provider(_Provider(good="x", existing=[{"id": "secr_1", "name": "anthropic"}]))
    asked = _answers(monkeypatch)
    assert setup._key("http://yaaf.test")["id"] == "secr_1"
    assert asked == [], "a re-run asked for a key the box already holds"


def test_the_installer_hands_over_to_the_wizard():
    """One command for a person: the script installs, then runs `yaaf setup`
    with whatever it was given, so `--port` reaches the wizard."""
    script = (Path(__file__).resolve().parents[2] / "deploy/install_desktop.sh").read_text()
    assert 'yaaf" setup "$@"' in script
    assert ".[host]" in script, "host mode's libraries are what the jail runs on"
