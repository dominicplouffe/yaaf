"""Invariant selftests: the quality gates a Code Factory change must pass.

What the diff says about itself is read by the platform from GitHub, and each
finding is a refusal or a hold that only the right party can lift:

  * a credential in the diff holds the merge and no signature lifts it
  * a weakened test holds the merge until a challenge seat signs a reason,
    and a push clears that reason with the signature
  * a new package is held until a challenge seat names it as checked
  * a path that needs a person waits for the chairman's yes on that commit
  * a diff the platform could not read fails closed
  * hidden scenarios: written only by a challenge seat, never readable by a
    seat that builds, run by the platform on the exact commit, and a result
    for an older commit counts for nothing
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.conftest import ADMIN, bearer, live_token
from tests.selftest_invariants.test_merge_gate import (
    FakeGitHub,
    _agent,
    _change,
    _claimed_task,
    _factory,
    _green,
    _hook,
    _merge,
    _open,
    _sign,
)
from yaaf.core.db import db_session
from yaaf.core.models import ChangeStatus, RunKind, RunQueue
from yaaf.runner import daemon
from yaaf.runner.types import RunJob
from yaaf.server import changes, github


@pytest.fixture
def gh(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(github, "_request", fake)
    monkeypatch.setattr(github, "platform_token", lambda session: "platform-token")
    return fake


def _ready_but_for(client, project, gh, files):
    gh.files = files
    change = _open(client, project, _claimed_task(client, project)).json()
    _green(client, project, change["head_sha"])
    return change


def _sign_with(client, project, change_id, **waivers):
    _, rhea = live_token(project["id"], _agent(project, "Rhea"))
    return client.post(f"/agent/changes/{change_id}/review", headers=bearer(rhea), json={
        "verdict": "sign", "note": "Read every file in the diff and ran it locally.",
        **waivers})


# ── what the diff says ──────────────────────────────────────────────────────────


def test_a_credential_in_the_diff_cannot_be_signed_through(client, gh):
    project = _factory(client)
    change = _ready_but_for(client, project, gh, [{
        "filename": "config/settings.py", "status": "modified",
        "patch": "@@\n+AWS_KEY = 'AKIAABCDEFGHIJKLMNOP'"}])
    assert not change["gates"]["secrets"]["ok"]
    assert "cannot be waived" in change["gates"]["secrets"]["detail"]
    signed = _sign_with(client, project, change["id"], allow_weaker_tests=None,
                        allow_packages=["anything"])
    assert signed.json()["change"]["status"] == ChangeStatus.OPEN
    assert _merge(project) == 0


def test_a_weakened_test_waits_for_a_signed_reason(client, gh):
    project = _factory(client)
    change = _ready_but_for(client, project, gh, [{
        "filename": "src/booking/hold.test.ts", "status": "modified",
        "patch": "@@\n-  expect(hold.minutes).toBe(10)\n-  expect(hold.owner).toEqual(p)\n"
                 "+  it.skip('two tabs share a hold', () => {})"}])
    assert not change["gates"]["tests"]["ok"]

    plain = _sign(client, project, change["id"]).json()
    assert plain["change"]["status"] == ChangeStatus.OPEN, "a signature alone is not a reason"

    short = _sign_with(client, project, change["id"], allow_weaker_tests="fine")
    assert short.status_code == 422

    reasoned = _sign_with(client, project, change["id"], allow_weaker_tests=(
        "The two assertions checked the old 15-minute hold, which R-03 replaced; the "
        "skipped race test is rewritten in the hidden scenarios."))
    assert reasoned.json()["change"]["status"] == ChangeStatus.READY

    _hook(client, project, "pull_request", {
        "action": "synchronize", "sender": {"login": "mika"},
        "pull_request": {"number": change["number"], "head": {"sha": "9" * 40},
                         "additions": 10, "deletions": 4, "changed_files": 1}}, "sync-w")
    row = _change(change["id"])
    assert row.waivers == {} and row.status == ChangeStatus.OPEN


def test_a_new_package_is_held_until_a_challenge_seat_names_it(client, gh):
    project = _factory(client)
    change = _ready_but_for(client, project, gh, [{
        "filename": "package.json", "status": "modified",
        "patch": '@@\n-    "react": "^18.2.0",\n+    "react": "^18.3.0",\n'
                 '+    "date-fns-tz": "^3.2.0",'}])
    packages = change["gates"]["packages"]
    assert not packages["ok"] and "date-fns-tz" in packages["detail"]
    assert "react" not in packages["detail"], "a version bump is not a new package"

    wrong = _sign_with(client, project, change["id"], allow_packages=["date-fns"])
    assert wrong.json()["change"]["status"] == ChangeStatus.OPEN
    right = _sign_with(client, project, change["id"], allow_packages=["date-fns-tz"])
    assert right.json()["change"]["status"] == ChangeStatus.READY


def test_a_protected_path_waits_for_the_chairman_on_that_commit(client, gh):
    project = _factory(client, protected_paths="billing/tax/**, migrations/**")
    change = _ready_but_for(client, project, gh, [{
        "filename": "billing/tax/vat.py", "status": "modified",
        "patch": "@@\n+ROUND_PER_LINE = True"}])
    assert not change["gates"]["protected"]["ok"]
    signed = _sign(client, project, change["id"]).json()
    assert signed["change"]["status"] == ChangeStatus.OPEN, "a seat's signature is not his yes"

    # An agent cannot reach the chairman's door.
    _, rhea = live_token(project["id"], _agent(project, "Rhea"))
    assert client.post(f"/admin/changes/{change['id']}/approve",
                       headers=bearer(rhea)).status_code in (401, 403)

    approved = client.post(f"/admin/changes/{change['id']}/approve", headers=ADMIN)
    assert approved.status_code == 200, approved.text
    assert approved.json()["change"]["status"] == ChangeStatus.READY
    assert _merge(project) == 1


def test_a_diff_the_platform_could_not_read_fails_closed(client, gh):
    gh.files_status = 502
    project = _factory(client)
    change = _ready_but_for(client, project, gh, gh.files)
    for gate in ("secrets", "tests", "packages", "protected"):
        assert not change["gates"][gate]["ok"]
    _sign(client, project, change["id"])
    assert _merge(project) == 0


# ── hidden scenarios ────────────────────────────────────────────────────────────


def _write_scenario(client, project, requirement_id, who="Rhea"):
    _, token = live_token(project["id"], _agent(project, who))
    return client.post("/agent/scenarios", headers=bearer(token), json={
        "requirement_id": requirement_id, "name": "two tabs, one hold",
        "checks": "a patient can hold a free slot, from two tabs at once",
        "command": "python3 scripts/hold_race.py --tabs 2"})


def test_only_a_challenge_seat_writes_scenarios_and_builders_never_read_them(client, gh):
    project = _factory(client, scenarios=True)
    change = _open(client, project, _claimed_task(client, project)).json()
    rid = change["requirement_id"]

    by_builder = _write_scenario(client, project, rid, who="Mika")
    assert by_builder.status_code == 403
    assert _write_scenario(client, project, rid).status_code == 200

    _, mika = live_token(project["id"], _agent(project, "Mika"))
    seen = client.get("/agent/scenarios", headers=bearer(mika))
    assert seen.status_code == 200
    assert "scenarios" not in seen.json()
    assert "hold_race" not in seen.text and "two tabs" not in seen.text
    assert seen.json()["counts"][0]["hidden"] == 1

    _, rhea = live_token(project["id"], _agent(project, "Rhea"))
    assert "hold_race" in client.get("/agent/scenarios", headers=bearer(rhea)).text


def test_a_change_waits_for_its_scenarios_on_its_exact_commit(client, gh):
    project = _factory(client, scenarios=True)
    change = _ready_but_for(client, project, gh, gh.files)
    gate = change["gates"]["scenarios"]
    assert not gate["ok"] and "no hidden scenario" in gate["detail"]

    _write_scenario(client, project, change["requirement_id"])
    _sign(client, project, change["id"])
    row = _change(change["id"])
    assert row.status == ChangeStatus.OPEN and "not yet run" in row.gates["scenarios"]["detail"]
    with db_session() as session:
        queued = session.query(RunQueue).filter_by(kind=RunKind.SCENARIO_CHECK).all()
        assert len(queued) == 1 and queued[0].trigger["sha"] == row.head_sha
        scenario_id = changes.live_scenarios(session, row.requirement_id)[0].id

    with db_session() as session:
        changes.record_scenarios(session, session.get(type(row), row.id), "old-sha",
                                 {scenario_id: True})
    assert _change(change["id"]).status == ChangeStatus.OPEN, "a pass on another commit"

    with db_session() as session:
        changes.record_scenarios(session, session.get(type(row), row.id), row.head_sha,
                                 {scenario_id: False})
    assert "0 of 1 pass" in _change(change["id"]).gates["scenarios"]["detail"]

    with db_session() as session:
        changes.record_scenarios(session, session.get(type(row), row.id), row.head_sha,
                                 {scenario_id: True})
    assert _change(change["id"]).status == ChangeStatus.READY
    assert _merge(project) == 1


# ── the runner, against a real git repository ───────────────────────────────────


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                        "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(cwd)})


@pytest.fixture
def repo(tmp_path) -> tuple[Path, str]:
    origin = tmp_path / "origin"
    origin.mkdir()
    _git("init", "-q", "-b", "main", cwd=origin)
    (origin / "hold.py").write_text("HOLD_MINUTES = 10\n")
    _git("add", ".", cwd=origin)
    _git("commit", "-q", "-m", "hold", cwd=origin)
    _git("checkout", "-q", "-b", "mika/hold", cwd=origin)
    (origin / "hold.py").write_text("HOLD_MINUTES = 10\nSHARED_ACROSS_TABS = True\n")
    _git("commit", "-q", "-am", "share", cwd=origin)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=origin, capture_output=True,
                         text=True, check=True).stdout.strip()
    return origin, sha


def test_the_runner_runs_scenarios_on_the_commit_and_leaves_nothing_behind(tmp_path, repo):
    origin, sha = repo
    where = tmp_path / "workspaces" / ".scenarios" / "run_1"
    job = RunJob(run_id="run_1", project_id="p", agent_id=None, kind=RunKind.SCENARIO_CHECK,
                 trigger={"change_id": "c"}, task_id=None, timeout_s=120,
                 workspace_dir=str(where), check_sha=sha, check_branch="mika/hold",
                 fetch_url=str(origin),
                 scenarios=[("s1", "grep -q SHARED_ACROSS_TABS hold.py"),
                            ("s2", "grep -q NOT_THERE hold.py")])
    result = daemon._run_scenarios(job)
    assert result.ok, result.summary
    assert '"s1": true' in result.check_output and '"s2": false' in result.check_output
    assert result.check_exit_code == 1
    assert not where.exists(), "the checkout holding the scenarios run is removed"


# ── CI, coverage and the platform's scan ────────────────────────────────────────

CI_FILE = [{"filename": ".github/workflows/ci.yml", "status": "modified",
            "patch": "@@\n-      run: make test\n+      run: make test || true"}]


def test_a_change_to_ci_waits_for_the_chairman(client, gh):
    project = _factory(client)
    change = _ready_but_for(client, project, gh, CI_FILE)
    assert not change["gates"]["ci_config"]["ok"]
    assert "chairman" in change["gates"]["ci_config"]["detail"]
    reasoned = _sign_with(client, project, change["id"], allow_ci_change=(
        "Adds a lint job; the test job is unchanged and still blocks on failure."))
    assert reasoned.json()["change"]["status"] == ChangeStatus.OPEN, "a seat is not enough"
    assert client.post(f"/admin/changes/{change['id']}/approve",
                       headers=ADMIN).json()["change"]["status"] == ChangeStatus.READY


def test_with_the_rule_off_a_ci_change_needs_a_signed_reason(client, gh):
    project = _factory(client, ci_changes_need_chairman=False)
    change = _ready_but_for(client, project, gh, CI_FILE)
    assert _sign(client, project, change["id"]).json()["change"]["status"] == ChangeStatus.OPEN
    reasoned = _sign_with(client, project, change["id"], allow_ci_change=(
        "Adds a lint job; the test job is unchanged and still blocks on failure."))
    assert reasoned.json()["change"]["status"] == ChangeStatus.READY


def _coverage(client, project, sha, title, delivery):
    return _hook(client, project, "check_run", {
        "action": "completed", "sender": {"login": "github-actions"},
        "check_run": {"head_sha": sha, "name": "coverage", "status": "completed",
                      "conclusion": "success", "output": {"title": title}}}, delivery)


def test_coverage_is_read_from_ci_on_the_commit_and_held_to_the_floor(client, gh):
    project = _factory(client, min_changed_line_coverage=80)
    change = _ready_but_for(client, project, gh, gh.files)
    assert "has not reported" in change["gates"]["coverage"]["detail"]
    _coverage(client, project, change["head_sha"], "61.0% of changed lines covered", "cov-1")
    _sign(client, project, change["id"])
    row = _change(change["id"])
    assert not row.gates["coverage"]["ok"] and "61%" in row.gates["coverage"]["detail"]
    _coverage(client, project, change["head_sha"], "87.5% of changed lines covered", "cov-2")
    assert _change(change["id"]).status == ChangeStatus.READY


def test_the_platform_scans_every_change_and_holds_on_what_it_finds(client, gh):
    project = _factory(client, platform_scans=True)
    change = _ready_but_for(client, project, gh, gh.files)
    assert "not scanned" in change["gates"]["scan"]["detail"]
    with db_session() as session:
        assert session.query(RunQueue).filter_by(kind=RunKind.CHANGE_SCAN).count() == 1
    _sign(client, project, change["id"])
    row = _change(change["id"])

    def scanned(result):
        with db_session() as session:
            changes.record_scan(session, session.get(type(row), row.id), row.head_sha, result)
        return _change(change["id"])

    ok = {"osv-scanner": True, "gitleaks": True}
    assert not scanned({"available": ok, "advisories_high": 1, "advisories": ["pillow"],
                        "secrets": 0}).gates["scan"]["ok"]
    assert "lacks gitleaks" in scanned({"available": {**ok, "gitleaks": False},
                                        "advisories_high": 0, "secrets": 0}
                                       ).gates["scan"]["detail"]
    assert scanned({"available": ok, "advisories_high": 0, "secrets": 0}
                   ).status == ChangeStatus.READY


def test_scan_output_is_read_from_the_tools_json():
    osv = ('{"results": [{"packages": [{"package": {"name": "pillow"}, '
           '"groups": [{"max_severity": "9.8"}], "vulnerabilities": [{"id": "GHSA-x"}]}, '
           '{"package": {"name": "left-pad"}, "groups": [{"max_severity": "4.3"}]}]}]}')
    leaks = '[{"RuleID": "aws-access-token", "File": "config.py"}]'
    result = daemon.parse_scan("osv-scanner=1\ngitleaks=0\n", osv, leaks)
    assert result["advisories"] == ["pillow"] and result["secrets"] == 1
    assert result["available"] == {"osv-scanner": True, "gitleaks": True}
    missing = daemon.parse_scan("osv-scanner=127\ngitleaks=127\n", None, None)
    assert missing["available"] == {"osv-scanner": False, "gitleaks": False}


def test_a_checkout_is_writable_by_the_sandbox_user(tmp_path, repo):
    """Found on the real image: the sandbox runs as uid 10001 and the runner
    (often root) makes the checkout, so every tool's report was silently
    unwritable — gitleaks read clean, lizard measured nothing, no SBOM."""
    import stat

    origin, sha = repo
    where = tmp_path / ".scans" / "run_w"
    job = RunJob(run_id="run_w", project_id="p", agent_id=None, kind=RunKind.CHANGE_SCAN,
                 trigger={}, task_id=None, timeout_s=60, workspace_dir=str(where),
                 check_sha=sha, check_branch="mika/hold", fetch_url=str(origin))
    assert daemon._checkout(job, where) is None
    for path in [where, *where.rglob("*")]:
        if path.is_symlink():
            continue
        mode = path.stat().st_mode
        assert mode & stat.S_IWOTH and mode & stat.S_IROTH, f"{path} {oct(mode)}"


def test_a_scanner_that_could_not_check_is_not_a_clean_scan():
    """Found on the real image: osv-scanner exits 129 when it cannot reach its
    advisory database, and leaves no report. That once read as "no
    advisories" and passed the gate."""
    offline = daemon.parse_scan("osv-scanner=129\ngitleaks=0\n", None, "[]")
    assert offline["available"] == {"osv-scanner": False, "gitleaks": True}
    unwritten = daemon.parse_scan("osv-scanner=1\ngitleaks=0\n", None, None)
    assert unwritten["available"] == {"osv-scanner": False, "gitleaks": False}, \
        "a tool whose report never landed checked nothing"
    no_packages = daemon.parse_scan("osv-scanner=128\ngitleaks=0\n", None, "[]")
    assert no_packages["available"] == {"osv-scanner": True, "gitleaks": True}


def test_a_runner_without_the_scanners_fails_the_gate_closed(tmp_path, repo):
    origin, sha = repo
    job = RunJob(run_id="run_s", project_id="p", agent_id=None, kind=RunKind.CHANGE_SCAN,
                 trigger={"change_id": "c"}, task_id=None, timeout_s=60,
                 workspace_dir=str(tmp_path / ".scans" / "run_s"), check_sha=sha,
                 check_branch="mika/hold", fetch_url=str(origin))
    result = daemon._run_change_scan(job)
    assert result.ok, result.summary
    import json
    data = json.loads(result.check_output)
    if not all(data["result"]["available"].values()):
        from yaaf.core.constitution import ConstitutionDoc
        from yaaf.core.models import Change
        fake = Change(head_sha=sha, findings={"scan": {"sha": sha, **data["result"]}})
        assert not changes.scan_gate(fake, ConstitutionDoc()).get("ok")
    assert not (tmp_path / ".scans" / "run_s").exists()


def test_a_change_left_waiting_only_on_the_chairman_tells_him_once(client, gh, monkeypatch):
    """Found by an audit of a new factory: its first change is its CI setup,
    which waits on the chairman — and the only sign of it was a button on the
    Changes tab nothing pointed at. Told once, when he is all that is left."""
    from yaaf.server import notify

    told: list[str] = []
    monkeypatch.setattr(notify, "chairman_notify",
                        lambda session, project, subject, body, **kw: told.append(subject))
    project = _factory(client)
    change = _ready_but_for(client, project, gh, CI_FILE)
    _sign_with(client, project, change["id"], allow_ci_change=(
        "Adds the first CI workflow; it runs the test suite and blocks on failure."))
    assert told == [f"Change #{change['number']} needs your approval"]
    with db_session() as session:
        from yaaf.core.models import Project

        changes.evaluate(session, session.get(Project, project["id"]),
                         session.get(changes.Change, change["id"]))
    assert len(told) == 1, "once per commit, however often the gates are read"
