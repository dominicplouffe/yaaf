"""Invariant selftests: a native install behaves like the image, or says where not.

Host mode was built as the image minus whatever was inconvenient: no resident
boardroom session, no run caps, a browser the agents were told about and did
not have, and command-line tools at whatever version the machine happened to
hold — and nothing said so until a run died. These hold the four back:

- the chat lane runs in a jail that stays up, as it runs in a container;
- every run is capped (2 CPUs, 2 GB unless setup was told otherwise);
- agents are offered the browser only where it exists;
- each tool the image pins has that pin as its floor here, and `yaaf doctor`
  names one that is missing or older.

The network is deliberately not on the list: the image does not restrict it
either, and the installer says so instead (deploy/install_desktop.sh).
"""

from __future__ import annotations

import contextlib
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from yaaf.runner import host_jail, host_tools, sandbox

REPO = Path(__file__).resolve().parents[2]


@dataclass
class _Settings:
    database_url: str = "sqlite:////nonexistent/yaaf.db"
    host_jail_binds: str = ""
    sandbox_mode: str = "host"
    allow_host_success_tests: bool = False
    host_run_cpus: float = 2.0
    host_run_memory_gb: float = 2.0
    workspaces_root: str = ""


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    settings = _Settings(workspaces_root=str(tmp_path / "workspaces"))
    for module in (host_jail, sandbox):
        monkeypatch.setattr(module, "settings", lambda: settings)
    host_jail._scope_works.cache_clear()
    return settings


# ── the tools ───────────────────────────────────────────────────────────────────

#: npm package → the command it installs, for the pins the Dockerfile writes.
_NPM_COMMANDS = {"@anthropic-ai/claude-code": "claude", "@openai/codex": "codex",
                 "opencode-ai": "opencode", "@google/gemini-cli": "gemini"}


def test_the_tool_floors_are_the_images_own_pins():
    """One source for what an agent runs on: a pin raised in the Dockerfile and
    left behind here would let a laptop fall behind the server unnoticed."""
    dockerfile = (REPO / "deploy" / "sandbox" / "Dockerfile").read_text()
    floors = {t.command: t.minimum for t in host_tools.TOOLS}
    for package, version in re.findall(r"npm install -g (\S+?)(?:@(\d[\w.]*))?(?:\s|$)",
                                       dockerfile):
        assert floors[_NPM_COMMANDS[package]] == (version or None), package
    node = re.search(r"^FROM node:(\d+)", dockerfile, re.M).group(1)
    assert floors["node"] == node
    for tool in ("git", "curl", "jq", "gawk"):
        assert re.search(rf"apt-get install[^\n]*\n[^\n]*\b{tool}\b", dockerfile), tool
        assert tool in floors


def test_an_older_tool_is_named_and_a_newer_one_is_not(tmp_path):
    assert host_tools.older("0.120.0", "0.162.1")
    assert not host_tools.older("0.167.0", "0.162.1")
    assert not host_tools.older("26.7.0", "22"), "a floor of 22 is met by any 22.x or later"
    assert host_tools.older("20.11.1", "22")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, says in (("codex", "codex-cli 0.120.0"), ("node", "v26.7.0")):
        tool = bin_dir / name
        tool.write_text(f"#!/bin/sh\necho '{says}'\n")
        tool.chmod(0o755)
    rows = {r["tool"].command: r for r in host_tools.report(str(bin_dir))}
    assert rows["codex"]["problem"] == "old" and rows["codex"]["version"] == "0.120.0"
    assert rows["node"]["problem"] is None
    assert rows["gemini"]["problem"] == "missing"


def test_setup_binds_where_a_version_manager_really_put_a_cli(monkeypatch, tmp_path):
    """mise and asdf answer a CLI's name with a shim that reads the caller's
    config — which a jail does not have — so the bind is the real install,
    asked of the manager. Before this a mise-installed Claude Code or Codex
    seat died on its first command on a native install."""
    from yaaf.cli import setup

    real = tmp_path / "installs" / "codex" / "0.167.0" / "bin" / "codex"
    real.parent.mkdir(parents=True)
    real.write_text("")
    shim = tmp_path / "shims" / "codex"
    shim.parent.mkdir()
    mise = tmp_path / "mise"
    mise.write_text("")
    shim.symlink_to(mise)
    monkeypatch.setattr(setup.shutil, "which", lambda name: {
        "codex": str(shim), "mise": str(mise)}.get(name))
    monkeypatch.setattr(setup.subprocess, "run", lambda args, **k: type(
        "Done", (), {"stdout": f"{real}\n"})())
    assert setup._real_tool("codex") == str(real)
    assert setup._tool_binds() == [str(real.parent.parent)], "the install, not its bin/"


def test_setup_remembers_the_caps_and_writes_them_to_the_service(tmp_path, monkeypatch):
    from yaaf.cli import setup

    monkeypatch.setattr(setup, "_tool_binds", lambda: [])
    monkeypatch.setattr(setup, "_npm_global_bind", lambda: None)
    assert setup.chosen_limits(tmp_path, ask=False) == (2.0, 2.0), "Docker's numbers"
    (tmp_path / setup.LIMITS_FILE).write_text("4 8\n")
    env = setup.service_environment(tmp_path)
    assert env["YAAF_HOST_RUN_CPUS"] == "4" and env["YAAF_HOST_RUN_MEMORY_GB"] == "8"
    assert 'Environment="YAAF_HOST_RUN_CPUS=4"' in setup.unit_file(24247, tmp_path)


# ── the browser ─────────────────────────────────────────────────────────────────

def test_agents_are_offered_the_browser_only_where_there_is_one(monkeypatch):
    from yaaf.server import contextpack

    monkeypatch.setattr(host_tools, "browser_available", lambda: False)
    assert "Chromium" not in contextpack._workspace_run()
    monkeypatch.setattr(host_tools, "browser_available", lambda: True)
    assert "Chromium with playwright" in contextpack._workspace_run()


def test_a_native_install_without_chromium_has_no_browser(cfg, monkeypatch, tmp_path):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "empty"))
    monkeypatch.setattr("yaaf.core.config.settings", lambda: cfg)
    host_tools.browser_available.cache_clear()
    try:
        assert host_tools.browser_available() is False
    finally:
        host_tools.browser_available.cache_clear()


# ── the caps ────────────────────────────────────────────────────────────────────

def test_every_run_is_capped_like_a_container(cfg, monkeypatch):
    monkeypatch.setattr(host_jail, "_scope_works", lambda props: None)
    prefix = host_jail.limits_prefix()
    assert prefix[:3] == ["systemd-run", "--user", "--scope"]
    assert "MemoryMax=2048M" in prefix and "CPUQuota=200%" in prefix
    cfg.host_run_cpus, cfg.host_run_memory_gb = 0.0, 0.0
    assert host_jail.limits_prefix() == [], "0 is no cap, chosen at setup"


def test_a_machine_that_cannot_cap_runs_uncapped_and_says_so(cfg, monkeypatch):
    """Refusing every run over a missing cap would stop the project for a
    limit's sake; the doctor names it instead."""
    monkeypatch.setattr(host_jail, "_scope_works", lambda props: "systemd-run is not installed")
    assert host_jail.limits_prefix() == []
    assert host_jail.limits_problem() == "systemd-run is not installed"


def test_a_run_over_its_memory_cap_is_killed_as_a_container_would_be(cfg, tmp_path):
    """Real jail, real scope: skipped where either cannot be made."""
    if host_jail.unavailable():
        pytest.skip("no bubblewrap jail on this machine")
    cfg.host_run_memory_gb = 0.25
    if host_jail.limits_problem():
        pytest.skip(host_jail.limits_problem())
    code, _ = host_jail.run([sys.executable, "-c", "x = bytearray(1024**3)"],
                            workspace_dir=str(tmp_path / "ws"), env=None, timeout_s=60)
    assert code in (137, 143), f"killed, in the shell's spelling deaths.py reads: {code}"


# ── the chat lane ───────────────────────────────────────────────────────────────

def test_host_mode_holds_the_boardroom_in_a_jail_that_stays_up(cfg, monkeypatch, tmp_path):
    """Start, find, stop: the three things the supervisor asks of a container,
    asked of a jail. The session is found only while it runs, and stopping it
    returns why it died — the only chance to read that, as with a container."""
    monkeypatch.setattr(host_jail, "unavailable", lambda: None)
    monkeypatch.setattr(host_jail, "limits_prefix", lambda: [])
    seen = {}

    def argv(workspace, command, *, entries=False, project_dir=None):
        seen.update(command=command, project_dir=project_dir, entries=entries)
        return ["sh", "-c", "echo session up; sleep 60"]

    monkeypatch.setattr(host_jail, "jail_argv", argv)
    sandbox.start_chat_container(project_id="p1", run_id="run_a", name="ignored",
                                 workspace_dir=str(tmp_path / "chat"), env={},
                                 project_dir=str(tmp_path / "proj"))
    assert seen["command"][-1].endswith("/chat_entry.py") and seen["entries"]
    assert seen["project_dir"] == str(tmp_path / "proj"), "the project, read-only at /project"
    assert sandbox.chat_container_run_ids("p1") == {"run_a"}
    time.sleep(0.3)
    forensics = sandbox.stop_chat_container("p1")
    assert "run=run_a" in forensics and "session up" in forensics
    assert sandbox.chat_container_run_ids("p1") == set()


def test_the_jail_mounts_the_project_read_only_at_slash_project(cfg, tmp_path):
    argv = host_jail.jail_argv(str(tmp_path / "ws"), ["true"], project_dir=str(tmp_path / "p"))
    i = argv.index("/project")
    assert argv[i - 2:i] == ["--ro-bind", str(tmp_path / "p")]


def test_the_supervisor_runs_in_host_mode_and_stands_down_only_without_a_sandbox(monkeypatch):
    from yaaf.runner import chat

    class Tick(BaseException):
        pass

    for mode, supervises in (("host", True), ("docker", True), ("subprocess", False)):
        calls = []
        monkeypatch.setattr(chat, "settings", lambda m=mode: type("S", (), {"sandbox_mode": m})())
        monkeypatch.setattr(chat, "chat_supervisor_once", lambda c=calls: c.append(1))

        def stop(_):
            raise Tick

        monkeypatch.setattr(time, "sleep", stop)
        with contextlib.suppress(Tick):
            chat.chat_supervisor_loop()
        assert bool(calls) is supervises, mode


def test_a_signal_kill_reads_as_the_container_lane_reads_it(cfg, tmp_path):
    if host_jail.unavailable():
        pytest.skip("no bubblewrap jail on this machine")
    cfg.host_run_cpus = cfg.host_run_memory_gb = 0.0
    code, _ = host_jail.run(["sh", "-c", "kill -9 $$"], workspace_dir=str(tmp_path / "ws"),
                            env=None, timeout_s=30)
    assert code == 137


def test_the_installer_warns_before_it_installs_anything():
    script = (REPO / "deploy" / "install_desktop.sh").read_text()
    warning = script.index("far less contained than Docker")
    assert warning < script.index('say "System packages"')
    assert "network" in script[warning:warning + 900]
    assert "playwright install chromium" in script


def test_a_seat_whose_cli_is_missing_or_old_is_refused_by_name(tmp_path, monkeypatch):
    """At the run, not three layers down: refused before the jail starts, with
    the command that fixes it."""
    monkeypatch.setattr(host_tools, "_checked", {})
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    old = bin_dir / "codex"
    old.write_text("#!/bin/sh\necho 'codex-cli 0.120.0'\n")
    old.chmod(0o755)
    why = host_tools.refusal_for("codex_cli_entry.py", str(bin_dir))
    assert why.startswith("Refused:") and "0.120.0" in why and "0.162.1" in why
    missing = host_tools.refusal_for("gemini_cli_entry.py", str(bin_dir))
    assert "no gemini is installed" in missing and "npm install -g" in missing
    assert host_tools.refusal_for("loop_entry.py", str(bin_dir)) is None, "needs no CLI"


def test_a_refused_seat_never_starts_a_jail(cfg, monkeypatch, tmp_path):
    monkeypatch.setattr(host_tools, "refusal_for", lambda entry, path: "Refused: no codex")
    monkeypatch.setattr(host_jail, "run", lambda *a, **k: pytest.fail("a jail started"))
    code, out = host_jail.run_agent(entrypoint="codex_cli_entry.py",
                                    workspace_dir=str(tmp_path), env={}, timeout_s=10)
    assert code == 126 and "no codex" in out


def test_setup_says_what_native_mode_gives_up_and_remembers_the_answer(tmp_path, monkeypatch):
    from yaaf.cli import setup

    monkeypatch.setenv("YAAF_ACCEPT_NATIVE", "1")
    setup._warn_native(tmp_path)
    assert (tmp_path / setup.ACCEPTED_FILE).exists()
    assert "far less contained than Docker" in setup.NATIVE_WARNING
    assert "network" in setup.NATIVE_WARNING
