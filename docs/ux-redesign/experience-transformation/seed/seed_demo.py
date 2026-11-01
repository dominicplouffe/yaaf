"""Seed a local YAAF with six projects in different states, for UX research.

Not product code. It reproduces the evidence behind ../report.html: projects
built from examples/, driven by the fire drill's scripted crew at $0, and one
supervised project holding a spend approval and an ask.

    python docs/ux-redesign/experience-transformation/seed/seed_demo.py /tmp/yaaf-demo

Then serve that database with yaaf-server (same YAAF_* variables as below)
and sign in as dominic@example.com / demo-password-123.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/yaaf-demo")
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

from starlette.testclient import TestClient  # noqa: E402

import yaaf.rehearsal as rehearsal  # noqa: E402
from yaaf.core import ids  # noqa: E402
from yaaf.core.config import settings  # noqa: E402
from yaaf.core.db import db_session, engine  # noqa: E402
from yaaf.core.models import Base, User  # noqa: E402
from yaaf.rehearsal import crew  # noqa: E402
from yaaf.runner.backends import mock as mock_backend  # noqa: E402
from yaaf.server import dispatcher, identity  # noqa: E402
from yaaf.server.app import create_app  # noqa: E402

EMAIL = "dominic@example.com"
CREDENTIAL_KEYS = ("billing_secret_id", "billing_secret_name", "credentials", "credential_names",
                   "fallback_secret_id", "fallback_credential_name")
AUTHORITY = ("plan_authority", "scope_authority", "commitment_authority", "verdict_policy",
             "capability_authority")
# example file, does the chairman act on what waits for him, ticks, start it?
PORTFOLIO = [
    ("01-your-first-hour.json", True, 40, True),
    ("07-tell-me-what-changed.json", True, 14, True),
    ("09-one-idea-thirty-days.json", False, 10, True),
    ("11-numbers-you-can-defend.json", False, 5, True),
    ("06-a-red-team.json", False, 0, False),
]


def client() -> TestClient:
    settings.cache_clear()
    Base.metadata.create_all(engine())
    with db_session() as session:
        user = identity.find_user(session, EMAIL)
        if user is None:
            user = User(id=ids.new_id(ids.USER), email=EMAIL, display_name="Dominic", is_admin=True)
            identity.set_password(user, "demo-password-123")
            session.add(user)
        _, token = identity.mint_token(session, user, "seed", days=30)
    http = TestClient(create_app(), base_url="http://seed")
    http.headers.update({"Authorization": f"Bearer {token}"})
    return http


def create(http: TestClient, filename: str, *, supervised: bool = False, start: bool = True) -> str:
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
    project_id = created.json()["id"]
    http.post(f"/admin/projects/{project_id}/amendments", json={
        "reason": "local demo: compress clock rules",
        "changes": {name: value for name, (value, _) in rehearsal.RELAXED.items()},
    })
    if start:
        http.post(f"/admin/projects/{project_id}/start")
    return project_id


def file_sample_decisions(api) -> None:
    """One spend approval and one ask, written the way the agent API requires."""
    api.post("/agent/approvals", json={
        "kind": "spend",
        "title": "Buy 12 months of EDGAR full-text search access ($75)",
        "body": "The free EDGAR endpoint rate-limits us and drops exhibits older than 2001. Two of "
                "the five companies in scope filed their key restatements before 2001. A paid "
                "full-text index covers both gaps.",
        "if_declined": "We continue on the free endpoint. Coverage of pre-2001 restatements stays "
                       "incomplete, and the final memo will say so for two of five companies.",
        "amount_usd": 75,
    })
    api.post("/agent/asks", json={
        "title": "Which fiscal year should be the baseline?",
        "body": "Three of the five filers changed their fiscal year end between 2019 and 2021.",
        "why": "Every comparison table depends on the baseline; changing it later means redoing "
               "all five extractions.",
        "options": ["Calendar 2019", "Each filer's own FY2019", "Most recent full year (FY2025)"],
        "recommendation": "Each filer's own FY2019 — it matches how they reported and needs no "
                          "restatement.",
        "answer_by_hours": 36,
        "default_action": "Use each filer's own FY2019 as the baseline and note it on every table.",
    })


def main() -> None:
    for name, value in rehearsal.COALESCE_PATCHES.items():
        setattr(dispatcher, name, value)
    rehearsal._patch_workspace_clone()
    http = client()
    for path in sorted((REPO / "roles").glob("*.json")):
        http.post("/admin/roles", json=json.loads(path.read_text()))
    for path in sorted((REPO / "crews").glob("*.json")):
        http.post("/admin/crews", json=json.loads(path.read_text()))

    plan = {create(http, f, start=s): (acts, ticks) for f, acts, ticks, s in PORTFOLIO}
    filed = {"done": False}

    def behave(api, job) -> str:
        summary = crew.dispatch(api, job, scope_project=True, holds=frozenset())
        if job.context_pack["you"]["kind"] == "ceo" and not filed["done"]:
            filed["done"] = True
            file_sample_decisions(api)
        return summary

    mock_backend.clear_registry()
    mock_backend.register("*", behave)
    for tick in range(1, 41):
        live = [pid for pid, (_, ticks) in plan.items() if ticks >= tick]
        if not live:
            break
        rehearsal._tick(http)
        for pid in live:
            if plan[pid][0]:
                rehearsal._be_the_chairman(http, pid, tick, [])

    filings = create(http, "10-read-the-filings.json", supervised=True)
    for _ in range(6):
        rehearsal._tick(http)
    print("seeded", OUT / "yaaf.db", "· supervised project", filings)


if __name__ == "__main__":
    main()
