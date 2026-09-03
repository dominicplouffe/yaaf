"""Invariant selftests: the fire drill.

A preflight has to be trustworthy in a specific way: it must not lie about
whether the project works, and it must not do anything a preflight has no
business doing. Both halves are attacked here.

  * it isolates — a throwaway SQLite file it created, asserted rather than
    assumed, because the engine is a process-global and a drill that ran
    inside the API process would re-point the server at its own scratch data
  * it reaches nothing — no command out of a spec file, no evidence URL, no
    credential, no socket, and no mail to the chairman it invented
  * it relaxes exactly the rules that measure clock time, says which, and
    reports every other rule as the chairman wrote it
  * a broken spec is a legible finding rather than a crash, and a working one
    walks all the way to a requirement settling true
"""

from __future__ import annotations

import copy
import json
import os

import pytest

from tests.conftest import ADMIN
from yaaf import rehearsal
from yaaf.rehearsal import report as rehearsal_report

# The drill re-points the process-global engine, so it must never run in the
# same process as a test that is using it. `client` is not requested by any
# test in this module for exactly that reason; the two that need HTTP get a
# fresh app of their own from the drill itself.

MINIMAL = {
    "slug": "drill",
    "name": "Drill",
    "goal": "Ship one briefing note for the committee, sourced and dated, this month.",
    "mode": "deliver",
    "ceo_role_slug": "ceo",
    "teams": [{"role_slug": "worker", "name": "Desk",
               "employees": ["Ada", "Grace"], "lead": "Ada"}],
}

ROLES = [
    {"slug": "ceo", "name": "Ceo", "is_ceo_role": True, "description": "plans",
     "system_prompt": "You are the CEO.", "model": "claude-sonnet-5"},
    {"slug": "worker", "name": "Worker", "is_ceo_role": False, "description": "works",
     "system_prompt": "You do the work.", "model": "claude-sonnet-5"},
]


def spec(**overrides) -> dict:
    project = {**MINIMAL, **overrides}
    return {"project": project, "roles": ROLES}


def walk(report: dict) -> dict:
    return {step["key"]: step for step in report["walk"]}


@pytest.fixture(autouse=True)
def caller_db(tmp_path):
    """A real database for the *caller*, restored after every drill.

    Two jobs. It gives these tests something to check the drill against — "the
    caller's data is untouched" is not a claim you can make about a database
    that does not exist. And it puts the process-global engine back afterwards,
    because the drill re-points it by design and then deletes the file it
    pointed at; without this a later test inherits a database that is gone.
    That hazard is real and the drill's own guard cannot cover it — the guard
    protects the drill's caller, not its successor.

    Restoring by URL is not good enough, which is what this used to do:
    `str(engine().url)` masks the password as `***`, so a Postgres run came
    back with a literal `***` where its credentials were and every later test
    in the worker died on `password authentication failed`. `swap_engine`
    exists for exactly this — it hands back the previous engine OBJECT.
    """
    from yaaf.core.db import engine, swap_engine
    from yaaf.core.models import Base

    restore = swap_engine(f"sqlite:///{tmp_path}/caller.db")
    Base.metadata.create_all(engine())
    try:
        yield
    finally:
        restore()


@pytest.fixture(scope="session")
def _default_drill():
    """One drill for every test that only wants to read its report.

    A drill is a whole project lifecycle — roughly three seconds — and this
    file ran the identical default spec seven times to produce seven identical
    dictionaries. That made this the slowest file in the suite by a wide
    margin, and under xdist the worker holding it set the floor for the whole
    run.

    Only for tests that read the report. A test asserting on a SIDE EFFECT —
    what the drill wrote, what it left behind, what it restored, what it
    refused to touch — must run its own, because the effect it is looking for
    happens once and this fixture would hand it a report from a drill that
    already finished.
    """
    return rehearsal.rehearse(spec())


#: The dial the drill's default spec no longer carries. A spec that says
#: nothing about the five authority rules is created autonomous now, so the
#: shared drill above rehearses an autonomous project — which is what a
#: chairman actually gets. The two tests below that are ABOUT the parking need
#: the other end of the dial, and pay for their own drill to get it.
SUPERVISED = {"plan_authority": "chairman", "scope_authority": "chairman",
              "commitment_authority": "chairman", "verdict_policy": "chairman",
              "capability_authority": "chairman"}


@pytest.fixture(scope="session")
def _supervised_drill():
    """One more drill, for the two tests about what parks on a human.

    Deliberately slow — a second whole project lifecycle, ~3 seconds — and
    shared for the same reason as `_default_drill`: the alternative is two
    drills of the identical supervised spec producing the identical report.
    """
    return rehearsal.rehearse(spec(slug="drill-super", constitution=SUPERVISED))


@pytest.fixture()
def supervised_report(_supervised_drill):
    return copy.deepcopy(_supervised_drill)


@pytest.fixture()
def report(_default_drill):
    """A private copy of the shared report.

    Handed out per test so that one test mutating the dict it was given cannot
    reach into another's, which is the only way sharing could couple them.
    """
    return copy.deepcopy(_default_drill)


# ── it works ────────────────────────────────────────────────────────────────────


def test_a_sound_spec_walks_all_the_way_to_a_requirement_settling_true(report):
    assert report["ok"], report["verdict"]
    steps = walk(report)
    for key in ("created", "started", "plan_proposed", "scope_accepted", "plan_gate",
                "work_created", "work_claimed", "work_closed", "requirement_met"):
        assert steps[key]["ok"], f"{key} not reached: {report['verdict']}"


def test_it_counts_every_time_the_project_stopped_and_waited_for_a_human(supervised_report):
    """The autonomy dial in numbers. Nothing else in the product could produce
    this before the money was spent, because the dial decides which verbs park
    and only the work decides how often they come up."""
    kinds = {row["kind"] for row in supervised_report["interventions"]}
    assert "accept scope" in kinds
    assert sum(row["count"] for row in supervised_report["interventions"]) >= 3


def test_an_autonomous_dial_waits_for_nobody(report, supervised_report):
    """The same spec, one dial moved: where the CEO holds the verb there is
    nothing to park, so the drill has nothing to do as chairman. That contrast
    is the whole argument for the dial, and it is now measurable.

    The autonomous side is the *default* drill, because a spec that says
    nothing about the dial is created autonomous — so this also holds the line
    that the drill rehearses the project the chairman will really get, right
    down to the CEO accepting its own scope and judging its own observables.
    """
    waits = {r["kind"] for r in report["interventions"]}
    assert "accept scope" not in waits
    assert "judge an observable" not in waits
    assert report["ok"], report["verdict"]
    assert (sum(r["count"] for r in report["interventions"])
            < sum(r["count"] for r in supervised_report["interventions"]))


def test_an_explore_project_is_not_marked_down_for_having_no_plan():
    """Explore has no requirements, no plan gate and no commitment BY DESIGN.
    Drawing those as misses would report the absence of a feature as the
    failure of one."""
    report = rehearsal.rehearse(spec(slug="wander", mode="explore"))
    steps = walk(report)
    for key in ("plan_gate", "commitment", "requirement_met"):
        assert steps[key]["na"], key
    assert steps["work_closed"]["ok"]
    assert report["ok"]


# ── it finds real problems ──────────────────────────────────────────────────────


def test_a_role_nothing_can_resolve_stops_the_drill_and_names_it():
    report = rehearsal.rehearse(spec(
        slug="norole",
        teams=[{"role_slug": "astrophysicist", "name": "Science",
                "employees": ["Ada"], "lead": "Ada"}]))
    assert not report["ok"]
    assert "astrophysicist" in walk(report)["roles"]["detail"]
    assert not walk(report)["created"]["ok"]


def test_a_spec_the_platform_refuses_reports_the_platform_s_own_words():
    """A drill that paraphrased the refusal would be a second voice to drift
    from the one the chairman sees when he presses Create."""
    report = rehearsal.rehearse(spec(
        slug="badsched",
        teams=[{"role_slug": "worker", "name": "Desk", "employees": ["Ada"],
                "lead": "Ada", "schedules": {"Nobody": "07:00"}}]))
    assert not report["ok"]
    assert "not among its employees" in walk(report)["created"]["detail"]


def test_a_project_with_nobody_in_it_is_told_so_rather_than_left_to_stall():
    """The case with no wall to report: a CEO with nobody to give work to never
    files a task, so the refusals list is empty and "it stalled" would be the
    whole story. The shape of the project is the finding."""
    report = rehearsal.rehearse(spec(slug="nohands", teams=[]))
    assert not report["ok"]
    assert any("no teams" in f for f in report["findings"])
    assert "nobody to attempt it" in report["verdict"]


def test_a_spec_with_no_goal_is_refused_before_anything_is_built():
    with pytest.raises(rehearsal.RehearsalError) as caught:
        rehearsal.rehearse({"project": {**MINIMAL, "goal": ""}, "roles": ROLES})
    assert "goal" in str(caught.value)


# ── what it is honest about ─────────────────────────────────────────────────────


def test_it_relaxes_only_rules_that_measure_clock_time_and_says_which(report):
    """The one thing the drill changes about the chairman's rules, declared in
    the report rather than buried in the code that does it."""
    relaxed = {row["rule"] for row in report["relaxed"]}
    assert relaxed == set(rehearsal.RELAXED) | {"notice and message batching"}
    for row in report["relaxed"]:
        assert row["why"], row["rule"]
    # Every one of them is about elapsed time, which is what justifies lifting
    # it. A rule that shaped WORK rather than PACE would not belong here.
    assert relaxed <= {
        "wakes_per_agent_per_hour", "runs_per_project_per_day",
        "runs_per_project_per_hour", "inbox_runs_per_project_per_hour",
        "max_concurrent_runs", "ceo_heartbeat_hours", "ceo_heartbeat_always",
        "notice and message batching",
    }


def test_the_arithmetic_uses_the_chairman_s_numbers_not_the_relaxed_ones():
    """The drill lifted the daily ceiling to 9999. If the report's pacing read
    the constitution in force at the end, it would tell him his own cap was
    9999 — the single most misleading thing this feature could do."""
    report = rehearsal.rehearse(spec(
        slug="paced", constitution={"runs_per_project_per_day": 6}))
    assert report["capacity"]["available"] < 100


def test_the_plan_is_reported_as_proposed_not_as_it_stands_at_the_end(report):
    """A project that finished everything has no live scope, and `plan_arithmetic`
    counts live scope. Reporting that would say "it planned nothing" about the
    one drill that went perfectly."""
    assert report["plan"]["requirements"] >= 1
    assert report["plan"]["estimated_runs"] >= 1


def test_it_names_a_break_even_price_rather_than_inventing_one():
    """A box with no priced runs cannot say what a plan costs. Inverting the
    question gives one number a chairman can check against a bill he has
    already seen, which is more than a fabricated total would."""
    report = rehearsal.rehearse(spec(slug="unpriced",
                                     constitution={"monthly_usd_cap": 120}))
    money = report["money"]
    assert money["usd_per_run"] is None
    assert money["total"] is None
    assert money["break_even_usd_per_run"] == pytest.approx(
        120 / report["plan"]["adjusted_runs"], rel=0.01)


def test_a_price_the_chairman_supplies_is_used_and_labelled_as_his():
    report = rehearsal.rehearse(spec(slug="priced"), usd_per_run=2.5)
    money = report["money"]
    assert money["source"] == "the number you gave me"
    assert money["total"] == pytest.approx(2.5 * report["plan"]["adjusted_runs"])


def test_it_says_what_it_did_not_exercise(report):
    joined = " ".join(report["not_exercised"])
    assert "command" in joined
    assert "verifier" in joined or "verification" in joined
    assert "socket" in joined or "credential" in joined
    assert "no evidence at all about whether a real model" in report["caveat"]


# ── what it must never do ───────────────────────────────────────────────────────


def test_it_runs_no_command_out_of_a_spec_file():
    """The property that keeps `rehearse` from being remote code execution with
    a friendly name: every test the drill's crew writes is an observable, so
    the platform's command lane never fires."""
    from pathlib import Path

    from yaaf.core.db import db_session
    from yaaf.core.models import Requirement, RunKind, Task

    marker = "touch /tmp/yaaf-rehearsal-should-never-exist"
    report = rehearsal.rehearse(spec(slug="rce", goal=f"Run this: {marker}"))
    assert report["ok"]
    assert not Path("/tmp/yaaf-rehearsal-should-never-exist").exists()
    # And nothing of the drill's reached the caller's database.
    with db_session() as session:
        assert session.query(Task).count() == 0
        assert session.query(Requirement).count() == 0
    assert RunKind.SUCCESS_CHECK not in {step["key"] for step in report["walk"]}


def test_every_test_the_crew_writes_is_an_observable():
    """Checked inside the drill's own database, before it is discarded."""
    seen: list[dict] = []
    original = rehearsal.report.build

    def capture(client, project_id, *args, **kwargs):
        if project_id is not None:
            for task in client.get(f"/observe/projects/{project_id}/board").json():
                seen.append(task["success_test"])
        return original(client, project_id, *args, **kwargs)

    rehearsal.report.build = capture
    try:
        rehearsal.rehearse(spec(slug="observables"))
    finally:
        rehearsal.report.build = original
    assert seen, "the drill created no work to inspect"
    assert all(test["type"] == "observable" for test in seen), seen


def test_it_refuses_to_run_against_a_database_it_did_not_create(tmp_path):
    """The guard that makes the drill safe to invoke from a server. The engine
    is a process-global; a drill that quietly accepted somebody else's database
    would build a project inside a live deployment. Simulated by making the
    re-point land somewhere other than the file the drill asked for."""
    from yaaf.core import db as db_module

    real = db_module.swap_engine

    def hijack(url: str):
        return real(f"sqlite:///{tmp_path}/somebody-elses.db")

    db_module.swap_engine = hijack
    try:
        with pytest.raises(rehearsal.RehearsalError) as caught:
            rehearsal.rehearse(spec())
    finally:
        db_module.swap_engine = real
    assert "throwaway file it created" in str(caught.value)
    # And it put the engine back on the way out, even from the refusal path.
    from yaaf.core.db import engine

    assert "somebody-elses" not in str(engine().url)


def test_the_drill_never_touches_the_caller_s_data():
    """The engine is restored by the fixture; what this proves is that the
    drill's own project never appears anywhere but its own database."""
    from yaaf.core.db import db_session
    from yaaf.core.models import Project

    rehearsal.rehearse(spec(slug="ephemeral"))
    with db_session() as session:
        slugs = {p.slug for p in session.query(Project).all()}
    assert "ephemeral" not in slugs


def test_the_drill_builds_no_workspace_in_the_live_root(tmp_path, monkeypatch):
    """Materializing a job prepares a workspace, and the drill runs the real
    dispatcher — so it mkdir'd `<workspaces_root>/<slug>` in the LIVE root. A
    preflight that promises to create nothing left a directory named after a
    project that does not exist, and, where the slug matched a real project,
    reached into that project's own working tree."""
    root = tmp_path / "live-workspaces"
    root.mkdir()
    monkeypatch.setenv("YAAF_WORKSPACES_ROOT", str(root))
    monkeypatch.setenv("YAAF_ARTIFACTS_ROOT", str(tmp_path / "live-artifacts"))

    report = rehearsal.rehearse(spec(slug="ephemeral"))

    assert report["ok"], report["verdict"]
    assert list(root.iterdir()) == []


def test_a_state_root_it_cannot_write_never_stops_it(tmp_path, monkeypatch):
    """Where the drill is actually invoked from, the live root is not merely
    the wrong place to write — it is unwritable. The API container mounts the
    workspaces volume read-only, because the API has no business writing agent
    workspaces, and the dashboard's fire drill died on an OSError from that
    mount before it could report anything at all. Simulated with a root whose
    parent is a file, which no permission bit can make writable."""
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("a file sits where the volume would be mounted")
    monkeypatch.setenv("YAAF_WORKSPACES_ROOT", str(blocked / "workspaces"))
    monkeypatch.setenv("YAAF_ARTIFACTS_ROOT", str(blocked / "artifacts"))

    # Its own drill, not the shared one: the effect under test is what this
    # drill does with an unwritable root, which only happens while the
    # monkeypatch above is in force.
    report = rehearsal.rehearse(spec())
    assert report["ok"], report["verdict"]
    assert walk(report)["work_claimed"]["ok"], walk(report)["work_claimed"]["detail"]


def test_a_github_backed_spec_is_never_cloned_by_the_drill(tmp_path, monkeypatch):
    """The drill says it opens no socket. Preparing a workspace clones the
    project's repo on first touch, so a spec carrying `github_repo` made it
    shell out to `git clone` and reach the network — a promise broken by the
    one path the drill deliberately runs for real."""
    from yaaf.runner import daemon as daemon_module

    monkeypatch.setenv("YAAF_WORKSPACES_ROOT", str(tmp_path / "live-workspaces"))
    called: list[tuple] = []

    def spy(*args, **kwargs):
        called.append(args)

    monkeypatch.setattr(daemon_module, "_clone_workspace", spy)

    report = rehearsal.rehearse(spec(github_repo="someone/private-repo"))

    assert report["ok"], report["verdict"]
    assert called == []
    # And what was there before is back afterwards — the drill hands the global
    # over rather than leaving its own stub for whatever runs next.
    assert daemon_module._clone_workspace is spy


def test_a_drill_never_delivers_a_notice_to_the_chairman_it_invented(monkeypatch):
    """The chairman a drill invents is `rehearsal@localhost`, and the notices a
    project raises are real — a verdict waiting, a commitment at risk, an audit
    sample. `chairman_notify` reads its mail and webhook config out of the
    environment, and the drill inherits the server's, so one fire drill put
    eight emails and eight outbound POSTs on the wire about a project that does
    not exist. The batching windows the drill lifts to zero were the only thing
    that had been holding any of them back."""
    from yaaf.server import janitor as janitor_module
    from yaaf.server import notify as notify_module

    monkeypatch.setenv("YAAF_SMTP_HOST", "smtp.example.invalid")
    monkeypatch.setenv("YAAF_CHAIRMAN_WEBHOOK_URL", "https://hooks.example.invalid/x")
    delivered: list[tuple] = []
    monkeypatch.setattr(notify_module, "_send_email",
                        lambda *args: delivered.append(("email", *args)))
    monkeypatch.setattr(notify_module, "_send_webhook",
                        lambda *args: delivered.append(("webhook", *args)))

    # The janitor binds `chairman_notify` at import, so this is the name its
    # own calls go through. Wrapping rather than replacing it keeps the drill
    # honest and makes the assertion below non-vacuous: nothing was delivered
    # BECAUSE the transport had nowhere to go, not because nothing was raised.
    raised: list[str] = []
    original = janitor_module.chairman_notify

    def spy(session, project, subject, body):
        raised.append(subject)
        return original(session, project, subject, body)

    monkeypatch.setattr(janitor_module, "chairman_notify", spy)

    # Its own drill, not the shared one: the spy above only sees notices raised
    # while it is installed.
    report = rehearsal.rehearse(spec())
    assert report["ok"], report["verdict"]
    assert raised, "the drill raised no notice at all; this test proves nothing"
    assert delivered == []


def test_it_gives_the_notification_channels_back_including_the_ones_that_were_unset(
        monkeypatch):
    """Same discipline as the state roots: a drill that muted the chairman and
    kept him muted would leave the real deployment silent for every notice
    after it — which is the failure mode you would not notice."""
    from yaaf.core.config import settings

    monkeypatch.setenv("YAAF_SMTP_HOST", "smtp.example.invalid")
    monkeypatch.delenv("YAAF_CHAIRMAN_WEBHOOK_URL", raising=False)
    settings.cache_clear()

    rehearsal.rehearse(spec())

    assert os.environ["YAAF_SMTP_HOST"] == "smtp.example.invalid"
    assert "YAAF_CHAIRMAN_WEBHOOK_URL" not in os.environ
    assert settings().smtp_host == "smtp.example.invalid"
    assert settings().chairman_webhook_url == ""


def test_it_refuses_to_run_while_a_notification_channel_still_has_a_target(
        tmp_path, monkeypatch):
    """The notification half of the database guard: the drill checks that the
    mute took rather than trusting that it did, and puts the environment back
    from the refusal path too."""
    from types import SimpleNamespace

    from yaaf.core import config as config_module

    stub = SimpleNamespace(smtp_host="smtp.example.invalid", chairman_webhook_url="")
    fake = lambda: stub  # noqa: E731 — a one-liner stand-in for the cached accessor
    fake.cache_clear = lambda: None
    monkeypatch.setattr(config_module, "settings", fake)

    before = dict(os.environ)
    with pytest.raises(rehearsal.RehearsalError) as caught:
        rehearsal._mute_chairman_notifications()
    assert "still have somewhere to go" in str(caught.value)
    assert dict(os.environ) == before


def test_it_gives_the_state_roots_back_including_the_ones_that_were_unset(tmp_path,
                                                                         monkeypatch):
    """Same discipline as the engine and the batching windows: a global the
    drill borrowed and kept would leave every later caller in this process
    writing into a directory that has been deleted."""
    from yaaf.core.config import settings

    monkeypatch.setenv("YAAF_WORKSPACES_ROOT", str(tmp_path / "live-workspaces"))
    monkeypatch.delenv("YAAF_ARTIFACTS_ROOT", raising=False)
    settings.cache_clear()
    before = settings().artifacts_root

    rehearsal.rehearse(spec())

    assert os.environ["YAAF_WORKSPACES_ROOT"] == str(tmp_path / "live-workspaces")
    assert "YAAF_ARTIFACTS_ROOT" not in os.environ
    assert settings().artifacts_root == before


def test_it_refuses_a_workspace_root_that_is_not_the_one_it_created(tmp_path,
                                                                    monkeypatch):
    """The workspace half of the database guard: the drill checks that the
    root it is about to write into is the throwaway one, rather than trusting
    that the re-point landed. Simulated by making the settings it reads back
    report somebody else's directory."""
    from types import SimpleNamespace

    from yaaf.core import config as config_module

    stub = SimpleNamespace(workspaces_root=str(tmp_path / "somebody-elses"),
                           artifacts_root=str(tmp_path / "somebody-elses"))
    fake = lambda: stub  # noqa: E731 — a one-liner stand-in for the cached accessor
    fake.cache_clear = lambda: None
    monkeypatch.setattr(config_module, "settings", fake)

    before = dict(os.environ)
    with pytest.raises(rehearsal.RehearsalError) as caught:
        rehearsal._isolate_state_roots(tmp_path / "drill")
    assert "throwaway directory it created" in str(caught.value)
    # And it put the environment back on the way out, from the refusal path too.
    assert dict(os.environ) == before


# ── the rendering ───────────────────────────────────────────────────────────────


def test_the_text_report_carries_the_verdict_the_walk_and_the_caveat(report):
    text = rehearsal_report.render_text(report)
    assert report["verdict"] in text
    for step in report["walk"]:
        assert step["label"] in text
    assert report["caveat"] in text
    assert "Rules the drill lifted" in text


def test_a_report_survives_json_because_the_api_route_ships_it_that_way(report):
    assert json.loads(json.dumps(report, default=str))["ok"] is True


def test_a_bad_spec_file_is_a_message_not_a_traceback(tmp_path):
    path = tmp_path / "spec.json"
    path.write_text("{not json")
    with pytest.raises(rehearsal.RehearsalError) as caught:
        rehearsal.load_spec(path)
    assert "not valid JSON" in str(caught.value)


def test_a_bare_project_spec_works_as_well_as_a_setup_file():
    """`yaaf create-project` takes the project object alone; a setup file wraps
    it. The drill takes either, because a chairman has whichever he has."""
    report = rehearsal.rehearse({**MINIMAL, "slug": "bare", "roles": ROLES})
    assert report["ok"], report["verdict"]


def test_the_admin_route_runs_the_drill_out_of_process(client):
    """It has to be a subprocess: the drill re-points a process-global engine,
    and doing that inside the API process would answer every later request from
    a scratch database. The proof is that the server still works afterwards."""
    body = {"spec": spec(slug="over-http"), "days": 14}
    response = client.post("/admin/rehearse", json=body, headers=ADMIN)
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    # The engine the test session is holding is untouched.
    assert client.get("/observe/projects", headers=ADMIN).status_code == 200


def test_a_bundle_that_asks_for_an_independent_read_still_finishes():
    """The drill is the one place a chairman finds out a bundle cannot run
    before he funds it — so a drill that cannot run a rule the platform ships,
    and the MCP `rehearse` description recommends, is worse than no drill.

    `_close` read a 200 as "closed". Under `deliverable_review` a 200 means the
    task went to REVIEW, nobody in the mock crew reviewed it, and REVIEW holds
    the author's one-at-a-time slot — so the whole crew wedged and the report
    said "it plans and creates work, but nothing closed". A true sentence about
    the drill, read by the chairman as a sentence about his bundle.
    """
    report = rehearsal.rehearse(spec(
        slug="reads", constitution={"deliverable_review": "independent_read"},
        teams=[{"role_slug": "worker", "name": "Desk",
                "employees": ["Ada", "Grace"], "lead": "Ada"},
               {"role_slug": "reader", "name": "Review", "employees": ["Juno"]}],
    ) | {"roles": ROLES + [
        {"slug": "reader", "name": "Reader", "is_ceo_role": False,
         "description": "reads", "category": "challenge",
         "system_prompt": "You read finished work.", "model": "claude-sonnet-5"}]})
    assert report["ok"], report["verdict"]
    assert "closed it with evidence" in report["verdict"]
    read = next(s for s in report["walk"] if s["key"] == "work_read")
    assert read["ok"] and not read["na"], read


def test_the_read_is_not_drawn_as_a_miss_on_a_bundle_that_never_asked(client=None):
    """The other half: a rung nobody switched on must render N/A, not red.
    Drawing it as a miss reports the absence of a feature as the failure of
    one — the mistake NOT_IN_EXPLORE exists to prevent."""
    report = rehearsal.rehearse(spec(slug="no-read"))
    read = next(s for s in report["walk"] if s["key"] == "work_read")
    assert read["na"] and not read["ok"], read
    assert report["ok"], report["verdict"]
