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


def test_host_mode_without_a_jail_starts_nothing(monkeypatch, tmp_path):
    """`--host` without bubblewrap is not a weaker host mode; it is agents run
    bare by accident. Said before anything is made, with the fix."""
    from yaaf.runner import host_jail

    monkeypatch.delenv("YAAF_SANDBOX_MODE", raising=False)
    monkeypatch.setattr(host_jail, "_bwrap", lambda: None)
    result = runner.invoke(cli.app, ["up", "--host", "--home", str(tmp_path)])
    assert result.exit_code == 2
    assert "bubblewrap" in result.output
    assert not (tmp_path / "master.key").exists(), "nothing is made before it can run"


def test_host_and_no_isolation_are_two_answers(monkeypatch, tmp_path):
    monkeypatch.delenv("YAAF_SANDBOX_MODE", raising=False)
    result = runner.invoke(cli.app, ["up", "--host", "--no-isolation", "--home", str(tmp_path)])
    assert result.exit_code == 2


def test_the_work_lives_beside_the_database(monkeypatch, tmp_path):
    """The server default, /var/lib/yaaf, is not writable by a desktop user:
    every project's first run died making its workspace there."""
    for name in ("YAAF_WORKSPACES_ROOT", "YAAF_ARTIFACTS_ROOT"):
        monkeypatch.delenv(name, raising=False)
    env = up.environment(tmp_path, 8091, "host")
    assert env["YAAF_WORKSPACES_ROOT"] == str(tmp_path / "workspaces")
    assert env["YAAF_ARTIFACTS_ROOT"] == str(tmp_path / "artifacts")


def test_a_desktop_install_listens_on_this_machine_only(monkeypatch, tmp_path):
    """The server's own default is 0.0.0.0, right inside a container and wrong
    on a laptop: it put the sign-in page, over plain HTTP, on every network
    the laptop joined."""
    monkeypatch.delenv("YAAF_API_HOST", raising=False)
    assert up.environment(tmp_path, 8091, "host")["YAAF_API_HOST"] == "127.0.0.1"
    assert "this machine only" in up.exposure("127.0.0.1", 8091)


def test_opening_it_to_a_network_is_one_flag_and_says_what_it_costs(monkeypatch, tmp_path):
    monkeypatch.setenv("YAAF_API_HOST", "10.0.0.5")
    assert up.environment(tmp_path, 8091, "host")["YAAF_API_HOST"] == "10.0.0.5", \
        "an address somebody configured wins over the default"
    assert up.environment(tmp_path, 8091, "host", "0.0.0.0")["YAAF_API_HOST"] == "0.0.0.0", \
        "and the flag on this command line wins over both"
    warning = up.exposure("0.0.0.0", 8091)
    assert "plain HTTP" in warning and "password" in warning
