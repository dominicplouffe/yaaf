"""Invariant selftests: the code-health ratchet. A change may leave the files it
touches as maintainable as it found them, or better; worse needs a challenge
seat's signed reason. Main is measured too, for the upkeep promise.

The runner tests run the real tools (lizard and ruff) on a real git repo.
"""

from __future__ import annotations

import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from tests.conftest import bearer, live_token
from tests.selftest_invariants.test_merge_gate import (
    FakeGitHub,
    _agent,
    _change,
    _claimed_task,
    _factory,
    _green,
    _open,
    _sign,
)
from tests.selftest_invariants.test_quality_gates import _git
from yaaf.core.db import db_session
from yaaf.core.models import Change, ChangeStatus, Project, RunKind, RunQueue, utcnow
from yaaf.runner import daemon, health
from yaaf.runner.types import RunJob
from yaaf.server import changes, github, upkeep

TOOLS = Path(__file__).resolve().parents[2] / ".venv" / "bin"


@pytest.fixture
def gh(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(github, "_request", fake)
    monkeypatch.setattr(github, "platform_token", lambda session: "platform-token")
    return fake


def _ready(client, project, gh):
    change = _open(client, project, _claimed_task(client, project)).json()
    _green(client, project, change["head_sha"])
    return change


def _measured(change_id, result):
    with db_session() as session:
        row = session.get(Change, change_id)
        changes.record_health(session, row, row.head_sha, result)
    return _change(change_id)


WORSE = {"worse": True, "detail": "2 more function(s) too complex or too long",
         "available": {"lizard": True, "ruff": True}, "worst": ["app.py:route (complexity 14)"]}
SAME = {"worse": False, "detail": "no worse", "available": {"lizard": True, "ruff": True}}


# ── the gate ────────────────────────────────────────────────────────────────────


def test_a_change_waits_until_its_health_is_measured(client, gh):
    project = _factory(client, code_health_ratchet=True)
    change = _ready(client, project, gh)
    assert "not measured" in change["gates"]["health"]["detail"]
    with db_session() as session:
        (queued,) = session.query(RunQueue).filter_by(kind=RunKind.CHANGE_SCAN).all()
        assert queued.trigger["base"] == "main"


def test_worse_code_waits_for_a_signed_reason(client, gh):
    project = _factory(client, code_health_ratchet=True)
    change = _ready(client, project, gh)
    _sign(client, project, change["id"])
    row = _measured(change["id"], WORSE)
    assert row.status == ChangeStatus.OPEN and "complexity 14" in row.gates["health"]["detail"]

    _, rhea = live_token(project["id"], _agent(project, "Rhea"))
    short = client.post(f"/agent/changes/{change['id']}/review", headers=bearer(rhea), json={
        "verdict": "sign", "note": "Read the whole diff carefully.",
        "allow_worse_health": "fine"})
    assert short.status_code == 422
    reasoned = client.post(f"/agent/changes/{change['id']}/review", headers=bearer(rhea), json={
        "verdict": "sign", "note": "Read the whole diff carefully.",
        "allow_worse_health": "The parser's branches mirror the grammar one to one; "
                              "splitting them would hide the grammar."})
    assert reasoned.json()["change"]["status"] == ChangeStatus.READY


def test_no_worse_code_passes_and_a_missing_tool_fails_closed(client, gh):
    project = _factory(client, code_health_ratchet=True)
    change = _ready(client, project, gh)
    _sign(client, project, change["id"])
    missing = _measured(change["id"], {**SAME, "available": {"lizard": False}})
    assert "lacks lizard" in missing.gates["health"]["detail"]
    assert _measured(change["id"], SAME).status == ChangeStatus.READY


# ── the measurement, with the real tools ────────────────────────────────────────

SIMPLE = "def total(xs):\n    return sum(xs)\n"
TANGLED = "def total(xs, mode):\n" + "".join(
    f"    if mode == {i}:\n        return sum(xs) + {i}\n" for i in range(14)) + \
    "    return '%s' % xs\n"


@pytest.fixture
def tools_on_path(monkeypatch):
    if not (TOOLS / "lizard").exists() or not (TOOLS / "ruff").exists():
        pytest.skip("lizard and ruff are dev dependencies; install .[dev]")
    monkeypatch.setenv("PATH", f"{TOOLS}:{__import__('os').environ['PATH']}")
    from yaaf.runner import sandbox

    real = sandbox.run_check_command

    def with_path(command, workspace_dir, timeout_s, env=None):
        return real(command=f"export PATH={TOOLS}:$PATH; {command}",
                    workspace_dir=workspace_dir, timeout_s=timeout_s)

    monkeypatch.setattr(sandbox, "run_check_command", with_path)


def _repo(tmp_path, after: str) -> tuple[Path, str]:
    origin = tmp_path / "origin"
    origin.mkdir()
    _git("init", "-q", "-b", "main", cwd=origin)
    (origin / "calc.py").write_text(SIMPLE)
    _git("add", ".", cwd=origin)
    _git("commit", "-q", "-m", "base", cwd=origin)
    _git("checkout", "-q", "-b", "mika/total", cwd=origin)
    (origin / "calc.py").write_text(after)
    _git("commit", "-q", "-am", "change", cwd=origin)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=origin, capture_output=True,
                         text=True, check=True).stdout.strip()
    return origin, sha


def _scan(tmp_path, origin, sha):
    where = tmp_path / ".scans" / "run_h"
    job = RunJob(run_id="run_h", project_id="p", agent_id=None, kind=RunKind.CHANGE_SCAN,
                 trigger={"change_id": "c", "base": "main"}, task_id=None, timeout_s=120,
                 workspace_dir=str(where), check_sha=sha, check_branch="mika/total",
                 fetch_url=str(origin))
    result = daemon._run_change_scan(job)
    assert result.ok, result.summary
    assert not where.exists() and not Path(str(where) + "-base").exists()
    import json
    return json.loads(result.check_output)["health"]


def test_a_tangled_rewrite_is_measured_as_worse(tmp_path, tools_on_path):
    origin, sha = _repo(tmp_path, TANGLED)
    verdict = _scan(tmp_path, origin, sha)
    assert verdict["available"] == {"lizard": True, "ruff": True}, verdict
    assert verdict["worse"] and verdict["head"]["over"] == 1 and verdict["base"]["over"] == 0
    assert verdict["head"]["lint"] > verdict["base"]["lint"], "printf formatting is caught"


def test_a_simplification_is_measured_as_no_worse(tmp_path, tools_on_path):
    origin, sha = _repo(tmp_path, SIMPLE + "\n\ndef mean(xs):\n    return total(xs) / len(xs)\n")
    verdict = _scan(tmp_path, origin, sha)
    assert not verdict["worse"], verdict


def test_the_parsers_read_the_tools_own_output():
    reading = health.parse_lizard('30,14,80,1,35,"f@1-35@a.py","a.py","f","f( x )",1,35\n'
                                  '4,1,10,0,4,"g@37-40@a.py","a.py","g","g( )",37,40\n')
    assert reading["functions"] == 2 and reading["over"] == 1
    assert health.parse_ruff('[{"code": "UP031"}, {"code": "E722"}]')["lint"] == 2
    assert health.availability("lizard=127\nruff=0\n", ["a.py"]) == {"lizard": False,
                                                                     "ruff": True}


# ── upkeep: main gets no harder to maintain ─────────────────────────────────────


def test_main_health_is_queued_daily_and_judged_over_the_cycle(client):
    from tests.selftest_invariants.test_code_factory import adopted

    project = adopted(client, slug="healthy-main")
    pid = project["id"]
    with db_session() as session:
        row = session.get(Project, pid)
        upkeep.on_push(session, row, "a" * 40)
    with db_session() as session:
        assert upkeep.queue_main_health(session, session.get(Project, pid))
        assert not upkeep.queue_main_health(session, session.get(Project, pid)), "once"
    with db_session() as session:
        row = session.get(Project, pid)
        upkeep.signal(session, row, "code_health", "a", {"sha": "a", "over": 4, "lint": 10},
                      at=utcnow() - timedelta(days=8))
        upkeep.signal(session, row, "code_health", "b", {"sha": "b", "over": 6, "lint": 10})
    with db_session() as session:
        value, note = upkeep.measure(session, session.get(Project, pid), "health_regressions",
                                     utcnow() - timedelta(days=7), utcnow())
    assert value == 2 and "2 more" in note


def test_a_reading_without_the_tools_is_not_recorded(client):
    from tests.selftest_invariants.test_code_factory import adopted

    project = adopted(client, slug="no-tools")
    with db_session() as session:
        row = session.get(Project, project["id"])
        upkeep.record_main_health(session, row, "a", {"available": {"lizard": False},
                                                      "over": 0, "lint": 0})
    with db_session() as session, pytest.raises(ValueError):
        upkeep.measure(session, session.get(Project, project["id"]), "health_regressions",
                       utcnow() - timedelta(days=7), utcnow())
