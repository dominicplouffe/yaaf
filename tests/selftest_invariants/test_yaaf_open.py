"""Invariant selftests: `yaaf open` opens the dashboard where the server is
configured to answer, and says so in one line when nothing answers there.

No browser is started: what is attacked is the address it picks and that it
never opens a tab onto a connection error.
"""

from __future__ import annotations

from typer.testing import CliRunner

from yaaf.cli import main as cli
from yaaf.core.config import DEFAULT_PORT

runner = CliRunner()


def _env(monkeypatch, **values):
    for name in ("YAAF_API", "YAAF_API_PORT"):
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_it_opens_the_default_port_when_nothing_is_configured(monkeypatch):
    _env(monkeypatch)
    result = runner.invoke(cli.app, ["open", "--print"])
    assert result.exit_code == 0
    assert result.output.strip() == f"http://localhost:{DEFAULT_PORT}/app/"


def test_the_configured_port_wins_over_the_default(monkeypatch):
    _env(monkeypatch, YAAF_API_PORT="8091")
    result = runner.invoke(cli.app, ["open", "--print"])
    assert result.output.strip() == "http://localhost:8091/app/"


def test_an_explicit_address_wins_over_the_port(monkeypatch):
    _env(monkeypatch, YAAF_API="https://yaaf.example.com/", YAAF_API_PORT="8091")
    result = runner.invoke(cli.app, ["open", "--print"])
    assert result.output.strip() == "https://yaaf.example.com/app/"


def test_a_screen_opens_through_the_dashboards_router(monkeypatch):
    _env(monkeypatch, YAAF_API_PORT="8091")
    result = runner.invoke(cli.app, ["open", "inbox", "--print"])
    assert result.output.strip() == "http://localhost:8091/app/#/inbox"


def test_nothing_is_opened_when_the_server_is_not_running(monkeypatch):
    # A port nothing listens on: the one line, and no browser asked.
    _env(monkeypatch, YAAF_API="http://127.0.0.1:9")
    opened = []
    monkeypatch.setattr(cli, "_open_in_browser", lambda url: opened.append(url) or True)
    result = runner.invoke(cli.app, ["open"])
    assert result.exit_code == 1
    assert "Is the server running?" in result.output
    assert opened == []


def test_it_opens_the_dashboard_when_the_server_answers(monkeypatch):
    _env(monkeypatch, YAAF_API="http://yaaf.test")
    opened = []
    monkeypatch.setattr(cli, "_open_in_browser", lambda url: opened.append(url) or True)
    monkeypatch.setattr(cli._Client, "get", lambda self, path, **kw: None)
    result = runner.invoke(cli.app, ["open"])
    assert result.exit_code == 0, result.output
    assert opened == ["http://yaaf.test/app/"]
