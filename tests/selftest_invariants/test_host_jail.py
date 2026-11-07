"""Invariant selftest: an agent run in host mode reaches nothing the runner holds.

``sandbox_mode="host"`` runs agents on the machine yaaf itself runs on, without
Docker, each inside a bubblewrap jail (yaaf/runner/host_jail.py). On that
machine sit the things that make the platform's rules real: the master key that
decrypts every stored credential, the database that is the record, the runner's
environment holding both, and — on a desktop — the chairman's own home folder.
An agent that reaches any of them does not need to argue with a rule; it can
write "met" itself.

So these attack the jail the way an agent would: read the key file, dump the
environment, read the runner's environment through /proc, list the host's
/run, outstay the timeout, ignore the kill. The first half need no bubblewrap
— they read the command line and the environment the jail would be given. The
second half run real jails and skip where bubblewrap cannot make one. Set
YAAF_REQUIRE_HOST_JAIL=1 wherever bubblewrap is installed on purpose (a CI job,
a host-mode box) and a skip becomes a failure.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from yaaf.runner import host_jail, sandbox

REPO = Path(__file__).resolve().parents[2]
SECRET = "yaaf-master-key-that-must-not-leave"


@dataclass
class _Settings:
    database_url: str = "postgresql+psycopg://x@localhost/x"
    host_jail_binds: str = ""
    sandbox_mode: str = "host"
    allow_host_success_tests: bool = False


@pytest.fixture()
def box(tmp_path, monkeypatch):
    """A desktop in miniature: a home folder, yaaf's data folder inside it
    holding the database and the key, a project workspace, and a runner whose
    environment carries the key and the database address."""
    home = tmp_path / "home"
    data = home / ".yaaf"
    workspace = data / "workspaces" / "proj"
    workspace.mkdir(parents=True)
    (data / "master.key").write_text(SECRET)
    (data / "yaaf.db").write_text("the record")
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_ed25519").write_text("PRIVATE KEY")
    cfg = _Settings(database_url=f"sqlite:///{data / 'yaaf.db'}")
    monkeypatch.setattr(host_jail, "settings", lambda: cfg)
    monkeypatch.setattr(sandbox, "settings", lambda: cfg)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("YAAF_MASTER_KEY", SECRET)
    monkeypatch.setenv("YAAF_DATABASE_URL", cfg.database_url)
    return cfg, home, data, workspace


def _binds(argv: list[str]) -> list[str]:
    """Every host path the command line mounts into the jail."""
    return [argv[i + 1] for i, a in enumerate(argv) if a in ("--bind", "--ro-bind")]


# ── what the jail is given, read off its command line ───────────────────────────


def test_a_run_inherits_nothing_from_the_runner_environment(box, monkeypatch):
    """The runner's environment is the master key and the database address;
    a desktop shell's is also the chairman's own provider keys. A run pays with
    the project's credential, which arrives in the job env, or not at all."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-the-chairmans-own")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    env = host_jail.jail_env({"YAAF_RUN_ID": "run_1", "ANTHROPIC_API_KEY": "sk-ant-project"})

    leaked = {"YAAF_MASTER_KEY", "YAAF_DATABASE_URL", "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN"}
    assert not leaked & env.keys(), f"the run inherited {sorted(leaked & env.keys())}"
    assert SECRET not in "".join(env.values())
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-project", "the project's key is the only key"
    assert env["HOME"] == host_jail.HOME, "a run's home is its own, not the chairman's"


def test_the_jail_mounts_the_workspace_and_never_the_data_folder(box):
    _, home, data, workspace = box
    argv = host_jail.jail_argv(str(workspace), ["true"], entries=True)
    binds = _binds(argv)

    assert str(workspace) in binds, "the run must get its own workspace"
    i = argv.index(str(workspace))
    assert argv[i + 1] == "/workspace", "at the path every role and check was written for"
    for path in binds:
        assert path != str(home) and not str(data).startswith(path.rstrip("/") + "/") \
            and path != str(data), f"{path} puts yaaf's data folder inside the jail"
    assert "--unshare-pid" in argv, \
        "without a pid namespace /proc/<runner>/environ hands over the master key"
    assert argv[argv.index("/run") - 1:argv.index("/run") + 1] == ["--tmpfs", "/run"], \
        "the host's /run holds the Docker socket and the session bus"


def test_an_operator_bind_that_would_expose_the_data_folder_is_refused(box, tmp_path):
    """`host_jail_binds` exists for harness CLIs living in a home folder. Naming
    the home folder itself, or anything holding the data folder, is the one
    setting that would undo the jail — so it is dropped, not honoured."""
    cfg, home, data, workspace = box
    tools = home / ".npm-global"
    (tools / "bin").mkdir(parents=True)
    cfg.host_jail_binds = os.pathsep.join([str(home), str(data), str(tools)])

    binds = _binds(host_jail.jail_argv(str(workspace), ["true"]))
    assert str(home) not in binds and str(data) not in binds
    assert str(tools) in binds, "a bind that exposes nothing is honoured"
    assert str(tools / "bin") in host_jail.jail_path(host_jail.extra_binds()).split(":")


def test_the_jail_ships_exactly_what_the_sandbox_image_ships():
    """One list of entry files in the Dockerfile, one here. A harness added to
    the image and not to the jail would be a seat that runs in docker mode and
    dies in host mode with an ImportError."""
    dockerfile = (REPO / "deploy/sandbox/Dockerfile").read_text()
    image = {Path(dst).name: REPO / src
             for src, dst in re.findall(r"^COPY (\S+\.py) (/opt/yaaf/\S+)", dockerfile, re.M)}
    assert {k: v.resolve() for k, v in image.items()} == \
        {k: v.resolve() for k, v in host_jail.ENTRY_FILES.items()}


def test_without_bubblewrap_nothing_runs_on_the_host(box, monkeypatch):
    """Host mode without a jail is not a weaker host mode, it is subprocess
    mode by accident. Refused, by name, with the fix — and never spawned."""
    _, _, _, workspace = box
    monkeypatch.setattr(host_jail, "_bwrap", lambda: None)

    def spawned(*a, **k):
        raise AssertionError("a process was started without a jail")

    monkeypatch.setattr(host_jail.subprocess, "Popen", spawned)
    monkeypatch.setattr(sandbox, "_subprocess_check", spawned)

    code, out = sandbox.run_check_command("echo met", str(workspace), 5)
    assert code == 126 and "bubblewrap" in out
    code, out = sandbox.run_agent(workspace_dir=str(workspace), env={}, timeout_s=5)
    assert code == 126 and "bubblewrap" in out, "a refused run must not read as a pass"


def test_host_mode_checks_go_to_the_jail_not_the_bare_host(box, monkeypatch):
    _, _, _, workspace = box
    went: list[str] = []
    monkeypatch.setattr(host_jail, "run_check", lambda c, w, t: (went.append("jail"), (0, ""))[1])
    monkeypatch.setattr(sandbox, "_subprocess_check",
                        lambda c, w, t: (went.append("host"), (0, ""))[1])
    sandbox.run_check_command("true", str(workspace), 5)
    assert went == ["jail"]


def test_a_run_names_only_an_entry_script_the_runner_ships(box):
    _, _, _, workspace = box
    code, out = host_jail.run_agent(entrypoint="../../bin/sh", workspace_dir=str(workspace),
                                    env={}, timeout_s=5)
    assert code == 126 and "Refused" in out


# ── real jails ──────────────────────────────────────────────────────────────────


def _jail_or_skip():
    host_jail._probe.cache_clear()
    problem = host_jail.unavailable()
    if problem:
        if os.environ.get("YAAF_REQUIRE_HOST_JAIL") == "1":
            pytest.fail(f"YAAF_REQUIRE_HOST_JAIL=1 and no jail can be made: {problem}")
        pytest.skip(problem)


@pytest.fixture()
def jail(box):
    _jail_or_skip()
    return box


def test_a_jailed_run_cannot_read_the_key_the_record_or_the_home(jail):
    _, home, data, workspace = jail
    (workspace / "brief.md").write_text("the work")
    probe = (
        f"cat {data}/master.key {data}/yaaf.db {home}/.ssh/id_ed25519 2>/dev/null; "
        "env; cat /workspace/brief.md; echo; ls -A /run"
    )
    code, out = host_jail.run(["bash", "-c", probe], workspace_dir=str(workspace),
                              env={"YAAF_RUN_ID": "run_1"}, timeout_s=30)
    assert code == 0, out
    assert SECRET not in out, "the master key reached the run"
    assert "the record" not in out, "the database file reached the run"
    assert "PRIVATE KEY" not in out, "the chairman's home folder reached the run"
    assert "the work" in out, "the run must see its own workspace"
    assert "docker.sock" not in out


def test_a_jailed_run_cannot_read_the_runner_environment_through_proc(jail):
    """The same uid can read /proc/<pid>/environ of any process it owns, and
    the runner is one. Simulated by a process started with the key in its
    environment, the way the runner is."""
    _, _, _, workspace = jail
    runner = subprocess.Popen(["sleep", "30"], env={"YAAF_MASTER_KEY": SECRET,
                                                     "PATH": os.environ.get("PATH", "")})
    try:
        code, out = host_jail.run(
            ["bash", "-c", "cat /proc/[0-9]*/environ 2>/dev/null | tr '\\0' '\\n'; "
                           f"cat /proc/{runner.pid}/environ 2>&1"],
            workspace_dir=str(workspace), env={}, timeout_s=30)
    finally:
        runner.kill()
        runner.wait()
    assert SECRET not in out, "the runner's environment was readable from the jail"


def test_every_shipped_entry_script_imports_inside_the_jail(jail):
    """The interpreter, its libraries and the entry mount all have to be there
    for a run to start; a missing one is a run that dies at import."""
    _, _, _, workspace = jail
    names = sorted(Path(n).stem for n in host_jail.ENTRY_FILES)
    script = (f"import sys; sys.path.insert(0, {host_jail.ENTRY_DIR!r}); "
              f"[__import__(m) for m in {names!r}]; print('imported', len({names!r}))")
    code, out = host_jail.run(
        [sys.executable, "-c", script], workspace_dir=str(workspace), entries=True,
        env={"YAAF_API_BASE_URL": "http://check.invalid", "YAAF_API_TOKEN": "t",
             "YAAF_RUN_ID": "r"}, timeout_s=60)
    assert code == 0 and f"imported {len(names)}" in out, out


def test_a_run_that_outstays_its_timeout_is_killed_with_everything_it_started(jail):
    """124, the code deaths.py reads as the runner's own timeout — and the
    background job the agent left behind dies with it."""
    _, _, _, workspace = jail
    started = time.monotonic()
    code, out = host_jail.run(
        ["bash", "-c", "(sleep 3; echo late > /workspace/late) & sleep 60"],
        workspace_dir=str(workspace), env={}, timeout_s=1)
    assert code == 124 and "timeout" in out
    assert time.monotonic() - started < 15
    time.sleep(4)
    assert not (workspace / "late").exists(), "a child outlived the run it belonged to"


def test_a_revoked_run_stops_now(jail):
    """137: a kill switch, a pause or a budget trip sets the stop event, and a
    revoked run must stop burning tokens, not just lose its API access."""
    _, _, _, workspace = jail
    stop = threading.Event()
    threading.Timer(0.5, stop.set).start()
    started = time.monotonic()
    code, out = host_jail.run(["sleep", "60"], workspace_dir=str(workspace), env={},
                              timeout_s=120, stop_event=stop)
    assert code == 137 and "stopped" in out
    assert time.monotonic() - started < 15


def test_a_success_check_runs_jailed_at_slash_workspace(jail):
    """A check is an agent-authored command, so it gets the same jail and no
    credentials at all — and it runs where the agent was told its files are."""
    _, _, data, workspace = jail
    (workspace / "out").mkdir()
    (workspace / "out" / "report.md").write_text("# report")
    code, out = sandbox.run_check_command(
        f"test -f /workspace/out/report.md && ! test -e {data}/master.key && "
        "test -z \"$YAAF_MASTER_KEY\"", str(workspace), 30)
    assert code == 0, out


def test_ubuntus_restriction_is_named_with_what_fixes_it(box, monkeypatch, tmp_path):
    """On Ubuntu 23.10 and later bwrap is installed and still cannot make a
    jail: AppArmor strips its capabilities. "Install bubblewrap" is then the
    wrong advice; the refusal says what is actually in the way and what moves
    it — and never runs anything bare instead."""
    switch = tmp_path / "apparmor_restrict_unprivileged_userns"
    switch.write_text("1\n")
    monkeypatch.setattr(host_jail, "USERNS_RESTRICTION", switch)
    monkeypatch.setattr(host_jail, "_bwrap", lambda: "/usr/bin/bwrap")
    monkeypatch.setattr(host_jail, "_probe",
                        lambda bwrap: "bubblewrap is installed but cannot make a sandbox "
                                      "here: setting up uid map: Permission denied")
    said = host_jail.unavailable()
    assert said.startswith("Refused") and "AppArmor" in said
    assert "userns" in said and "apparmor_restrict_unprivileged_userns=0" in said
    switch.write_text("0\n")
    assert "AppArmor" not in host_jail.unavailable(), "only where the restriction is on"
