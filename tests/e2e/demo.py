"""A local YAAF with weeks of believable history — for screenshots, design
review and the browser tests in web/e2e.

Not product code, and not a test: it builds a database the way production
would have, then ages it. Every project is created from examples/ through the
real admin API and worked by the fire drill's scripted crew through the real
agent API, so the rows are the platform's own. Then, because a drill compresses
a fortnight into seconds, each project's history is stretched back over the
days a real one would have taken, every run is priced, files are published
through the agent API, and two projects are made to go wrong the way real
ones do (a broken promise, a provider refusing the key).

    python tests/e2e/demo.py /tmp/yaaf-demo
    YAAF_DATABASE_URL=sqlite:////tmp/yaaf-demo/yaaf.db ... yaaf-server

or, to seed and then serve it on one port (what web/playwright.config.ts runs):

    python tests/e2e/demo.py /tmp/yaaf-demo --serve=8765

Sign in as dominic@example.com / demo-password-123. Every number on screen is
sample data in the shape of the real thing; nothing here is a real project.
"""

from __future__ import annotations

import base64
import json
import os
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
OUT = Path(_ARGS[0] if _ARGS else "/tmp/yaaf-demo").resolve()
SERVE = next((int(a.split("=", 1)[1]) for a in sys.argv[1:] if a.startswith("--serve=")), None)
if (OUT / "yaaf.db").exists():
    sys.exit(f"{OUT / 'yaaf.db'} already exists — point this at a new, empty directory.")
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update({
    "YAAF_DATABASE_URL": f"sqlite:///{OUT}/yaaf.db",
    "YAAF_SANDBOX_MODE": "subprocess",
    "YAAF_WORKSPACES_ROOT": str(OUT / "ws"),
    "YAAF_ARTIFACTS_ROOT": str(OUT / "art"),
    "YAAF_MASTER_KEY": "kkP1p6Rt0DZ2rWv1w5Zq3o8y6bT4uY9cQ1eS7gJ0aXk=",
    "YAAF_ALLOW_HOST_SUCCESS_TESTS": "1",
    "YAAF_SMTP_HOST": "",
    "YAAF_CHAIRMAN_WEBHOOK_URL": "",
})
sys.path.insert(0, str(REPO))

from sqlalchemy import DateTime, select, update  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

import yaaf.rehearsal as rehearsal  # noqa: E402
from yaaf.core import ids  # noqa: E402
from yaaf.core.config import settings  # noqa: E402
from yaaf.core.constitution import effective  # noqa: E402
from yaaf.core.db import db_session, engine  # noqa: E402
from yaaf.core.models import (  # noqa: E402
    Artifact,
    AuditLog,
    Base,
    Commitment,
    CommitmentStatus,
    Constitution,
    LedgerEntry,
    LedgerKind,
    Project,
    Run,
    RunStatus,
    User,
    utcnow,
)
from yaaf.core.tokens import mint  # noqa: E402
from yaaf.rehearsal import crew  # noqa: E402
from yaaf.runner.backends import mock as mock_backend  # noqa: E402
from yaaf.server import dispatcher, identity, policy  # noqa: E402
from yaaf.server.app import create_app  # noqa: E402

EMAIL = "dominic@example.com"
PASSWORD = "demo-password-123"
CREDENTIAL_KEYS = ("billing_secret_id", "billing_secret_name", "credentials", "credential_names",
                   "fallback_secret_id", "fallback_credential_name")
AUTHORITY = ("plan_authority", "scope_authority", "commitment_authority", "verdict_policy",
             "capability_authority")

#: name → (example file, does a scripted chairman answer what waits on him,
#: drill ticks, start it?, supervised?, days of history it is aged to)
PLAN = {
    "first-hour": ("01-your-first-hour.json", True, 40, True, False, 9),
    "change-watch": ("07-tell-me-what-changed.json", True, 30, True, False, 34),
    "idea-hunt": ("09-one-idea-thirty-days.json", False, 30, True, False, 13),
    "numbers": ("11-numbers-you-can-defend.json", True, 5, True, True, 30),
    "filings": ("10-read-the-filings.json", True, 7, True, True, 21),
    "argument": ("02-settle-an-argument.json", False, 3, True, True, 4),
    "desk": ("03-a-desk-the-world-wakes.json", False, 3, True, False, 6),
    "red-team": ("06-a-red-team.json", False, 0, False, False, 2),
}

rng = random.Random(7)


def client() -> TestClient:
    settings.cache_clear()
    Base.metadata.create_all(engine())
    with db_session() as session:
        user = User(id=ids.new_id(ids.USER), email=EMAIL, display_name="Dominic", is_admin=True)
        identity.set_password(user, PASSWORD)
        session.add(user)
        _, token = identity.mint_token(session, user, "seed", days=30)
    http = TestClient(create_app(), base_url="http://seed")
    http.headers.update({"Authorization": f"Bearer {token}"})
    return http


def create(http: TestClient, filename: str, *, supervised: bool, start: bool) -> dict:
    spec = json.loads((REPO / "examples" / filename).read_text())
    for role in spec.get("roles") or []:
        http.post("/admin/roles", json=role)
    body = dict(spec["project"])
    body["chairman_email"] = EMAIL
    for key in CREDENTIAL_KEYS:
        body.pop(key, None)
    integrations = dict(body.get("integrations") or {})
    integrations["runtime"] = "mock"
    integrations.pop("slack", None)
    integrations.pop("mcp_servers", None)
    body["integrations"] = integrations
    if supervised:
        body["constitution"] = {**(body.get("constitution") or {}), **dict.fromkeys(AUTHORITY, "chairman")}
    created = http.post("/admin/projects", json=body)
    created.raise_for_status()
    project = created.json()
    with db_session() as session:
        house = effective(session, project["id"])
        project["house_rules"] = {name: getattr(house, name) for name in rehearsal.RELAXED}
    set_rules(project["id"], {name: value for name, (value, _) in rehearsal.RELAXED.items()})
    if start:
        http.post(f"/admin/projects/{project['id']}/start")
    return project


def set_rules(project_id: str, values: dict) -> None:
    """The drill's clock rules, set and later put back on every version in
    place rather than by amendment. An amendment is the chairman's act and
    would be in the record — every brief would open "You changed 7 rules" —
    whereas this leaves the record as if the project had always run at the
    house pace, which is the history a real one would have."""
    with db_session() as session:
        for row in session.execute(
                select(Constitution).where(Constitution.project_id == project_id)).scalars():
            row.body = {**row.body, **values}


def agent_token(project_id: str, agent_id: str) -> tuple[str, str]:
    """A running run and its token, minted as the runner mints one."""
    plaintext, token_hash = mint()
    with db_session() as session:
        run = Run(id=ids.new_id(ids.RUN), project_id=project_id, agent_id=agent_id,
                  kind="agent_run", trigger={"type": "demo"}, status=RunStatus.RUNNING,
                  token_hash=token_hash, started_at=utcnow(), heartbeat_at=utcnow())
        session.add(run)
        run_id = run.id
    return run_id, plaintext


def finish(run_id: str) -> None:
    with db_session() as session:
        run = session.get(Run, run_id)
        run.status = RunStatus.DONE
        run.finished_at = utcnow()


def as_agent(http: TestClient, project: dict, agent_id: str, method: str, path: str, body: dict):
    run_id, token = agent_token(project["id"], agent_id)
    response = http.request(method, path, json=body, headers={"Authorization": f"Bearer {token}"})
    finish(run_id)
    return response


def publish(http: TestClient, project: dict, agent_id: str, filename: str, text: str,
            satisfies: list[str]) -> None:
    response = as_agent(http, project, agent_id, "POST", "/agent/artifacts", {
        "filename": filename, "satisfies": satisfies,
        "content_base64": base64.b64encode(text.encode()).decode(),
    })
    response.raise_for_status()


def employee_of(project: dict) -> str:
    for team in project["teams"]:
        for agent in team["agents"]:
            return agent["id"]
    return project["ceo_agent_id"]


def met_keys(project_id: str) -> list[str]:
    with db_session() as session:
        return [r.key for r in policy.scope_requirements(session, project_id)
                if r.status == "met"]


def price_runs(project_id: str, low: float, high: float) -> None:
    """A run on a real provider costs money; the drill's cost nothing. Price
    each the way a model run is booked — on the run and on the ledger."""
    with db_session() as session:
        for run in session.execute(select(Run).where(Run.project_id == project_id)).scalars():
            if run.kind not in dispatcher.MODEL_RUN_KINDS:
                continue
            usd = round(rng.uniform(low, high), 4)
            tokens_in = int(usd * 90_000)
            run.cost_usd = usd
            session.add(LedgerEntry(
                id=ids.new_id(ids.LEDGER), project_id=project_id, kind=LedgerKind.MODEL_USAGE,
                tokens_in=tokens_in, tokens_out=tokens_in // 9, usd=usd, run_id=run.id,
                note=f"{run.kind} {run.status}", at=run.finished_at or run.started_at or utcnow()))


def age(project_id: str, days: float) -> None:
    """Stretch a project's compressed history back over `days`.

    Every timestamp in every table that belongs to the project is moved: one
    in the past is rescaled from [created, now] onto [now - days, now], so the
    order of events and the shape of the burn-up survive; one in the future
    (an expiry, an answer-by, a kill date) keeps its distance from now."""
    now = utcnow()
    with db_session() as session:
        project = session.get(Project, project_id)
        born = project.created_at if project.created_at.tzinfo else project.created_at.replace(tzinfo=UTC)
    span = max((now - born).total_seconds(), 1.0)
    start = now - timedelta(days=days)
    scale = (now - start).total_seconds() / span

    def moved(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        aware = value if value.tzinfo else value.replace(tzinfo=UTC)
        if aware > now:
            return aware
        return start + timedelta(seconds=(aware - born).total_seconds() * scale)

    with engine().begin() as conn:
        for table in Base.metadata.sorted_tables:
            stamps = [c for c in table.columns if isinstance(c.type, DateTime)
                      or type(c.type).__name__ == "UTCDateTime"]
            if not stamps:
                continue
            if table.name == "projects":
                where = table.c.id == project_id
            elif "project_id" in table.c:
                where = table.c.project_id == project_id
            else:
                continue
            keys = list(table.primary_key.columns)
            for row in conn.execute(select(*keys, *stamps).where(where)).mappings():
                values = {c.name: moved(row[c.name]) for c in stamps}
                match = [k == row[k.name] for k in keys]
                conn.execute(update(table).where(*match).values(**values))


def spend_today(project_id: str, usd: float) -> None:
    """Spend inside this calendar month, so the money meters have something
    to say on a demo built on the 1st."""
    with db_session() as session:
        session.add(LedgerEntry(
            id=ids.new_id(ids.LEDGER), project_id=project_id, kind=LedgerKind.MODEL_USAGE,
            tokens_in=int(usd * 90_000), tokens_out=int(usd * 10_000), usd=usd,
            note="agent_run done", at=utcnow() - timedelta(hours=rng.uniform(1, 9))))


def main() -> None:
    for name, value in rehearsal.COALESCE_PATCHES.items():
        setattr(dispatcher, name, value)
    rehearsal._patch_workspace_clone()
    http = client()
    for path in sorted((REPO / "roles").glob("*.json")):
        http.post("/admin/roles", json=json.loads(path.read_text()))
    for path in sorted((REPO / "crews").glob("*.json")):
        http.post("/admin/crews", json=json.loads(path.read_text()))

    projects = {key: create(http, f, supervised=sup, start=start)
                for key, (f, _acts, _ticks, start, sup, _days) in PLAN.items()}

    mock_backend.clear_registry()
    mock_backend.register("*", lambda api, job: crew.dispatch(api, job, scope_project=True,
                                                              holds=frozenset()))
    paused: set[str] = set()
    for tick in range(1, 41):
        for key, (_f, _acts, ticks, start, _s, _d) in PLAN.items():
            if start and tick > ticks and key not in paused:
                http.post(f"/admin/projects/{projects[key]['id']}/pause",
                          json={"reason": "demo: enough drill"})
                paused.add(key)
        if len(paused) == sum(1 for v in PLAN.values() if v[3]):
            break
        rehearsal._tick(http)
        for key, (_f, acts, ticks, _start, _s, _d) in PLAN.items():
            if acts and tick <= ticks:
                rehearsal._be_the_chairman(http, projects[key]["id"], tick, [])
    for key in paused:
        http.post(f"/admin/projects/{projects[key]['id']}/start")
    # The drill lifted the clock rules to run a fortnight in seconds. Put them
    # back, or every page that projects cost from them (9,999 runs a day)
    # shows a month no real project would spend.
    for project in projects.values():
        set_rules(project["id"], project["house_rules"])

    filings = projects["filings"]
    as_agent(http, filings, filings["ceo_agent_id"], "POST", "/agent/approvals", {
        "kind": "spend", "title": "Buy 12 months of EDGAR full-text search access",
        "recommendation": "Approve it: it is the only way to reach the two pre-2001 restatements.",
        "body": "Two of the five companies in scope filed their key restatements before 2001, "
                "and the free EDGAR endpoint drops exhibits that old. A paid full-text index "
                "covers both gaps.",
        "if_declined": "We continue on the free endpoint; the memo marks two of five filers "
                       "as incomplete.",
        "amount_usd": 75,
    }).raise_for_status()
    as_agent(http, filings, filings["ceo_agent_id"], "POST", "/agent/asks", {
        "title": "Which fiscal year should be the baseline?",
        "body": "Three of the five filers changed their fiscal year end between 2019 and 2021.",
        "why": "Every comparison table depends on it; changing it later redoes all five.",
        "options": ["Calendar 2019", "Each filer's own FY2019", "Most recent full year (FY2025)"],
        "recommendation": "Each filer's own FY2019 — it matches how they reported.",
        "answer_by_hours": 36,
        "default_action": "Use each filer's own FY2019 and say so on every table.",
    }).raise_for_status()

    # Each file dated where a real one would sit in its project's history, so
    # "published 7h ago" is not what three weekly digests all say.
    def weeks_ago(n: int) -> str:
        return (utcnow() - timedelta(days=7 * n)).date().isoformat()

    files = {
        "first-hour": [("marathon-briefing.md", "# Running a marathon\n\nWhat it is…\n", 3.0),
                       ("glossary.md", "# Glossary\n\nBonk, taper, negative split…\n", 1.5)],
        "change-watch": [(f"digest-{weeks_ago(n)}.md", f"# What changed\n\n- {item}\n", 7.0 * n)
                         for n, item in ((3, "Python 3.14.0…"), (2, "Django 6.0 beta…"),
                                         (1, "FastAPI 0.120…"))],
        "filings": [("filings-archive.csv", "filer,form,year,url\n", 12.0),
                    ("restatements.csv", "filer,year,restated\n", 5.0)],
        "numbers": [("earnings-by-major.csv", "major,median_early,median_mid\n", 9.0)],
    }
    for key, entries in files.items():
        met = met_keys(projects[key]["id"])
        for i, (name, text, _ago) in enumerate(entries):
            publish(http, projects[key], employee_of(projects[key]), name, text,
                    met[i:i + 1] if met else [])

    for key in PLAN:
        price_runs(projects[key]["id"], 0.06, 0.42)

    for key, (_f, _a, _t, _s, _sup, days) in PLAN.items():
        age(projects[key]["id"], days)

    with db_session() as session:
        for key, entries in files.items():
            for name, _text, ago in entries:
                session.execute(update(Artifact).where(
                    Artifact.project_id == projects[key]["id"], Artifact.name == name)
                    .values(published_at=utcnow() - timedelta(days=ago)))

    # The desk's provider refuses its key: its last three runs failed.
    desk = projects["desk"]
    with db_session() as session:
        for i in range(3):
            at = utcnow() - timedelta(hours=3 - i)
            session.add(Run(id=ids.new_id(ids.RUN), project_id=desk["id"],
                            agent_id=desk["ceo_agent_id"], kind="agent_run",
                            trigger={"type": "heartbeat"}, status=RunStatus.FAILED,
                            error_kind="auth", started_at=at, finished_at=at + timedelta(minutes=1),
                            cost_usd=0.0))

    # Finished a couple of hours ago: the sign-off is waiting, its clock running.
    with db_session() as session:
        for row in session.execute(select(AuditLog).where(
                AuditLog.project_id == projects["first-hour"]["id"],
                AuditLog.action == "project.completion_ready")).scalars():
            row.at = utcnow() - timedelta(hours=2)

    # Filings promised the 20th and is pacing inside it.
    with db_session() as session:
        promise = session.execute(
            select(Commitment).where(Commitment.project_id == projects["filings"]["id"])
            .order_by(Commitment.created_at.desc()).limit(1)).scalar_one_or_none()
        if promise is not None:
            promise.deliver_by = utcnow() + timedelta(days=19)
            promise.status = CommitmentStatus.ACCEPTED

    # Numbers promised a date it has now missed with work still open.
    numbers = projects["numbers"]
    with db_session() as session:
        promise = session.execute(
            select(Commitment).where(Commitment.project_id == numbers["id"])
            .order_by(Commitment.created_at.desc()).limit(1)).scalar_one_or_none()
        if promise is not None:
            promise.deliver_by = utcnow() - timedelta(days=3)
            promise.projected_done_at = utcnow() + timedelta(days=9)
            promise.status = CommitmentStatus.BREACHED

    for key, usd in (("filings", 8.40), ("change-watch", 13.20), ("idea-hunt", 52.10),
                     ("numbers", 21.70), ("first-hour", 4.20), ("desk", 0.90)):
        spend_today(projects[key]["id"], usd)

    print("seeded", OUT / "yaaf.db")
    for key, project in projects.items():
        print(f"  {key:12s} {project['id']}")


def serve(port: int) -> None:
    """Replace this process with the real server on the database just built:
    the same YAAF_* environment, its dispatcher and janitor running, so what a
    browser does here is what it would do in production."""
    os.environ.update({
        "YAAF_API_HOST": "127.0.0.1",
        "YAAF_API_PORT": str(port),
        "YAAF_PUBLIC_BASE_URL": f"http://127.0.0.1:{port}",
        "YAAF_WEB_DIST": str(REPO / "web" / "dist"),
    })
    os.chdir(REPO)
    sys.stdout.flush()
    os.execv(sys.executable, [sys.executable, "-c", "from yaaf.server.main import main; main()"])


if __name__ == "__main__":
    main()
    if SERVE:
        serve(SERVE)
