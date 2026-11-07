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


def _catalog() -> dict[str, dict]:
    """The provider list exactly as the server serves it."""
    from yaaf.runtimes.providers import PROVIDERS
    from yaaf.server.api.admin import provider_view

    return {name: provider_view(p) for name, p in PROVIDERS.items()}


def test_every_provider_is_offered_and_none_twice():
    """The wizard offered two providers of twenty-three. It reads the catalog
    now, so a provider added to it is offered the day it lands — grouped by
    what it needs beyond a key, not ranked."""
    catalog = _catalog()
    groups = dict(setup.provider_groups(list(catalog.values())))
    offered = [p["name"] for group in groups.values() for p in group]
    keyed = [n for n, p in catalog.items() if any(k["mode"] == "api_key" for k in p["kinds"])]
    assert sorted(offered) == sorted(keyed) and len(offered) == len(set(offered))
    by_group = {p["name"]: title for title, group in groups.items() for p in group}
    assert by_group["deepseek"].startswith("A key, and a web search key"), \
        "a provider that cannot search must say so before it is chosen"
    assert by_group["anthropic"] == by_group["openai"] == by_group["google"]
    assert by_group["ollama"].startswith("Runs on this machine")
    assert by_group["azure_openai"] == by_group["custom"]


class _Install:
    """The settings API as the wizard meets it: credentials checked against a
    provider that accepts exactly one key, and a readiness report computed
    from what has been set — a lineup, prices, a search backend."""

    def __init__(self, provider: str, good: str, *, models=("m-1", "m-2"),
                 searches=False, priced=False, existing=None):
        self.provider, self.good, self.models = provider, good, list(models)
        self.searches, self.priced = searches, priced
        self.rows, self.deleted = list(existing or []), []
        self.lineup: dict[str, str] = {}
        self.prices: dict[str, tuple] = {}
        self.search: dict | None = None
        self.search_key: str | None = None
        self.default = None

    def _issues(self) -> list[dict]:
        issues = []
        unset = [c for c in ("fast", "balanced", "frontier") if c not in self.lineup]
        if unset:
            issues.append({"code": "no_model", "severity": "block", "text": "no model",
                           "fix": {"action": "set_lineup", "class": unset[0], "classes": unset}})
        unpriced = sorted({m for m in self.lineup.values()
                           if not self.priced and m not in self.prices})
        if unpriced:
            issues.append({"code": "unpriced", "severity": "block", "text": "no price",
                           "fix": {"action": "set_price", "models": unpriced}})
        if not self.searches and not self.search:
            issues.append({"code": "no_search", "severity": "warn", "text": "cannot search",
                           "fix": {"action": "web_search"}})
        return issues

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        body = json.loads(request.content) if request.content else {}
        if path == "/admin/providers":
            return httpx.Response(200, json=list(_catalog().values()))
        if path == "/admin/settings/llm-credentials" and method == "GET":
            return httpx.Response(200, json=[{k: r[k] for k in ("id", "name", "provider")}
                                             for r in self.rows])
        if path == "/admin/settings/llm-credentials" and method == "POST":
            row = {"id": f"secr_{len(self.rows) + len(self.deleted)}", "name": body["name"],
                   "provider": body["provider"], "value": body["value"],
                   "base_url": body.get("base_url")}
            self.rows.append(row)
            return httpx.Response(201, json={k: row[k] for k in ("id", "name", "provider")})
        if path.endswith("/models") and method == "GET":
            row = next(r for r in self.rows if r["id"] == path.split("/")[-2])
            if row.get("value", self.good) == self.good:
                return httpx.Response(200, json={"models": [
                    {"id": m, "chat": True, "price": None} for m in self.models]})
            return httpx.Response(502, json={"detail": "HTTP 401: invalid key"})
        if method == "DELETE" and "/llm-credentials/" in path:
            gone = path.split("/")[-1]
            self.deleted.append(gone)
            self.rows = [r for r in self.rows if r["id"] != gone]
            return httpx.Response(200, json={})
        if path == "/admin/settings/models/default-provider":
            self.default = body["provider"]
            return httpx.Response(200, json={})
        if path == "/admin/settings/models/lineup":
            self.lineup[body["class"]] = body["model"]
            return httpx.Response(200, json={})
        if path.startswith("/admin/settings/model-prices/") and method == "PUT":
            self.prices[path.removeprefix("/admin/settings/model-prices/")] = \
                (body["input"], body["output"])
            return httpx.Response(200, json={})
        if path == "/admin/settings/web-search" and method == "PUT":
            self.search = body
            return httpx.Response(200, json={})
        if path == "/admin/settings/web-search/test":
            ok = self.search_key is None or (self.search or {}).get("key") == self.search_key
            if not ok:
                self.search = None
            return httpx.Response(200, json={"ok": ok, "detail": "answered" if ok
                                             else "HTTP 401"})
        if path == "/admin/settings/providers/readiness":
            issues = self._issues()
            return httpx.Response(200, json={"providers": [{
                "provider": self.provider, "issues": issues,
                "ready": not any(i["severity"] == "block" for i in issues),
                "tiers": [{"label": c, "model": m} for c, m in self.lineup.items()]}]})
        return httpx.Response(404, json={"detail": "Not Found"})


@pytest.fixture()
def install(monkeypatch):
    def make(fake: _Install) -> _Install:
        monkeypatch.setattr(setup, "_client", lambda base: httpx.Client(
            base_url=base, transport=httpx.MockTransport(fake)))
        return fake
    return make


def _number(name: str) -> str:
    """The number the wizard prints beside a provider."""
    numbered = [p["name"] for _, group in setup.provider_groups(list(_catalog().values()))
                for p in group]
    return str(numbered.index(name) + 1)


def test_a_key_the_provider_refuses_is_not_kept(monkeypatch, install):
    """Checked with the provider's own free model list before it is kept. A
    refused key is deleted, not left on the box looking configured."""
    fake = install(_Install("anthropic", good="sk-ant-good", priced=True, searches=True))
    _answers(monkeypatch, _number("anthropic"), "sk-ant-typo", "sk-ant-good", "1")
    kept = setup._key("http://yaaf.test")

    assert kept is not None and [r["value"] for r in fake.rows] == ["sk-ant-good"]
    assert fake.deleted, "the refused key stayed on the box"


def test_three_refused_keys_leave_nothing_behind(monkeypatch, install):
    fake = install(_Install("anthropic", good="never-typed"))
    _answers(monkeypatch, _number("anthropic"), "a", "b", "c")
    assert setup._key("http://yaaf.test") is None
    assert fake.rows == [] and len(fake.deleted) == 3


def test_a_provider_that_needs_more_than_a_key_is_made_ready(monkeypatch, install):
    """Groq-shaped: no lineup the install trusts, a model the feed cannot
    price, no search of its own. Each is what a project door would refuse,
    so each is asked for here — and a search key that fails its test is asked
    for again rather than kept."""
    from yaaf.runtimes.wire import SEARCH_BACKENDS

    fake = install(_Install("groq", good="gsk-good", models=("groq/a", "groq/b")))
    fake.search_key = "good-search-key"
    brave = str(list(SEARCH_BACKENDS).index("brave") + 1)
    _answers(monkeypatch, _number("groq"), "gsk-good",
             "2",                        # the model: groq/b
             "0.5", "0.8",               # its price, per million in and out
             brave, "bad-search-key",    # refused by the search service...
             brave, "good-search-key")   # ...so asked again
    setup._key("http://yaaf.test")

    assert fake.default == "groq", "the provider chosen is where classes resolve"
    assert fake.lineup == {"fast": "groq/b", "balanced": "groq/b", "frontier": "groq/b"}
    assert fake.prices == {"groq/b": (0.5, 0.8)}
    assert fake.search and fake.search["key"] == "good-search-key"


def test_a_model_on_this_machine_is_priced_at_nothing_not_asked(monkeypatch, install):
    """An Ollama model costs nothing per token. Asking its price would be a
    question with one answer; leaving it unpriced would refuse every seat."""
    fake = install(_Install("ollama", good="", models=("ollama/llama3",)))
    _answers(monkeypatch, _number("ollama"), "http://localhost:11434/v1", "", "1", "s")
    setup._key("http://yaaf.test")

    assert fake.rows[0]["base_url"] == "http://localhost:11434/v1"
    assert fake.prices == {"ollama/llama3": (0.0, 0.0)}


def test_a_box_that_has_a_key_is_not_asked_for_another(monkeypatch, install):
    """A re-run still makes the provider ready — that is how a gap left last
    time gets closed — but never asks for a second key."""
    fake = install(_Install("anthropic", good="x", priced=True, searches=True,
                            existing=[{"id": "secr_1", "name": "anthropic",
                                       "provider": "anthropic"}]))
    asked = _answers(monkeypatch, "1")
    assert setup._key("http://yaaf.test")["id"] == "secr_1"
    assert not any("key" in q.lower() for q in asked), "a re-run asked for a key"
    assert set(fake.lineup) == {"fast", "balanced", "frontier"}


def test_the_installer_hands_over_to_the_wizard():
    """One command for a person: the script installs, then runs `yaaf setup`
    with whatever it was given, so `--port` reaches the wizard."""
    script = (Path(__file__).resolve().parents[2] / "deploy/install_desktop.sh").read_text()
    assert 'yaaf" setup "$@"' in script
    assert ".[host]" in script, "host mode's libraries are what the jail runs on"
