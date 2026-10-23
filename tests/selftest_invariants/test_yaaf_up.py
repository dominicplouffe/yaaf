"""Invariant selftests: `yaaf up` fills in a dev box and decides nothing it
should not.

The whole command is two processes and a port, which the suite does not
start; what is attacked here is what it decides: that it never picks the
no-isolation sandbox on its own, that the master key it makes is kept (and
private), and that anything already configured wins over its defaults.
"""

from __future__ import annotations

import stat

from typer.testing import CliRunner

from yaaf.cli import main as cli
from yaaf.cli import up

runner = CliRunner()


def test_it_will_not_choose_to_run_agents_unsandboxed(monkeypatch, tmp_path):
    monkeypatch.delenv("YAAF_SANDBOX_MODE", raising=False)
    result = runner.invoke(cli.app, ["up", "--home", str(tmp_path)])
    assert result.exit_code == 2
    assert "--no-isolation" in result.output
    assert not (tmp_path / "master.key").exists(), "nothing is made before it is told"


def test_the_key_it_makes_is_kept_and_private(monkeypatch, tmp_path):
    monkeypatch.delenv("YAAF_MASTER_KEY", raising=False)
    first = up.ensure_key(tmp_path)
    assert up.ensure_key(tmp_path) == first, "losing the key loses every stored secret"
    mode = stat.S_IMODE((tmp_path / "master.key").stat().st_mode)
    assert mode == 0o600


def test_what_is_already_configured_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("YAAF_DATABASE_URL", "postgresql+psycopg://somebody/else")
    monkeypatch.setenv("YAAF_MASTER_KEY", "a-key-somebody-chose")
    monkeypatch.setenv("YAAF_API_BASE_URL", "http://runner-sees-this:8080")
    env = up.environment(tmp_path, 8091, "subprocess")
    assert env["YAAF_DATABASE_URL"] == "postgresql+psycopg://somebody/else"
    assert env["YAAF_MASTER_KEY"] == "a-key-somebody-chose"
    assert env["YAAF_API_BASE_URL"] == "http://runner-sees-this:8080"
    assert env["YAAF_API_PORT"] == "8091"
    assert not (tmp_path / "master.key").exists()


def test_a_bare_box_gets_a_database_and_an_address_of_its_own(monkeypatch, tmp_path):
    for name in ("YAAF_DATABASE_URL", "YAAF_MASTER_KEY", "YAAF_API_BASE_URL", "YAAF_API"):
        monkeypatch.delenv(name, raising=False)
    env = up.environment(tmp_path, 8091, "subprocess")
    assert env["YAAF_DATABASE_URL"] == f"sqlite:///{tmp_path / 'yaaf.db'}"
    assert env["YAAF_API"] == env["YAAF_API_BASE_URL"] == "http://localhost:8091"
    assert env["YAAF_SANDBOX_MODE"] == "subprocess"
